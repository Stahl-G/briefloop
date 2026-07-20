from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from multi_agent_brief.cli.main import main
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


def _workspace_file_bytes(ws: Path) -> dict[str, bytes]:
    return {
        path.relative_to(ws).as_posix(): path.read_bytes()
        for path in ws.rglob("*")
        if path.is_file()
    }


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


def test_trajectory_regulation_direct_import_has_no_runtime_state_cycle() -> None:
    root = Path(__file__).resolve().parent.parent
    env = dict(os.environ)
    env["PYTHONPATH"] = str(root / "src")
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from multi_agent_brief.product.trajectory_regulation import "
                "project_workspace_trajectory_regulation; "
                "print(project_workspace_trajectory_regulation)"
            ),
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=root,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert "project_workspace_trajectory_regulation" in result.stdout


def test_state_operator_cli_is_retired_with_typed_rejection(tmp_path: Path, capsys) -> None:
    ws = _workspace(tmp_path)
    database_before = (ws / "briefloop.db").read_bytes()
    files_before = _workspace_file_bytes(ws)
    retired_commands = (
        (
            "state",
            "stage-complete",
            "--workspace",
            str(ws),
            "--stage",
            "doctor",
            "--reason",
            "Synthetic doctor complete.",
        ),
        (
            "state",
            "decide",
            "--workspace",
            str(ws),
            "--stage",
            "source-discovery",
            "--decision",
            "retry_stage",
            "--reason",
            "Synthetic source discovery retry.",
        ),
    )

    for argv in retired_commands:
        # retired public `state` operator CLI; typed rejection
        # with zero writes replaces the pre-CX stage/decision transactions.
        assert main(list(argv)) == 1
        assert capsys.readouterr().out == "runtime_command_unsupported\n"
        assert (ws / "briefloop.db").read_bytes() == database_before
        assert _workspace_file_bytes(ws) == files_before


def test_trajectory_regulation_suppresses_actions_for_completed_prior_stage(tmp_path: Path) -> None:
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


def test_trajectory_regulation_enforces_retry_budget_decision_narrowing(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    workflow = _workflow_state()
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
    assert projection["status"] == "action_required"
    assert projection["summary_counts"]["retry_stage_count"] == 3
    assert projection["recommended_actions"] == [
        {
            "action": "request_human_review",
            "stage_id": "source-discovery",
            "reason": "retry_budget_exhausted",
        }
    ]
    assert source_stage["recommended_decision"] == "request_human_review"
    assert source_stage["reasons"] == ["retry_budget_exhausted"]
    assert source_stage["exhausted_attempt_budget"] is True
    # workflow_state decision narrowing
    # (workflow["trajectory_regulation"] / next_allowed_decisions) and the
    # "[status] trajectory_*" formatter lines belonged to the retired
    # `state decide` operator path; the read-only projection carries the
    # retry-budget invariant.


def test_trajectory_regulation_ignores_stale_prior_run_events(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    workflow = _workflow_state(
        current_stage="doctor",
        stage_statuses={"doctor": "complete"},
    )
    events = [
        {
            "schema_version": "multi-agent-brief-event-log/v1",
            "event_id": "evt-old",
            "run_id": "mabw-20260101T000000Z-old",
            "created_at": "2026-01-01T00:00:00+00:00",
            "event_type": "decision_recorded",
            "actor": "orchestrator",
            "stage_id": "doctor",
            "artifact_id": None,
            "decision": "retry_stage",
            "reason": "Old retry must not count.",
            "metadata": {},
        }
    ]

    projection = project_workspace_trajectory_regulation(
        ws,
        workflow_state=workflow,
        event_records=events,
        event_log_present=True,
        run_id=workflow["run_id"],
    )

    assert validate_trajectory_regulation_payload(projection) is None
    assert projection["status"] == "ok"
    assert projection["summary_counts"]["retry_stage_count"] == 0
    assert projection["recommended_actions"] == []


def test_trajectory_regulation_missing_event_log_is_explicit(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    workflow = _workflow_state()

    projection = project_workspace_trajectory_regulation(
        ws,
        workflow_state=workflow,
        event_records=[],
        event_log_present=False,
        run_id=workflow["run_id"],
    )

    assert validate_trajectory_regulation_payload(projection) is None
    assert projection["status"] == "missing_event_log"
    assert projection["event_log_present"] is False
    assert projection["recommended_actions"] == []


def test_trajectory_regulation_corrupt_event_log_is_not_ok(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    workflow = _workflow_state()

    projection = project_workspace_trajectory_regulation(
        ws,
        workflow_state=workflow,
        event_records=[],
        event_log_present=True,
        event_log_corrupt_count=1,
        run_id=workflow["run_id"],
    )

    assert validate_trajectory_regulation_payload(projection) is None
    assert projection["status"] == "event_log_invalid"
    assert projection["event_log_corrupt_count"] == 1
    assert projection["recommended_actions"] == []
    # status["events"]/stale_or_unknown surfacing of corrupt
    # event logs belonged to the retired legacy JSON status surface.


def test_trajectory_regulation_ignores_hand_edited_non_object_event_metadata(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    workflow = _workflow_state()
    events = [
        {
            "schema_version": "multi-agent-brief-event-log/v1",
            "event_id": "evt-bad-metadata",
            "run_id": workflow["run_id"],
            "created_at": "2026-07-01T00:00:00+00:00",
            "event_type": "repair_started",
            "actor": "orchestrator",
            "stage_id": None,
            "artifact_id": None,
            "decision": None,
            "reason": "",
            "metadata": "hand-edited-invalid-metadata",
        }
    ]

    projection = project_workspace_trajectory_regulation(
        ws,
        workflow_state=workflow,
        event_records=events,
        run_id=workflow["run_id"],
    )

    assert validate_trajectory_regulation_payload(projection) is None
    assert projection["status"] == "ok"
    assert projection["recommended_actions"] == []


def test_quality_panel_surfaces_trajectory_action_without_state_authority(tmp_path: Path) -> None:
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
