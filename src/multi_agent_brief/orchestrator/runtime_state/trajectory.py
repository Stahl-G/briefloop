"""Trajectory Regulation decision-narrowing for workflow state.

Deterministically narrows next_allowed_decisions after retry/repair/blocker
budgets are exhausted. Control-state projection only; executes no repair.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from multi_agent_brief.product.trajectory_regulation import project_workspace_trajectory_regulation
from multi_agent_brief.orchestrator.runtime_state.completion_gates import _raise_completion_reasons
from multi_agent_brief.orchestrator.runtime_state.errors import (
    E_ILLEGAL_TRANSITION,
    RuntimeStateError,
)
from multi_agent_brief.orchestrator.runtime_state.workflow import (
    STAGE_READY,
    _allowed_decisions_for_stage,
)


TRAJECTORY_DECISION_NARROWING_STATUS = "decision_narrowed"
TRAJECTORY_NARROWED_DECISIONS = ["request_human_review", "block_run"]


def _trajectory_decision_narrowing(
    *,
    workspace: Path,
    workflow: dict[str, Any],
    event_records: list[dict[str, Any]],
    run_id: str,
    stage_id: str | None = None,
    assume_stage_ready: bool = False,
) -> dict[str, Any] | None:
    current_stage = str(stage_id or workflow.get("current_stage") or "")
    if not current_stage:
        return None
    projection_workflow = workflow
    if stage_id or assume_stage_ready:
        projection_workflow = dict(workflow)
        projection_workflow["current_stage"] = current_stage
        if assume_stage_ready:
            statuses = dict(projection_workflow.get("stage_statuses") or {})
            entry = statuses.get(current_stage) if isinstance(statuses.get(current_stage), dict) else {}
            entry = dict(entry)
            entry["status"] = STAGE_READY
            statuses[current_stage] = entry
            projection_workflow["stage_statuses"] = statuses
    projection = project_workspace_trajectory_regulation(
        workspace,
        workflow_state=projection_workflow,
        event_records=event_records,
        event_log_present=True,
        event_log_corrupt_count=0,
        run_id=run_id,
    )
    if projection.get("status") != "action_required":
        return None
    actions = projection.get("recommended_actions") if isinstance(projection.get("recommended_actions"), list) else []
    relevant_actions: list[dict[str, str]] = []
    reasons: list[str] = []
    for action in actions:
        if not isinstance(action, dict):
            continue
        decision = str(action.get("action") or "")
        stage_id = str(action.get("stage_id") or "")
        if stage_id != current_stage or decision not in TRAJECTORY_NARROWED_DECISIONS:
            continue
        reason = str(action.get("reason") or "trajectory_budget_exceeded")
        relevant_actions.append({
            "action": decision,
            "stage_id": stage_id,
            "reason": reason,
        })
        if reason not in reasons:
            reasons.append(reason)
    if not relevant_actions:
        return None
    return {
        "status": TRAJECTORY_DECISION_NARROWING_STATUS,
        "stage_id": current_stage,
        "reasons": reasons or ["trajectory_budget_exceeded"],
        "allowed_decisions": list(TRAJECTORY_NARROWED_DECISIONS),
        "recommended_actions": relevant_actions,
        "runtime_effect": "decision_narrowing",
        "source": "trajectory_regulation",
    }


def _raise_if_trajectory_narrows_repair_route(
    *,
    workspace: Path,
    workflow: dict[str, Any],
    event_records: list[dict[str, Any]],
    run_id: str,
    route: dict[str, Any],
) -> None:
    repair_owner = str(route.get("repair_owner") or "")
    if not repair_owner:
        return
    narrowing = _trajectory_decision_narrowing(
        workspace=workspace,
        workflow=workflow,
        event_records=event_records,
        run_id=run_id,
        stage_id=repair_owner,
        assume_stage_ready=True,
    )
    if not narrowing:
        return
    raise RuntimeStateError(
        "Repair start is blocked because trajectory regulation narrowed the selected repair route.",
        details={
            "stage_id": repair_owner,
            "decision": "delegate_repair",
            "allowed_decisions": list(TRAJECTORY_NARROWED_DECISIONS),
            "trajectory_regulation": narrowing,
            "selected_route": {
                "repair_owner": route.get("repair_owner"),
                "source": route.get("source") or {},
                "recommended_action": route.get("recommended_action"),
            },
        },
        error_code=E_ILLEGAL_TRANSITION,
    )


def _workflow_with_trajectory_decision_narrowing(
    *,
    workspace: Path,
    workflow: dict[str, Any],
    stages: list[dict[str, Any]],
    event_records: list[dict[str, Any]],
    run_id: str,
) -> dict[str, Any]:
    updated = dict(workflow)
    updated.pop("trajectory_regulation", None)
    narrowing = _trajectory_decision_narrowing(
        workspace=workspace,
        workflow=updated,
        event_records=event_records,
        run_id=run_id,
    )
    if narrowing:
        updated["trajectory_regulation"] = narrowing
        updated["next_allowed_decisions"] = list(TRAJECTORY_NARROWED_DECISIONS)
    else:
        updated["next_allowed_decisions"] = _allowed_decisions_for_stage(
            stages,
            str(updated.get("current_stage") or "") or None,
        )
    return updated


def _trajectory_narrowing_changed(old_workflow: dict[str, Any], workflow: dict[str, Any]) -> bool:
    return old_workflow.get("trajectory_regulation") != workflow.get("trajectory_regulation")


def _raise_if_trajectory_narrows_success_path(
    *,
    workspace: Path,
    workflow: dict[str, Any],
    event_records: list[dict[str, Any]],
    run_id: str,
    stage_id: str,
    decision: str,
) -> None:
    narrowing = _trajectory_decision_narrowing(
        workspace=workspace,
        workflow=workflow,
        event_records=event_records,
        run_id=run_id,
    )
    if not narrowing:
        return
    if str(narrowing.get("stage_id") or "") != stage_id:
        return
    allowed = [str(item) for item in narrowing.get("allowed_decisions") or []]
    if decision in allowed:
        return
    reasons = [
        (
            "Trajectory regulation narrowed current-stage decisions to "
            f"{', '.join(allowed) or ', '.join(TRAJECTORY_NARROWED_DECISIONS)}; "
            f"decision '{decision}' is not allowed."
        )
    ]
    _raise_completion_reasons(
        message=f"Cannot complete stage '{stage_id}' because trajectory regulation narrowed decisions",
        reasons=reasons,
        error_code=E_ILLEGAL_TRANSITION,
        details={
            "stage_id": stage_id,
            "decision": decision,
            "allowed_decisions": allowed or list(TRAJECTORY_NARROWED_DECISIONS),
            "trajectory_regulation": narrowing,
        },
    )
