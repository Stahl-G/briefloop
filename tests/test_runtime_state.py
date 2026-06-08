"""Tests for v0.6.1 Orchestrator runtime state."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import multi_agent_brief.orchestrator.runtime_state as runtime_state
from multi_agent_brief.cli.main import main
from multi_agent_brief.orchestrator.runtime_state import (
    RUNTIME_STATE_FILES,
    RuntimeStateError,
    check_runtime_state,
    initialize_runtime_state,
    record_decision,
    show_runtime_state,
)


ROOT = Path(__file__).resolve().parent.parent


def _write_workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "input").mkdir()
    (ws / "config.yaml").write_text(
        """
project:
  name: "Runtime State Test"
output:
  path: "output"
input:
  path: "input"
""".strip(),
        encoding="utf-8",
    )
    (ws / "user.md").write_text("# User\n", encoding="utf-8")
    (ws / "sources.yaml").write_text("manual:\n  sources: []\n", encoding="utf-8")
    return ws


def _state_file(ws: Path, key: str) -> Path:
    return ws / RUNTIME_STATE_FILES[key]


def _intermediate(ws: Path) -> Path:
    path = ws / "output" / "intermediate"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_json_artifact(ws: Path, name: str, payload: str = "[]\n") -> None:
    (_intermediate(ws) / name).write_text(payload, encoding="utf-8")


def _advance_to_finalize(ws: Path) -> None:
    record_decision(workspace=ws, repo_workdir=ROOT, stage_id="doctor", decision="continue", reason="doctor complete")
    record_decision(workspace=ws, repo_workdir=ROOT, stage_id="source-discovery", decision="continue", reason="source complete")
    record_decision(workspace=ws, repo_workdir=ROOT, stage_id="input-governance", decision="continue", reason="input complete")
    _write_json_artifact(ws, "candidate_claims.json")
    record_decision(workspace=ws, repo_workdir=ROOT, stage_id="scout", decision="continue", reason="scout complete")
    _write_json_artifact(ws, "screened_candidates.json")
    record_decision(workspace=ws, repo_workdir=ROOT, stage_id="screener", decision="continue", reason="screener complete")
    _write_json_artifact(ws, "claim_ledger.json")
    record_decision(workspace=ws, repo_workdir=ROOT, stage_id="claim-ledger", decision="continue", reason="ledger complete")
    (_intermediate(ws) / "audited_brief.md").write_text("# Brief\n", encoding="utf-8")
    record_decision(workspace=ws, repo_workdir=ROOT, stage_id="analyst", decision="continue", reason="analyst complete")
    record_decision(workspace=ws, repo_workdir=ROOT, stage_id="editor", decision="continue", reason="editor complete")
    _write_json_artifact(ws, "audit_report.json", "{}\n")
    record_decision(workspace=ws, repo_workdir=ROOT, stage_id="auditor", decision="continue", reason="auditor complete")


def test_state_init_creates_runtime_control_files_without_old_run_manifest(tmp_path):
    ws = _write_workspace(tmp_path)

    state = initialize_runtime_state(workspace=ws, repo_workdir=ROOT)

    assert state["ok"] is True
    assert _state_file(ws, "runtime_manifest").exists()
    assert _state_file(ws, "workflow_state").exists()
    assert _state_file(ws, "event_log").exists()
    assert not (ws / "output" / "intermediate" / "run_manifest.json").exists()

    manifest = json.loads(_state_file(ws, "runtime_manifest").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "multi-agent-brief-runtime-manifest/v1"
    assert manifest["workspace"] == "."
    assert manifest["runtime_state_files"] == RUNTIME_STATE_FILES
    assert manifest["stage_order"][0] == "doctor"


def test_state_check_fresh_workspace_is_not_globally_blocked(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    workflow = state["workflow_state"]
    registry = state["artifact_registry"]["artifacts"]

    assert workflow["blocked"] is False
    assert workflow["current_stage"] == "doctor"
    assert workflow["stage_statuses"]["doctor"]["status"] == "ready"
    assert workflow["stage_statuses"]["claim-ledger"]["status"] == "pending"
    assert registry["claim_ledger"]["status"] == "expected"
    assert registry["audited_brief"]["status"] == "expected"
    assert registry["reader_brief"]["status"] == "expected"


def test_state_check_strict_fresh_workspace_returns_zero(tmp_path):
    ws = _write_workspace(tmp_path)

    rc = main([
        "state",
        "check",
        "--workspace",
        str(ws),
        "--repo-workdir",
        str(ROOT),
        "--strict",
        "--json",
    ])

    assert rc == 0


def test_required_stage_output_missing_rejects_continue_and_preserves_workflow(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)

    for stage in ("doctor", "source-discovery", "input-governance"):
        record_decision(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id=stage,
            decision="continue",
            reason=f"{stage} complete",
        )

    before = json.loads(_state_file(ws, "workflow_state").read_text(encoding="utf-8"))
    with pytest.raises(RuntimeStateError, match="Cannot continue stage 'scout'"):
        record_decision(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="scout",
            decision="continue",
            reason="scout complete",
        )
    after = json.loads(_state_file(ws, "workflow_state").read_text(encoding="utf-8"))

    assert after == before


def test_finalize_missing_reader_brief_rejects_finalize(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _advance_to_finalize(ws)

    before = json.loads(_state_file(ws, "workflow_state").read_text(encoding="utf-8"))
    with pytest.raises(RuntimeStateError, match="Cannot finalize stage 'finalize'"):
        record_decision(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="finalize",
            decision="finalize",
            reason="finalize complete",
        )
    after = json.loads(_state_file(ws, "workflow_state").read_text(encoding="utf-8"))

    assert after == before


def test_invalid_optional_expected_artifact_rejects_continue(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    record_decision(workspace=ws, repo_workdir=ROOT, stage_id="doctor", decision="continue", reason="doctor complete")
    (ws / "source_candidates.yaml").write_text(": [", encoding="utf-8")

    with pytest.raises(RuntimeStateError, match="Optional expected artifact 'source_candidates'"):
        record_decision(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="source-discovery",
            decision="continue",
            reason="source discovery complete",
        )


def test_optional_feedback_artifacts_do_not_become_missing_after_auditor_complete(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _advance_to_finalize(ws)

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    registry = state["artifact_registry"]["artifacts"]

    assert registry["feedback_issues"]["status"] == "expected"
    assert registry["repair_plan"]["status"] == "expected"
    assert registry["delta_audit_report"]["status"] == "expected"
    assert registry["feedback_issues"]["validation_result"] == "not_checked"
    assert registry["repair_plan"]["validation_result"] == "not_checked"
    assert registry["delta_audit_report"]["validation_result"] == "not_checked"


def test_delta_audit_report_missing_only_when_repair_active(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _advance_to_finalize(ws)
    out = _intermediate(ws)
    (out / "feedback_issues.json").write_text(
        json.dumps({
            "schema_version": "multi-agent-brief-feedback-issues/v1",
            "created_at": "2026-06-08T00:00:00+00:00",
            "updated_at": "2026-06-08T00:00:00+00:00",
            "issues": [
                {
                    "issue_id": "fb_active",
                    "source": "human",
                    "severity": "blocking",
                    "stage_id": "auditor",
                    "artifact_id": "audit_report",
                    "category": "unsupported_claim",
                    "summary": "Repair requires delta audit.",
                    "feedback_excerpt": "Repair requires delta audit.",
                    "raw_feedback_ref": "feedback.txt",
                    "source_artifact": "feedback.txt",
                    "supporting_context": [],
                    "metadata": {},
                    "status": "in_progress",
                    "created_at": "2026-06-08T00:00:00+00:00",
                    "updated_at": "2026-06-08T00:00:00+00:00",
                    "fingerprint": "active",
                }
            ],
        }),
        encoding="utf-8",
    )
    (out / "repair_plan.json").write_text(
        json.dumps({
            "schema_version": "multi-agent-brief-repair-plan/v1",
            "created_at": "2026-06-08T00:00:00+00:00",
            "updated_at": "2026-06-08T00:00:00+00:00",
            "repair_plans": [
                {
                    "repair_plan_id": "rp_active",
                    "created_at": "2026-06-08T00:00:00+00:00",
                    "updated_at": "2026-06-08T00:00:00+00:00",
                    "target_stage": "auditor",
                    "target_artifacts": ["audit_report"],
                    "issue_ids": ["fb_active"],
                    "allowed_decision": "delegate_repair",
                    "repair_scope": "minimal",
                    "instructions": ["Run delta audit."],
                    "requires_human_review": False,
                    "status": "in_progress",
                    "fingerprint": "active",
                }
            ],
        }),
        encoding="utf-8",
    )
    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)

    assert state["artifact_registry"]["artifacts"]["delta_audit_report"]["status"] == "missing"


def test_invalid_current_stage_output_blocks_only_that_stage(tmp_path):
    ws = _write_workspace(tmp_path)
    output = ws / "output" / "intermediate"
    output.mkdir(parents=True)
    (output / "candidate_claims.json").write_text("{broken", encoding="utf-8")
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)

    for stage in ("doctor", "source-discovery", "input-governance"):
        record_decision(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id=stage,
            decision="continue",
            reason=f"{stage} complete",
        )

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    workflow = state["workflow_state"]
    registry = state["artifact_registry"]["artifacts"]

    assert registry["candidate_claims"]["status"] == "invalid"
    assert workflow["current_stage"] == "scout"
    assert workflow["stage_statuses"]["scout"]["status"] == "blocked"
    assert workflow["stage_statuses"]["claim-ledger"]["status"] == "pending"


def test_state_decide_validates_decision_vocabulary(tmp_path, capsys):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)

    rc = main([
        "state",
        "decide",
        "--workspace",
        str(ws),
        "--repo-workdir",
        str(ROOT),
        "--stage",
        "doctor",
        "--decision",
        "invent_decision",
        "--reason",
        "bad",
        "--json",
    ])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "Unknown Orchestrator decision" in payload["error"]


def test_state_decide_records_event_and_last_decision(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)

    state = record_decision(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="doctor",
        decision="continue",
        reason="doctor passed",
    )

    workflow = state["workflow_state"]
    assert workflow["last_decision"]["decision"] == "continue"
    assert workflow["stage_statuses"]["doctor"]["status"] == "complete"
    assert workflow["current_stage"] == "source-discovery"
    events = _state_file(ws, "event_log").read_text(encoding="utf-8").strip().splitlines()
    assert any(json.loads(line)["event_type"] == "decision_recorded" for line in events)


def test_state_decide_rejects_out_of_order_stage_and_leaves_workflow_unchanged(tmp_path, capsys):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    before = json.loads(_state_file(ws, "workflow_state").read_text(encoding="utf-8"))

    rc = main([
        "state",
        "decide",
        "--workspace",
        str(ws),
        "--repo-workdir",
        str(ROOT),
        "--stage",
        "auditor",
        "--decision",
        "continue",
        "--reason",
        "out of order",
        "--json",
    ])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "does not match current stage" in payload["error"]
    after = json.loads(_state_file(ws, "workflow_state").read_text(encoding="utf-8"))
    assert after == before


def test_state_decide_event_failure_leaves_workflow_unchanged(tmp_path, monkeypatch):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    before = json.loads(_state_file(ws, "workflow_state").read_text(encoding="utf-8"))

    def fail_append(*args, **kwargs):
        raise RuntimeStateError("event append failed")

    monkeypatch.setattr(runtime_state, "_append_jsonl", fail_append)

    with pytest.raises(RuntimeStateError):
        record_decision(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="doctor",
            decision="continue",
            reason="doctor passed",
        )

    after = json.loads(_state_file(ws, "workflow_state").read_text(encoding="utf-8"))
    assert after == before


def test_state_check_preserves_explicit_block_decision(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    record_decision(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="doctor",
        decision="block_run",
        reason="doctor failed",
    )

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)

    assert state["workflow_state"]["blocked"] is True
    assert state["workflow_state"]["current_stage"] == "doctor"
    assert state["workflow_state"]["stage_statuses"]["doctor"]["status"] == "blocked"
    assert state["workflow_state"]["blocking_reason"] == "doctor failed"


def test_state_check_event_failure_leaves_state_unchanged(tmp_path, monkeypatch):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    for stage in ("doctor", "source-discovery", "input-governance"):
        record_decision(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id=stage,
            decision="continue",
            reason=f"{stage} complete",
        )
    _write_json_artifact(ws, "candidate_claims.json")
    record_decision(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="scout",
        decision="continue",
        reason="scout complete",
    )
    before = json.loads(_state_file(ws, "workflow_state").read_text(encoding="utf-8"))

    def fail_append(*args, **kwargs):
        raise RuntimeStateError("event append failed")

    monkeypatch.setattr(runtime_state, "_append_jsonl", fail_append)

    with pytest.raises(RuntimeStateError):
        check_runtime_state(workspace=ws, repo_workdir=ROOT)

    after = json.loads(_state_file(ws, "workflow_state").read_text(encoding="utf-8"))
    assert after == before
    assert not _state_file(ws, "artifact_registry").exists()


def test_state_check_only_writes_changed_events_once(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    for stage in ("doctor", "source-discovery", "input-governance"):
        record_decision(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id=stage,
            decision="continue",
            reason=f"{stage} complete",
        )
    (_intermediate(ws) / "candidate_claims.json").write_text("{broken", encoding="utf-8")

    check_runtime_state(workspace=ws, repo_workdir=ROOT)
    first_events = _state_file(ws, "event_log").read_text(encoding="utf-8").strip().splitlines()
    check_runtime_state(workspace=ws, repo_workdir=ROOT)
    second_events = _state_file(ws, "event_log").read_text(encoding="utf-8").strip().splitlines()

    assert len(second_events) == len(first_events)
    event_types = [json.loads(line)["event_type"] for line in first_events]
    assert event_types.count("stage_status_changed") == 1
    assert event_types.count("run_blocked") == 1


def test_state_show_json_handles_corrupted_state_without_traceback(tmp_path, capsys):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _state_file(ws, "workflow_state").write_text("{broken", encoding="utf-8")

    rc = main(["state", "show", "--workspace", str(ws), "--json"])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "Invalid JSON state file" in payload["error"]


def test_reset_state_archives_old_event_log(tmp_path):
    ws = _write_workspace(tmp_path)
    first = initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    old_run_id = first["manifest"]["run_id"]

    second = initialize_runtime_state(
        workspace=ws,
        repo_workdir=ROOT,
        reset_state=True,
    )

    assert second["manifest"]["run_id"] != old_run_id
    archived = ws / "output" / "intermediate" / f"event_log.{old_run_id}.jsonl"
    assert archived.exists()
    assert _state_file(ws, "event_log").exists()
    reset_event = json.loads(_state_file(ws, "event_log").read_text(encoding="utf-8").splitlines()[0])
    assert reset_event["event_type"] == "run_reset"
    assert reset_event["metadata"]["previous_run_id"] == old_run_id
    assert reset_event["metadata"]["archived_event_log"] == f"output/intermediate/event_log.{old_run_id}.jsonl"


def test_reset_state_recovers_from_corrupted_workflow_state(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _state_file(ws, "workflow_state").write_text("{broken", encoding="utf-8")

    state = initialize_runtime_state(
        workspace=ws,
        repo_workdir=ROOT,
        reset_state=True,
    )

    assert state["ok"] is True
    assert state["workflow_state"]["current_stage"] == "doctor"


def test_state_paths_are_workspace_relative(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)

    assert state["manifest"]["runtime_state_files"] == RUNTIME_STATE_FILES
    for record in state["artifact_registry"]["artifacts"].values():
        assert not Path(record["path"]).is_absolute()


def test_show_runtime_state_reports_event_count(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)

    state = show_runtime_state(workspace=ws)

    assert state["event_count"] >= 1
