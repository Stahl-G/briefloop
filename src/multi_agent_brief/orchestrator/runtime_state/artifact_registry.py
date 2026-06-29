"""Artifact registry helpers for Orchestrator runtime state."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

import yaml

from multi_agent_brief.contracts.schemas.audit_report import AuditReportContract
from multi_agent_brief.contracts.schemas.atomic_claim_graph import AtomicClaimGraphContract
from multi_agent_brief.contracts.schemas.claim import ClaimContract
from multi_agent_brief.contracts.schemas.claim_draft import ClaimDraftContract
from multi_agent_brief.contracts.schemas.claim_support_matrix import ClaimSupportMatrixContract
from multi_agent_brief.contracts.schemas.evidence_span_registry import EvidenceSpanRegistryContract
from multi_agent_brief.contracts.schemas.semantic_assessment_report import SemanticAssessmentReportContract
from multi_agent_brief.contracts.schemas.source_evidence_pack_manifest import SourceEvidencePackManifestContract
from multi_agent_brief.contracts.source_metadata import (
    local_file_without_url_missing_identity,
    source_category_error,
    source_url_error,
)
from multi_agent_brief.core.claim_ledger import ClaimLedger
from multi_agent_brief.core.schemas import Claim
from multi_agent_brief.feedback.feedback_contract import optional_feedback_artifact_activated
from multi_agent_brief.orchestrator.runtime_state._io import _sha256_file
from multi_agent_brief.orchestrator.runtime_state.atomic_claim_graph import (
    ATOMIC_CLAIM_GRAPH_VALIDATION_PREFIX,
    validate_atomic_claim_graph_against_ledger,
)
from multi_agent_brief.orchestrator.runtime_state.claim_support_matrix import (
    CLAIM_SUPPORT_MATRIX_VALIDATION_PREFIX,
    validate_claim_support_matrix_against_artifacts,
)
from multi_agent_brief.orchestrator.runtime_state.evidence_span_registry import (
    EVIDENCE_SPAN_REGISTRY_VALIDATION_PREFIX,
    validate_evidence_span_registry_against_source_pack,
)
from multi_agent_brief.orchestrator.runtime_state.semantic_assessment_report import (
    SEMANTIC_ASSESSMENT_REPORT_VALIDATION_PREFIX,
    validate_semantic_assessment_report_against_artifacts,
)
from multi_agent_brief.orchestrator.runtime_state.source_evidence_pack import (
    SOURCE_EVIDENCE_PACK_VALIDATION_PREFIX,
    validate_source_evidence_pack_manifest,
)
from multi_agent_brief.orchestrator.runtime_state.errors import (
    E_TRANSACTION_INTEGRITY,
    RuntimeStateError,
)
from multi_agent_brief.orchestrator.runtime_state.workflow import (
    project_stage_completion_for_read,
    interpret_stage_completion,
    _stage_is_complete_or_skipped,
)
from multi_agent_brief.product.release_approval import (
    validate_human_approval_ledger_event_links,
    validate_human_approval_ledger_payload,
    validate_release_readiness_report_event_link,
    validate_release_readiness_report_payload,
)
from multi_agent_brief.product.quality_panel import validate_quality_panel_payload
from multi_agent_brief.provenance.contract import provenance_artifact_activated
from multi_agent_brief.quality_gates.contract import quality_gate_artifact_activated


ARTIFACT_REGISTRY_SCHEMA = "multi-agent-brief-artifact-registry/v1"

ARTIFACT_EXPECTED = "expected"
ARTIFACT_MISSING = "missing"
ARTIFACT_PRESENT = "present"
ARTIFACT_VALID = "valid"
ARTIFACT_INVALID = "invalid"
ARTIFACT_STALE = "stale"
CLAIM_LEDGER_FROZEN_EDIT_GUIDANCE = (
    "claim_ledger.json is frozen. Do not hand-edit metadata or synchronize hashes manually. "
    "Rebuild the fact layer or use a deterministic metadata enrichment transaction when available."
)
FROZEN_ARTIFACT_CONTROL_FILE_GUIDANCE = (
    "Do not manually update artifact_registry.json, runtime_manifest.json, workflow_state.json, "
    "event_log.jsonl, or SHA fields to hide the change."
)

_SCREENING_STATUSES = {
    "keep",
    "selected",
    "reject",
    "rejected",
    "deprioritized",
    "exclude",
    "excluded",
    "watch",
}
_SCREENING_STATUSES_REQUIRING_REASON = {
    "reject",
    "rejected",
    "deprioritized",
    "exclude",
    "excluded",
}
_SCREENING_DISCARD_REASON_CODES = {
    "capacity_capped",
    "duplicate_source",
    "low_confidence",
    "low_tier",
    "off_focus",
    "other",
    "outside_scope",
    "stale_source",
    "unsafe_evidence_boundary",
    "weak_relevance",
}
_SCREENING_DISCARD_REASON_ALIASES = {
    "capacity_cut": "capacity_capped",
    "capacity_cap": "capacity_capped",
    "capacity_capped": "capacity_capped",
    "duplicate": "duplicate_source",
    "duplicate_source": "duplicate_source",
    "duplicate_sources": "duplicate_source",
    "low_confidence": "low_confidence",
    "low_tier": "low_tier",
    "off_focus": "off_focus",
    "off_topic": "off_focus",
    "other": "other",
    "outside_scope": "outside_scope",
    "stale": "stale_source",
    "stale_source": "stale_source",
    "stale_sources": "stale_source",
    "unsafe_evidence": "unsafe_evidence_boundary",
    "unsafe_evidence_boundary": "unsafe_evidence_boundary",
    "weak_relevance": "weak_relevance",
}
_INPUT_CLASSIFICATION_BUCKETS = {"evidence", "context", "feedback", "instruction", "skipped"}
_INPUT_CLASSIFICATION_PATH_KEYS = {
    "path",
    "file",
    "source_path",
    "relative_path",
    "input_path",
    "workspace_path",
    "extracted_markdown",
}


@dataclass(frozen=True)
class FrozenArtifactIntegrityVerdict:
    """Single interpretation of frozen artifact integrity."""

    kind: str
    value: dict[str, Any]
    reasons: tuple[str, ...] = ()
    contaminates_run: bool = False


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
            if artifact_id == "claim_drafts":
                return _validate_claim_drafts_payload(payload)
            if artifact_id == "atomic_claim_graph":
                return _validate_atomic_claim_graph_payload(payload, artifact_path=path)
            if artifact_id == "evidence_span_registry":
                return _validate_evidence_span_registry_payload(payload, artifact_path=path)
            if artifact_id == "claim_support_matrix":
                return _validate_claim_support_matrix_payload(payload, artifact_path=path)
            if artifact_id == "semantic_assessment_report":
                return _validate_semantic_assessment_report_payload(payload, artifact_path=path)
            if artifact_id == "audit_report":
                return _validate_audit_report_payload(payload)
            if artifact_id == "candidate_claims":
                return _validate_candidate_claims_payload(payload)
            if artifact_id == "screened_candidates":
                return _validate_screened_candidates_payload(payload)
            if artifact_id == "input_classification":
                return _validate_input_classification_payload(payload, artifact_path=path)
            if artifact_id == "source_evidence_pack_manifest":
                return _validate_source_evidence_pack_manifest_payload(payload, artifact_path=path)
            if artifact_id == "human_approval_ledger":
                return _validate_human_approval_ledger_payload(payload, artifact_path=path)
            if artifact_id == "release_readiness_report":
                return _validate_release_readiness_report_payload(payload, artifact_path=path)
            if artifact_id == "quality_panel":
                return _validate_quality_panel_payload(payload)
        elif fmt in {"yaml", "yml"}:
            yaml.safe_load(text)
        elif fmt == "markdown":
            pass
    except json.JSONDecodeError:
        return ARTIFACT_INVALID, "parse_error"
    except yaml.YAMLError:
        return ARTIFACT_INVALID, "parse_error"

    return ARTIFACT_VALID, "valid_minimum"


def _validate_candidate_claims_payload(payload: Any) -> tuple[str, str]:
    if not isinstance(payload, list):
        return ARTIFACT_INVALID, "candidate_claims_schema_error:not_list"

    seen_ids: set[str] = set()
    for idx, candidate in enumerate(payload):
        if not isinstance(candidate, dict):
            return ARTIFACT_INVALID, f"candidate_claims_schema_error:candidate[{idx}]"
        if _candidate_claim_uses_legacy_shape(candidate):
            status, result = _validate_legacy_candidate_claim(candidate, idx=idx, seen_ids=seen_ids)
        else:
            status, result = _validate_contract_candidate_claim(candidate, idx=idx, seen_ids=seen_ids)
        if status != ARTIFACT_VALID:
            return status, result

    return ARTIFACT_VALID, "valid_candidate_claims_schema"


def _candidate_claim_uses_legacy_shape(candidate: dict[str, Any]) -> bool:
    return "statement" not in candidate and ("claim" in candidate or "candidate_id" in candidate)


def _validate_legacy_candidate_claim(
    candidate: dict[str, Any],
    *,
    idx: int,
    seen_ids: set[str],
) -> tuple[str, str]:
    for field in ("candidate_id", "claim", "source_id"):
        value = candidate.get(field)
        if not isinstance(value, str) or not value.strip():
            return ARTIFACT_INVALID, f"candidate_claims_schema_error:candidate[{idx}].{field}"
    candidate_id = str(candidate["candidate_id"]).strip()
    if candidate_id in seen_ids:
        return ARTIFACT_INVALID, f"candidate_claims_schema_error:duplicate_candidate_id:{candidate_id}"
    seen_ids.add(candidate_id)
    return ARTIFACT_VALID, "valid_candidate_claims_schema"


def _validate_contract_candidate_claim(
    candidate: dict[str, Any],
    *,
    idx: int,
    seen_ids: set[str],
) -> tuple[str, str]:
    for field in ("statement", "evidence_text", "topic", "claim_type"):
        value = candidate.get(field)
        if not isinstance(value, str) or not value.strip():
            return ARTIFACT_INVALID, f"candidate_claims_schema_error:candidate[{idx}].{field}"
    url_error = source_url_error(candidate.get("source_url"))
    if url_error:
        return ARTIFACT_INVALID, f"candidate_claims_schema_error:candidate[{idx}].source_url"
    category_error = source_category_error(candidate.get("source_category"))
    if category_error:
        return ARTIFACT_INVALID, f"candidate_claims_schema_error:candidate[{idx}].source_category"
    local_identity_error = local_file_without_url_missing_identity(candidate)
    if local_identity_error:
        return ARTIFACT_INVALID, (
            f"candidate_claims_schema_error:candidate[{idx}].{local_identity_error}"
        )
    if not _candidate_claim_has_source_identity(candidate):
        return ARTIFACT_INVALID, f"candidate_claims_schema_error:candidate[{idx}].source_url_or_source_path"
    if not _candidate_claim_has_source_date(candidate):
        return ARTIFACT_INVALID, (
            f"candidate_claims_schema_error:candidate[{idx}].published_at_or_retrieved_at"
        )
    if not _non_empty_scalar(candidate.get("confidence")):
        return ARTIFACT_INVALID, f"candidate_claims_schema_error:candidate[{idx}].confidence"
    for field in ("source_id", "source_path"):
        value = candidate.get(field)
        if value is not None and not _non_empty_string(value):
            return ARTIFACT_INVALID, f"candidate_claims_schema_error:candidate[{idx}].{field}"
    candidate_id = candidate.get("candidate_id")
    if candidate_id is not None:
        if not _non_empty_string(candidate_id):
            return ARTIFACT_INVALID, f"candidate_claims_schema_error:candidate[{idx}].candidate_id"
        normalized_id = candidate_id.strip()
        if normalized_id in seen_ids:
            return ARTIFACT_INVALID, f"candidate_claims_schema_error:duplicate_candidate_id:{normalized_id}"
        seen_ids.add(normalized_id)
    return ARTIFACT_VALID, "valid_candidate_claims_schema"


def _candidate_claim_has_source_identity(candidate: dict[str, Any]) -> bool:
    return _non_empty_string(candidate.get("source_url")) or _non_empty_string(
        candidate.get("source_path")
    )


def _candidate_claim_has_source_date(candidate: dict[str, Any]) -> bool:
    return _non_empty_string(candidate.get("published_at")) or _non_empty_string(
        candidate.get("retrieved_at")
    )


def _non_empty_scalar(value: Any) -> bool:
    return (isinstance(value, str) and bool(value.strip())) or (
        isinstance(value, (int, float)) and not isinstance(value, bool)
    )


def _validate_screened_candidates_payload(payload: Any) -> tuple[str, str]:
    if isinstance(payload, list):
        return _validate_legacy_screened_candidates(payload)
    if isinstance(payload, dict):
        return _validate_contract_screened_candidates(payload)
    return ARTIFACT_INVALID, "screened_candidates_schema_error:not_list_or_object"


def _validate_legacy_screened_candidates(payload: list[Any]) -> tuple[str, str]:
    for idx, candidate in enumerate(payload):
        if not isinstance(candidate, dict):
            return ARTIFACT_INVALID, f"screened_candidates_schema_error:candidate[{idx}]"
        candidate_id = candidate.get("candidate_id")
        if not isinstance(candidate_id, str) or not candidate_id.strip():
            return ARTIFACT_INVALID, f"screened_candidates_schema_error:candidate[{idx}].candidate_id"
        status = candidate.get("screening_status")
        if not isinstance(status, str) or status.strip() not in _SCREENING_STATUSES:
            return ARTIFACT_INVALID, f"screened_candidates_schema_error:candidate[{idx}].screening_status"
        if status.strip() in _SCREENING_STATUSES_REQUIRING_REASON:
            has_reason = any(
                _non_empty_string(candidate.get(field))
                for field in ("reason", "screening_reason", "excluded_reason")
            )
            if not has_reason:
                return ARTIFACT_INVALID, f"screened_candidates_schema_error:candidate[{idx}].reason"

    return ARTIFACT_VALID, "valid_screened_candidates_schema"


def _validate_contract_screened_candidates(payload: dict[str, Any]) -> tuple[str, str]:
    selected = payload.get("selected")
    if not isinstance(selected, list):
        return ARTIFACT_INVALID, "screened_candidates_schema_error:selected"
    for idx, candidate in enumerate(selected):
        validation_error = _selected_screened_candidate_error(candidate)
        if validation_error:
            return ARTIFACT_INVALID, f"screened_candidates_schema_error:selected[{idx}].{validation_error}"

    screening_policy = payload.get("screening_policy")
    if not isinstance(screening_policy, dict) or not screening_policy:
        return ARTIFACT_INVALID, "screened_candidates_schema_error:screening_policy"

    total_candidates, total_error = _screened_candidates_total(payload, screening_policy)
    if total_error:
        return ARTIFACT_INVALID, f"screened_candidates_schema_error:{total_error}"
    requires_discard_audit = total_candidates is not None

    has_discard_bucket = False
    for bucket in ("excluded", "deprioritized"):
        entries = payload.get(bucket)
        if entries is None:
            continue
        if not isinstance(entries, list):
            return ARTIFACT_INVALID, f"screened_candidates_schema_error:{bucket}"
        has_discard_bucket = True
        for idx, candidate in enumerate(entries):
            if not _valid_screened_candidate_entry(candidate):
                return ARTIFACT_INVALID, f"screened_candidates_schema_error:{bucket}[{idx}]"
            if requires_discard_audit or _screened_candidate_declares_discard_audit(candidate):
                if not _screened_candidate_reason_code(candidate):
                    return ARTIFACT_INVALID, f"screened_candidates_schema_error:{bucket}[{idx}].reason_code"
                if not _screened_candidate_has_short_explanation(candidate):
                    return ARTIFACT_INVALID, f"screened_candidates_schema_error:{bucket}[{idx}].explanation"
            elif not _screened_candidate_has_reason(candidate):
                return ARTIFACT_INVALID, f"screened_candidates_schema_error:{bucket}[{idx}].reason"
    if not has_discard_bucket:
        return ARTIFACT_INVALID, "screened_candidates_schema_error:excluded_or_deprioritized"

    if total_candidates is not None:
        discard_count = _screened_candidates_discard_count(payload)
        expected_discards = total_candidates - len(selected)
        if expected_discards < 0:
            return ARTIFACT_INVALID, "screened_candidates_schema_error:total_candidates"
        if expected_discards > 0 and discard_count == 0:
            return ARTIFACT_INVALID, "screened_candidates_schema_error:discard_audit_missing"
        if len(selected) + discard_count != total_candidates:
            return ARTIFACT_INVALID, "screened_candidates_schema_error:discard_audit_count"

    return ARTIFACT_VALID, "valid_screened_candidates_schema"


def _selected_screened_candidate_error(candidate: Any) -> str | None:
    if not isinstance(candidate, dict):
        return "entry"
    for field in ("statement", "evidence_text"):
        if not _non_empty_string(candidate.get(field)):
            return field
    if source_url_error(candidate.get("source_url")):
        return "source_url"
    if source_category_error(candidate.get("source_category")):
        return "source_category"
    local_identity_error = local_file_without_url_missing_identity(candidate)
    if local_identity_error:
        return local_identity_error
    if not _screened_candidate_has_source_identity(candidate):
        return "source_id_or_source_url_or_source_path"
    if not _candidate_claim_has_source_date(candidate):
        return "published_at_or_retrieved_at"
    return None


def _screened_candidate_has_source_identity(candidate: dict[str, Any]) -> bool:
    return any(
        _non_empty_string(candidate.get(field))
        for field in ("source_id", "source_url", "source_path")
    )


def _valid_screened_candidate_entry(candidate: Any) -> bool:
    if not isinstance(candidate, dict):
        return False
    return any(_non_empty_string(candidate.get(field)) for field in ("candidate_id", "statement", "claim"))


def _screened_candidate_has_reason(candidate: dict[str, Any]) -> bool:
    return any(
        _non_empty_string(candidate.get(field))
        for field in ("reason", "screening_reason", "excluded_reason", "deprioritized_reason")
    )


def _screened_candidate_declares_discard_audit(candidate: dict[str, Any]) -> bool:
    return any(
        _non_empty_string(candidate.get(field))
        for field in (
            "reason_code",
            "screening_reason_code",
            "excluded_reason_code",
            "deprioritized_reason_code",
            "explanation",
            "short_explanation",
            "screening_explanation",
            "reason_explanation",
        )
    )


def _screened_candidate_reason_code(candidate: dict[str, Any]) -> str:
    for field in (
        "reason_code",
        "screening_reason_code",
        "excluded_reason_code",
        "deprioritized_reason_code",
        "reason",
    ):
        code = _normalize_screening_reason_code(candidate.get(field))
        if code:
            return code
    return ""


def _normalize_screening_reason_code(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    raw = value.strip().lower()
    if not raw:
        return ""
    normalized = re.sub(r"[^a-z0-9]+", "_", raw).strip("_")
    if normalized in _SCREENING_DISCARD_REASON_CODES:
        return normalized
    return _SCREENING_DISCARD_REASON_ALIASES.get(normalized, "")


def _screened_candidate_has_short_explanation(candidate: dict[str, Any]) -> bool:
    code = _screened_candidate_reason_code(candidate)
    for field in (
        "explanation",
        "short_explanation",
        "screening_explanation",
        "reason_explanation",
        "screening_reason",
        "excluded_reason",
        "deprioritized_reason",
    ):
        value = candidate.get(field)
        if not _non_empty_string(value):
            continue
        if _normalize_screening_reason_code(value) == code:
            continue
        return True
    return False


def _screened_candidates_total(
    payload: dict[str, Any],
    screening_policy: dict[str, Any],
) -> tuple[int | None, str | None]:
    total_values: list[int] = []
    for container, prefix in (
        (payload, ""),
        (screening_policy, "screening_policy."),
    ):
        for key in (
            "total_candidates",
            "candidate_count",
            "input_candidate_count",
            "found_candidate_count",
        ):
            value = container.get(key)
            if value is None:
                continue
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                return None, f"{prefix}{key}"
            total_values.append(value)
    if not total_values:
        return None, None
    first = total_values[0]
    if any(value != first for value in total_values[1:]):
        return None, "total_candidates_mismatch"
    return first, None


def _screened_candidates_discard_count(payload: dict[str, Any]) -> int:
    count = 0
    for bucket in ("excluded", "deprioritized"):
        entries = payload.get(bucket)
        if isinstance(entries, list):
            count += len(entries)
    return count


def _validate_input_classification_payload(payload: Any, *, artifact_path: Path) -> tuple[str, str]:
    if not isinstance(payload, dict):
        return ARTIFACT_INVALID, "input_classification_schema_error:not_object"

    workspace = _workspace_root_for_input_classification(artifact_path)
    for bucket in sorted(_INPUT_CLASSIFICATION_BUCKETS):
        entries = payload.get(bucket)
        if entries is None:
            continue
        if not isinstance(entries, list):
            return ARTIFACT_INVALID, f"input_classification_schema_error:{bucket}"
        for idx, entry in enumerate(entries):
            if not isinstance(entry, dict):
                continue
            for key, value in entry.items():
                if key not in _INPUT_CLASSIFICATION_PATH_KEYS or not isinstance(value, str):
                    continue
                if _input_classification_path_is_unsafe(value, workspace=workspace):
                    return ARTIFACT_INVALID, f"input_classification_schema_error:{bucket}[{idx}].{key}"

    return ARTIFACT_VALID, "valid_input_classification_schema"


def _validate_source_evidence_pack_manifest_payload(payload: Any, *, artifact_path: Path) -> tuple[str, str]:
    if not isinstance(payload, dict):
        return ARTIFACT_INVALID, "source_evidence_pack_manifest_schema_error:not_object"
    violations = SourceEvidencePackManifestContract.validate(payload)
    errors = [violation for violation in violations if violation.severity == "error"]
    if errors:
        first = errors[0]
        return ARTIFACT_INVALID, f"source_evidence_pack_manifest_schema_error:{first.field}"

    workspace = artifact_path.parents[2]
    reason = validate_source_evidence_pack_manifest(
        manifest_payload=payload,
        workspace=workspace,
    )
    if reason:
        return ARTIFACT_INVALID, f"{SOURCE_EVIDENCE_PACK_VALIDATION_PREFIX}:{reason}"
    return ARTIFACT_VALID, "experimental_source_evidence_pack_manifest"


def _validate_human_approval_ledger_payload(payload: Any, *, artifact_path: Path) -> tuple[str, str]:
    reason = validate_human_approval_ledger_payload(payload)
    if reason:
        return ARTIFACT_INVALID, reason
    workspace = artifact_path.parents[2]
    link_reason = validate_human_approval_ledger_event_links(payload, workspace=workspace)
    if link_reason:
        return ARTIFACT_INVALID, link_reason
    return ARTIFACT_VALID, "experimental_human_approval_ledger"


def _validate_release_readiness_report_payload(payload: Any, *, artifact_path: Path) -> tuple[str, str]:
    reason = validate_release_readiness_report_payload(payload)
    if reason:
        return ARTIFACT_INVALID, reason
    workspace = artifact_path.parents[2]
    link_reason = validate_release_readiness_report_event_link(payload, workspace=workspace)
    if link_reason:
        return ARTIFACT_INVALID, link_reason
    return ARTIFACT_VALID, "experimental_release_readiness_report"


def _validate_quality_panel_payload(payload: Any) -> tuple[str, str]:
    reason = validate_quality_panel_payload(payload)
    if reason:
        return ARTIFACT_INVALID, reason
    return ARTIFACT_VALID, "experimental_quality_panel"


def _workspace_root_for_input_classification(artifact_path: Path) -> Path | None:
    if artifact_path.name == "input_classification.json" and artifact_path.parent.name == "output":
        return artifact_path.parent.parent
    return None


def _input_classification_path_is_unsafe(value: str, *, workspace: Path | None) -> bool:
    raw = value.strip()
    if not raw:
        return False
    if raw.startswith("~"):
        return True
    normalized = raw.replace("\\", "/")
    posix_path = PurePosixPath(normalized)
    windows_path = PureWindowsPath(raw)
    if ".." in posix_path.parts or ".." in windows_path.parts:
        return True
    path = Path(raw)
    if path.is_absolute():
        if workspace is None:
            return True
        try:
            path.resolve(strict=False).relative_to(workspace.resolve(strict=False))
        except ValueError:
            return True
        return False
    if windows_path.drive:
        return True
    return False


def _non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


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


def _validate_claim_drafts_payload(payload: Any) -> tuple[str, str]:
    if not isinstance(payload, dict):
        return ARTIFACT_INVALID, "claim_drafts_schema_error:not_object"
    violations = ClaimDraftContract.validate(payload)
    errors = [violation for violation in violations if violation.severity == "error"]
    if errors:
        first = errors[0]
        return ARTIFACT_INVALID, f"claim_drafts_schema_error:{first.field}"
    return ARTIFACT_VALID, "valid_claim_drafts_schema"


def _validate_atomic_claim_graph_payload(payload: Any, *, artifact_path: Path) -> tuple[str, str]:
    if not isinstance(payload, dict):
        return ARTIFACT_INVALID, "atomic_claim_graph_schema_error:not_object"
    violations = AtomicClaimGraphContract.validate(payload)
    errors = [violation for violation in violations if violation.severity == "error"]
    if errors:
        first = errors[0]
        return ARTIFACT_INVALID, f"atomic_claim_graph_schema_error:{first.field}"

    ledger_path = artifact_path.with_name("claim_ledger.json")
    try:
        ledger_payload = json.loads(ledger_path.read_text(encoding="utf-8"))
        ledger_claims = ClaimLedger._claim_items_from_json(ledger_payload)
    except FileNotFoundError:
        return ARTIFACT_INVALID, f"{ATOMIC_CLAIM_GRAPH_VALIDATION_PREFIX}:claim_ledger_missing"
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        return ARTIFACT_INVALID, f"{ATOMIC_CLAIM_GRAPH_VALIDATION_PREFIX}:claim_ledger_unreadable:{exc}"

    reason = validate_atomic_claim_graph_against_ledger(
        graph_payload=payload,
        ledger_claims=ledger_claims,
    )
    if reason:
        return ARTIFACT_INVALID, f"{ATOMIC_CLAIM_GRAPH_VALIDATION_PREFIX}:{reason}"

    return ARTIFACT_VALID, "experimental_atomic_claim_graph_schema"


def _validate_evidence_span_registry_payload(payload: Any, *, artifact_path: Path) -> tuple[str, str]:
    if not isinstance(payload, dict):
        return ARTIFACT_INVALID, "evidence_span_registry_schema_error:not_object"
    violations = EvidenceSpanRegistryContract.validate(payload)
    errors = [violation for violation in violations if violation.severity == "error"]
    if errors:
        first = errors[0]
        return ARTIFACT_INVALID, f"evidence_span_registry_schema_error:{first.field}"

    workspace = artifact_path.parents[2]
    reason = validate_evidence_span_registry_against_source_pack(
        registry_payload=payload,
        workspace=workspace,
    )
    if reason:
        return ARTIFACT_INVALID, f"{EVIDENCE_SPAN_REGISTRY_VALIDATION_PREFIX}:{reason}"

    return ARTIFACT_VALID, "experimental_evidence_span_registry_schema"


def _validate_claim_support_matrix_payload(payload: Any, *, artifact_path: Path) -> tuple[str, str]:
    if not isinstance(payload, dict):
        return ARTIFACT_INVALID, "claim_support_matrix_schema_error:not_object"
    violations = ClaimSupportMatrixContract.validate(payload)
    errors = [violation for violation in violations if violation.severity == "error"]
    if errors:
        first = errors[0]
        return ARTIFACT_INVALID, f"claim_support_matrix_schema_error:{first.field}"

    ledger_claims, reason = _claim_support_matrix_ledger_claims(artifact_path.with_name("claim_ledger.json"))
    if reason:
        return ARTIFACT_INVALID, f"{CLAIM_SUPPORT_MATRIX_VALIDATION_PREFIX}:{reason}"
    graph_payload, reason = _claim_support_matrix_atomic_graph_payload(artifact_path.with_name("atomic_claim_graph.json"))
    if reason:
        return ARTIFACT_INVALID, f"{CLAIM_SUPPORT_MATRIX_VALIDATION_PREFIX}:{reason}"
    evidence_payload, reason = _claim_support_matrix_evidence_span_registry_payload(
        artifact_path.with_name("evidence_span_registry.json")
    )
    if reason:
        return ARTIFACT_INVALID, f"{CLAIM_SUPPORT_MATRIX_VALIDATION_PREFIX}:{reason}"

    reason = validate_claim_support_matrix_against_artifacts(
        matrix_payload=payload,
        ledger_claims=ledger_claims or [],
        graph_payload=graph_payload or {},
        evidence_span_registry_payload=evidence_payload or {},
    )
    if reason:
        return ARTIFACT_INVALID, f"{CLAIM_SUPPORT_MATRIX_VALIDATION_PREFIX}:{reason}"
    return ARTIFACT_VALID, "experimental_claim_support_matrix_schema"


def _validate_semantic_assessment_report_payload(payload: Any, *, artifact_path: Path) -> tuple[str, str]:
    if not isinstance(payload, dict):
        return ARTIFACT_INVALID, "semantic_assessment_report_schema_error:not_object"
    violations = SemanticAssessmentReportContract.validate(payload)
    errors = [violation for violation in violations if violation.severity == "error"]
    if errors:
        first = errors[0]
        return ARTIFACT_INVALID, f"semantic_assessment_report_schema_error:{first.field}"

    ledger_claims, reason = _claim_support_matrix_ledger_claims(artifact_path.with_name("claim_ledger.json"))
    if reason:
        return ARTIFACT_INVALID, f"{SEMANTIC_ASSESSMENT_REPORT_VALIDATION_PREFIX}:{reason}"
    graph_payload, reason = _claim_support_matrix_atomic_graph_payload(artifact_path.with_name("atomic_claim_graph.json"))
    if reason:
        return ARTIFACT_INVALID, f"{SEMANTIC_ASSESSMENT_REPORT_VALIDATION_PREFIX}:{reason}"
    evidence_payload, reason = _claim_support_matrix_evidence_span_registry_payload(
        artifact_path.with_name("evidence_span_registry.json")
    )
    if reason:
        return ARTIFACT_INVALID, f"{SEMANTIC_ASSESSMENT_REPORT_VALIDATION_PREFIX}:{reason}"

    reason = validate_semantic_assessment_report_against_artifacts(
        report_payload=payload,
        ledger_claims=ledger_claims or [],
        graph_payload=graph_payload or {},
        evidence_span_registry_payload=evidence_payload or {},
    )
    if reason:
        return ARTIFACT_INVALID, f"{SEMANTIC_ASSESSMENT_REPORT_VALIDATION_PREFIX}:{reason}"
    return ARTIFACT_VALID, "experimental_semantic_assessment_report_schema"


def _claim_support_matrix_ledger_claims(path: Path) -> tuple[list[dict[str, Any]] | None, str | None]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, "claim_ledger_missing"
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, f"claim_ledger_unreadable:{exc}"
    status, validation_result = _validate_claim_ledger_payload(payload)
    if status != ARTIFACT_VALID:
        return None, _dependency_invalid_reason(
            "claim_ledger",
            validation_result,
            prefixes=("claim_ledger_schema_error",),
        )
    try:
        claims = ClaimLedger._claim_items_from_json(payload)
    except ValueError as exc:
        return None, f"claim_ledger_unreadable:{exc}"
    return claims, None


def _claim_support_matrix_atomic_graph_payload(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    payload, reason = _read_claim_support_matrix_json(path, missing_reason="atomic_claim_graph_missing")
    if reason:
        return None, reason
    assert payload is not None
    status, validation_result = _validate_atomic_claim_graph_payload(payload, artifact_path=path)
    if status != ARTIFACT_VALID:
        return None, _dependency_invalid_reason(
            "atomic_claim_graph",
            validation_result,
            prefixes=("atomic_claim_graph_schema_error", ATOMIC_CLAIM_GRAPH_VALIDATION_PREFIX),
        )
    return payload, None


def _claim_support_matrix_evidence_span_registry_payload(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    payload, reason = _read_claim_support_matrix_json(path, missing_reason="evidence_span_registry_missing")
    if reason:
        return None, reason
    assert payload is not None
    status, validation_result = _validate_evidence_span_registry_payload(payload, artifact_path=path)
    if status != ARTIFACT_VALID:
        return None, _dependency_invalid_reason(
            "evidence_span_registry",
            validation_result,
            prefixes=("evidence_span_registry_schema_error", EVIDENCE_SPAN_REGISTRY_VALIDATION_PREFIX),
        )
    return payload, None


def _read_claim_support_matrix_json(path: Path, *, missing_reason: str) -> tuple[Any | None, str | None]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except FileNotFoundError:
        return None, missing_reason
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, f"{missing_reason.removesuffix('_missing')}_unreadable:{exc}"


def _dependency_invalid_reason(label: str, validation_result: str, *, prefixes: tuple[str, ...]) -> str:
    for prefix in prefixes:
        marker = f"{prefix}:"
        if validation_result.startswith(marker):
            return f"{label}_invalid:{validation_result.removeprefix(marker)}"
    return f"{label}_invalid:{validation_result}"


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
    stale_metadata = _producer_stage_stale_after_repair(workflow, producer_stage)
    if stale_metadata and path.exists() and path.is_file():
        status = ARTIFACT_STALE
        validation_result = "stale_after_repair"
        repair_tx = stale_metadata.get("repair_transaction_id") or "<unknown>"
        repair_owner = stale_metadata.get("repair_owner") or "<unknown>"
        blocking_reason = (
            f"Artifact '{rel_path}' was produced before owner-stage repair "
            f"{repair_tx} by '{repair_owner}'; rerun producer stage '{producer_stage}' "
            "before consuming it."
        )
    stale_baseline_sha256 = None
    if stale_metadata:
        baselines = (
            stale_metadata.get("stale_artifact_baselines")
            if isinstance(stale_metadata.get("stale_artifact_baselines"), dict)
            else {}
        )
        baseline = baselines.get(artifact_id) if isinstance(baselines.get(artifact_id), dict) else {}
        baseline_sha = baseline.get("sha256")
        if isinstance(baseline_sha, str) and baseline_sha:
            stale_baseline_sha256 = baseline_sha

    record = {
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
    if stale_baseline_sha256:
        record["stale_baseline_sha256"] = stale_baseline_sha256
    return record


def _producer_stage_stale_after_repair(
    workflow: dict[str, Any],
    producer_stage: str,
) -> dict[str, Any] | None:
    statuses = workflow.get("stage_statuses") if isinstance(workflow.get("stage_statuses"), dict) else {}
    stage_status = statuses.get(producer_stage) if isinstance(statuses.get(producer_stage), dict) else {}
    metadata = stage_status.get("metadata") if isinstance(stage_status.get("metadata"), dict) else {}
    if metadata.get("stale_after_repair") is True:
        return metadata
    return None


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
                f" {FROZEN_ARTIFACT_CONTROL_FILE_GUIDANCE}"
            )
        elif new_sha != old_sha:
            reason = (
                f"Frozen artifact '{path}' from owner stage '{producer_stage}' changed after stage-complete; "
                "route repair back to the owner stage instead of downstream in-place conversion. "
                f"{FROZEN_ARTIFACT_CONTROL_FILE_GUIDANCE}"
            )
            if artifact_id == "claim_ledger":
                reason = f"{reason} {CLAIM_LEDGER_FROZEN_EDIT_GUIDANCE}"
            reasons.append(reason)
    if reasons:
        return FrozenArtifactIntegrityVerdict(
            kind="degraded",
            value={"status": "changed", "matched": False, "contaminates_run": True, "reasons": reasons},
            reasons=tuple(reasons),
            contaminates_run=True,
        )
    return FrozenArtifactIntegrityVerdict(
        kind="canonical",
        value={"status": "matched", "matched": True, "contaminates_run": False, "reasons": []},
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
        value={"status": "unknown", "matched": False, "contaminates_run": False, "reasons": [reason]},
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
            record.get("status") in {
                ARTIFACT_PRESENT,
                ARTIFACT_VALID,
                ARTIFACT_INVALID,
                ARTIFACT_MISSING,
                ARTIFACT_STALE,
            }
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
