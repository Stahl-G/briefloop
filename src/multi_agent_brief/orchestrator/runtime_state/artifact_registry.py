"""Artifact registry helpers for Orchestrator runtime state."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from multi_agent_brief.contracts.schemas.audit_report import AuditReportContract
from multi_agent_brief.contracts.schemas.claim import ClaimContract
from multi_agent_brief.core.claim_ledger import ClaimLedger
from multi_agent_brief.core.schemas import Claim
from multi_agent_brief.feedback.feedback_contract import optional_feedback_artifact_activated
from multi_agent_brief.orchestrator.runtime_state._io import _sha256_file
from multi_agent_brief.orchestrator.runtime_state.errors import (
    E_TRANSACTION_INTEGRITY,
    RuntimeStateError,
)
from multi_agent_brief.orchestrator.runtime_state.workflow import (
    project_stage_completion_for_read,
    interpret_stage_completion,
    _stage_is_complete_or_skipped,
)
from multi_agent_brief.provenance.contract import provenance_artifact_activated
from multi_agent_brief.quality_gates.contract import quality_gate_artifact_activated


ARTIFACT_REGISTRY_SCHEMA = "multi-agent-brief-artifact-registry/v1"

ARTIFACT_EXPECTED = "expected"
ARTIFACT_MISSING = "missing"
ARTIFACT_PRESENT = "present"
ARTIFACT_VALID = "valid"
ARTIFACT_INVALID = "invalid"


@dataclass(frozen=True)
class FrozenArtifactIntegrityVerdict:
    """Single interpretation of frozen artifact integrity."""

    kind: str
    value: dict[str, Any]
    reasons: tuple[str, ...] = ()


def _validate_artifact(path: Path, fmt: str, artifact_id: str = "") -> tuple[str, str]:
    if not path.exists():
        return ARTIFACT_EXPECTED, "not_checked"
    if not path.is_file():
        return ARTIFACT_INVALID, "not_a_file"
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return ARTIFACT_INVALID, "decode_error"
    except OSError:
        return ARTIFACT_INVALID, "read_error"
    if not text.strip():
        return ARTIFACT_INVALID, "empty"

    try:
        if fmt == "json":
            payload = json.loads(text)
            if artifact_id == "claim_ledger":
                return _validate_claim_ledger_payload(payload)
            if artifact_id == "audit_report":
                return _validate_audit_report_payload(payload)
        elif fmt in {"yaml", "yml"}:
            yaml.safe_load(text)
        elif fmt == "markdown":
            pass
    except json.JSONDecodeError:
        return ARTIFACT_INVALID, "parse_error"
    except yaml.YAMLError:
        return ARTIFACT_INVALID, "parse_error"

    return ARTIFACT_VALID, "valid_minimum"


def _validate_claim_ledger_payload(payload: Any) -> tuple[str, str]:
    try:
        claims = ClaimLedger._claim_items_from_json(payload)
    except ValueError as exc:
        return ARTIFACT_INVALID, f"claim_ledger_schema_error:{exc}"

    seen_ids: set[str] = set()
    for idx, claim in enumerate(claims):
        for field in ("claim_id", "statement", "source_id", "evidence_text"):
            value = claim.get(field)
            if not isinstance(value, str) or not value.strip():
                return ARTIFACT_INVALID, f"claim_ledger_schema_error:claim[{idx}].{field}"
        claim_id = str(claim["claim_id"]).strip()
        if claim_id in seen_ids:
            return ARTIFACT_INVALID, f"claim_ledger_schema_error:duplicate_claim_id:{claim_id}"
        seen_ids.add(claim_id)
        violations = ClaimContract.validate(claim)
        errors = [violation for violation in violations if violation.severity == "error"]
        if errors:
            first = errors[0]
            return ARTIFACT_INVALID, f"claim_ledger_schema_error:claim[{idx}].{first.field}"

    try:
        ledger = ClaimLedger([Claim.from_dict(item) for item in claims])
    except (TypeError, ValueError) as exc:
        return ARTIFACT_INVALID, f"claim_ledger_schema_error:{exc}"
    errors = ledger.validate_claims()
    if errors:
        return ARTIFACT_INVALID, f"claim_ledger_schema_error:{errors[0]}"
    return ARTIFACT_VALID, "valid_claim_ledger_schema"


def _validate_audit_report_payload(payload: Any) -> tuple[str, str]:
    if not isinstance(payload, dict):
        return ARTIFACT_INVALID, "audit_report_schema_error:not_object"
    violations = AuditReportContract.validate(payload)
    errors = [violation for violation in violations if violation.severity == "error"]
    if errors:
        first = errors[0]
        return ARTIFACT_INVALID, f"audit_report_schema_error:{first.field}"
    findings = payload.get("findings")
    if findings is not None and not isinstance(findings, list):
        return ARTIFACT_INVALID, "audit_report_schema_error:findings"
    for idx, finding in enumerate(findings or []):
        if not isinstance(finding, dict):
            return ARTIFACT_INVALID, f"audit_report_schema_error:findings[{idx}]"
        for field in ("finding_id", "severity", "finding_type", "description"):
            value = finding.get(field)
            if not isinstance(value, str) or not value.strip():
                return ARTIFACT_INVALID, f"audit_report_schema_error:findings[{idx}].{field}"
        if finding.get("severity") not in {"low", "medium", "high"}:
            return ARTIFACT_INVALID, f"audit_report_schema_error:findings[{idx}].severity"
    return ARTIFACT_VALID, "valid_audit_report_schema"


def _artifact_record(
    *,
    workspace: Path,
    artifact: dict[str, Any],
    workflow: dict[str, Any],
) -> dict[str, Any]:
    artifact_id = str(artifact.get("artifact_id") or "")
    rel_path = str(artifact.get("path") or "")
    fmt = str(artifact.get("format") or "")
    producer_stage = str(artifact.get("producer_stage") or "")
    status, validation_result = _validate_artifact(workspace / rel_path, fmt, artifact_id)

    activated_optional = optional_feedback_artifact_activated(
        workspace=workspace,
        artifact_id=artifact_id,
    ) or quality_gate_artifact_activated(
        workspace=workspace,
        artifact_id=artifact_id,
    ) or provenance_artifact_activated(
        workspace=workspace,
        artifact_id=artifact_id,
    )
    if (
        status == ARTIFACT_EXPECTED
        and _stage_is_complete_or_skipped(workflow, producer_stage)
        and (bool(artifact.get("required", False)) or activated_optional)
    ):
        status = ARTIFACT_MISSING
        validation_result = "missing"

    blocking_reason = ""
    if status == ARTIFACT_MISSING:
        blocking_reason = f"Producer stage '{producer_stage}' completed but '{rel_path}' is missing."
    elif status == ARTIFACT_INVALID:
        blocking_reason = f"Artifact '{rel_path}' failed minimum {fmt} validation."

    path = workspace / rel_path
    size_bytes = path.stat().st_size if path.exists() and path.is_file() else None
    mtime = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).replace(microsecond=0).isoformat() if path.exists() else None
    sha256 = _sha256_file(path) if path.exists() and path.is_file() else None

    return {
        "artifact_id": artifact_id,
        "path": rel_path,
        "format": fmt,
        "required": bool(artifact.get("required", False)),
        "producer_stage": producer_stage,
        "producer_role": artifact.get("producer_role", ""),
        "consumer_stages": artifact.get("consumer_stages", []),
        "status": status,
        "validation_result": validation_result,
        "blocking_reason": blocking_reason,
        "allowed_decisions": artifact.get("allowed_decisions", []),
        "retry_or_human_review_decision": artifact.get("retry_or_human_review_decision", ""),
        "size_bytes": size_bytes,
        "mtime": mtime,
        "sha256": sha256,
    }


def _build_artifact_registry(
    *,
    workspace: Path,
    run_id: str,
    artifacts: list[dict[str, Any]],
    workflow: dict[str, Any],
    updated_at: str,
) -> dict[str, Any]:
    records = {
        str(artifact.get("artifact_id")): _artifact_record(
            workspace=workspace,
            artifact=artifact,
            workflow=workflow,
        )
        for artifact in artifacts
        if artifact.get("artifact_id")
    }
    return {
        "schema_version": ARTIFACT_REGISTRY_SCHEMA,
        "run_id": run_id,
        "updated_at": updated_at,
        "artifacts": records,
    }


def interpret_frozen_artifact_integrity(
    *,
    old_registry: dict[str, Any] | None,
    registry: dict[str, Any],
    workflow: dict[str, Any],
    artifacts: list[dict[str, Any]],
    stages: list[dict[str, Any]],
    mutating_stage: str | None = None,
) -> FrozenArtifactIntegrityVerdict:
    reasons: list[str] = []
    if old_registry is not None:
        old_records_raw = old_registry.get("artifacts")
        if not isinstance(old_records_raw, dict):
            return _degraded_frozen_artifact_integrity(
                "artifact_registry.json artifacts must be an object before frozen integrity can be verified."
            )
        old_records = old_records_raw
    else:
        old_records = {}
    new_records = registry.get("artifacts")
    if not isinstance(new_records, dict):
        return _degraded_frozen_artifact_integrity(
            "artifact_registry.json artifacts must be an object before frozen integrity can be verified."
        )
    mutating_stage_produces = {
        str(item)
        for stage in stages
        if str(stage.get("stage_id") or "") == str(mutating_stage or "")
        for item in (stage.get("produces") or [])
    }
    for artifact in artifacts:
        artifact_id = str(artifact.get("artifact_id") or "")
        if not artifact_id:
            continue
        if artifact_id in mutating_stage_produces:
            continue
        producer_stage = str(artifact.get("producer_stage") or "")
        old_record = old_records.get(artifact_id) or {}
        old_sha = old_record.get("sha256")
        if not old_sha:
            continue
        producer_verdict = interpret_stage_completion(workflow, producer_stage)
        if producer_verdict.kind != "canonical":
            return _degraded_frozen_artifact_integrity(
                f"Cannot verify frozen artifact '{artifact_id}' because producer stage "
                f"'{producer_stage}' status is malformed: {' '.join(producer_verdict.reasons)}"
            )
        producer_projection = project_stage_completion_for_read(producer_verdict)
        if producer_projection.get("complete_or_skipped") is not True:
            continue
        new_record = new_records.get(artifact_id) or {}
        new_sha = new_record.get("sha256")
        path = str(new_record.get("path") or old_record.get("path") or artifact.get("path") or artifact_id)
        if new_record.get("status") == ARTIFACT_MISSING or not new_sha:
            reasons.append(
                f"Frozen artifact '{path}' from owner stage '{producer_stage}' is missing after stage-complete; route repair back to the owner stage."
            )
        elif new_sha != old_sha:
            reasons.append(
                f"Frozen artifact '{path}' from owner stage '{producer_stage}' changed after stage-complete; route repair back to the owner stage instead of downstream in-place conversion."
            )
    if reasons:
        return FrozenArtifactIntegrityVerdict(
            kind="degraded",
            value={"status": "changed", "matched": False, "reasons": reasons},
            reasons=tuple(reasons),
        )
    return FrozenArtifactIntegrityVerdict(
        kind="canonical",
        value={"status": "matched", "matched": True, "reasons": []},
    )


def project_frozen_artifact_integrity_for_read(verdict: FrozenArtifactIntegrityVerdict) -> dict[str, Any]:
    """Return the read-side projection for frozen artifact integrity."""

    return dict(verdict.value)


def require_frozen_artifact_integrity_pass(verdict: FrozenArtifactIntegrityVerdict) -> list[str]:
    """Return integrity blockers for write paths; empty means pass."""

    if verdict.kind == "canonical":
        return []
    return list(verdict.reasons)


def _degraded_frozen_artifact_integrity(reason: str) -> FrozenArtifactIntegrityVerdict:
    return FrozenArtifactIntegrityVerdict(
        kind="degraded",
        value={"status": "unknown", "matched": False, "reasons": [reason]},
        reasons=(reason,),
    )


def _changed_artifact_events(
    *,
    old_registry: dict[str, Any] | None,
    registry: dict[str, Any],
) -> list[dict[str, Any]]:
    old_records = ((old_registry or {}).get("artifacts") or {})
    events: list[dict[str, Any]] = []
    for artifact_id, record in (registry.get("artifacts") or {}).items():
        old_record = old_records.get(artifact_id) or {}
        observed_changed = (
            record.get("status") in {ARTIFACT_VALID, ARTIFACT_INVALID}
            and (
                old_record.get("status") != record.get("status")
                or old_record.get("size_bytes") != record.get("size_bytes")
                or old_record.get("mtime") != record.get("mtime")
            )
        )
        if observed_changed:
            events.append({
                "event_type": "artifact_observed",
                "artifact_id": str(artifact_id),
                "metadata": {
                    "path": record.get("path"),
                    "size_bytes": record.get("size_bytes"),
                    "mtime": record.get("mtime"),
                },
            })

        validated_changed = (
            record.get("status") in {ARTIFACT_PRESENT, ARTIFACT_VALID, ARTIFACT_INVALID, ARTIFACT_MISSING}
            and (
                old_record.get("status") != record.get("status")
                or old_record.get("validation_result") != record.get("validation_result")
                or old_record.get("blocking_reason") != record.get("blocking_reason")
            )
        )
        if validated_changed:
            events.append({
                "event_type": "artifact_validated",
                "artifact_id": str(artifact_id),
                "reason": str(record.get("blocking_reason") or ""),
                "metadata": {
                    "path": record.get("path"),
                    "status": record.get("status"),
                    "validation_result": record.get("validation_result"),
                },
            })
    return events


def _artifact_registry_sha(
    registry: dict[str, Any],
    artifact_id: str,
) -> str:
    record = ((registry.get("artifacts") or {}).get(artifact_id) or {})
    sha256 = str(record.get("sha256") or "")
    if not sha256:
        path = str(record.get("path") or artifact_id)
        raise RuntimeStateError(
            f"Artifact '{artifact_id}' has no frozen sha256 in artifact_registry.json.",
            details={"artifact_id": artifact_id, "path": path},
            error_code=E_TRANSACTION_INTEGRITY,
        )
    return sha256


def _artifact_registry_path(
    registry: dict[str, Any],
    artifact_id: str,
    default: str,
) -> str:
    record = ((registry.get("artifacts") or {}).get(artifact_id) or {})
    return str(record.get("path") or default)
