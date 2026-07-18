"""Decision recording for runtime workflow state."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from multi_agent_brief.orchestrator_contract import DECISION_VOCABULARY, resolve_repo_workdir
from multi_agent_brief.orchestrator.runtime_state._io import _write_json_atomic
from multi_agent_brief.orchestrator.runtime_state._transactions import _load_manifest_and_workflow
from multi_agent_brief.orchestrator.runtime_state.contracts_loader import (
    load_artifact_contracts,
    load_stage_specs,
)
from multi_agent_brief.orchestrator.runtime_state.errors import (
    E_COMPLETION_TRANSACTION_REQUIRED,
    E_ILLEGAL_TRANSITION,
    RuntimeStateError,
)
from multi_agent_brief.orchestrator.runtime_state.event_log import (
    _read_event_log_records,
    append_event,
)
from multi_agent_brief.orchestrator.runtime_state.identity import utc_now
from multi_agent_brief.orchestrator.runtime_state.lifecycle import (
    show_runtime_state,
)
from multi_agent_brief.orchestrator.runtime_state.paths import (
    _require_workspace,
    runtime_state_paths,
)
from multi_agent_brief.orchestrator.runtime_state.repair import _delegate_repair_transaction_required_error
from multi_agent_brief.orchestrator.runtime_state.trajectory import (
    TRAJECTORY_DECISION_NARROWING_STATUS,
    TRAJECTORY_NARROWED_DECISIONS,
    _trajectory_decision_narrowing,
    _trajectory_narrowing_changed,
    _workflow_with_trajectory_decision_narrowing,
)
from multi_agent_brief.orchestrator.runtime_state.workflow import (
    STAGE_BLOCKED,
    STAGE_COMPLETE,
    STAGE_READY,
    _allowed_decisions_for_stage,
    _next_stage_id,
    _status_entry,
)


def record_decision(
    *,
    workspace: str | Path,
    stage_id: str,
    decision: str,
    reason: str,
    repo_workdir: str | Path | None = None,
    actor: str = "orchestrator",
) -> dict[str, Any]:
    ws = _require_workspace(workspace)
    ws, paths, manifest, workflow = _load_manifest_and_workflow(ws)
    workflow_before_decision = dict(workflow)
    repo = resolve_repo_workdir(repo_workdir, workspace=ws)
    stages = load_stage_specs(repo)
    artifacts = load_artifact_contracts(repo)
    stage_by_id = {str(stage.get("stage_id")): stage for stage in stages}
    if stage_id not in stage_by_id:
        raise RuntimeStateError(
            f"Unknown stage: {stage_id}",
            details={"stage_id": stage_id, "known_stages": list(stage_by_id)},
        )
    if decision not in DECISION_VOCABULARY:
        raise RuntimeStateError(
            f"Unknown Orchestrator decision: {decision}",
            details={
                "decision": decision,
                "allowed_decisions": list(DECISION_VOCABULARY),
            },
        )
    stage_allowed = [
        str(item) for item in (stage_by_id[stage_id].get("allowed_decisions") or [])
    ]
    if decision not in stage_allowed:
        raise RuntimeStateError(
            f"Decision '{decision}' is not allowed for stage '{stage_id}'.",
            details={
                "stage_id": stage_id,
                "decision": decision,
                "stage_allowed_decisions": stage_allowed,
            },
        )
    current_stage_before = workflow.get("current_stage")
    if current_stage_before is None:
        raise RuntimeStateError(
            "Cannot record a decision because the workflow has no current stage.",
            details={"stage_id": stage_id, "decision": decision},
        )
    if stage_id != current_stage_before:
        raise RuntimeStateError(
            f"Decision stage '{stage_id}' does not match current stage '{current_stage_before}'.",
            details={
                "stage_id": stage_id,
                "current_stage": current_stage_before,
                "decision": decision,
            },
        )

    run_id = str(manifest["run_id"])
    event_records = _read_event_log_records(paths["event_log"])
    existing_narrowing = _trajectory_decision_narrowing(
        workspace=ws,
        workflow=workflow,
        event_records=event_records,
        run_id=run_id,
    )
    if existing_narrowing and decision not in TRAJECTORY_NARROWED_DECISIONS:
        raise RuntimeStateError(
            f"Decision '{decision}' is blocked because trajectory regulation narrowed current-stage decisions.",
            details={
                "stage_id": stage_id,
                "decision": decision,
                "allowed_decisions": list(TRAJECTORY_NARROWED_DECISIONS),
                "trajectory_regulation": existing_narrowing,
            },
            error_code=E_ILLEGAL_TRANSITION,
        )

    if decision == "delegate_repair":
        raise _delegate_repair_transaction_required_error(
            workspace=ws,
            stage_id=stage_id,
            decision=decision,
            repo_workdir=repo_workdir,
        )

    if decision in {"continue", "finalize"}:
        command = "finalize-complete" if decision == "finalize" else "stage-complete"
        raise RuntimeStateError(
            (
                f"Decision '{decision}' must be recorded with `briefloop state {command}`. "
                "`state decide` is reserved for retry_stage, delegate_repair, request_human_review, and block_run."
            ),
            details={
                "stage_id": stage_id,
                "decision": decision,
                "required_command": command,
            },
            error_code=E_COMPLETION_TRANSACTION_REQUIRED,
        )

    now = utc_now()
    statuses = dict(workflow.get("stage_statuses") or {})
    blocked = False
    blocking_reason = ""
    current_stage: str | None = stage_id

    if decision in {"continue", "finalize"}:
        statuses[stage_id] = _status_entry(STAGE_COMPLETE, reason, now)
        next_stage = _next_stage_id(stages, stage_id)
        if next_stage and decision != "finalize":
            statuses[next_stage] = _status_entry(STAGE_READY, "", now)
            current_stage = next_stage
        else:
            current_stage = None
    elif decision == "retry_stage":
        statuses[stage_id] = _status_entry(STAGE_READY, reason, now)
    elif decision in {"request_human_review", "block_run"}:
        statuses[stage_id] = _status_entry(STAGE_BLOCKED, reason, now)
        blocked = True
        blocking_reason = reason

    workflow["updated_at"] = now
    workflow["current_stage"] = current_stage
    workflow["blocked"] = blocked
    workflow["blocking_reason"] = blocking_reason
    workflow["stage_statuses"] = statuses
    workflow["last_decision"] = {
        "stage_id": stage_id,
        "decision": decision,
        "reason": reason,
        "created_at": now,
    }
    decision_metadata = {"next_stage": current_stage}
    post_decision_events = [
        *event_records,
        {
            "run_id": run_id,
            "event_type": "decision_recorded",
            "stage_id": stage_id,
            "decision": decision,
            "reason": reason,
            "metadata": decision_metadata,
        },
    ]
    workflow = _workflow_with_trajectory_decision_narrowing(
        workspace=ws,
        workflow=workflow,
        stages=stages,
        event_records=post_decision_events,
        run_id=run_id,
    )
    trajectory_narrowing_changed = _trajectory_narrowing_changed(
        workflow_before_decision,
        workflow,
    )

    append_event(
        workspace=ws,
        run_id=run_id,
        event_type="decision_recorded",
        actor=actor,
        stage_id=stage_id,
        decision=decision,
        reason=reason,
        metadata=decision_metadata,
    )
    narrowing = workflow.get("trajectory_regulation")
    if (
        trajectory_narrowing_changed
        and isinstance(narrowing, dict)
        and narrowing.get("status") == TRAJECTORY_DECISION_NARROWING_STATUS
    ):
        append_event(
            workspace=ws,
            run_id=run_id,
            event_type="trajectory_decision_narrowed",
            actor=actor,
            stage_id=str(narrowing.get("stage_id") or stage_id),
            reason=", ".join(str(item) for item in narrowing.get("reasons") or []),
            metadata={
                "allowed_decisions": list(narrowing.get("allowed_decisions") or []),
                "recommended_actions": list(narrowing.get("recommended_actions") or []),
            },
        )
    _write_json_atomic(paths["workflow_state"], workflow)
    return show_runtime_state(workspace=ws)
