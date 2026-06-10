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
    validate_next_revision,
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
    return {
        "ok": not diagnostics,
        "workspace": str(ws),
        "ledger_path": str(improvement_ledger_path(ws)),
        "diagnostics": diagnostics,
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
    return {
        "ok": True,
        "workspace": str(ws),
        "ledger_path": str(improvement_ledger_path(ws)),
        "entry_id": entry_id,
        "current": current,
        "revisions": revisions,
        "diagnostics": [_diagnostic_to_dict(item) for item in read_result.diagnostics],
    }


def improvement_stats(*, workspace: str | Path) -> dict[str, Any]:
    ws = _require_workspace(workspace)
    read_result = _read_ledger(ws)
    current_entries = list(read_result.current_entries.values())
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

    approved_count = by_status.get("approved", 0)
    return {
        "ok": True,
        "workspace": str(ws),
        "ledger_path": str(improvement_ledger_path(ws)),
        "entry_count": len(current_entries),
        "revision_count": len(read_result.valid_revisions),
        "approved_count": approved_count,
        "eligible_for_materialization_count": approved_count,
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
) -> dict[str, Any]:
    ws = _require_workspace(workspace)
    _validate_category_scope(category=category, scope=scope)
    if from_issue and source_summary:
        raise ImprovementLedgerError(
            "--from-issue and --source-summary are mutually exclusive.",
            details={"from_issue": from_issue},
        )
    if from_issue:
        source_evidence = [_evidence_from_feedback_issue(workspace=ws, issue_id=from_issue)]
    elif source_summary:
        source_evidence = [{
            "source_type": "human_feedback",
            "summary": source_summary,
            "run_id": None,
            "issue_id": None,
        }]
    else:
        raise ImprovementLedgerError(
            "--source-summary is required unless --from-issue is used.",
            details={"required": "source_summary"},
        )

    read_result = _read_ledger(ws)
    entry_id = _next_entry_id(read_result.valid_revisions)
    revision = {
        "schema_version": IMPROVEMENT_LEDGER_SCHEMA,
        "entry_id": entry_id,
        "revision": 1,
        "previous_revision_sha256": None,
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
    previous = _current_entry(ws, entry_id)
    revision = _status_revision(previous, status="reverted")
    revision["reverted_by"] = reverted_by
    revision["reverted_at"] = _utc_now()
    revision["revert_reason"] = reason
    return _append_transition(
        workspace=ws,
        revision=revision,
        event_type="improvement_reverted",
        event_reason=reason,
    )


def _append_transition(
    *,
    workspace: Path,
    revision: dict[str, Any],
    event_type: str,
    event_reason: str,
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
    return {
        "ok": not any(item.severity == "error" for item in read_result.diagnostics),
        "workspace": str(workspace),
        "ledger_path": str(improvement_ledger_path(workspace)),
        "current_entries": read_result.current_entries,
        "revision_count": len(read_result.valid_revisions),
        "entry_count": len(read_result.current_entries),
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
        "change": deepcopy(previous["change"]),
        "source_evidence": deepcopy(previous["source_evidence"]),
    }


def _current_entry(workspace: Path, entry_id: str) -> dict[str, Any]:
    read_result = _read_ledger(workspace)
    current = read_result.current_entries.get(entry_id)
    if current is None:
        raise ImprovementLedgerError(
            f"Unknown improvement entry: {entry_id}",
            details={"entry_id": entry_id},
        )
    return current


def _evidence_from_feedback_issue(*, workspace: Path, issue_id: str) -> dict[str, Any]:
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
    return evidence


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
    ):
        if isinstance(value, str) and value.strip():
            text = value.strip()
            if key == "control_file":
                text = Path(text).name
            origin[key] = text
    return origin


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
