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
    complete_finalize_transaction,
    complete_stage_transaction,
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


def _event_records(ws: Path) -> list[dict]:
    path = _state_file(ws, "event_log")
    if not path.exists() or not path.read_text(encoding="utf-8").strip():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _write_quality_gate_report(
    ws: Path,
    *,
    status: str = "pass",
    blocking: bool = False,
    stage_id: str = "auditor",
) -> None:
    findings = []
    if blocking:
        findings.append({
            "finding_id": "QG_TARGET_RELEVANCE_001",
            "finding_type": "target_relevance_failed",
            "severity": "high",
            "blocking_level": "blocking",
            "blocking": True,
            "stage_id": stage_id,
            "gate_stage_id": stage_id,
            "artifact_id": "quality_gate_report",
            "gate_artifact_id": "quality_gate_report",
            "repair_stage_id": stage_id,
            "repair_artifact_id": "audited_brief",
            "repair_owner": "orchestrator",
            "message": "Synthetic blocking finding.",
            "metadata": {},
        })
    (_intermediate(ws) / "quality_gate_report.json").write_text(
        json.dumps({
            "schema_version": "multi-agent-brief-quality-gates/v1",
            "created_at": "2026-06-11T00:00:00+00:00",
            "updated_at": "2026-06-11T00:00:00+00:00",
            "workspace": ".",
            "report_date": "2026-06-11",
            "policy_pack": "default",
            "status": status,
            "gate_results": [
                {
                    "gate_id": "freshness",
                    "status": "pass",
                    "blocking": False,
                    "finding_ids": [],
                },
                {
                    "gate_id": "material_fact",
                    "status": "pass",
                    "blocking": False,
                    "finding_ids": [],
                },
                {
                    "gate_id": "target_relevance",
                    "status": "fail" if blocking else status,
                    "blocking": blocking,
                    "finding_ids": [item["finding_id"] for item in findings],
                }
            ],
            "findings": findings,
            "metadata": {
                "brief": "output/intermediate/audited_brief.md",
                "ledger": "output/intermediate/claim_ledger.json",
                "stage_id": stage_id,
                "gate_stage_id": stage_id,
                "gate_artifact_id": "quality_gate_report",
            },
        }),
        encoding="utf-8",
    )


def _write_finalize_report(
    ws: Path,
    *,
    status: str = "pass",
    reader_clean_status: str = "pass",
) -> None:
    output = ws / "output"
    (output / "brief.md").write_text("# Reader Brief\n\nClean reader text.\n", encoding="utf-8")
    (_intermediate(ws) / "finalize_report.json").write_text(
        json.dumps({
            "status": status,
            "audited_brief": str(_intermediate(ws) / "audited_brief.md"),
            "reader_brief": str(output / "brief.md"),
            "named_reader_brief": "",
            "reader_docx": "",
            "named_reader_docx": "",
            "source_appendix": "",
            "reader_clean": {
                "status": reader_clean_status,
                "src_marker_count": 0,
                "bare_claim_id_count": 0,
                "source_id_count": 0,
                "process_wording_count": 0,
                "blank_citation_row_count": 0,
                "local_path_count": 0,
                "debug_residue_count": 0,
                "sample_findings": [],
            },
        }),
        encoding="utf-8",
    )


