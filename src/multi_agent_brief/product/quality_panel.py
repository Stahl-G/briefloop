"""Product-layer quality panel projection.

The Quality Panel summarizes existing control-plane artifacts for operator
review. It does not run gates, call LLMs, mutate workflow state, approve
delivery, or decide release eligibility.
"""

from __future__ import annotations

import json
import os
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from multi_agent_brief.core.claim_ledger import ClaimLedger

QUALITY_PANEL_SCHEMA_VERSION = "briefloop.quality_panel.v1"
QUALITY_PANEL_BOUNDARY = "product_quality_panel_projection_only_not_gate_or_release_authority"
QUALITY_PANEL_RUNTIME_EFFECT = "projection_only"
QUALITY_SUMMARY_BOUNDARY = (
    "deterministic projection of quality_panel.json only; not a quality score, "
    "not a truth proof, not a gate report replacement, and not a release authorization"
)

_INTERMEDIATE = Path("output") / "intermediate"
_BLOCKING_SUPPORT_LABELS = {"unsupported", "contradicted", "insufficient_evidence"}
_QUALITY_SUMMARY_FORBIDDEN_PHRASES = (
    "ready to publish",
    "truth proven",
    "approved for publication",
    "approved for release",
    "release authorized",
)
_QUALITY_PANEL_SHA_PREFIX = "Quality-Panel-SHA256: sha256:"


class QualityPanelError(ValueError):
    """Raised when a Quality Panel projection cannot be built or rendered."""


def quality_panel_path(workspace: str | Path) -> Path:
    return Path(workspace).expanduser().resolve() / _INTERMEDIATE / "quality_panel.json"


def quality_summary_path(workspace: str | Path) -> Path:
    return Path(workspace).expanduser().resolve() / _INTERMEDIATE / "quality_summary.md"


def build_quality_panel(workspace: str | Path) -> dict[str, Any]:
    """Build a read-only machine-readable quality projection."""

    from multi_agent_brief.status import build_workspace_status

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
        workflow=workflow,
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
        "generated_at": _utc_now(),
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


def render_quality_summary(
    panel_payload: Mapping[str, Any],
    *,
    quality_panel_sha256: str,
) -> str:
    """Render a compact human-readable summary from a valid Quality Panel payload."""

    reason = validate_quality_panel_payload(panel_payload)
    if reason:
        raise QualityPanelError(f"quality_panel invalid: {reason}")
    panel_sha256 = _normalize_sha256(quality_panel_sha256)
    if not panel_sha256:
        raise QualityPanelError("quality_panel_sha256 must be a SHA-256 hex digest.")

    source = panel_payload.get("source_evidence")
    source = source if isinstance(source, Mapping) else {}
    gates = panel_payload.get("gates")
    gates = gates if isinstance(gates, Mapping) else {}
    claims = panel_payload.get("claims")
    claims = claims if isinstance(claims, Mapping) else {}
    delivery = panel_payload.get("delivery")
    delivery = delivery if isinstance(delivery, Mapping) else {}
    control = panel_payload.get("control_integrity")
    control = control if isinstance(control, Mapping) else {}
    actions = panel_payload.get("recommended_actions")
    actions = actions if isinstance(actions, list) else []

    lines = [
        "# Quality Summary",
        "",
        f"Boundary: {QUALITY_SUMMARY_BOUNDARY}.",
        f"{_QUALITY_PANEL_SHA_PREFIX}{panel_sha256}",
        "",
        "This summary is a read-only operator view of existing BriefLoop control artifacts.",
        "Use the source gate reports, artifact registry, event log, and human review records as authority.",
        "",
        "## Overall",
        "",
        f"- Overall status: `{_text(panel_payload.get('overall_status')) or 'unknown'}`",
        f"- Run ID: `{_text(panel_payload.get('run_id')) or 'unknown'}`",
        f"- Runtime effect: `{_text(panel_payload.get('runtime_effect')) or 'unknown'}`",
        f"- Quality Panel boundary: `{_text(panel_payload.get('boundary')) or 'unknown'}`",
        "",
        "## Blocking Issues",
        "",
    ]
    _extend_bullets(lines, _quality_summary_blocking_items(control, gates, claims, delivery))
    lines.extend(["", "## Warnings", ""])
    _extend_bullets(lines, _quality_summary_warning_items(source, gates, claims, delivery))
    lines.extend(["", "## Missing Or Incomplete Surfaces", ""])
    _extend_bullets(lines, _quality_summary_missing_items(control, source, gates, delivery))
    lines.extend(["", "## Source Evidence", ""])
    lines.extend([
        f"- Source pack status: `{_text(source.get('source_pack_status')) or 'unknown'}`",
        f"- Durable source records: `{_intish(source.get('source_count'))}`",
        f"- Missing source titles: `{_intish(source.get('missing_title_count'))}`",
        f"- Missing publishers/institutions: `{_intish(source.get('missing_publisher_count'))}`",
        f"- Retrieval source mix: {_inline_mapping(source.get('retrieval_source_mix'))}",
        f"- Underlying evidence mix: {_inline_mapping(source.get('underlying_evidence_mix'))}",
        "",
        "## Gates And Reader Clean",
        "",
        f"- Auditor gate: `{_text(gates.get('auditor_status')) or 'unknown'}`",
        f"- Finalize gate: `{_text(gates.get('finalize_status')) or 'unknown'}`",
        f"- Gate blocking findings: `{_intish(gates.get('blocking_count'))}`",
        f"- Gate warnings: `{_intish(gates.get('warning_count'))}`",
        f"- Reader-clean status: `{_text(delivery.get('reader_clean_status')) or 'unknown'}`",
        f"- Duplicate citation count: `{_intish(delivery.get('duplicate_citation_count'))}`",
        f"- Source appendix warnings: `{_intish(delivery.get('source_appendix_warning_count'))}`",
        "",
        "## Claims And Support Records",
        "",
        f"- Claim count: `{_intish(claims.get('claim_count'))}`",
        f"- Claim-Support Matrix status: `{_text(claims.get('claim_support_matrix_status')) or 'unknown'}`",
        f"- Unsupported/contradicted/insufficient support rows: `{_intish(claims.get('unsupported_count'))}`",
        f"- Weak-support atoms: `{_intish(claims.get('weak_support_count'))}`",
        "",
        "## Recommended Next Actions",
        "",
    ])
    _extend_bullets(lines, _quality_summary_action_items(actions))
    text = "\n".join(lines).rstrip() + "\n"
    reason = validate_quality_summary_markdown(text)
    if reason:
        raise QualityPanelError(f"quality_summary invalid: {reason}")
    return text


