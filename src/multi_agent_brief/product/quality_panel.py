"""Product-layer quality panel projection.

The Quality Panel summarizes existing control-plane artifacts for operator
review. It does not run gates, call LLMs, mutate workflow state, approve
delivery, or decide release eligibility.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

from multi_agent_brief.core.claim_ledger import ClaimLedger
from multi_agent_brief.orchestrator.runtime_state._io import _write_json_atomic
from multi_agent_brief.orchestrator.runtime_state.identity import utc_now
from multi_agent_brief.status import build_workspace_status

QUALITY_PANEL_SCHEMA_VERSION = "briefloop.quality_panel.v1"
QUALITY_PANEL_BOUNDARY = "product_quality_panel_projection_only_not_gate_or_release_authority"
QUALITY_PANEL_RUNTIME_EFFECT = "projection_only"

_INTERMEDIATE = Path("output") / "intermediate"
_BLOCKING_SUPPORT_LABELS = {"unsupported", "contradicted", "insufficient_evidence"}


def quality_panel_path(workspace: str | Path) -> Path:
    return Path(workspace).expanduser().resolve() / _INTERMEDIATE / "quality_panel.json"


def build_quality_panel(workspace: str | Path) -> dict[str, Any]:
    """Build a read-only machine-readable quality projection."""

    ws = Path(workspace).expanduser().resolve()
    workspace_status = build_workspace_status(ws)
    registry_payload = _read_json_mapping(ws / _INTERMEDIATE / "artifact_registry.json") or {}
    artifacts = registry_payload.get("artifacts") if isinstance(registry_payload, dict) else {}
    artifacts = artifacts if isinstance(artifacts, dict) else {}

    runtime = workspace_status.get("runtime") if isinstance(workspace_status.get("runtime"), dict) else {}
    workflow = workspace_status.get("workflow") if isinstance(workspace_status.get("workflow"), dict) else {}
    run_integrity = workflow.get("run_integrity") if isinstance(workflow.get("run_integrity"), dict) else {}
    source_evidence = _source_evidence_summary(ws, artifacts)
    gates = _gate_summary(ws)
    claims = _claim_summary(ws, workspace_status, artifacts)
    delivery = _delivery_summary(ws, workspace_status)
    control_integrity = {
        "run_integrity": run_integrity.get("status") or "unknown",
        "reference_eligible": bool(run_integrity.get("reference_eligible")),
        "fact_layer_status": _fact_layer_status(artifacts, source_evidence),
    }
    recommended_actions = _recommended_actions(
        workflow=workflow,
        control_integrity=control_integrity,
        source_evidence=source_evidence,
        gates=gates,
        claims=claims,
        delivery=delivery,
    )
    overall_status = _overall_status(
        workspace_status=workspace_status,
        control_integrity=control_integrity,
        source_evidence=source_evidence,
        gates=gates,
        claims=claims,
        delivery=delivery,
    )

    return {
        "schema_version": QUALITY_PANEL_SCHEMA_VERSION,
        "workspace": ".",
        "run_id": _text(runtime.get("run_id")) or "unknown",
        "generated_at": utc_now(),
        "read_only": True,
        "runtime_effect": QUALITY_PANEL_RUNTIME_EFFECT,
        "boundary": QUALITY_PANEL_BOUNDARY,
        "overall_status": overall_status,
        "control_integrity": control_integrity,
        "source_evidence": source_evidence,
        "gates": gates,
        "claims": claims,
        "delivery": delivery,
        "recommended_actions": recommended_actions,
        "non_goals": [
            "quality_score",
            "semantic_truth_proof",
            "release_eligibility_decision",
            "delivery_approval",
            "gate_reimplementation",
            "automatic_repair",
        ],
    }


def write_quality_panel(
    *,
    workspace: str | Path,
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    ws = Path(workspace).expanduser().resolve()
    target = Path(output_path).expanduser() if output_path else quality_panel_path(ws)
    if not target.is_absolute():
        target = ws / target
    target = target.resolve()
    try:
        target.relative_to(ws)
    except ValueError as exc:
        raise ValueError("quality_panel output must stay inside the workspace.") from exc
    payload = build_quality_panel(ws)
    _write_json_atomic(target, payload)
    return payload


def validate_quality_panel_payload(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return "quality_panel_schema_error:not_object"
    if payload.get("schema_version") != QUALITY_PANEL_SCHEMA_VERSION:
        return "quality_panel_schema_error:schema_version"
    if payload.get("boundary") != QUALITY_PANEL_BOUNDARY:
        return "quality_panel_schema_error:boundary"
    if payload.get("runtime_effect") != QUALITY_PANEL_RUNTIME_EFFECT:
        return "quality_panel_schema_error:runtime_effect"
    if payload.get("workspace") != ".":
        return "quality_panel_schema_error:workspace"
    if not _text(payload.get("run_id")):
        return "quality_panel_schema_error:run_id"
    if payload.get("overall_status") not in {"pass", "warning", "block", "incomplete"}:
        return "quality_panel_schema_error:overall_status"
    for field in ("control_integrity", "source_evidence", "gates", "claims", "delivery"):
        if not isinstance(payload.get(field), dict):
            return f"quality_panel_schema_error:{field}"
    if not isinstance(payload.get("recommended_actions"), list):
        return "quality_panel_schema_error:recommended_actions"
    if not isinstance(payload.get("non_goals"), list):
        return "quality_panel_schema_error:non_goals"
    forbidden = {"semantic_truth_proof", "release_eligibility_decision", "delivery_approval"}
    if not forbidden.issubset(set(str(item) for item in payload.get("non_goals", []))):
        return "quality_panel_schema_error:non_goals"
    return None


def _source_evidence_summary(workspace: Path, artifacts: Mapping[str, Any]) -> dict[str, Any]:
    record = _artifact_record(artifacts, "source_evidence_pack_manifest")
    source_pack_status = _source_pack_status(record)
    manifest = _read_json_mapping(workspace / _INTERMEDIATE / "source_evidence_pack_manifest.json") or {}
    records = manifest.get("records") if isinstance(manifest, dict) else []
    records = records if isinstance(records, list) else []
    retrieval_mix: Counter[str] = Counter()
    underlying_mix: Counter[str] = Counter()
    missing_title_count = 0
    missing_publisher_count = 0
    usable_records = 0
    for item in records:
        if not isinstance(item, dict):
            continue
        usable_records += 1
        title = _first_text(item, "source_title", "title", "source_name")
        publisher = _first_text(item, "publisher", "publisher_or_institution", "source_name")
        if not title:
            missing_title_count += 1
        if not publisher:
            missing_publisher_count += 1
        retrieval_mix[_first_text(item, "retrieval_source_type") or "unknown"] += 1
        underlying_mix[_first_text(item, "underlying_evidence_type", "source_category") or "unknown"] += 1
    return {
        "source_pack_status": source_pack_status,
        "source_count": int(manifest.get("record_count") or usable_records or 0)
        if isinstance(manifest, dict)
        else 0,
        "missing_title_count": missing_title_count,
        "missing_publisher_count": missing_publisher_count,
        "retrieval_source_mix": dict(sorted(retrieval_mix.items())),
        "underlying_evidence_mix": dict(sorted(underlying_mix.items())),
    }


def _gate_summary(workspace: Path) -> dict[str, Any]:
    auditor = _gate_file_summary(workspace / _INTERMEDIATE / "gates" / "auditor_quality_gate_report.json")
    finalize = _gate_file_summary(workspace / _INTERMEDIATE / "gates" / "finalize_quality_gate_report.json")
    return {
        "auditor_status": auditor["status"],
        "finalize_status": finalize["status"],
        "blocking_count": auditor["blocking_count"] + finalize["blocking_count"],
        "warning_count": auditor["warning_count"] + finalize["warning_count"],
    }


def _gate_file_summary(path: Path) -> dict[str, int | str]:
    payload = _read_json_mapping(path)
    if payload is None:
        return {"status": "missing", "blocking_count": 0, "warning_count": 0}
    findings = payload.get("findings") if isinstance(payload.get("findings"), list) else []
    blocking = 0
    warning = 0
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        if finding.get("blocking") is True or finding.get("blocking_level") == "blocking":
            blocking += 1
        else:
            warning += 1
    status = _text(payload.get("status")) or "unknown"
    return {"status": status, "blocking_count": blocking, "warning_count": warning}


def _claim_summary(
    workspace: Path,
    workspace_status: Mapping[str, Any],
    artifacts: Mapping[str, Any],
) -> dict[str, Any]:
    matrix = workspace_status.get("claim_support_matrix")
    matrix = matrix if isinstance(matrix, dict) else {}
    counts = matrix.get("summary_counts") if isinstance(matrix.get("summary_counts"), dict) else {}
    rows = _matrix_rows(workspace)
    return {
        "claim_count": _claim_count(workspace / _INTERMEDIATE / "claim_ledger.json"),
        "claim_support_matrix_status": _optional_artifact_status(
            _artifact_record(artifacts, "claim_support_matrix"),
            not_available="not_available",
        ),
        "weak_support_count": int(counts.get("weak_atom_count") or 0),
        "unsupported_count": sum(
            1
            for row in rows
            if isinstance(row, dict) and _text(row.get("support_label")) in _BLOCKING_SUPPORT_LABELS
        ),
    }


def _delivery_summary(workspace: Path, workspace_status: Mapping[str, Any]) -> dict[str, Any]:
    reader = workspace_status.get("reader_clean")
    reader = reader if isinstance(reader, dict) else {}
    finalize_report = _read_json_mapping(workspace / _INTERMEDIATE / "finalize_report.json") or {}
    source_warnings = finalize_report.get("source_appendix_warnings")
    trace_warnings = finalize_report.get("source_appendix_trace_warnings")
    source_warning_count = len(source_warnings) if isinstance(source_warnings, list) else 0
    trace_warning_count = len(trace_warnings) if isinstance(trace_warnings, list) else 0
    return {
        "reader_clean_status": reader.get("status") or "missing",
        "duplicate_citation_count": int(finalize_report.get("duplicate_citation_count") or 0)
        if isinstance(finalize_report, dict)
        else 0,
        "source_appendix_warning_count": source_warning_count + trace_warning_count,
    }


def _overall_status(
    *,
    workspace_status: Mapping[str, Any],
    control_integrity: Mapping[str, Any],
    source_evidence: Mapping[str, Any],
    gates: Mapping[str, Any],
    claims: Mapping[str, Any],
    delivery: Mapping[str, Any],
) -> str:
    if not workspace_status.get("ok"):
        return "incomplete"
    if (
        control_integrity.get("run_integrity") not in {"clean", "unknown"}
        or gates.get("blocking_count", 0) > 0
        or delivery.get("reader_clean_status") == "fail"
        or claims.get("unsupported_count", 0) > 0
    ):
        return "block"
    if (
        control_integrity.get("fact_layer_status") in {"missing", "incomplete"}
        or source_evidence.get("source_pack_status") in {"missing", "not_available"}
        or gates.get("auditor_status") == "missing"
    ):
        return "incomplete"
    if (
        source_evidence.get("source_pack_status") == "invalid"
        or claims.get("claim_support_matrix_status") == "invalid"
        or gates.get("warning_count", 0) > 0
        or delivery.get("source_appendix_warning_count", 0) > 0
        or claims.get("weak_support_count", 0) > 0
    ):
        return "warning"
    return "pass"


def _recommended_actions(
    *,
    workflow: Mapping[str, Any],
    control_integrity: Mapping[str, Any],
    source_evidence: Mapping[str, Any],
    gates: Mapping[str, Any],
    claims: Mapping[str, Any],
    delivery: Mapping[str, Any],
) -> list[dict[str, str]]:
    actions: list[dict[str, str]] = []
    if workflow.get("blocked"):
        actions.append({
            "action": "inspect_workflow_blocker",
            "reason": _text(workflow.get("blocking_reason")) or "workflow_blocked",
        })
    if source_evidence.get("source_pack_status") in {"missing", "not_available"}:
        actions.append({
            "action": "materialize_durable_source_evidence",
            "reason": "source_evidence_pack_missing",
        })
    elif source_evidence.get("source_pack_status") == "invalid":
        actions.append({
            "action": "repair_source_evidence_pack_manifest",
            "reason": "source_evidence_pack_invalid",
        })
    if gates.get("blocking_count", 0) > 0:
        actions.append({"action": "resolve_quality_gate_blockers", "reason": "blocking_gate_findings"})
    if claims.get("unsupported_count", 0) > 0:
        actions.append({"action": "review_claim_support_records", "reason": "unsupported_claim_support_rows"})
    if delivery.get("reader_clean_status") == "fail":
        actions.append({"action": "repair_reader_final_residue", "reason": "reader_clean_failed"})
    if control_integrity.get("run_integrity") not in {"clean", "unknown"}:
        actions.append({"action": "inspect_run_integrity", "reason": "run_integrity_not_clean"})
    return actions[:20]


def _fact_layer_status(artifacts: Mapping[str, Any], source_evidence: Mapping[str, Any]) -> str:
    claim_ledger_status = _optional_artifact_status(_artifact_record(artifacts, "claim_ledger"))
    source_pack_status = str(source_evidence.get("source_pack_status") or "")
    if claim_ledger_status == "valid" and source_pack_status == "present":
        return "complete"
    if claim_ledger_status in {"missing", "not_available"}:
        return "missing"
    return "incomplete"


def _source_pack_status(record: Mapping[str, Any] | None) -> str:
    status = _optional_artifact_status(record, not_available="not_available")
    if status == "valid":
        return "present"
    if status in {"expected", "missing", "not_available"}:
        return "missing"
    if status in {"invalid", "stale"}:
        return "invalid"
    return status


def _optional_artifact_status(
    record: Mapping[str, Any] | None,
    *,
    not_available: str = "not_available",
) -> str:
    if not isinstance(record, Mapping):
        return not_available
    status = _text(record.get("status"))
    return status or not_available


def _artifact_record(artifacts: Mapping[str, Any], artifact_id: str) -> Mapping[str, Any] | None:
    record = artifacts.get(artifact_id)
    return record if isinstance(record, Mapping) else None


def _claim_count(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return len(ClaimLedger._claim_items_from_json(payload))
    except (OSError, json.JSONDecodeError, ValueError):
        return 0


def _matrix_rows(workspace: Path) -> list[dict[str, Any]]:
    payload = _read_json_mapping(workspace / _INTERMEDIATE / "claim_support_matrix.json")
    rows = payload.get("rows") if isinstance(payload, dict) else None
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _read_json_mapping(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _first_text(mapping: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = _text(mapping.get(key))
        if value:
            return value
    return ""


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""
