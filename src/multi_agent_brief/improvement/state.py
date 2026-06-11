"""File-backed Improvement Ledger state helpers.

This layer owns workspace file IO for ``improvement/ledger.jsonl``.  It keeps
the ledger as the source of truth and treats runtime events as optional trace
records when a runtime state already exists.
"""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from multi_agent_brief.feedback.feedback_contract import (
    feedback_state_paths,
    validate_feedback_issues_payload,
)
from multi_agent_brief.improvement.contract import (
    ALLOWED_STATUSES,
    AUDIENCE_GUIDANCE_CATEGORIES,
    AUDIENCE_GUIDANCE_SCOPES,
    IMPROVEMENT_LEDGER_SCHEMA,
    LEDGER_RELATIVE_PATH,
    LedgerDiagnostic,
    canonical_json,
    read_ledger_text,
    revision_sha256,
    supersedes_id,
    validate_next_revision,
)
from multi_agent_brief.improvement.product_definition import (
    ProductDefinitionDecision,
    classify_improvement_source,
    classify_ledger_entry_materialization,
)
from multi_agent_brief.orchestrator.runtime_state import (
    RuntimeStateError,
    append_event,
    load_artifact_contracts,
    load_stage_specs,
    runtime_state_paths,
)
from multi_agent_brief.orchestrator_contract import resolve_repo_workdir


IMPROVEMENT_EVENT_TYPES = {
    "proposed": "improvement_proposed",
    "approved": "improvement_approved",
    "rejected": "improvement_rejected",
    "reverted": "improvement_reverted",
}


class ImprovementLedgerError(Exception):
    """Raised when the Improvement Ledger cannot be read or updated safely."""

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {"ok": False, "error": str(self), "details": self.details}


def improvement_ledger_path(workspace: str | Path) -> Path:
    return Path(workspace).expanduser().resolve() / LEDGER_RELATIVE_PATH


def show_improvement_ledger(*, workspace: str | Path) -> dict[str, Any]:
    ws = _require_workspace(workspace)
    read_result = _read_ledger(ws)
    return _state_payload(workspace=ws, read_result=read_result)


def validate_improvement_ledger(*, workspace: str | Path) -> dict[str, Any]:
    ws = _require_workspace(workspace)
    read_result = _read_ledger(ws)
    diagnostics = [_diagnostic_to_dict(item) for item in read_result.diagnostics]
    materialization_diagnostics = _materialization_diagnostics(read_result.current_entries)
    return {
        "ok": not diagnostics,
        "workspace": str(ws),
        "ledger_path": str(improvement_ledger_path(ws)),
        "diagnostics": diagnostics,
        "materialization_diagnostics": materialization_diagnostics,
        "revision_count": len(read_result.valid_revisions),
        "entry_count": len(read_result.current_entries),
    }


def list_improvements(
    *,
    workspace: str | Path,
    status: str | None = None,
) -> dict[str, Any]:
    ws = _require_workspace(workspace)
    if status is not None and status not in ALLOWED_STATUSES:
        raise ImprovementLedgerError(
            f"Unknown improvement status: {status}",
            details={"status": status, "known_statuses": sorted(ALLOWED_STATUSES)},
        )
    state = show_improvement_ledger(workspace=ws)
    entries = list(state["current_entries"].values())
    if status is not None:
        entries = [entry for entry in entries if entry.get("status") == status]
    return {
        **state,
        "current_entries": entries,
        "entry_count": len(entries),
        "filter": {"status": status},
    }


def show_improvement(
    *,
    workspace: str | Path,
    entry_id: str,
) -> dict[str, Any]:
    ws = _require_workspace(workspace)
    read_result = _read_ledger(ws)
    revisions = [
        revision
        for revision in read_result.valid_revisions
        if revision.get("entry_id") == entry_id
    ]
    current = read_result.current_entries.get(entry_id)
    if current is None:
        raise ImprovementLedgerError(
            f"Unknown improvement entry: {entry_id}",
            details={"entry_id": entry_id},
        )
    computed = _computed_current_entries(read_result.current_entries)
    return {
        "ok": True,
        "workspace": str(ws),
        "ledger_path": str(improvement_ledger_path(ws)),
        "entry_id": entry_id,
        "current": computed.get(entry_id, dict(current)),
        "revisions": revisions,
        "diagnostics": [_diagnostic_to_dict(item) for item in read_result.diagnostics],
    }


