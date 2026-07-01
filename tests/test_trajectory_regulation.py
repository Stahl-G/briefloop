from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from multi_agent_brief.cli.main import main
from multi_agent_brief.product.quality_panel import build_quality_panel, validate_quality_panel_payload
from multi_agent_brief.product.trajectory_regulation import (
    project_workspace_trajectory_regulation,
    validate_trajectory_regulation_payload,
)
from multi_agent_brief.status import build_workspace_status, format_workspace_status


def _workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "config.yaml").write_text("project:\n  name: Trajectory Test\n", encoding="utf-8")
    (ws / "sources.yaml").write_text("manual:\n  sources: []\n", encoding="utf-8")
    (ws / "user.md").write_text("# Trajectory test\n", encoding="utf-8")
    assert main(["state", "init", "--workspace", str(ws)]) == 0
    return ws


def _advance_to_source_discovery(ws: Path) -> None:
    assert main([
        "state",
        "stage-complete",
        "--workspace",
        str(ws),
        "--stage",
        "doctor",
        "--reason",
        "Synthetic doctor complete.",
    ]) == 0


def _retry_source_discovery(ws: Path, count: int) -> None:
    for idx in range(count):
        assert main([
            "state",
            "decide",
            "--workspace",
            str(ws),
            "--stage",
            "source-discovery",
            "--decision",
            "retry_stage",
            "--reason",
            f"Synthetic source discovery retry {idx + 1}.",
        ]) == 0


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_trajectory_regulation_direct_import_has_no_runtime_state_cycle() -> None:
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
    )

    assert result.returncode == 0, result.stderr
    assert "project_workspace_trajectory_regulation" in result.stdout


def test_trajectory_regulation_projects_retry_budget_without_writing_state(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    _advance_to_source_discovery(ws)
    _retry_source_discovery(ws, 3)
    workflow_before = _json(ws / "output" / "intermediate" / "workflow_state.json")

    status = build_workspace_status(ws)
    formatted = format_workspace_status(status)
    projection = status["trajectory_regulation"]
    workflow_after = _json(ws / "output" / "intermediate" / "workflow_state.json")

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
    assert workflow_before == workflow_after
    assert workflow_after["blocked"] is False
    assert "[status] trajectory_regulation: action_required" in formatted
    assert "runtime_effect=none" in formatted


def test_trajectory_regulation_ignores_stale_prior_run_events(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    workflow = _json(ws / "output" / "intermediate" / "workflow_state.json")
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
    (ws / "output" / "intermediate" / "event_log.jsonl").unlink()

    projection = build_workspace_status(ws)["trajectory_regulation"]

    assert validate_trajectory_regulation_payload(projection) is None
    assert projection["status"] == "missing_event_log"
    assert projection["event_log_present"] is False
    assert projection["recommended_actions"] == []


def test_trajectory_regulation_corrupt_event_log_is_not_ok(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    event_log = ws / "output" / "intermediate" / "event_log.jsonl"
    with event_log.open("a", encoding="utf-8") as fh:
        fh.write("{not-json}\n")

    status = build_workspace_status(ws)
    projection = status["trajectory_regulation"]
    direct_projection = project_workspace_trajectory_regulation(ws)

    assert status["events"]["corrupt_count"] == 1
    assert validate_trajectory_regulation_payload(projection) is None
    assert projection["status"] == "event_log_invalid"
    assert projection["event_log_corrupt_count"] == 1
    assert projection["recommended_actions"] == []
    assert "event_log contains unreadable records" in status["stale_or_unknown"]
    assert direct_projection["status"] == "event_log_invalid"


def test_trajectory_regulation_ignores_hand_edited_non_object_event_metadata(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    event_log = ws / "output" / "intermediate" / "event_log.jsonl"
    with event_log.open("a", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {
                    "schema_version": "multi-agent-brief-event-log/v1",
                    "event_id": "evt-bad-metadata",
                    "run_id": _json(ws / "output" / "intermediate" / "runtime_manifest.json")["run_id"],
                    "created_at": "2026-07-01T00:00:00+00:00",
                    "event_type": "repair_started",
                    "actor": "orchestrator",
                    "stage_id": None,
                    "artifact_id": None,
                    "decision": None,
                    "reason": "",
                    "metadata": "hand-edited-invalid-metadata",
                },
                sort_keys=True,
            )
            + "\n"
        )

    projection = build_workspace_status(ws)["trajectory_regulation"]

    assert validate_trajectory_regulation_payload(projection) is None
    assert projection["status"] == "ok"
    assert projection["recommended_actions"] == []


def test_quality_panel_surfaces_trajectory_action_without_state_authority(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    _advance_to_source_discovery(ws)
    _retry_source_discovery(ws, 3)

    panel = build_quality_panel(ws)

    assert validate_quality_panel_payload(panel) is None
    assert panel["trajectory_regulation"]["status"] == "action_required"
    assert {
        "action": "request_human_review",
        "stage_id": "source-discovery",
        "reason": "retry_budget_exhausted",
    } in panel["recommended_actions"]
    assert "automatic_repair" in panel["non_goals"]
