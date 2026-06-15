"""Control-trace timing projection from runtime event logs.

This module is intentionally read-only. It derives coarse timing buckets from
runtime control events; it does not infer exact LLM or subagent wall-clock time.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from multi_agent_brief.orchestrator.run_integrity import interpret_run_integrity, project_for_read


CONTROL_TIMING_SCHEMA = "mabw.control_timing.v1"
CONTROL_TIMING_KIND = "control_trace_timing_buckets"
CONTROL_TIMING_SOURCE = "event_log"
CONTROL_TIMING_PRECISION = "control_trace_bucket"

_COMPLETION_DECISIONS = {"continue", "finalize"}


def derive_control_timing(
    *,
    event_records: list[dict[str, Any]],
    workflow_state: dict[str, Any] | None = None,
    run_integrity: dict[str, Any] | None = None,
    stage_order: list[str] | None = None,
    expected_run_id: str | None = None,
) -> dict[str, Any]:
    """Return a deterministic timing projection from control events."""

    workflow_supplied = isinstance(workflow_state, dict)
    workflow = workflow_state if workflow_supplied else {}
    integrity = _run_integrity_projection(
        run_integrity=run_integrity,
        workflow_state=workflow if workflow_supplied else None,
    )
    expected = expected_run_id if isinstance(expected_run_id, str) and expected_run_id.strip() else workflow.get("run_id")
    warnings: list[str] = []
    if expected:
        mismatch = _event_run_id_mismatch(event_records, str(expected))
        if mismatch:
            warnings.append(mismatch)
            return _timing_payload(
                status="invalid_event_log",
                stages=[],
                finalize=None,
                warnings=warnings,
                run_integrity=integrity,
                total_elapsed_seconds=None,
            )
    stages = [
        stage_id
        for stage_id in (stage_order or _stage_order_from_workflow(workflow) or _stage_order_from_events(event_records))
        if stage_id != "finalize"
    ]
    if not event_records:
        warnings.append("event_log_missing_or_empty")
        return _timing_payload(
            status="unknown",
            stages=[],
            finalize=None,
            warnings=warnings,
            run_integrity=integrity,
            total_elapsed_seconds=None,
        )

    run_started = _first_event_time(event_records, "run_initialized")
    completions = _completion_events_by_stage(event_records)
    if not completions:
        warnings.append("completion_events_missing")
    stage_entries: list[dict[str, Any]] = []
    boundary_time = run_started
    boundary_event_id = _first_event_id(event_records, "run_initialized")
    any_unknown = False
    any_incomplete = False
    finalize_entry: dict[str, Any] | None = None

    for stage_id in stages:
        completion = completions.get(stage_id)
        stage_status = _workflow_stage_status(workflow, stage_id)
        if not completion:
            if stage_status == "complete":
                any_incomplete = True
                stage_entries.append(_incomplete_stage(stage_id, "completion_event_missing"))
                warnings.append(f"{stage_id}: completion event missing")
                boundary_time = None
                boundary_event_id = None
            continue
        completed_at = _event_time(completion)
        if completed_at is None:
            any_unknown = True
            entry = _unknown_stage(stage_id, completion, "completion_time_missing_or_invalid")
            stage_entries.append(entry)
            warnings.append(f"{stage_id}: completion timestamp missing or invalid")
        elif boundary_time is None:
            any_unknown = True
            entry = _unknown_stage(stage_id, completion, "start_boundary_missing_or_invalid")
            entry["completed_at"] = _event_timestamp(completion)
            stage_entries.append(entry)
            warnings.append(f"{stage_id}: start boundary missing or invalid")
        else:
            elapsed = max(0.0, (completed_at - boundary_time).total_seconds())
            entry = {
                "stage_id": stage_id,
                "status": _completion_status(completion),
                "started_at": _format_dt(boundary_time),
                "completed_at": _event_timestamp(completion),
                "elapsed_seconds": elapsed,
                "confidence": CONTROL_TIMING_PRECISION,
                "start_event_id": boundary_event_id,
                "completion_event_id": completion.get("event_id"),
            }
            entry.update(_completion_metadata(completion))
            if completion.get("decision") == "finalize":
                finalize_entry = entry
            else:
                stage_entries.append(entry)
        boundary_time = completed_at
        boundary_event_id = completion.get("event_id")

    if "finalize" in completions:
        completion = completions["finalize"]
        completed_at = _event_time(completion)
        if completed_at is not None and boundary_time is not None:
            finalize_entry = {
                "stage_id": "finalize",
                "status": "complete",
                "started_at": _format_dt(boundary_time),
                "completed_at": _event_timestamp(completion),
                "elapsed_seconds": max(0.0, (completed_at - boundary_time).total_seconds()),
                "confidence": CONTROL_TIMING_PRECISION,
                "start_event_id": boundary_event_id,
                "completion_event_id": completion.get("event_id"),
            }
        else:
            finalize_entry = _unknown_stage("finalize", completion, "start_or_completion_boundary_missing")
            any_unknown = True

    if _workflow_finalized(workflow) and "finalize" not in completions:
        any_incomplete = True
        finalize_entry = _incomplete_stage("finalize", "completion_event_missing")
        warnings.append("finalize: completion event missing")

    first_time = run_started
    last_time = _latest_completion_time(completions)
    total_elapsed = None
    if first_time is not None and last_time is not None:
        total_elapsed = max(0.0, (last_time - first_time).total_seconds())

    status = "available"
    if integrity.get("status") == "contaminated":
        status = "contaminated"
        warnings.append("run_integrity_contaminated")
    elif integrity.get("status") == "unknown":
        status = "unknown"
        warnings.append("run_integrity_unknown")
    elif any_incomplete:
        status = "incomplete"
    elif any_unknown:
        status = "partial"
    elif not completions:
        status = "unknown"
    return _timing_payload(
        status=status,
        stages=stage_entries,
        finalize=finalize_entry,
        warnings=warnings,
        run_integrity=integrity,
        total_elapsed_seconds=total_elapsed,
    )


def derive_control_timing_from_path(
    event_log_path: str | Path,
    *,
    workflow_state: dict[str, Any] | None = None,
    run_integrity: dict[str, Any] | None = None,
    stage_order: list[str] | None = None,
    expected_run_id: str | None = None,
) -> dict[str, Any]:
    """Read an event log path and return a timing projection without writing."""

    path = Path(event_log_path)
    try:
        records = _read_event_records(path)
    except Exception as exc:
        integrity = _run_integrity_projection(
            run_integrity=run_integrity,
            workflow_state=workflow_state if isinstance(workflow_state, dict) else None,
        )
        return _timing_payload(
            status="invalid_event_log",
            stages=[],
            finalize=None,
            warnings=[f"event_log_unreadable: {exc}"],
            run_integrity=integrity,
            total_elapsed_seconds=None,
        )
    return derive_control_timing(
        event_records=records,
        workflow_state=workflow_state,
        run_integrity=run_integrity,
        stage_order=stage_order,
        expected_run_id=expected_run_id,
    )


def _run_integrity_projection(
    *,
    run_integrity: dict[str, Any] | None = None,
    workflow_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if isinstance(run_integrity, dict):
        return project_for_read(interpret_run_integrity(run_integrity, field_present=True))
    if isinstance(workflow_state, dict):
        return project_for_read(
            interpret_run_integrity(
                workflow_state.get("run_integrity"),
                field_present="run_integrity" in workflow_state,
            )
        )
    return project_for_read(interpret_run_integrity(None, field_present=True))


def _timing_payload(
    *,
    status: str,
    stages: list[dict[str, Any]],
    finalize: dict[str, Any] | None,
    warnings: list[str],
    run_integrity: dict[str, Any],
    total_elapsed_seconds: float | None,
) -> dict[str, Any]:
    return {
        "schema_version": CONTROL_TIMING_SCHEMA,
        "kind": CONTROL_TIMING_KIND,
        "source": CONTROL_TIMING_SOURCE,
        "precision": CONTROL_TIMING_PRECISION,
        "status": status,
        "total_elapsed_seconds": total_elapsed_seconds,
        "run_integrity": {
            "status": run_integrity.get("status"),
            "reference_eligible": run_integrity.get("reference_eligible"),
        },
        "stages": stages,
        "finalize": finalize,
        "warnings": sorted(set(warnings)),
    }


def _read_event_records(path: Path) -> list[dict[str, Any]]:
    from multi_agent_brief.orchestrator.runtime_state import read_event_log_records_strict

    return read_event_log_records_strict(path)


def _event_run_id_mismatch(event_records: list[dict[str, Any]], expected_run_id: str) -> str:
    for idx, event in enumerate(event_records, start=1):
        run_id = event.get("run_id")
        if run_id != expected_run_id:
            return f"event_log line {idx} run_id mismatch: expected {expected_run_id}, got {run_id}"
    return ""


def _stage_order_from_workflow(workflow: dict[str, Any]) -> list[str]:
    statuses = workflow.get("stage_statuses") if isinstance(workflow.get("stage_statuses"), dict) else {}
    return [str(stage_id) for stage_id in statuses]


def _stage_order_from_events(event_records: list[dict[str, Any]]) -> list[str]:
    stage_ids: list[str] = []
    for event in event_records:
        if not _is_completion_event(event):
            continue
        stage_id = event.get("stage_id")
        if isinstance(stage_id, str) and stage_id and stage_id not in stage_ids:
            stage_ids.append(stage_id)
    return stage_ids


def _completion_events_by_stage(event_records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    completions: dict[str, dict[str, Any]] = {}
    for event in event_records:
        if not _is_completion_event(event):
            continue
        stage_id = event.get("stage_id")
        if not isinstance(stage_id, str) or not stage_id:
            continue
        completions[stage_id] = event
    return completions


def _is_completion_event(event: dict[str, Any]) -> bool:
    return _is_completion_transaction_event(event) or _is_topology_satisfaction_event(event)


def _is_completion_transaction_event(event: dict[str, Any]) -> bool:
    if event.get("event_type") != "decision_recorded" or event.get("decision") not in _COMPLETION_DECISIONS:
        return False
    metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
    transaction_id = metadata.get("transaction_id")
    return isinstance(transaction_id, str) and bool(transaction_id.strip())


def _is_topology_satisfaction_event(event: dict[str, Any]) -> bool:
    if event.get("event_type") != "stage_satisfied_by_topology":
        return False
    metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
    transaction_id = metadata.get("transaction_id")
    return isinstance(transaction_id, str) and bool(transaction_id.strip())


def _completion_status(event: dict[str, Any]) -> str:
    if _is_topology_satisfaction_event(event):
        return "satisfied_by_topology"
    return "complete"


def _completion_metadata(event: dict[str, Any]) -> dict[str, Any]:
    if not _is_topology_satisfaction_event(event):
        return {}
    metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
    return {
        "completion_event_type": "stage_satisfied_by_topology",
        "topology": metadata.get("topology"),
        "satisfied_by": metadata.get("satisfied_by"),
        "satisfied_by_stage": metadata.get("satisfied_by_stage"),
        "required_artifacts": metadata.get("required_artifacts") if isinstance(metadata.get("required_artifacts"), list) else [],
    }


def _workflow_stage_status(workflow: dict[str, Any], stage_id: str) -> str:
    statuses = workflow.get("stage_statuses") if isinstance(workflow.get("stage_statuses"), dict) else {}
    stage = statuses.get(stage_id) if isinstance(statuses.get(stage_id), dict) else {}
    return str(stage.get("status") or "")


def _workflow_finalized(workflow: dict[str, Any]) -> bool:
    return workflow.get("current_stage") is None and _workflow_stage_status(workflow, "finalize") == "complete"


def _first_event_time(event_records: list[dict[str, Any]], event_type: str) -> datetime | None:
    for event in event_records:
        if event.get("event_type") == event_type:
            return _event_time(event)
    return None


def _first_event_id(event_records: list[dict[str, Any]], event_type: str) -> str | None:
    for event in event_records:
        if event.get("event_type") == event_type:
            event_id = event.get("event_id")
            return str(event_id) if event_id is not None else None
    return None


def _latest_completion_time(completions: dict[str, dict[str, Any]]) -> datetime | None:
    times = [_event_time(event) for event in completions.values()]
    valid = [time for time in times if time is not None]
    if not valid:
        return None
    return max(valid)


def _event_time(event: dict[str, Any]) -> datetime | None:
    value = event.get("created_at") or event.get("timestamp")
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _event_timestamp(event: dict[str, Any]) -> str | None:
    value = event.get("created_at") or event.get("timestamp")
    return str(value) if value is not None else None


def _format_dt(value: datetime) -> str:
    text = value.isoformat()
    return text.replace("+00:00", "Z")


def _unknown_stage(stage_id: str, completion: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "stage_id": stage_id,
        "status": "unknown",
        "started_at": None,
        "completed_at": _event_timestamp(completion),
        "elapsed_seconds": None,
        "confidence": "unknown",
        "reason": reason,
        "completion_event_id": completion.get("event_id"),
    }


def _incomplete_stage(stage_id: str, reason: str) -> dict[str, Any]:
    return {
        "stage_id": stage_id,
        "status": "incomplete",
        "started_at": None,
        "completed_at": None,
        "elapsed_seconds": None,
        "confidence": "incomplete",
        "reason": reason,
    }