def improvement_stats(*, workspace: str | Path) -> dict[str, Any]:
    ws = _require_workspace(workspace)
    read_result = _read_ledger(ws)
    computed_entries = _computed_current_entries(read_result.current_entries)
    current_entries = list(computed_entries.values())
    by_status = _count_by(current_entries, "status")
    by_category = _count_by(
        current_entries,
        lambda entry: ((entry.get("change") or {}).get("category") or "unknown"),
    )
    by_source_type: dict[str, int] = {}
    for entry in current_entries:
        for evidence in entry.get("source_evidence") or []:
            if isinstance(evidence, dict):
                source_type = str(evidence.get("source_type") or "unknown")
                by_source_type[source_type] = by_source_type.get(source_type, 0) + 1

    approved_entries = [entry for entry in current_entries if entry.get("status") == "approved"]
    approved_count = len(approved_entries)
    superseded_count = sum(1 for entry in approved_entries if entry.get("is_superseded") is True)
    eligible_for_materialization_count = sum(
        1
        for entry in approved_entries
        if not entry.get("is_superseded")
        and classify_ledger_entry_materialization(entry).materializable
    )
    return {
        "ok": True,
        "workspace": str(ws),
        "ledger_path": str(improvement_ledger_path(ws)),
        "entry_count": len(current_entries),
        "revision_count": len(read_result.valid_revisions),
        "approved_count": approved_count,
        "superseded_count": superseded_count,
        "eligible_for_materialization_count": eligible_for_materialization_count,
        "reverted_count": by_status.get("reverted", 0),
        "counts_by_status": by_status,
        "counts_by_category": by_category,
        "counts_by_source_type": by_source_type,
        "diagnostics": [_diagnostic_to_dict(item) for item in read_result.diagnostics],
    }


def propose_improvement(
    *,
    workspace: str | Path,
    guidance: str,
    category: str,
    scope: str,
    source_summary: str | None = None,
    from_issue: str | None = None,
    supersedes: str | None = None,
) -> dict[str, Any]:
    ws = _require_workspace(workspace)
    _validate_category_scope(category=category, scope=scope)
    if from_issue and source_summary:
        raise ImprovementLedgerError(
            "--from-issue and --source-summary are mutually exclusive.",
            details={"from_issue": from_issue},
        )
    if from_issue:
        evidence, product_decision = _evidence_from_feedback_issue(workspace=ws, issue_id=from_issue)
        source_evidence = [evidence]
    elif source_summary:
        product_decision = classify_improvement_source(
            source_type="human_feedback",
            issue_category=category,
        )
        evidence: dict[str, Any] = {
            "source_type": "human_feedback",
            "summary": source_summary,
            "run_id": None,
            "issue_id": None,
        }
        origin = _human_feedback_origin(ws)
        if origin:
            evidence["origin"] = origin
        source_evidence = [evidence]
    else:
        raise ImprovementLedgerError(
            "--source-summary is required unless --from-issue is used.",
            details={"required": "source_summary"},
        )

    read_result = _read_ledger(ws)
    entry_id = _next_entry_id(read_result.valid_revisions)
    supersedes_value = supersedes.strip() if isinstance(supersedes, str) and supersedes.strip() else None
    warnings: list[dict[str, Any]] = []
    if supersedes_value is not None:
        warnings.extend(_validate_supersedes_target(
            current_entries=read_result.current_entries,
            new_entry_id=entry_id,
            supersedes_id=supersedes_value,
            category=category,
            scope=scope,
        ))
    else:
        warnings.extend(_duplicate_guidance_warnings(
            current_entries=read_result.current_entries,
            category=category,
            scope=scope,
        ))
    revision = {
        "schema_version": IMPROVEMENT_LEDGER_SCHEMA,
        "entry_id": entry_id,
        "revision": 1,
        "previous_revision_sha256": None,
        "supersedes_id": supersedes_value,
        "created_at": _utc_now(),
        "status": "proposed",
        "level": 2,
        "target_kind": "audience_guidance",
        "change": {
            "category": category,
            "scope": scope,
            "guidance_text": guidance,
        },
        "source_evidence": source_evidence,
    }
    return _append_transition(
        workspace=ws,
        revision=revision,
        event_type="improvement_proposed",
        event_reason="Improvement guidance proposed.",
        product_decision=product_decision,
        warnings=warnings,
    )


