"""Read-only trajectory regulation projection from workflow state and events."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping


TRAJECTORY_REGULATION_SCHEMA_VERSION = "briefloop.trajectory_regulation.v1"
TRAJECTORY_REGULATION_BOUNDARY = (
    "trajectory_regulation_projection_only_not_state_transition_or_repair_execution"
)
TRAJECTORY_REGULATION_RUNTIME_EFFECT = "none"

DEFAULT_TRAJECTORY_LIMITS = {
    "max_retry_stage_events_per_stage": 3,
    "max_repair_cycles_per_stage": 3,
    "max_repeated_blockers_per_stage": 2,
    "hard_block_attempts_per_stage": 5,
}

_INTERMEDIATE = Path("output/intermediate")


def project_workspace_trajectory_regulation(
    workspace: str | Path,
    *,
    workflow_state: Mapping[str, Any] | None = None,
    event_records: list[dict[str, Any]] | None = None,
    event_log_present: bool | None = None,
    event_log_corrupt_count: int = 0,
    run_id: str | None = None,
    limits: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Project retry/repair-loop risk without mutating workspace state."""

    ws = Path(workspace).expanduser().resolve()
    workflow = dict(workflow_state) if isinstance(workflow_state, Mapping) else _read_json(ws / _INTERMEDIATE / "workflow_state.json")
    if not isinstance(workflow, dict):
        return _not_available("workflow_state_missing")

    resolved_run_id = _text(run_id) or _text(workflow.get("run_id"))
    if event_records is None:
        event_path = ws / _INTERMEDIATE / "event_log.jsonl"
        event_log_present = event_path.exists()
        event_records, read_corrupt_count = _read_jsonl_best_effort(event_path)
        event_log_corrupt_count = read_corrupt_count
    elif event_log_present is None:
        event_log_present = True

    safe_limits = _normalize_limits(limits)
    corrupt_count = _non_negative_int(event_log_corrupt_count)
    if corrupt_count > 0:
        return _event_log_invalid(
            workflow=workflow,
            run_id=resolved_run_id,
            event_log_present=bool(event_log_present),
            event_log_corrupt_count=corrupt_count,
            limits=safe_limits,
        )
    filtered_events = [
        event
        for event in (event_records or [])
        if isinstance(event, dict) and (not resolved_run_id or _text(event.get("run_id")) == resolved_run_id)
    ]
    stages = _stage_summaries(
        workflow=workflow,
        events=filtered_events,
        limits=safe_limits,
    )
    recommended_actions = _recommended_actions(stages)
    status = _projection_status(
        event_log_present=bool(event_log_present),
        stages=stages,
        recommended_actions=recommended_actions,
    )
    return {
        "schema_version": TRAJECTORY_REGULATION_SCHEMA_VERSION,
        "status": status,
        "read_only": True,
        "runtime_effect": TRAJECTORY_REGULATION_RUNTIME_EFFECT,
        "boundary": TRAJECTORY_REGULATION_BOUNDARY,
        "run_id": resolved_run_id or "unknown",
        "current_stage": _text(workflow.get("current_stage")) or None,
        "event_log_present": bool(event_log_present),
        "event_log_corrupt_count": corrupt_count,
        "limits": safe_limits,
        "summary_counts": _summary_counts(stages, recommended_actions),
        "stages": stages,
        "recommended_actions": recommended_actions,
        "non_goals": [
            "state_transition",
            "repair_execution",
            "gate_decision",
            "release_authority",
            "quality_score",
        ],
    }


