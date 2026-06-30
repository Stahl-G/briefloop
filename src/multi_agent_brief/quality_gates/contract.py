"""Quality-gate report contract helpers.

The quality-gate layer is intentionally side-effect free here. It validates
stage-scoped quality gate reports and computes current-stage blockers for
runtime state code without running agents, repair, source discovery, or web
fetches. ``quality_gate_report.json`` is retained as a legacy/latest
projection; authoritative runtime checks use stage-scoped reports.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


QUALITY_GATE_SCHEMA = "multi-agent-brief-quality-gates/v1"
QUALITY_GATE_REPORT_FILE = "output/intermediate/quality_gate_report.json"
AUDITOR_QUALITY_GATE_REPORT_FILE = "output/intermediate/gates/auditor_quality_gate_report.json"
FINALIZE_QUALITY_GATE_REPORT_FILE = "output/intermediate/gates/finalize_quality_gate_report.json"
QUALITY_GATE_STATE_FILES = {
    "quality_gate_report": QUALITY_GATE_REPORT_FILE,
    "auditor_quality_gate_report": AUDITOR_QUALITY_GATE_REPORT_FILE,
    "finalize_quality_gate_report": FINALIZE_QUALITY_GATE_REPORT_FILE,
}

GATE_IDS = {
    "coverage_omission",
    "editor_new_fact",
    "final_abstract_quality",
    "material_fact",
    "freshness",
    "target_relevance",
}
GATE_STATUSES = {"pass", "warning", "fail"}
FINDING_SEVERITIES = {"low", "medium", "high"}
BLOCKING_LEVELS = {"none", "warning", "blocking"}


class QualityGateContractError(Exception):
    """Raised when quality-gate state violates the contract."""

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": False,
            "error": str(self),
            "details": self.details,
        }


@dataclass(frozen=True)
class QualityGateBindingVerdict:
    """Single interpretation of a stage-scoped quality-gate binding."""

    kind: str
    value: dict[str, Any]
    reasons: tuple[str, ...] = ()


def quality_gate_paths(workspace: str | Path) -> dict[str, Path]:
    ws = Path(workspace).expanduser().resolve()
    return {key: ws / rel_path for key, rel_path in QUALITY_GATE_STATE_FILES.items()}


def quality_gate_report_key_for_stage(stage_id: str | None) -> str:
    return "finalize_quality_gate_report" if stage_id == "finalize" else "auditor_quality_gate_report"


def quality_gate_report_path_for_stage(workspace: str | Path, stage_id: str | None) -> Path:
    paths = quality_gate_paths(workspace)
    return paths[quality_gate_report_key_for_stage(stage_id)]


def stage_ids(stages: list[dict[str, Any]]) -> set[str]:
    return {str(stage["stage_id"]) for stage in stages if stage.get("stage_id")}


def artifact_ids(artifacts: list[dict[str, Any]]) -> set[str]:
    return {
        str(artifact["artifact_id"])
        for artifact in artifacts
        if artifact.get("artifact_id")
    }


def _load_quality_gate_report_path(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise QualityGateContractError(
            f"Invalid JSON quality gate report: {path}",
            details={"path": str(path), "reason": str(exc)},
        ) from exc
    except OSError as exc:
        raise QualityGateContractError(
            f"Failed to read quality gate report: {path}",
            details={"path": str(path), "reason": str(exc)},
        ) from exc
    if not isinstance(data, dict):
        raise QualityGateContractError(
            f"Quality gate report must contain a JSON object: {path}",
            details={"path": str(path)},
        )
    return data


def load_quality_gate_report(workspace: str | Path) -> dict[str, Any] | None:
    """Load the legacy/latest quality gate report projection."""
    return _load_quality_gate_report_path(quality_gate_paths(workspace)["quality_gate_report"])


def load_quality_gate_report_for_stage(
    workspace: str | Path,
    stage_id: str | None,
    *,
    allow_legacy: bool = True,
) -> dict[str, Any] | None:
    """Load the authoritative report for a stage, optionally falling back to legacy."""
    paths = quality_gate_paths(workspace)
    scoped = _load_quality_gate_report_path(paths[quality_gate_report_key_for_stage(stage_id)])
    if scoped is not None:
        return scoped
    if allow_legacy:
        return _load_quality_gate_report_path(paths["quality_gate_report"])
    return None


def quality_gate_artifact_activated(
    *,
    workspace: str | Path,
    artifact_id: str,
) -> bool:
    """Return whether an optional quality gate control artifact is active."""
    if artifact_id not in {
        "quality_gate_report",
        "auditor_quality_gate_report",
        "finalize_quality_gate_report",
    }:
        return False
    paths = quality_gate_paths(workspace)
    path = paths.get(artifact_id)
    if path and path.exists():
        return True
    if artifact_id == "auditor_quality_gate_report" and paths["quality_gate_report"].exists():
        return True

    config = _load_quality_gate_config(workspace)
    return bool(config.get("enabled", False))


def _load_quality_gate_config(workspace: str | Path) -> dict[str, Any]:
    config_path = Path(workspace).expanduser().resolve() / "config.yaml"
    if not config_path.exists():
        return {}
    try:
        config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    if not isinstance(config, dict):
        return {}
    gates = config.get("quality_gates") or config.get("gates") or {}
    return gates if isinstance(gates, dict) else {}


def quality_gate_required_for_stage(
    *,
    workspace: str | Path,
    current_stage: str | None,
) -> bool:
    """Return whether missing stage-scoped quality gate report should block a stage."""
    if current_stage is None:
        return False
    gates = _load_quality_gate_config(workspace)
    if not bool(gates.get("enabled", False)):
        return False
    required_stages = gates.get("required_stages")
    if required_stages is None:
        return current_stage in {"auditor", "finalize"}
    if not isinstance(required_stages, list):
        return False
    return current_stage in {str(stage) for stage in required_stages}


def empty_quality_gate_report(*, updated_at: str = "") -> dict[str, Any]:
    return {
        "schema_version": QUALITY_GATE_SCHEMA,
        "created_at": updated_at,
        "updated_at": updated_at,
        "workspace": ".",
        "report_date": "",
        "policy_pack": "default",
        "status": "pass",
        "gate_results": [],
        "findings": [],
        "metadata": {},
    }


def validate_quality_gate_report_payload(
    payload: dict[str, Any],
    *,
    stages: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
) -> list[str]:
    errors: list[str] = []
    if payload.get("schema_version") != QUALITY_GATE_SCHEMA:
        errors.append("quality_gate_report.json has an unsupported schema_version.")

    status = payload.get("status")
    if status not in GATE_STATUSES:
        errors.append(f"quality_gate_report.json status must be one of {sorted(GATE_STATUSES)}.")

    gate_results = payload.get("gate_results")
    if not isinstance(gate_results, list):
        errors.append("quality_gate_report.json gate_results must be a list.")
        gate_results = []

    finding_ids = {
        str(item.get("finding_id"))
        for item in payload.get("findings") or []
        if isinstance(item, dict) and item.get("finding_id")
    }
    seen_gates: set[str] = set()
    for idx, result in enumerate(gate_results):
        prefix = f"gate_results[{idx}]"
        if not isinstance(result, dict):
            errors.append(f"{prefix} must be an object.")
            continue
        gate_id = str(result.get("gate_id") or "")
        if gate_id not in GATE_IDS:
            errors.append(f"{prefix}.gate_id must be one of {sorted(GATE_IDS)}.")
        elif gate_id in seen_gates:
            errors.append(f"{prefix}.gate_id is duplicated: {gate_id}.")
        seen_gates.add(gate_id)
        if result.get("status") not in GATE_STATUSES:
            errors.append(f"{prefix}.status must be one of {sorted(GATE_STATUSES)}.")
        if not isinstance(result.get("blocking"), bool):
            errors.append(f"{prefix}.blocking must be a boolean.")
        refs = result.get("finding_ids")
        if not isinstance(refs, list):
            errors.append(f"{prefix}.finding_ids must be a list.")
        else:
            missing = [str(ref) for ref in refs if str(ref) not in finding_ids]
            if missing:
                errors.append(f"{prefix}.finding_ids references missing findings: {missing}.")

    findings = payload.get("findings")
    if not isinstance(findings, list):
        errors.append("quality_gate_report.json findings must be a list.")
        return errors

    known_stages = stage_ids(stages)
    known_artifacts = artifact_ids(artifacts)
    seen_findings: set[str] = set()
    for idx, finding in enumerate(findings):
        prefix = f"findings[{idx}]"
        if not isinstance(finding, dict):
            errors.append(f"{prefix} must be an object.")
            continue
        finding_id = str(finding.get("finding_id") or "")
        if not finding_id:
            errors.append(f"{prefix}.finding_id is required.")
        elif finding_id in seen_findings:
            errors.append(f"{prefix}.finding_id is duplicated: {finding_id}.")
        seen_findings.add(finding_id)

        if not str(finding.get("finding_type") or "").strip():
            errors.append(f"{prefix}.finding_type is required.")
        if finding.get("severity") not in FINDING_SEVERITIES:
            errors.append(f"{prefix}.severity must be one of {sorted(FINDING_SEVERITIES)}.")
        blocking_level = finding.get("blocking_level")
        if blocking_level not in BLOCKING_LEVELS:
            errors.append(f"{prefix}.blocking_level must be one of {sorted(BLOCKING_LEVELS)}.")
        if finding.get("blocking") != (blocking_level == "blocking"):
            errors.append(f"{prefix}.blocking must match blocking_level == 'blocking'.")

        for key in ("stage_id", "gate_stage_id", "repair_stage_id"):
            stage_id = finding.get(key)
            if stage_id is not None and str(stage_id) not in known_stages:
                errors.append(f"{prefix}.{key} is unknown: {stage_id}.")

        for key in ("artifact_id", "gate_artifact_id", "repair_artifact_id"):
            artifact_id = finding.get(key)
            if artifact_id is not None and str(artifact_id) not in known_artifacts:
                errors.append(f"{prefix}.{key} is unknown: {artifact_id}.")
    return errors


def validate_quality_gate_workspace(
    *,
    workspace: str | Path,
    stages: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
) -> dict[str, Any]:
    payloads: list[tuple[str, dict[str, Any]]] = []
    errors: list[str] = []
    for stage in ("auditor", "finalize"):
        key = quality_gate_report_key_for_stage(stage)
        try:
            payload = load_quality_gate_report_for_stage(workspace, stage, allow_legacy=False)
        except QualityGateContractError as exc:
            errors.append(f"{key}: {exc}")
            continue
        if payload is not None:
            payloads.append((key, payload))

    if not payloads:
        try:
            legacy_payload = load_quality_gate_report(workspace)
        except QualityGateContractError as exc:
            errors.append(f"quality_gate_report: {exc}")
            legacy_payload = None
        if legacy_payload is not None:
            payloads.append(("quality_gate_report", legacy_payload))

    if not payloads:
        return {
            "ok": not errors,
            "errors": errors,
            "report_present": False,
            "finding_count": 0,
            "blocking_count": 0,
        }

    finding_count = 0
    blocking_count = 0
    statuses: dict[str, str] = {}
    for key, payload in payloads:
        report_errors = validate_quality_gate_report_payload(
            payload,
            stages=stages,
            artifacts=artifacts,
        )
        errors.extend(f"{key}: {error}" for error in report_errors)
        findings = [item for item in payload.get("findings") or [] if isinstance(item, dict)]
        finding_count += len(findings)
        blocking_count += sum(1 for item in findings if item.get("blocking_level") == "blocking")
        statuses[key] = str(payload.get("status") or "")
    return {
        "ok": not errors,
        "errors": errors,
        "report_present": True,
        "finding_count": finding_count,
        "blocking_count": blocking_count,
        "statuses": statuses,
        "status": _rollup_quality_gate_status(statuses.values()),
    }


def _rollup_quality_gate_status(statuses: Any) -> str | None:
    values = {str(status) for status in statuses if str(status)}
    if "fail" in values:
        return "fail"
    if "warning" in values:
        return "warning"
    if "pass" in values:
        return "pass"
    return None


def interpret_quality_gate_binding(
    *,
    workspace: str | Path,
    stage_id: str,
    expected_brief: str,
    expected_ledger: str,
    stages: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
) -> QualityGateBindingVerdict:
    """Interpret whether a stage-scoped gate report authorizes stage progress."""

    ws = Path(workspace).expanduser().resolve()
    try:
        payload = load_quality_gate_report_for_stage(ws, stage_id, allow_legacy=False)
    except QualityGateContractError as exc:
        return _degraded_quality_gate_binding(f"Quality gate report is invalid: {exc}")
    if payload is None:
        report_path = quality_gate_report_path_for_stage(ws, stage_id)
        try:
            rel_path = report_path.relative_to(ws).as_posix()
        except ValueError:
            rel_path = str(report_path)
        return _degraded_quality_gate_binding(
            f"{rel_path} is required before completing stage '{stage_id}'."
        )

    reasons = _quality_gate_binding_reasons(
        payload=payload,
        stage_id=stage_id,
        expected_brief=expected_brief,
        expected_ledger=expected_ledger,
        stages=stages,
        artifacts=artifacts,
    )
    if reasons:
        return QualityGateBindingVerdict(
            kind="degraded",
            value=_quality_gate_binding_projection(payload, status="blocked"),
            reasons=tuple(reasons),
        )
    return QualityGateBindingVerdict(
        kind="canonical",
        value=_quality_gate_binding_projection(payload, status="pass"),
    )


def project_quality_gate_binding_for_read(verdict: QualityGateBindingVerdict) -> dict[str, Any]:
    return dict(verdict.value)


def require_quality_gate_binding_pass(verdict: QualityGateBindingVerdict) -> list[str]:
    if verdict.kind == "canonical":
        return []
    return list(verdict.reasons)


def _degraded_quality_gate_binding(reason: str) -> QualityGateBindingVerdict:
    return QualityGateBindingVerdict(
        kind="degraded",
        value={
            "status": "blocked",
            "gate_status": "unknown",
            "blocking_findings": 0,
        },
        reasons=(reason,),
    )


def _quality_gate_binding_projection(payload: dict[str, Any], *, status: str) -> dict[str, Any]:
    findings = payload.get("findings") if isinstance(payload.get("findings"), list) else []
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    blocking_count = sum(
        1
        for finding in findings
        if isinstance(finding, dict) and finding.get("blocking_level") == "blocking"
    )
    return {
        "status": status,
        "gate_status": payload.get("status"),
        "gate_stage_id": metadata.get("gate_stage_id") or metadata.get("stage_id"),
        "blocking_findings": blocking_count,
    }


def _quality_gate_binding_reasons(
    *,
    payload: dict[str, Any],
    stage_id: str,
    expected_brief: str,
    expected_ledger: str,
    stages: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
) -> list[str]:
    errors = validate_quality_gate_report_payload(payload, stages=stages, artifacts=artifacts)
    if errors:
        return [f"Quality gate report is invalid: {' '.join(errors)}"]
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    gate_stage_id = str(metadata.get("gate_stage_id") or metadata.get("stage_id") or "")
    if gate_stage_id != stage_id:
        return [
            f"Quality gate report must be generated for {stage_id} completion "
            f"(metadata.gate_stage_id='{stage_id}'); got {gate_stage_id or '<missing>'}."
        ]
    brief_ref = str(metadata.get("brief") or metadata.get("audited_brief") or "")
    ledger_ref = str(metadata.get("ledger") or metadata.get("claim_ledger") or "")
    if brief_ref != expected_brief:
        return [f"Quality gate report brief metadata must be {expected_brief}; got {brief_ref}."]
    if ledger_ref != expected_ledger:
        return [f"Quality gate report ledger metadata must be {expected_ledger}; got {ledger_ref}."]
    gate_ids = {
        str(result.get("gate_id") or "")
        for result in payload.get("gate_results") or []
        if isinstance(result, dict)
    }
    required_gate_ids = {"material_fact", "freshness", "target_relevance"}
    missing_gate_ids = sorted(required_gate_ids - gate_ids)
    if missing_gate_ids:
        return [
            "Quality gate report must include material_fact, freshness, and target_relevance gate_results; "
            f"missing: {', '.join(missing_gate_ids)}."
        ]
    if payload.get("status") == "fail":
        return ["Quality gate report status is fail."]
    failed_gate_ids = sorted(
        str(result.get("gate_id") or "")
        for result in payload.get("gate_results") or []
        if isinstance(result, dict) and result.get("status") == "fail"
    )
    if failed_gate_ids:
        return [f"Quality gate report has failing gate_results: {', '.join(failed_gate_ids)}."]
    blocking_gate_ids = sorted(
        str(result.get("gate_id") or "")
        for result in payload.get("gate_results") or []
        if isinstance(result, dict) and result.get("blocking") is True
    )
    if blocking_gate_ids:
        return [f"Quality gate report has blocking gate_results: {', '.join(blocking_gate_ids)}."]
    blocking_findings = [
        str(finding.get("finding_id") or "")
        for finding in payload.get("findings") or []
        if isinstance(finding, dict) and finding.get("blocking_level") == "blocking"
    ]
    if blocking_findings:
        return [
            "Quality gate report has blocking findings: "
            + ", ".join(finding for finding in blocking_findings if finding)
        ]
    return []


def current_stage_quality_gate_blocking_reasons(
    *,
    workspace: str | Path,
    current_stage: str | None,
    stages: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
) -> list[str]:
    """Return quality-gate blockers for the current stage only."""
    if current_stage is None:
        return []
    try:
        payload = load_quality_gate_report_for_stage(
            workspace,
            current_stage,
            allow_legacy=False,
        )
    except QualityGateContractError as exc:
        return [f"Quality gate report is invalid for current stage '{current_stage}': {exc}"]
    if payload is None:
        if quality_gate_required_for_stage(workspace=workspace, current_stage=current_stage):
            key = quality_gate_report_key_for_stage(current_stage)
            paths = quality_gate_paths(workspace)
            rel_path = paths[key].relative_to(Path(workspace).expanduser().resolve()).as_posix()
            return [
                f"Current stage '{current_stage}' requires {rel_path} before continuing. "
                f"Run multi-agent-brief gates check --stage {current_stage} or request human review."
            ]
        return []

    errors = validate_quality_gate_report_payload(
        payload,
        stages=stages,
        artifacts=artifacts,
    )
    if errors:
        return [f"Quality gate report is invalid for current stage '{current_stage}': {' '.join(errors)}"]

    current_findings = [
        finding
        for finding in payload.get("findings") or []
        if isinstance(finding, dict)
        and (
            finding.get("gate_stage_id")
            or (payload.get("metadata") or {}).get("gate_stage_id")
            or (payload.get("metadata") or {}).get("stage_id")
            or finding.get("stage_id")
        ) == current_stage
        and finding.get("blocking_level") == "blocking"
    ]
    if not current_findings:
        return []
    ids = ", ".join(str(finding.get("finding_id")) for finding in current_findings)
    return [
        f"Current stage '{current_stage}' has blocking quality gate findings: {ids}. "
        "Use request_human_review, block_run, or explicit feedback/repair routing before continuing."
    ]