def _set_current_stage(ws: Path, stage_id: str) -> None:
    stages = runtime_state.load_stage_specs(ROOT)
    stage_ids = [str(stage.get("stage_id") or "") for stage in stages if stage.get("stage_id")]
    assert stage_id in stage_ids
    workflow = json.loads(_state_file(ws, "workflow_state").read_text(encoding="utf-8"))
    now = runtime_state.utc_now()
    statuses = {}
    for item in stage_ids:
        if stage_ids.index(item) < stage_ids.index(stage_id):
            statuses[item] = {"status": "complete", "reason": f"{item} fixture complete", "updated_at": now}
        elif item == stage_id:
            statuses[item] = {"status": "ready", "reason": "", "updated_at": now}
        else:
            statuses[item] = {"status": "pending", "reason": "", "updated_at": now}
    workflow["updated_at"] = now
    workflow["current_stage"] = stage_id
    workflow["blocked"] = False
    workflow["blocking_reason"] = ""
    workflow["stage_statuses"] = statuses
    workflow["next_allowed_decisions"] = runtime_state._allowed_decisions_for_stage(stages, stage_id)
    _state_file(ws, "workflow_state").write_text(
        json.dumps(workflow, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _advance_to_finalize(ws: Path) -> None:
    _write_json_artifact(ws, "candidate_claims.json")
    _write_json_artifact(ws, "screened_candidates.json")
    _write_json_artifact(ws, "claim_ledger.json")
    (_intermediate(ws) / "audited_brief.md").write_text("# Brief\n", encoding="utf-8")
    _write_json_artifact(ws, "audit_report.json", "{}\n")
    _set_current_stage(ws, "finalize")


def _advance_to_auditor(ws: Path) -> None:
    _write_json_artifact(ws, "candidate_claims.json")
    _write_json_artifact(ws, "screened_candidates.json")
    _write_json_artifact(ws, "claim_ledger.json")
    (_intermediate(ws) / "audited_brief.md").write_text("# Brief\n", encoding="utf-8")
    _write_json_artifact(ws, "audit_report.json", "{}\n")
    _set_current_stage(ws, "auditor")


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
    assert registry["quality_gate_report"]["status"] == "expected"
    assert registry["quality_gate_report"]["validation_result"] == "not_checked"


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


def test_state_decide_continue_requires_completion_transaction(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _advance_to_auditor(ws)

    before = json.loads(_state_file(ws, "workflow_state").read_text(encoding="utf-8"))
    with pytest.raises(RuntimeStateError) as excinfo:
        record_decision(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="auditor",
            decision="continue",
            reason="auditor complete",
        )
    after = json.loads(_state_file(ws, "workflow_state").read_text(encoding="utf-8"))

    assert excinfo.value.error_code == "E_COMPLETION_TRANSACTION_REQUIRED"
    assert excinfo.value.details["required_command"] == "stage-complete"
    assert after == before


def test_state_decide_finalize_requires_completion_transaction(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _advance_to_finalize(ws)

    before = json.loads(_state_file(ws, "workflow_state").read_text(encoding="utf-8"))
    with pytest.raises(RuntimeStateError) as excinfo:
        record_decision(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="finalize",
            decision="finalize",
            reason="finalize complete",
        )
    after = json.loads(_state_file(ws, "workflow_state").read_text(encoding="utf-8"))

    assert excinfo.value.error_code == "E_COMPLETION_TRANSACTION_REQUIRED"
    assert excinfo.value.details["required_command"] == "finalize-complete"
    assert after == before


def test_invalid_optional_expected_artifact_rejects_continue(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    complete_stage_transaction(workspace=ws, repo_workdir=ROOT, stage_id="doctor", reason="doctor complete")
    (ws / "source_candidates.yaml").write_text(": [", encoding="utf-8")

    with pytest.raises(RuntimeStateError, match="Optional expected artifact 'source_candidates'"):
        complete_stage_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="source-discovery",
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
    assert registry["quality_gate_report"]["status"] == "expected"
    assert registry["feedback_issues"]["validation_result"] == "not_checked"
    assert registry["repair_plan"]["validation_result"] == "not_checked"
    assert registry["delta_audit_report"]["validation_result"] == "not_checked"
    assert registry["quality_gate_report"]["validation_result"] == "not_checked"


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
    _set_current_stage(ws, "scout")

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
        decision="block_run",
        reason="doctor failed",
    )

    workflow = state["workflow_state"]
    assert workflow["last_decision"]["decision"] == "block_run"
    assert workflow["stage_statuses"]["doctor"]["status"] == "blocked"
    assert workflow["current_stage"] == "doctor"
    events = _state_file(ws, "event_log").read_text(encoding="utf-8").strip().splitlines()
    assert any(json.loads(line)["event_type"] == "decision_recorded" for line in events)


def test_stage_complete_records_transaction_event_and_advances(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)

    state = complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="doctor",
        reason="doctor passed",
    )

    workflow = state["workflow_state"]
    transaction_id = workflow["last_completion_transaction"]["transaction_id"]
    assert workflow["current_stage"] == "source-discovery"
    assert workflow["stage_statuses"]["doctor"]["status"] == "complete"
    assert state["transaction"]["transaction_id"] == transaction_id
    decision_events = [
        event for event in _event_records(ws)
        if event["event_type"] == "decision_recorded"
        and (event.get("metadata") or {}).get("transaction_id") == transaction_id
    ]
    assert len(decision_events) == 1
    assert decision_events[0]["decision"] == "continue"


def test_stage_complete_duplicate_rejects_without_duplicate_event(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="doctor",
        reason="doctor passed",
    )
    before_events = _event_records(ws)

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_stage_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="doctor",
            reason="doctor passed again",
        )

    assert excinfo.value.error_code == "E_STAGE_ALREADY_COMPLETED"
    assert _event_records(ws) == before_events


def test_stage_complete_missing_required_output_writes_nothing(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _set_current_stage(ws, "scout")
    before_workflow = json.loads(_state_file(ws, "workflow_state").read_text(encoding="utf-8"))
    before_events = _event_records(ws)

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_stage_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="scout",
            reason="scout complete",
        )

    assert excinfo.value.error_code == "E_REQUIRED_ARTIFACT_MISSING"
    assert json.loads(_state_file(ws, "workflow_state").read_text(encoding="utf-8")) == before_workflow
    assert _event_records(ws) == before_events


def test_stage_complete_cli_json_error_includes_error_code(tmp_path, capsys):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)

    rc = main([
        "state",
        "stage-complete",
        "--workspace",
        str(ws),
        "--repo-workdir",
        str(ROOT),
        "--stage",
        "auditor",
        "--reason",
        "out of order",
        "--json",
    ])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error_code"] == "E_STAGE_MISMATCH"


def test_stage_complete_event_append_failure_is_detectable_partial_write(tmp_path, monkeypatch):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)

    def fail_append(*args, **kwargs):
        raise RuntimeStateError("event append failed")

    monkeypatch.setattr(runtime_state, "_append_jsonl", fail_append)

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_stage_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="doctor",
            reason="doctor passed",
        )

    assert excinfo.value.error_code == "E_TRANSACTION_PARTIAL_WRITE"
    workflow = json.loads(_state_file(ws, "workflow_state").read_text(encoding="utf-8"))
    assert workflow["current_stage"] == "source-discovery"
    assert workflow["last_completion_transaction"]["transaction_id"]

    monkeypatch.undo()
    checked = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    assert checked["workflow_state"]["blocked"] is True
    assert checked["transaction_integrity_warning"]["error_code"] == "E_TRANSACTION_INTEGRITY"