def validate_trajectory_regulation_payload(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return "trajectory_regulation_schema_error:not_object"
    if payload.get("schema_version") != TRAJECTORY_REGULATION_SCHEMA_VERSION:
        return "trajectory_regulation_schema_error:schema_version"
    if payload.get("runtime_effect") != TRAJECTORY_REGULATION_RUNTIME_EFFECT:
        return "trajectory_regulation_schema_error:runtime_effect"
    if payload.get("boundary") != TRAJECTORY_REGULATION_BOUNDARY:
        return "trajectory_regulation_schema_error:boundary"
    if payload.get("status") not in {
        "not_available",
        "ok",
        "warning",
        "action_required",
        "missing_event_log",
        "event_log_invalid",
    }:
        return "trajectory_regulation_schema_error:status"
    corrupt_count = payload.get("event_log_corrupt_count", 0)
    if isinstance(corrupt_count, bool) or not isinstance(corrupt_count, int) or corrupt_count < 0:
        return "trajectory_regulation_schema_error:event_log_corrupt_count"
    for field in ("limits", "summary_counts"):
        if not isinstance(payload.get(field), dict):
            return f"trajectory_regulation_schema_error:{field}"
    for field in ("stages", "recommended_actions", "non_goals"):
        if not isinstance(payload.get(field), list):
            return f"trajectory_regulation_schema_error:{field}"
    for item in payload.get("recommended_actions", []):
        if not isinstance(item, dict):
            return "trajectory_regulation_schema_error:recommended_actions"
        if _text(item.get("action")) not in {"request_human_review", "block_run"}:
            return "trajectory_regulation_schema_error:recommended_actions.action"
    forbidden = {"state_transition", "repair_execution", "release_authority"}
    if not forbidden.issubset({str(item) for item in payload.get("non_goals", [])}):
        return "trajectory_regulation_schema_error:non_goals"
    return None


def _not_available(reason: str) -> dict[str, Any]:
    return {
        "schema_version": TRAJECTORY_REGULATION_SCHEMA_VERSION,
        "status": "not_available",
        "read_only": True,
        "runtime_effect": TRAJECTORY_REGULATION_RUNTIME_EFFECT,
        "boundary": TRAJECTORY_REGULATION_BOUNDARY,
        "run_id": "unknown",
        "current_stage": None,
        "event_log_present": False,
        "event_log_corrupt_count": 0,
        "limits": dict(DEFAULT_TRAJECTORY_LIMITS),
        "summary_counts": {},
        "stages": [],
        "recommended_actions": [],
        "reason": reason,
        "non_goals": [
            "state_transition",
            "repair_execution",
            "gate_decision",
            "release_authority",
            "quality_score",
        ],
    }


def _event_log_invalid(
    *,
    workflow: Mapping[str, Any],
    run_id: str,
    event_log_present: bool,
    event_log_corrupt_count: int,
    limits: dict[str, int],
) -> dict[str, Any]:
    return {
        "schema_version": TRAJECTORY_REGULATION_SCHEMA_VERSION,
        "status": "event_log_invalid",
        "read_only": True,
        "runtime_effect": TRAJECTORY_REGULATION_RUNTIME_EFFECT,
        "boundary": TRAJECTORY_REGULATION_BOUNDARY,
        "run_id": run_id or _text(workflow.get("run_id")) or "unknown",
        "current_stage": _text(workflow.get("current_stage")) or None,
        "event_log_present": event_log_present,
        "event_log_corrupt_count": event_log_corrupt_count,
        "limits": limits,
        "summary_counts": {
            "stage_count": 0,
            "action_required_stage_count": 0,
            "retry_stage_count": 0,
            "repair_started_count": 0,
            "repair_completed_count": 0,
            "repeated_blocker_stage_count": 0,
        },
        "stages": [],
        "recommended_actions": [],
        "reason": "event_log_invalid",
        "non_goals": [
            "state_transition",
            "repair_execution",
            "gate_decision",
            "release_authority",
            "quality_score",
        ],
    }


def _stage_summaries(
    *,
    workflow: Mapping[str, Any],
    events: list[dict[str, Any]],
    limits: dict[str, int],
) -> list[dict[str, Any]]:
    stage_ids = _stage_ids(workflow, events)
    current_stage = _text(workflow.get("current_stage"))
    retry_counts: Counter[str] = Counter()
    repair_started_counts: Counter[str] = Counter()
    repair_completed_counts: Counter[str] = Counter()
    blocker_counts: Counter[str] = Counter()
    blocker_reason_counts: dict[str, Counter[str]] = defaultdict(Counter)

    for event in events:
        metadata = _metadata(event)
        stage_id = _text(event.get("stage_id")) or _text(metadata.get("repair_owner"))
        if not stage_id:
            continue
        event_type = _text(event.get("event_type"))
        decision = _text(event.get("decision"))
        if event_type == "decision_recorded" and decision == "retry_stage":
            retry_counts[stage_id] += 1
        if event_type == "repair_started":
            repair_started_counts[stage_id] += 1
        if event_type == "repair_completed":
            repair_completed_counts[stage_id] += 1
        if event_type in {"run_blocked"} or (
            event_type == "decision_recorded" and decision in {"block_run", "request_human_review"}
        ):
            blocker_counts[stage_id] += 1
            reason_key = _reason_key(event)
            if reason_key:
                blocker_reason_counts[stage_id][reason_key] += 1

    stages: list[dict[str, Any]] = []
    for stage_id in stage_ids:
        stage_status = _workflow_stage_status(workflow, stage_id)
        recommendation_eligible = (
            stage_id == current_stage
            and stage_status not in {"complete", "skipped"}
        )
        retry_count = retry_counts[stage_id]
        repair_started = repair_started_counts[stage_id]
        repair_completed = repair_completed_counts[stage_id]
        blocker_count = blocker_counts[stage_id]
        repeated_blocker_count = max(blocker_reason_counts[stage_id].values() or [0])
        attempt_count = retry_count + repair_started
        reasons: list[str] = []
        raw_recommended_decision = "none"
        if attempt_count >= limits["hard_block_attempts_per_stage"]:
            raw_recommended_decision = "block_run"
            reasons.append("hard_attempt_budget_exceeded")
        elif repeated_blocker_count >= limits["max_repeated_blockers_per_stage"]:
            raw_recommended_decision = "request_human_review"
            reasons.append("repeated_blocker")
        elif retry_count >= limits["max_retry_stage_events_per_stage"]:
            raw_recommended_decision = "request_human_review"
            reasons.append("retry_budget_exhausted")
        elif repair_started >= limits["max_repair_cycles_per_stage"]:
            raw_recommended_decision = "request_human_review"
            reasons.append("repair_cycle_budget_exhausted")
        recommended_decision = raw_recommended_decision if recommendation_eligible else "none"
        warning = (
            recommendation_eligible
            and raw_recommended_decision == "none"
            and recommended_decision == "none"
            and attempt_count > 0
            and attempt_count >= max(1, limits["max_retry_stage_events_per_stage"] - 1)
        )
        history_only = (
            not recommendation_eligible
            and raw_recommended_decision != "none"
        )
        stages.append({
            "stage_id": stage_id,
            "stage_status": stage_status or "unknown",
            "recommendation_eligible": recommendation_eligible,
            "history_only": history_only,
            "historical_recommended_decision": raw_recommended_decision if history_only else "none",
            "retry_stage_count": retry_count,
            "repair_started_count": repair_started,
            "repair_completed_count": repair_completed,
            "repair_cycle_count": min(repair_started, repair_completed),
            "blocker_count": blocker_count,
            "repeated_blocker_count": repeated_blocker_count,
            "attempt_count": attempt_count,
            "warning": warning,
            "exhausted_attempt_budget": recommended_decision != "none",
            "recommended_decision": recommended_decision,
            "reasons": reasons if recommended_decision != "none" else [],
            "history_only_reasons": reasons if history_only else [],
        })
    return stages


def _workflow_stage_status(workflow: Mapping[str, Any], stage_id: str) -> str:
    statuses = workflow.get("stage_statuses")
    if not isinstance(statuses, Mapping):
        return ""
    entry = statuses.get(stage_id)
    if not isinstance(entry, Mapping):
        return ""
    return _text(entry.get("status"))


def _stage_ids(workflow: Mapping[str, Any], events: list[dict[str, Any]]) -> list[str]:
    seen: list[str] = []
    statuses = workflow.get("stage_statuses")
    if isinstance(statuses, Mapping):
        for stage_id in statuses:
            text = _text(stage_id)
            if text and text not in seen:
                seen.append(text)
    current_stage = _text(workflow.get("current_stage"))
    if current_stage and current_stage not in seen:
        seen.append(current_stage)
    for event in events:
        metadata = _metadata(event)
        stage_id = _text(event.get("stage_id")) or _text(metadata.get("repair_owner"))
        if stage_id and stage_id not in seen:
            seen.append(stage_id)
    return seen


def _recommended_actions(stages: list[dict[str, Any]]) -> list[dict[str, str]]:
    actions: list[dict[str, str]] = []
    for stage in stages:
        decision = _text(stage.get("recommended_decision"))
        if decision == "none":
            continue
        reasons = stage.get("reasons") if isinstance(stage.get("reasons"), list) else []
        actions.append({
            "action": decision,
            "stage_id": _text(stage.get("stage_id")),
            "reason": _text(reasons[0]) if reasons else "trajectory_budget_exceeded",
        })
    return actions


def _projection_status(
    *,
    event_log_present: bool,
    stages: list[dict[str, Any]],
    recommended_actions: list[dict[str, str]],
) -> str:
    if not event_log_present:
        return "missing_event_log"
    if recommended_actions:
        return "action_required"
    if any(stage.get("warning") for stage in stages):
        return "warning"
    return "ok"


def _summary_counts(stages: list[dict[str, Any]], recommended_actions: list[dict[str, str]]) -> dict[str, int]:
    return {
        "stage_count": len(stages),
        "action_required_stage_count": len(recommended_actions),
        "retry_stage_count": sum(int(stage.get("retry_stage_count") or 0) for stage in stages),
        "repair_started_count": sum(int(stage.get("repair_started_count") or 0) for stage in stages),
        "repair_completed_count": sum(int(stage.get("repair_completed_count") or 0) for stage in stages),
        "repeated_blocker_stage_count": sum(
            1 for stage in stages if int(stage.get("repeated_blocker_count") or 0) > 1
        ),
    }


def _normalize_limits(limits: Mapping[str, Any] | None) -> dict[str, int]:
    safe = dict(DEFAULT_TRAJECTORY_LIMITS)
    if not isinstance(limits, Mapping):
        return safe
    for key in safe:
        value = limits.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            continue
        safe[key] = value
    return safe


def _reason_key(event: Mapping[str, Any]) -> str:
    reason = _text(event.get("reason"))
    if reason:
        return reason.lower()
    metadata = _metadata(event)
    for key in ("reason_code", "finding_type", "error_code"):
        value = _text(metadata.get(key))
        if value:
            return value.lower()
    return ""


def _metadata(event: Mapping[str, Any]) -> Mapping[str, Any]:
    metadata = event.get("metadata")
    return metadata if isinstance(metadata, Mapping) else {}


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _read_jsonl_best_effort(path: Path) -> tuple[list[dict[str, Any]], int]:
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return [], 0
    records: list[dict[str, Any]] = []
    corrupt_count = 0
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            corrupt_count += 1
            continue
        if isinstance(payload, dict):
            records.append(payload)
        else:
            corrupt_count += 1
    return records, corrupt_count


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _non_negative_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value
