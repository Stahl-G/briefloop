from __future__ import annotations

from multi_agent_brief.product.trajectory_regulation import (
    project_workspace_trajectory_regulation,
    validate_trajectory_regulation_payload,
)
from tests.helpers import initialized_workspace_writer


_workspace = initialized_workspace_writer(
    project_name="Trajectory Test",
    user_text="# Trajectory test\n",
)

_RUN_ID = "mabw-20260701T000000Z-trajectory"


def _workflow_state(
    *,
    current_stage: str = "source-discovery",
    stage_statuses: dict[str, str] | None = None,
) -> dict:
    statuses = stage_statuses or {"doctor": "complete", "source-discovery": "ready"}
    return {
        "schema_version": "multi-agent-brief-workflow-state/v1",
        "run_id": _RUN_ID,
        "current_stage": current_stage,
        "stage_statuses": {
            stage_id: {
                "status": status,
                "reason": "",
                "updated_at": "2026-07-01T00:00:00+00:00",
            }
            for stage_id, status in statuses.items()
        },
    }


def _retry_events(stage_id: str, count: int) -> list[dict]:
    return [
        {
            "schema_version": "multi-agent-brief-event-log/v1",
            "event_id": f"evt-retry-{idx + 1}",
            "run_id": _RUN_ID,
            "created_at": "2026-07-01T00:00:00+00:00",
            "event_type": "decision_recorded",
            "actor": "orchestrator",
            "stage_id": stage_id,
            "artifact_id": None,
            "decision": "retry_stage",
            "reason": f"Synthetic source discovery retry {idx + 1}.",
            "metadata": {},
        }
        for idx in range(count)
    ]


def test_trajectory_regulation_suppresses_actions_for_completed_prior_stage(tmp_path) -> None:
    ws = _workspace(tmp_path)
    workflow = _workflow_state(
        current_stage="input-governance",
        stage_statuses={
            "doctor": "complete",
            "source-discovery": "complete",
            "input-governance": "ready",
        },
    )
    events = _retry_events("source-discovery", 3)

    projection = project_workspace_trajectory_regulation(
        ws,
        workflow_state=workflow,
        event_records=events,
        run_id=workflow["run_id"],
    )
    source_stage = next(
        stage for stage in projection["stages"] if stage["stage_id"] == "source-discovery"
    )

    assert validate_trajectory_regulation_payload(projection) is None
    assert projection["status"] == "ok"
    assert projection["summary_counts"]["retry_stage_count"] == 3
    assert projection["recommended_actions"] == []
    assert source_stage["stage_status"] == "complete"
    assert source_stage["recommendation_eligible"] is False
    assert source_stage["history_only"] is True
    assert source_stage["historical_recommended_decision"] == "request_human_review"
    assert source_stage["recommended_decision"] == "none"


def test_quality_panel_surfaces_trajectory_action_without_state_authority(tmp_path) -> None:
    ws = _workspace(tmp_path)
    workflow = _workflow_state()
    events = _retry_events("source-discovery", 3)

    projection = project_workspace_trajectory_regulation(
        ws,
        workflow_state=workflow,
        event_records=events,
        run_id=workflow["run_id"],
    )

    assert validate_trajectory_regulation_payload(projection) is None
    assert projection["status"] == "action_required"
    assert {
        "action": "request_human_review",
        "stage_id": "source-discovery",
        "reason": "retry_budget_exhausted",
    } in projection["recommended_actions"]
    # the retired legacy quality-panel fold-in is removed; the
    # projection itself carries the no-state-authority boundary.
    assert projection["read_only"] is True
    assert projection["runtime_effect"] == "none"
    assert "state_transition" in projection["non_goals"]
    assert "repair_execution" in projection["non_goals"]