def test_stage_complete_stale_gate_report_does_not_block_early_stage(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _set_current_stage(ws, "scout")
    _write_json_artifact(ws, "candidate_claims.json")
    _write_quality_gate_report(ws, blocking=True, stage_id="auditor")

    state = complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="scout",
        reason="scout complete",
    )

    assert state["workflow_state"]["current_stage"] == "screener"


def test_auditor_stage_complete_requires_passing_quality_gate_report(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _advance_to_auditor(ws)

    with pytest.raises(RuntimeStateError) as missing:
        complete_stage_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="auditor",
            reason="auditor complete",
        )
    assert missing.value.error_code == "E_QUALITY_GATE_REQUIRED"

    _write_quality_gate_report(ws, blocking=True, stage_id="auditor")
    with pytest.raises(RuntimeStateError) as blocking:
        complete_stage_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="auditor",
            reason="auditor complete",
        )
    assert blocking.value.error_code == "E_QUALITY_GATE_REQUIRED"


def test_auditor_stage_complete_rejects_wrong_stage_quality_gate_report(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _advance_to_auditor(ws)
    _write_quality_gate_report(ws, stage_id="doctor")

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_stage_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="auditor",
            reason="auditor complete",
        )

    assert excinfo.value.error_code == "E_QUALITY_GATE_REQUIRED"
    assert "gate_stage_id='auditor'" in str(excinfo.value)


def test_auditor_stage_complete_rejects_incomplete_quality_gate_report(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _advance_to_auditor(ws)
    _write_quality_gate_report(ws)
    report_path = _intermediate(ws) / "quality_gate_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["gate_results"] = [
        result for result in report["gate_results"] if result["gate_id"] != "freshness"
    ]
    report_path.write_text(json.dumps(report), encoding="utf-8")

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_stage_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="auditor",
            reason="auditor complete",
        )

    assert excinfo.value.error_code == "E_QUALITY_GATE_REQUIRED"
    assert "missing: freshness" in str(excinfo.value)