def approve_improvement(
    *,
    workspace: str | Path,
    entry_id: str,
    approved_by: str,
) -> dict[str, Any]:
    ws = _require_workspace(workspace)
    previous = _current_entry(ws, entry_id)
    revision = _status_revision(previous, status="approved")
    revision["approved_by"] = approved_by
    revision["approved_at"] = _utc_now()
    return _append_transition(
        workspace=ws,
        revision=revision,
        event_type="improvement_approved",
        event_reason="Improvement guidance approved.",
    )


def reject_improvement(
    *,
    workspace: str | Path,
    entry_id: str,
    rejected_by: str,
    reason: str,
) -> dict[str, Any]:
    ws = _require_workspace(workspace)
    previous = _current_entry(ws, entry_id)
    revision = _status_revision(previous, status="rejected")
    revision["rejected_by"] = rejected_by
    revision["rejected_at"] = _utc_now()
    revision["rejection_reason"] = reason
    return _append_transition(
        workspace=ws,
        revision=revision,
        event_type="improvement_rejected",
        event_reason=reason,
    )


def revert_improvement(
    *,
    workspace: str | Path,
    entry_id: str,
    reverted_by: str,
    reason: str,
) -> dict[str, Any]:
    ws = _require_workspace(workspace)
    read_result = _read_ledger(ws)
    previous = _current_entry_from_read_result(read_result, entry_id)
    revision = _status_revision(previous, status="reverted")
    revision["reverted_by"] = reverted_by
    revision["reverted_at"] = _utc_now()
    revision["revert_reason"] = reason
    warnings = _reexposed_entries_on_revert(
        current_entries=read_result.current_entries,
        superseder_entry_id=entry_id,
        revert_revision=revision,
    )
    return _append_transition(
        workspace=ws,
        revision=revision,
        event_type="improvement_reverted",
        event_reason=reason,
        warnings=warnings,
    )