def write_quality_summary(
    *,
    workspace: str | Path,
    output_path: str | Path | None = None,
    panel_payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    ws = Path(workspace).expanduser().resolve()
    panel_path = quality_panel_path(ws)
    source_panel_payload = _read_json_mapping(panel_path)
    if source_panel_payload is None:
        raise QualityPanelError("quality_panel.json is required before writing quality_summary.md.")
    if panel_payload is None:
        panel_payload = source_panel_payload
    elif dict(panel_payload) != source_panel_payload:
        raise QualityPanelError("panel_payload must match quality_panel.json before writing quality_summary.md.")
    text = render_quality_summary(panel_payload, quality_panel_sha256=_sha256_file(panel_path))
    target = Path(output_path).expanduser() if output_path else quality_summary_path(ws)
    if not target.is_absolute():
        target = ws / target
    target = target.resolve()
    try:
        target.relative_to(ws)
    except ValueError as exc:
        raise ValueError("quality_summary output must stay inside the workspace.") from exc
    _write_text_atomic(target, text)
    return {
        "path": _workspace_relative(ws, target),
        "sha256": _sha256_text(text),
    }


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


def validate_quality_summary_markdown(text: Any) -> str | None:
    if not isinstance(text, str):
        return "quality_summary_schema_error:not_text"
    if not text.strip():
        return "quality_summary_schema_error:empty"
    if not text.startswith("# Quality Summary\n"):
        return "quality_summary_schema_error:title"
    if f"Boundary: {QUALITY_SUMMARY_BOUNDARY}." not in text:
        return "quality_summary_schema_error:boundary"
    panel_sha = quality_summary_panel_sha256(text)
    if not panel_sha:
        return "quality_summary_schema_error:quality_panel_sha256"
    lower = text.lower()
    for phrase in _QUALITY_SUMMARY_FORBIDDEN_PHRASES:
        if phrase in lower:
            return f"quality_summary_schema_error:forbidden_phrase:{phrase.replace(' ', '_')}"
    required_sections = (
        "## Overall",
        "## Blocking Issues",
        "## Warnings",
        "## Missing Or Incomplete Surfaces",
        "## Source Evidence",
        "## Gates And Reader Clean",
        "## Claims And Support Records",
        "## Recommended Next Actions",
    )
    for section in required_sections:
        if section not in text:
            return f"quality_summary_schema_error:missing_section:{section[3:].lower().replace(' ', '_')}"
    return None


def quality_summary_panel_sha256(text: str) -> str | None:
    for line in text.splitlines():
        if line.startswith(_QUALITY_PANEL_SHA_PREFIX):
            return _normalize_sha256(line.removeprefix(_QUALITY_PANEL_SHA_PREFIX).strip())
    return None


def _source_evidence_summary(workspace: Path, artifacts: Mapping[str, Any]) -> dict[str, Any]:
    record = _artifact_record(artifacts, "source_evidence_pack_manifest")
    source_pack_status = _source_pack_status(record)
    if source_pack_status == "present":
        manifest = _read_json_mapping(workspace / _INTERMEDIATE / "source_evidence_pack_manifest.json") or {}
    else:
        manifest = {}
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
    matrix_status = _optional_artifact_status(
        _artifact_record(artifacts, "claim_support_matrix"),
        not_available="not_available",
    )
    counts = (
        matrix.get("summary_counts")
        if matrix_status == "valid" and isinstance(matrix.get("summary_counts"), dict)
        else {}
    )
    rows = _matrix_rows(workspace) if matrix_status == "valid" else []
    return {
        "claim_count": _claim_count(workspace / _INTERMEDIATE / "claim_ledger.json"),
        "claim_support_matrix_status": matrix_status,
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
    workflow: Mapping[str, Any],
    control_integrity: Mapping[str, Any],
    source_evidence: Mapping[str, Any],
    gates: Mapping[str, Any],
    claims: Mapping[str, Any],
    delivery: Mapping[str, Any],
) -> str:
    if not workspace_status.get("ok"):
        return "incomplete"
    auditor_gate_level = _gate_status_level(gates.get("auditor_status"))
    finalize_gate_level = _gate_status_level(gates.get("finalize_status"))
    reader_clean_status = _text(delivery.get("reader_clean_status"))
    if (
        workflow.get("blocked") or
        control_integrity.get("run_integrity") not in {"clean", "unknown"}
        or gates.get("blocking_count", 0) > 0
        or auditor_gate_level == "block"
        or finalize_gate_level == "block"
        or reader_clean_status == "fail"
        or claims.get("unsupported_count", 0) > 0
    ):
        return "block"
    if (
        control_integrity.get("fact_layer_status") in {"missing", "incomplete"}
        or source_evidence.get("source_pack_status") in {"missing", "not_available"}
        or auditor_gate_level in {"missing", "incomplete"}
        or finalize_gate_level in {"missing", "incomplete"}
        or reader_clean_status != "pass"
    ):
        return "incomplete"
    if (
        source_evidence.get("source_pack_status") == "invalid"
        or claims.get("claim_support_matrix_status") == "invalid"
        or auditor_gate_level == "warning"
        or finalize_gate_level == "warning"
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
    gate_levels = {
        "auditor": _gate_status_level(gates.get("auditor_status")),
        "finalize": _gate_status_level(gates.get("finalize_status")),
    }
    failed_gate_stages = [stage for stage, level in gate_levels.items() if level == "block"]
    if failed_gate_stages and gates.get("blocking_count", 0) == 0:
        actions.append({
            "action": "resolve_quality_gate_blockers",
            "reason": "quality_gate_status_failed",
        })
    if (
        gate_levels["finalize"] in {"missing", "incomplete"}
        or _text(delivery.get("reader_clean_status")) in {"", "missing", "unknown", "invalid"}
    ):
        actions.append({
            "action": "complete_finalize_delivery_hygiene",
            "reason": "finalize_or_reader_clean_missing",
        })
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


def _gate_status_level(value: Any) -> str:
    status = _text(value)
    if status == "pass":
        return "pass"
    if status in {"fail", "failed", "block", "blocked", "blocking"}:
        return "block"
    if status == "warning":
        return "warning"
    if status == "missing":
        return "missing"
    return "incomplete"


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


def _quality_summary_blocking_items(
    control: Mapping[str, Any],
    gates: Mapping[str, Any],
    claims: Mapping[str, Any],
    delivery: Mapping[str, Any],
) -> list[str]:
    items: list[str] = []
    if _text(control.get("run_integrity")) not in {"", "clean", "unknown"}:
        items.append(f"Run integrity is `{_text(control.get('run_integrity'))}`.")
    if _intish(gates.get("blocking_count")) > 0:
        items.append(f"Quality gates report `{_intish(gates.get('blocking_count'))}` blocking finding(s).")
    if _gate_status_level(gates.get("auditor_status")) == "block":
        items.append(f"Auditor gate status is `{_text(gates.get('auditor_status'))}`.")
    if _gate_status_level(gates.get("finalize_status")) == "block":
        items.append(f"Finalize gate status is `{_text(gates.get('finalize_status'))}`.")
    if _text(delivery.get("reader_clean_status")) == "fail":
        items.append("Reader-clean status is `fail`.")
    if _intish(claims.get("unsupported_count")) > 0:
        items.append(
            "Claim-Support Matrix projection includes "
            f"`{_intish(claims.get('unsupported_count'))}` unsupported/contradicted/insufficient row(s)."
        )
    return items


def _quality_summary_warning_items(
    source: Mapping[str, Any],
    gates: Mapping[str, Any],
    claims: Mapping[str, Any],
    delivery: Mapping[str, Any],
) -> list[str]:
    items: list[str] = []
    if _text(source.get("source_pack_status")) == "invalid":
        items.append("Durable source evidence pack manifest is invalid.")
    if _intish(gates.get("warning_count")) > 0:
        items.append(f"Quality gates report `{_intish(gates.get('warning_count'))}` warning finding(s).")
    if _gate_status_level(gates.get("auditor_status")) == "warning":
        items.append("Auditor gate status is `warning`.")
    if _gate_status_level(gates.get("finalize_status")) == "warning":
        items.append("Finalize gate status is `warning`.")
    if _text(claims.get("claim_support_matrix_status")) == "invalid":
        items.append("Claim-Support Matrix is invalid and is not interpreted as support authority.")
    if _intish(claims.get("weak_support_count")) > 0:
        items.append(f"`{_intish(claims.get('weak_support_count'))}` atom(s) have weak-support projection.")
    if _intish(delivery.get("source_appendix_warning_count")) > 0:
        items.append(
            f"Source appendix surfaces `{_intish(delivery.get('source_appendix_warning_count'))}` warning(s)."
        )
    return items


def _quality_summary_missing_items(
    control: Mapping[str, Any],
    source: Mapping[str, Any],
    gates: Mapping[str, Any],
    delivery: Mapping[str, Any],
) -> list[str]:
    items: list[str] = []
    if _text(control.get("fact_layer_status")) in {"", "missing", "incomplete"}:
        items.append(f"Fact layer status is `{_text(control.get('fact_layer_status')) or 'unknown'}`.")
    if _text(source.get("source_pack_status")) in {"", "missing", "not_available"}:
        items.append("Durable source evidence pack is missing or not available.")
    if _gate_status_level(gates.get("auditor_status")) in {"missing", "incomplete"}:
        items.append(f"Auditor gate status is `{_text(gates.get('auditor_status')) or 'unknown'}`.")
    if _gate_status_level(gates.get("finalize_status")) in {"missing", "incomplete"}:
        items.append(f"Finalize gate status is `{_text(gates.get('finalize_status')) or 'unknown'}`.")
    if _text(delivery.get("reader_clean_status")) != "pass":
        items.append(f"Reader-clean status is `{_text(delivery.get('reader_clean_status')) or 'unknown'}`.")
    return items


def _quality_summary_action_items(actions: list[Any]) -> list[str]:
    items: list[str] = []
    for action in actions:
        if not isinstance(action, Mapping):
            continue
        action_name = _text(action.get("action")) or "unknown_action"
        reason = _text(action.get("reason")) or "unspecified"
        items.append(f"`{action_name}` - {reason}.")
    return items


def _extend_bullets(lines: list[str], items: list[str]) -> None:
    if not items:
        lines.append("- None reported by `quality_panel.json`.")
        return
    for item in items:
        lines.append(f"- {item}")


def _inline_mapping(value: Any) -> str:
    if not isinstance(value, Mapping) or not value:
        return "`none`"
    parts = [
        f"{_text(key) or str(key)}={_intish(count)}"
        for key, count in sorted(value.items(), key=lambda item: str(item[0]))
    ]
    return "`" + ", ".join(parts) + "`"


def _intish(value: Any) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _sha256_text(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _normalize_sha256(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if stripped.startswith("sha256:"):
        stripped = stripped.removeprefix("sha256:")
    if len(stripped) != 64:
        return None
    lowered = stripped.lower()
    if any(ch not in "0123456789abcdef" for ch in lowered):
        return None
    return lowered


def _workspace_relative(workspace: Path, path: Path) -> str:
    return path.resolve().relative_to(workspace.resolve()).as_posix()


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


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