def test_auditor_stage_complete_rejects_missing_quality_gate_input_metadata(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _advance_to_auditor(ws)
    _write_quality_gate_report(ws)
    report_path = _intermediate(ws) / "quality_gate_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["metadata"].pop("brief")
    report["metadata"].pop("ledger")
    report_path.write_text(json.dumps(report), encoding="utf-8")

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_stage_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            stage_id="auditor",
            reason="auditor complete",
        )

    assert excinfo.value.error_code == "E_QUALITY_GATE_REQUIRED"
    assert "brief metadata must be output/intermediate/audited_brief.md" in str(excinfo.value)


def test_auditor_stage_complete_passes_with_clean_quality_gate_report(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _advance_to_auditor(ws)
    _write_quality_gate_report(ws)

    state = complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="auditor",
        reason="auditor and gates passed",
    )

    assert state["workflow_state"]["current_stage"] == "finalize"


def test_finalize_complete_rejects_forged_clean_report_with_dirty_artifact(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _advance_to_finalize(ws)
    _write_quality_gate_report(ws)
    _write_finalize_report(ws)
    (ws / "output" / "brief.md").write_text("# Brief\n\nLeaked [CL-0001].\n", encoding="utf-8")

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_finalize_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            reason="finalize complete",
        )

    assert excinfo.value.error_code == "E_READER_FINAL_GATE_FAILED"


def test_finalize_complete_records_terminal_transaction(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _advance_to_finalize(ws)
    _write_quality_gate_report(ws)
    _write_finalize_report(ws)

    state = complete_finalize_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        reason="reader artifacts finalized and clean",
    )

    workflow = state["workflow_state"]
    transaction_id = workflow["last_completion_transaction"]["transaction_id"]
    assert workflow["current_stage"] is None
    assert workflow["stage_statuses"]["finalize"]["status"] == "complete"
    assert workflow["last_decision"]["decision"] == "finalize"
    assert any(
        event["event_type"] == "decision_recorded"
        and (event.get("metadata") or {}).get("transaction_id") == transaction_id
        for event in _event_records(ws)
    )


def test_finalize_complete_accepts_reader_facing_quality_gate_report(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _advance_to_finalize(ws)
    _write_finalize_report(ws)
    _write_quality_gate_report(ws, stage_id="finalize")
    report_path = _intermediate(ws) / "quality_gate_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["metadata"]["brief"] = "output/brief.md"
    report["metadata"]["ledger"] = "output/intermediate/claim_ledger.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    state = complete_finalize_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        reason="reader-facing gates and final artifacts passed",
    )

    assert state["workflow_state"]["current_stage"] is None
    assert state["workflow_state"]["stage_statuses"]["finalize"]["status"] == "complete"


def test_completion_transactions_preserve_manifest_extensions(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    manifest_path = _state_file(ws, "runtime_manifest")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["recipe"] = "fast-rerun"
    manifest["improvement"] = {
        "ledger_sha256": None,
        "memory_sha256": "zero",
        "snapshot_path": None,
        "snapshot_sha256": None,
        "materialized_entry_ids": [],
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="doctor",
        reason="doctor passed",
    )
    after_stage = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert after_stage["recipe"] == "fast-rerun"
    assert after_stage["improvement"] == manifest["improvement"]

    check_runtime_state(workspace=ws, repo_workdir=ROOT)
    after_check = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert after_check["recipe"] == "fast-rerun"
    assert after_check["improvement"] == manifest["improvement"]

    _advance_to_finalize(ws)
    _write_quality_gate_report(ws)
    _write_finalize_report(ws)
    complete_finalize_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        reason="reader artifacts finalized and clean",
    )
    after_finalize = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert after_finalize["recipe"] == "fast-rerun"
    assert after_finalize["improvement"] == manifest["improvement"]


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
            decision="block_run",
            reason="doctor failed",
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
    _write_json_artifact(ws, "candidate_claims.json")
    _set_current_stage(ws, "scout")
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
    _set_current_stage(ws, "scout")
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