def _append_transition(
    *,
    workspace: Path,
    revision: dict[str, Any],
    event_type: str,
    event_reason: str,
    product_decision: ProductDefinitionDecision | None = None,
    warnings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    existing_text = _read_ledger_text(workspace)
    preflight = validate_next_revision(existing_text, revision)
    if not preflight.ok:
        raise ImprovementLedgerError(
            "Improvement ledger revision failed validation.",
            details={"diagnostics": [_diagnostic_to_dict(item) for item in preflight.diagnostics]},
        )
    event_preflight = _runtime_event_surface_preflight(workspace)

    path = improvement_ledger_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(canonical_json(revision))
        handle.write("\n")

    event_state = _append_transition_event(
        workspace=workspace,
        revision=revision,
        event_type=event_type,
        reason=event_reason,
        event_preflight=event_preflight,
    )
    state = show_improvement(workspace=workspace, entry_id=str(revision["entry_id"]))
    state.update(event_state)
    state["entry"] = revision
    if product_decision is not None:
        state["product_definition"] = product_decision.to_dict()
    state["warnings"] = warnings or []
    return state


def _append_transition_event(
    *,
    workspace: Path,
    revision: dict[str, Any],
    event_type: str,
    reason: str,
    event_preflight: dict[str, Any],
) -> dict[str, Any]:
    if not event_preflight.get("active"):
        return {"event_recorded": False, "event_reason": "no_runtime_state"}
    run_id = str(event_preflight["run_id"])

    try:
        event = append_event(
            workspace=workspace,
            run_id=run_id,
            event_type=event_type,
            actor="cli",
            reason=reason,
            metadata={
                "entry_id": revision.get("entry_id"),
                "revision": revision.get("revision"),
                "status": revision.get("status"),
            },
        )
    except Exception as exc:  # event trace is best-effort after ledger append
        return {
            "event_recorded": False,
            "event_reason": "event_append_failed",
            "event_error": str(exc),
        }
    return {
        "event_recorded": True,
        "event_reason": "recorded",
        "event_id": event.get("event_id"),
    }


def _runtime_event_surface_preflight(workspace: Path) -> dict[str, Any]:
    runtime_paths = runtime_state_paths(workspace)
    manifest_path = runtime_paths["runtime_manifest"]
    event_log_path = runtime_paths["event_log"]
    manifest_exists = manifest_path.exists()
    event_log_exists = event_log_path.exists()
    if not manifest_exists and not event_log_exists:
        return {"active": False}
    if manifest_exists != event_log_exists:
        missing = "event_log" if manifest_exists else "runtime_manifest"
        raise ImprovementLedgerError(
            "Runtime event surface is incomplete; refusing to append ledger revision.",
            details={"missing": missing},
        )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ImprovementLedgerError(
            "runtime_manifest.json is not valid JSON; refusing to append ledger revision.",
            details={"path": str(manifest_path), "error": str(exc)},
        ) from exc
    run_id = manifest.get("run_id")
    if not isinstance(run_id, str) or not run_id.strip():
        raise ImprovementLedgerError(
            "runtime_manifest.json is missing run_id; refusing to append ledger revision.",
            details={"path": str(manifest_path)},
        )
    _preflight_event_log_jsonl(event_log_path)
    return {"active": True, "run_id": run_id.strip()}


def _preflight_event_log_jsonl(path: Path) -> None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ImprovementLedgerError(
            "event_log.jsonl cannot be read; refusing to append ledger revision.",
            details={"path": str(path), "error": str(exc)},
        ) from exc
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ImprovementLedgerError(
                "event_log.jsonl contains invalid JSON; refusing to append ledger revision.",
                details={"path": str(path), "line_number": line_number, "error": str(exc)},
            ) from exc
        if not isinstance(event, dict):
            raise ImprovementLedgerError(
                "event_log.jsonl line must contain an object; refusing to append ledger revision.",
                details={"path": str(path), "line_number": line_number},
            )


def _state_payload(*, workspace: Path, read_result) -> dict[str, Any]:
    current_entries = _computed_current_entries(read_result.current_entries)
    return {
        "ok": not any(item.severity == "error" for item in read_result.diagnostics),
        "workspace": str(workspace),
        "ledger_path": str(improvement_ledger_path(workspace)),
        "current_entries": current_entries,
        "revision_count": len(read_result.valid_revisions),
        "entry_count": len(current_entries),
        "diagnostics": [_diagnostic_to_dict(item) for item in read_result.diagnostics],
    }


def _status_revision(previous: dict[str, Any], *, status: str) -> dict[str, Any]:
    return {
        "schema_version": IMPROVEMENT_LEDGER_SCHEMA,
        "entry_id": previous["entry_id"],
        "revision": int(previous["revision"]) + 1,
        "previous_revision_sha256": revision_sha256(previous),
        "created_at": _utc_now(),
        "status": status,
        "level": previous["level"],
        "target_kind": previous["target_kind"],
        "supersedes_id": supersedes_id(previous),
        "change": deepcopy(previous["change"]),
        "source_evidence": deepcopy(previous["source_evidence"]),
    }


def _current_entry(workspace: Path, entry_id: str) -> dict[str, Any]:
    read_result = _read_ledger(workspace)
    return _current_entry_from_read_result(read_result, entry_id)


def _current_entry_from_read_result(read_result, entry_id: str) -> dict[str, Any]:
    current = read_result.current_entries.get(entry_id)
    if current is None:
        raise ImprovementLedgerError(
            f"Unknown improvement entry: {entry_id}",
            details={"entry_id": entry_id},
        )
    return current


def _evidence_from_feedback_issue(
    *,
    workspace: Path,
    issue_id: str,
) -> tuple[dict[str, Any], ProductDefinitionDecision]:
    issues_path = feedback_state_paths(workspace)["feedback_issues"]
    if not issues_path.exists():
        raise ImprovementLedgerError(
            "feedback_issues.json is not initialized.",
            details={"path": str(issues_path)},
        )
    try:
        payload = json.loads(issues_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ImprovementLedgerError(
            "feedback_issues.json is not valid JSON.",
            details={"path": str(issues_path), "error": str(exc)},
        ) from exc
    _validate_feedback_payload(workspace=workspace, payload=payload)
    issues = payload.get("issues")
    if not isinstance(issues, list):
        raise ImprovementLedgerError(
            "feedback_issues.json issues must be a list.",
            details={"path": str(issues_path)},
        )
    issue = next(
        (item for item in issues if isinstance(item, dict) and item.get("issue_id") == issue_id),
        None,
    )
    if issue is None:
        raise ImprovementLedgerError(
            f"Unknown feedback issue: {issue_id}",
            details={"issue_id": issue_id, "path": str(issues_path)},
        )

    metadata = issue.get("metadata") if isinstance(issue.get("metadata"), dict) else {}
    product_decision = classify_improvement_source(
        source_type="feedback_issue",
        issue_category=str(issue.get("category") or ""),
        finding_type=str(metadata.get("finding_type") or ""),
        source=str(issue.get("source") or ""),
        control_file=str(issue.get("source_artifact") or ""),
    )
    if not product_decision.guidance_eligible:
        raise ImprovementLedgerError(
            "Feedback issue cannot be promoted directly into audience guidance.",
            details=product_decision.to_dict(),
        )

    run_id = _issue_run_id(workspace=workspace, issue_id=issue_id, issue=issue)
    if not run_id:
        raise ImprovementLedgerError(
            "Feedback issue evidence requires a run_id; rebuild or ingest feedback in a runtime workspace.",
            details={"issue_id": issue_id},
        )
    origin = _issue_origin(issue)
    evidence: dict[str, Any] = {
        "source_type": "feedback_issue",
        "summary": str(issue.get("summary") or "Feedback issue.").strip(),
        "run_id": run_id,
        "issue_id": issue_id,
    }
    if origin:
        evidence["origin"] = origin
    return evidence, product_decision


def _validate_feedback_payload(*, workspace: Path, payload: dict[str, Any]) -> None:
    try:
        repo = resolve_repo_workdir(None, workspace=workspace)
        stages = load_stage_specs(repo)
        artifacts = load_artifact_contracts(repo)
    except Exception as exc:
        raise ImprovementLedgerError(
            "Unable to load feedback contract references.",
            details={"error": str(exc)},
        ) from exc
    errors = validate_feedback_issues_payload(payload, stages=stages, artifacts=artifacts)
    if errors:
        raise ImprovementLedgerError(
            "feedback_issues.json failed contract validation.",
            details={"errors": errors},
        )


def _issue_run_id(*, workspace: Path, issue_id: str, issue: dict[str, Any]) -> str | None:
    for value in (
        issue.get("run_id"),
        (issue.get("metadata") or {}).get("run_id") if isinstance(issue.get("metadata"), dict) else None,
    ):
        if isinstance(value, str) and value.strip():
            return value.strip()
    return _issue_run_id_from_events(workspace=workspace, issue_id=issue_id)


def _issue_run_id_from_events(*, workspace: Path, issue_id: str) -> str | None:
    event_dir = runtime_state_paths(workspace)["event_log"].parent
    if not event_dir.exists():
        return None
    event_paths = sorted(event_dir.glob("event_log*.jsonl"))
    for path in event_paths:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
            if (
                event.get("event_type") == "feedback_issue_created"
                and metadata.get("issue_id") == issue_id
                and isinstance(event.get("run_id"), str)
                and event.get("run_id").strip()
            ):
                return event["run_id"].strip()
    return None


def _issue_origin(issue: dict[str, Any]) -> dict[str, str]:
    metadata = issue.get("metadata") if isinstance(issue.get("metadata"), dict) else {}
    origin: dict[str, str] = {}
    for key, value in (
        ("control_file", issue.get("source_artifact")),
        ("source_item_id", metadata.get("source_finding_id") if isinstance(metadata, dict) else None),
        ("finding_type", metadata.get("finding_type") if isinstance(metadata, dict) else None),
        ("blocking_level", metadata.get("blocking_level") if isinstance(metadata, dict) else None),
        ("issue_category", issue.get("category")),
        ("issue_source", issue.get("source")),
    ):
        if isinstance(value, str) and value.strip():
            text = value.strip()
            if key == "control_file":
                text = Path(text).name
            origin[key] = text
    return origin


def _human_feedback_origin(workspace: Path) -> dict[str, str]:
    manifest_path = runtime_state_paths(workspace)["runtime_manifest"]
    if not manifest_path.exists():
        return {}
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    if not isinstance(manifest, dict):
        return {}
    runtime = manifest.get("runtime")
    if isinstance(runtime, str) and runtime.strip():
        return {"origin_runtime": runtime.strip()}
    return {}


def _materialization_diagnostics(current_entries: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    superseded_by = _superseded_by_map(current_entries)
    for entry_id, entry in sorted(current_entries.items()):
        if entry.get("status") != "approved":
            continue
        if superseded_by.get(entry_id):
            diagnostics.append({
                "entry_id": entry_id,
                "materializable": False,
                "non_materializable_reason": "superseded_by_active_guidance",
                "requires_product_definition_review": False,
                "classification": "audience_guidance",
                "action": "skip",
                "reason_code": "superseded_by_active_guidance",
                "message": f"Entry is superseded by {', '.join(superseded_by[entry_id])}.",
                "superseded_by": list(superseded_by[entry_id]),
                "is_superseded": True,
            })
            continue
        decision = classify_ledger_entry_materialization(entry)
        diagnostics.append({
            "entry_id": entry_id,
            "materializable": decision.materializable,
            "non_materializable_reason": None if decision.materializable else decision.reason_code,
            "requires_product_definition_review": decision.requires_product_definition_review,
            "classification": decision.classification,
            "action": decision.action,
            "reason_code": decision.reason_code,
            "message": decision.message,
            "superseded_by": [],
            "is_superseded": False,
        })
    diagnostics.extend(_non_materializable_superseder_warnings(current_entries))
    return diagnostics


def _superseded_by_map(current_entries: dict[str, dict[str, Any]]) -> dict[str, list[str]]:
    superseded: dict[str, list[str]] = {}
    for entry_id, entry in sorted(current_entries.items()):
        if entry.get("status") != "approved":
            continue
        target_id = supersedes_id(entry)
        if target_id is None:
            continue
        superseded.setdefault(target_id, []).append(entry_id)
    return {key: sorted(value) for key, value in superseded.items()}


def _computed_current_entries(current_entries: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    superseded = _superseded_by_map(current_entries)
    computed: dict[str, dict[str, Any]] = {}
    for entry_id, entry in sorted(current_entries.items()):
        item = deepcopy(entry)
        item["supersedes_id"] = supersedes_id(entry)
        item["superseded_by"] = list(superseded.get(entry_id, []))
        item["is_superseded"] = bool(item["superseded_by"])
        decision = classify_ledger_entry_materialization(entry)
        item["materializable"] = (
            entry.get("status") == "approved"
            and decision.materializable
            and not item["is_superseded"]
        )
        item["non_materializable_reason"] = (
            "superseded_by_active_guidance"
            if item["is_superseded"]
            else (None if decision.materializable else decision.reason_code)
        )
        computed[entry_id] = item
    return computed


def _validate_supersedes_target(
    *,
    current_entries: dict[str, dict[str, Any]],
    new_entry_id: str,
    supersedes_id: str,
    category: str,
    scope: str,
) -> list[dict[str, Any]]:
    if supersedes_id == new_entry_id:
        raise ImprovementLedgerError(
            "Improvement entry cannot supersede itself.",
            details={"supersedes_id": supersedes_id, "entry_id": new_entry_id},
        )
    target = current_entries.get(supersedes_id)
    if target is None:
        raise ImprovementLedgerError(
            f"Unknown supersedes target: {supersedes_id}",
            details={"supersedes_id": supersedes_id},
        )
    if target.get("status") != "approved":
        raise ImprovementLedgerError(
            "supersedes target must be currently approved.",
            details={"supersedes_id": supersedes_id, "status": target.get("status")},
        )
    target_decision = classify_ledger_entry_materialization(target)
    if not target_decision.materializable:
        raise ImprovementLedgerError(
            "supersedes target must be materializable audience guidance.",
            details={"supersedes_id": supersedes_id, "reason_code": target_decision.reason_code},
        )

    warnings: list[dict[str, Any]] = []
    target_change = target.get("change") if isinstance(target.get("change"), dict) else {}
    if target_change.get("category") != category or target_change.get("scope") != scope:
        warnings.append({
            "code": "supersedes_category_scope_mismatch",
            "entry_id": supersedes_id,
            "message": (
                f"Supersedes target {supersedes_id} has category/scope "
                f"{target_change.get('category')}/{target_change.get('scope')}."
            ),
        })
    existing_superseders = _superseded_by_map(current_entries).get(supersedes_id, [])
    if existing_superseders:
        warnings.append({
            "code": "supersedes_target_already_superseded",
            "entry_id": supersedes_id,
            "superseded_by": list(existing_superseders),
            "message": (
                f"Supersedes target {supersedes_id} is already superseded by "
                f"{', '.join(existing_superseders)}."
            ),
        })
    return warnings


def _duplicate_guidance_warnings(
    *,
    current_entries: dict[str, dict[str, Any]],
    category: str,
    scope: str,
) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    superseded = _superseded_by_map(current_entries)
    for entry_id, entry in sorted(current_entries.items()):
        if entry.get("status") not in {"proposed", "approved"}:
            continue
        if superseded.get(entry_id):
            continue
        change = entry.get("change") if isinstance(entry.get("change"), dict) else {}
        if (
            entry.get("target_kind") == "audience_guidance"
            and entry.get("level") == 2
            and change.get("category") == category
            and change.get("scope") == scope
        ):
            warnings.append({
                "code": "possible_duplicate_active_guidance",
                "entry_id": entry_id,
                "message": (
                    f"Existing active guidance has the same category/scope. "
                    f"Consider --supersedes {entry_id}."
                ),
            })
    return warnings


def _reexposed_entries_on_revert(
    *,
    current_entries: dict[str, dict[str, Any]],
    superseder_entry_id: str,
    revert_revision: dict[str, Any],
) -> list[dict[str, Any]]:
    previous = current_entries.get(superseder_entry_id)
    if previous is None or previous.get("status") != "approved":
        return []
    target_id = supersedes_id(previous)
    if target_id is None:
        return []
    next_entries = dict(current_entries)
    next_entries[superseder_entry_id] = revert_revision
    target = next_entries.get(target_id)
    if target is None or target.get("status") != "approved":
        return []
    if _superseded_by_map(next_entries).get(target_id):
        return []
    decision = classify_ledger_entry_materialization(target)
    if not decision.materializable:
        return []
    return [{
        "code": "supersession_reexposes_entry",
        "entry_id": target_id,
        "message": f"Reverting {superseder_entry_id} re-exposes {target_id}.",
    }]


def _non_materializable_superseder_warnings(current_entries: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    for entry_id, entry in sorted(current_entries.items()):
        if entry.get("status") != "approved":
            continue
        target_id = supersedes_id(entry)
        if target_id is None:
            continue
        decision = classify_ledger_entry_materialization(entry)
        if decision.materializable:
            continue
        warnings.append({
            "entry_id": entry_id,
            "materializable": False,
            "non_materializable_reason": "non_materializable_superseder_suppresses_target",
            "requires_product_definition_review": decision.requires_product_definition_review,
            "classification": decision.classification,
            "action": "warn",
            "reason_code": "non_materializable_superseder_suppresses_target",
            "message": f"{entry_id} suppresses {target_id} but is not materializable.",
            "supersedes_id": target_id,
            "is_superseded": False,
        })
    return warnings


def _validate_category_scope(*, category: str, scope: str) -> None:
    if category not in AUDIENCE_GUIDANCE_CATEGORIES:
        raise ImprovementLedgerError(
            f"Unknown improvement category: {category}",
            details={"category": category, "known_categories": sorted(AUDIENCE_GUIDANCE_CATEGORIES)},
        )
    if scope not in AUDIENCE_GUIDANCE_SCOPES:
        raise ImprovementLedgerError(
            f"Unknown improvement scope: {scope}",
            details={"scope": scope, "known_scopes": sorted(AUDIENCE_GUIDANCE_SCOPES)},
        )


def _next_entry_id(revisions: list[dict[str, Any]]) -> str:
    max_number = 0
    for revision in revisions:
        entry_id = str(revision.get("entry_id") or "")
        if entry_id.startswith("AG-"):
            try:
                max_number = max(max_number, int(entry_id.removeprefix("AG-")))
            except ValueError:
                continue
    return f"AG-{max_number + 1:04d}"


def _read_ledger(workspace: Path):
    return read_ledger_text(_read_ledger_text(workspace))


def _read_ledger_text(workspace: Path) -> str:
    path = improvement_ledger_path(workspace)
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def _count_by(entries: list[dict[str, Any]], key_or_getter) -> dict[str, int]:
    counts: dict[str, int] = {}
    for entry in entries:
        key = key_or_getter(entry) if callable(key_or_getter) else entry.get(key_or_getter)
        text = str(key or "unknown")
        counts[text] = counts.get(text, 0) + 1
    return counts


def _require_workspace(workspace: str | Path) -> Path:
    ws = Path(workspace).expanduser().resolve()
    if not ws.exists():
        raise ImprovementLedgerError(
            f"Workspace does not exist: {ws}",
            details={"workspace": str(ws)},
        )
    if not (ws / "config.yaml").exists():
        raise ImprovementLedgerError(
            f"Workspace config.yaml not found: {ws / 'config.yaml'}",
            details={"workspace": str(ws)},
        )
    return ws


def _diagnostic_to_dict(diagnostic: LedgerDiagnostic) -> dict[str, Any]:
    return asdict(diagnostic)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
