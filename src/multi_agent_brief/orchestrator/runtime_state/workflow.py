"""Workflow state helpers for Orchestrator runtime state."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from multi_agent_brief.orchestrator.run_integrity import interpret_run_integrity, require_persistable
from multi_agent_brief.orchestrator.runtime_state.contracts_loader import _stage_ids
from multi_agent_brief.orchestrator.runtime_state.errors import E_TRANSACTION_INTEGRITY, RuntimeStateError


WORKFLOW_STATE_SCHEMA = "multi-agent-brief-workflow-state/v1"

STAGE_PENDING = "pending"
STAGE_READY = "ready"
STAGE_COMPLETE = "complete"
STAGE_BLOCKED = "blocked"
STAGE_SKIPPED = "skipped"
PERSISTED_STAGE_STATUSES = {STAGE_PENDING, STAGE_READY, STAGE_COMPLETE, STAGE_BLOCKED, STAGE_SKIPPED}


@dataclass(frozen=True)
class StageCompletionVerdict:
    """Single interpretation of one persisted stage status."""

    kind: str
    value: dict[str, Any]
    reasons: tuple[str, ...] = ()


def _workflow_is_finalized(workflow: dict[str, Any] | None) -> bool:
    if not workflow:
        return False
    finalize = project_stage_completion_for_read(
        interpret_stage_completion(workflow, "finalize")
    )
    return workflow.get("current_stage") is None and finalize.get("complete") is True


def _initial_stage_statuses(stages: list[dict[str, Any]], *, now: str) -> dict[str, dict[str, Any]]:
    statuses: dict[str, dict[str, Any]] = {}
    first = True
    for stage_id in _stage_ids(stages):
        statuses[stage_id] = {
            "status": STAGE_READY if first else STAGE_PENDING,
            "reason": "",
            "updated_at": now,
        }
        first = False
    return statuses


def _initial_workflow_state(
    *,
    run_id: str,
    stages: list[dict[str, Any]],
    created_at: str,
    updated_at: str,
) -> dict[str, Any]:
    stage_statuses = _initial_stage_statuses(stages, now=updated_at)
    current_stage = _stage_ids(stages)[0] if stages else None
    return {
        "schema_version": WORKFLOW_STATE_SCHEMA,
        "run_id": run_id,
        "created_at": created_at,
        "updated_at": updated_at,
        "current_stage": current_stage,
        "blocked": False,
        "blocking_reason": "",
        "stage_statuses": stage_statuses,
        "last_decision": None,
        "next_allowed_decisions": _allowed_decisions_for_stage(stages, current_stage),
        "run_integrity": require_persistable(
            interpret_run_integrity(None, field_present=False),
            path="workflow_state.run_integrity",
        ),
    }


def _allowed_decisions_for_stage(
    stages: list[dict[str, Any]],
    stage_id: str | None,
) -> list[str]:
    if stage_id is None:
        return []
    for stage in stages:
        if stage.get("stage_id") == stage_id:
            decisions = stage.get("allowed_decisions") or []
            return [str(decision) for decision in decisions]
    return []


def _current_stage_index(stages: list[dict[str, Any]], stage_id: str | None) -> int | None:
    ids = _stage_ids(stages)
    if stage_id in ids:
        return ids.index(str(stage_id))
    return None


def _next_stage_id(stages: list[dict[str, Any]], stage_id: str) -> str | None:
    ids = _stage_ids(stages)
    if stage_id not in ids:
        return None
    idx = ids.index(stage_id)
    if idx + 1 >= len(ids):
        return None
    return ids[idx + 1]


def _stage_status(workflow: dict[str, Any], stage_id: str) -> str:
    projection = project_stage_completion_for_read(interpret_stage_completion(workflow, stage_id))
    return str(projection.get("status") or STAGE_PENDING)


def _stage_is_complete_or_skipped(workflow: dict[str, Any], stage_id: str) -> bool:
    projection = project_stage_completion_for_read(interpret_stage_completion(workflow, stage_id))
    return bool(projection.get("complete_or_skipped"))


def interpret_stage_completion(workflow: Any, stage_id: str) -> StageCompletionVerdict:
    """Interpret one stage status once for read and write adapters."""

    stage_label = stage_id or "<missing>"
    if not isinstance(workflow, dict):
        return _degraded_stage_completion(stage_label, "workflow_state.json must be an object.")
    statuses = workflow.get("stage_statuses")
    if not isinstance(statuses, dict):
        return _degraded_stage_completion(stage_label, "workflow_state.stage_statuses must be an object.")
    if stage_id not in statuses:
        return _degraded_stage_completion(stage_label, f"workflow_state.stage_statuses.{stage_label} is missing.")
    entry = statuses.get(stage_id)
    if not isinstance(entry, dict):
        return _degraded_stage_completion(stage_label, f"workflow_state.stage_statuses.{stage_label} must be an object.")
    raw_status = entry.get("status")
    if not isinstance(raw_status, str) or not raw_status.strip():
        return _degraded_stage_completion(stage_label, f"workflow_state.stage_statuses.{stage_label}.status is required.")
    status = raw_status.strip()
    if status not in PERSISTED_STAGE_STATUSES:
        return _degraded_stage_completion(
            stage_label,
            f"workflow_state.stage_statuses.{stage_label}.status is invalid: {status}.",
        )
    return StageCompletionVerdict(
        kind="canonical",
        value=_stage_completion_projection(stage_label, status=status),
    )


def project_stage_completion_for_read(verdict: StageCompletionVerdict) -> dict[str, Any]:
    """Return the read-side projection for a stage-completion verdict."""

    return dict(verdict.value)


def require_stage_completion_persistable(
    verdict: StageCompletionVerdict,
    *,
    path: str | Path,
) -> dict[str, Any]:
    """Return canonical stage-completion projection or fail closed."""

    if verdict.kind != "canonical":
        raise RuntimeStateError(
            "workflow_state.stage_statuses is malformed.",
            details={"path": str(path), "reasons": list(verdict.reasons)},
            error_code=E_TRANSACTION_INTEGRITY,
        )
    return dict(verdict.value)


def workflow_with_persistable_stage_completions(
    workflow: dict[str, Any],
    *,
    stages: list[dict[str, Any]],
    path: str | Path,
) -> dict[str, Any]:
    """Return a shallow workflow copy after validating persisted stage statuses."""

    if not isinstance(workflow.get("stage_statuses"), dict):
        raise RuntimeStateError(
            "workflow_state.stage_statuses is malformed.",
            details={"path": str(path), "reason": "workflow_state.stage_statuses must be an object."},
            error_code=E_TRANSACTION_INTEGRITY,
        )
    stage_ids = _stage_ids(stages)
    current_stage = workflow.get("current_stage")
    if current_stage is not None and str(current_stage) not in stage_ids:
        raise RuntimeStateError(
            "workflow_state.current_stage is invalid.",
            details={"path": str(path), "current_stage": current_stage},
            error_code=E_TRANSACTION_INTEGRITY,
        )
    for stage_id in stage_ids:
        require_stage_completion_persistable(
            interpret_stage_completion(workflow, stage_id),
            path=f"{path}.stage_statuses.{stage_id}",
        )
    if current_stage is None:
        finalize = project_stage_completion_for_read(interpret_stage_completion(workflow, "finalize"))
        if finalize.get("complete") is not True:
            raise RuntimeStateError(
                "workflow_state.current_stage is null but finalize is not complete.",
                details={"path": str(path), "finalize_status": finalize.get("status")},
                error_code=E_TRANSACTION_INTEGRITY,
            )
    return dict(workflow)


def _stage_completion_projection(stage_id: str, *, status: str) -> dict[str, Any]:
    return {
        "stage_id": stage_id,
        "status": status,
        "complete": status == STAGE_COMPLETE,
        "skipped": status == STAGE_SKIPPED,
        "complete_or_skipped": status in {STAGE_COMPLETE, STAGE_SKIPPED},
    }


def _degraded_stage_completion(stage_id: str, reason: str) -> StageCompletionVerdict:
    return StageCompletionVerdict(
        kind="degraded",
        value=_stage_completion_projection(stage_id, status=STAGE_PENDING),
        reasons=(reason,),
    )


def _stage_entry(workflow: dict[str, Any], stage_id: str | None) -> dict[str, Any]:
    if stage_id is None:
        return {}
    return ((workflow.get("stage_statuses") or {}).get(stage_id) or {})


def _changed_workflow_events(
    *,
    old_workflow: dict[str, Any],
    workflow: dict[str, Any],
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    current_stage = workflow.get("current_stage")
    old_current_stage = old_workflow.get("current_stage")
    old_entry = _stage_entry(old_workflow, str(current_stage) if current_stage else None)
    new_entry = _stage_entry(workflow, str(current_stage) if current_stage else None)
    stage_changed = (
        current_stage != old_current_stage
        or old_entry.get("status") != new_entry.get("status")
        or old_entry.get("reason") != new_entry.get("reason")
    )
    if current_stage and stage_changed:
        events.append({
            "event_type": "stage_status_changed",
            "stage_id": str(current_stage),
            "reason": str(workflow.get("blocking_reason") or ""),
            "metadata": {"status": new_entry.get("status")},
        })

    run_block_changed = (
        bool(workflow.get("blocked")) is True
        and (
            bool(old_workflow.get("blocked")) is not True
            or old_workflow.get("blocking_reason") != workflow.get("blocking_reason")
            or old_current_stage != current_stage
        )
    )
    if run_block_changed:
        events.append({
            "event_type": "run_blocked",
            "stage_id": str(current_stage) if current_stage else None,
            "reason": str(workflow.get("blocking_reason") or ""),
            "metadata": {},
        })
    return events


def _required_consumed_artifacts(
    *,
    stage: dict[str, Any],
    artifacts_by_id: dict[str, dict[str, Any]],
) -> list[str]:
    consumed = stage.get("consumes") or []
    required: list[str] = []
    for item in consumed:
        artifact_id = str(item)
        contract = artifacts_by_id.get(artifact_id)
        if contract and bool(contract.get("required", False)):
            required.append(artifact_id)
    return required


def _status_entry(
    status: str,
    reason: str,
    updated_at: str,
    *,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    entry = {
        "status": status,
        "reason": reason,
        "updated_at": updated_at,
    }
    if metadata:
        entry["metadata"] = metadata
    return entry


def _workflow_after_completion(
    *,
    workflow: dict[str, Any],
    stages: list[dict[str, Any]],
    stage_id: str,
    reason: str,
    now: str,
    transaction_id: str,
    finalize: bool,
) -> dict[str, Any]:
    decision = "finalize" if finalize else "continue"
    next_stage = _next_stage_id(stages, stage_id)
    current_stage = None if finalize else next_stage
    statuses = dict(workflow.get("stage_statuses") or {})
    statuses[stage_id] = _status_entry(STAGE_COMPLETE, reason, now)
    if current_stage:
        statuses[current_stage] = _status_entry(STAGE_READY, "", now)
    updated = dict(workflow)
    updated["updated_at"] = now
    updated["current_stage"] = current_stage
    updated["blocked"] = False
    updated["blocking_reason"] = ""
    updated["stage_statuses"] = statuses
    updated["last_decision"] = {
        "stage_id": stage_id,
        "decision": decision,
        "reason": reason,
        "created_at": now,
    }
    updated["last_completion_transaction"] = {
        "transaction_id": transaction_id,
        "stage_id": stage_id,
        "decision": decision,
        "reason": reason,
        "created_at": now,
    }
    updated["next_allowed_decisions"] = _allowed_decisions_for_stage(stages, current_stage)
    return updated
