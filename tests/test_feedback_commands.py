"""Tests for v0.6.2 feedback issue and repair-plan controls."""

from __future__ import annotations

import json
from functools import partial
from pathlib import Path

import pytest

import multi_agent_brief.orchestrator.runtime_state as runtime_state
import multi_agent_brief.feedback.feedback_state as feedback_state
from multi_agent_brief.cli.main import main
from multi_agent_brief.feedback.feedback_state import (
    ingest_feedback,
    plan_feedback,
    resolve_feedback,
    validate_feedback_workspace,
)
from multi_agent_brief.orchestrator.runtime_state import (
    RuntimeStateError,
    check_runtime_state,
    complete_stage_transaction,
    initialize_runtime_state,
    record_decision,
)
from multi_agent_brief.orchestrator.runtime_state.workflow import _allowed_decisions_for_stage
from tests.helpers import write_workspace_files_under


ROOT = Path(__file__).resolve().parent.parent


_write_workspace_files = partial(
    write_workspace_files_under,
    config_text="""
project:
  name: "Feedback Test"
output:
  path: "output"
input:
  path: "input"
""".strip(),
    user_text="# User\n",
    include_input_dir=True,
)


def _write_workspace(tmp_path: Path) -> Path:
    ws = _write_workspace_files(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT, runtime="operator")
    return ws


def _write_uninitialized_workspace(tmp_path: Path) -> Path:
    return _write_workspace_files(tmp_path)


def _issues_path(ws: Path) -> Path:
    return ws / "output" / "intermediate" / "feedback_issues.json"


def _plan_path(ws: Path) -> Path:
    return ws / "output" / "intermediate" / "repair_plan.json"


def _intermediate(ws: Path) -> Path:
    path = ws / "output" / "intermediate"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_json_artifact(ws: Path, name: str, payload: str = "[]\n") -> None:
    (_intermediate(ws) / name).write_text(payload, encoding="utf-8")


def _workspace_file_bytes(ws: Path) -> dict[str, bytes]:
    return {
        path.relative_to(ws).as_posix(): path.read_bytes()
        for path in ws.rglob("*")
        if path.is_file()
    }


def _set_current_stage(ws: Path, stage_id: str) -> None:
    stages = runtime_state.load_stage_specs(ROOT)
    stage_ids = [str(stage.get("stage_id") or "") for stage in stages if stage.get("stage_id")]
    workflow_path = ws / "output" / "intermediate" / "workflow_state.json"
    workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
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
    workflow["next_allowed_decisions"] = _allowed_decisions_for_stage(stages, stage_id)
    workflow_path.write_text(json.dumps(workflow, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _advance_to_analyst(ws: Path) -> None:
    _write_json_artifact(ws, "candidate_claims.json")
    _write_json_artifact(ws, "screened_candidates.json")
    _write_json_artifact(ws, "claim_ledger.json")
    _set_current_stage(ws, "analyst")


def _events(ws: Path) -> list[dict[str, object]]:
    path = ws / "output" / "intermediate" / "event_log.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.mark.parametrize("action", ["ingest", "plan", "resolve", "validate"])
def test_feedback_public_cli_is_retired_with_typed_rejection(tmp_path, capsys, action):
    # retired public `feedback` CLI surface; feedback issue and
    # repair-plan state is driven only through the deterministic feedback_state
    # module seam used by the tests below.
    ws = _write_workspace(tmp_path)
    feedback = tmp_path / "feedback.txt"
    feedback.write_text("Retired surface probe.\n", encoding="utf-8")
    args = ["feedback", action, "--workspace", str(ws), "--repo-workdir", str(ROOT), "--json"]
    if action == "ingest":
        args += ["--feedback", str(feedback), "--source", "human"]
    if action == "resolve":
        args += ["--issue-id", "issue_retired", "--repair-plan-id", "rp_retired", "--reason", "probe"]
    before = _workspace_file_bytes(ws)

    rc = main(args)

    assert rc == 1
    assert capsys.readouterr().out.strip() == "legacy_workspace_unsupported"
    assert _workspace_file_bytes(ws) == before
    assert not _issues_path(ws).exists()
    assert not _plan_path(ws).exists()


@pytest.mark.parametrize(
    "action_args",
    [
        ["stage-complete", "--stage", "analyst", "--reason", "probe"],
        ["decide", "--stage", "analyst", "--decision", "delegate_repair", "--reason", "probe"],
    ],
)
def test_state_public_cli_is_retired_with_typed_rejection(tmp_path, capsys, action_args):
    # retired public `state` CLI surface; runtime-state transitions
    # are driven only through the deterministic runtime_state module seam used by
    # the tests below.
    ws = _write_workspace(tmp_path)
    before = _workspace_file_bytes(ws)

    rc = main([
        "state",
        *action_args,
        "--workspace",
        str(ws),
        "--repo-workdir",
        str(ROOT),
        "--json",
    ])

    assert rc == 1
    assert capsys.readouterr().out.strip() == "legacy_workspace_unsupported"
    assert _workspace_file_bytes(ws) == before


def test_human_feedback_ingest_creates_valid_feedback_issues(tmp_path):
    ws = _write_workspace(tmp_path)
    feedback = tmp_path / "feedback.txt"
    feedback.write_text("The audited brief needs a clearer citation.\n", encoding="utf-8")

    payload = ingest_feedback(
        workspace=ws,
        feedback_path=feedback,
        source="human",
        stage_id="analyst",
        artifact_id="audited_brief",
        category="citation_error",
        severity="blocking",
        repo_workdir=ROOT,
    )

    assert payload["ok"] is True
    issues = payload["feedback_issues"]["issues"]
    assert len(issues) == 1
    assert issues[0]["source"] == "human"
    assert issues[0]["status"] == "open"
    assert issues[0]["feedback_excerpt"]
    assert "evidence" not in issues[0]
    assert _issues_path(ws).exists()

    result = validate_feedback_workspace(workspace=ws, repo_workdir=ROOT)
    assert result["ok"] is True


def test_human_feedback_without_mapping_becomes_triage(tmp_path):
    ws = _write_workspace(tmp_path)
    feedback = tmp_path / "feedback.txt"
    feedback.write_text("This section does not answer the executive question.\n", encoding="utf-8")

    payload = ingest_feedback(
        workspace=ws,
        feedback_path=feedback,
        source="human",
        repo_workdir=ROOT,
    )

    issue = payload["feedback_issues"]["issues"][0]
    assert issue["status"] == "triage"
    assert issue["stage_id"] is None
    assert issue["artifact_id"] is None
    assert issue["category"] is None


def test_audit_feedback_ingest_preserves_audit_finding_fields(tmp_path):
    ws = _write_workspace(tmp_path)
    audit = tmp_path / "audit_report.json"
    audit.write_text(
        json.dumps({
            "findings": [
                {
                    "id": "AUDIT_001",
                    "blocking_level": "blocking",
                    "repair_owner": "editor",
                    "finding_type": "unsupported_claim",
                    "artifact_id": "audited_brief",
                    "summary": "Revenue claim is not supported by the ledger.",
                }
            ]
        }),
        encoding="utf-8",
    )

    payload = ingest_feedback(
        workspace=ws,
        feedback_path=audit,
        source="audit",
        repo_workdir=ROOT,
    )

    issue = payload["feedback_issues"]["issues"][0]
    assert issue["source"] == "audit"
    assert issue["status"] == "open"
    assert issue["stage_id"] == "editor"
    assert issue["artifact_id"] == "audited_brief"
    assert issue["metadata"]["blocking_level"] == "blocking"
    assert issue["metadata"]["repair_owner"] == "editor"
    assert issue["metadata"]["finding_type"] == "unsupported_claim"
    assert issue["metadata"]["source_finding_id"] == "AUDIT_001"


def test_audit_feedback_ingest_ignores_semantic_support_proposals(tmp_path):
    ws = _write_workspace(tmp_path)
    audit = tmp_path / "audit_report.json"
    audit.write_text(
        json.dumps({
            "findings": [
                {
                    "finding_id": "SAR-0001",
                    "finding_type": "semantic_support_proposal",
                    "severity": "low",
                    # Present but advisory: must NOT become an editor feedback issue.
                    "repair_owner": "editor",
                    "artifact_id": "audited_brief",
                    "summary": "Advisory: draft may overstate CL-0001.",
                },
                {
                    "finding_id": "AUDIT_001",
                    "blocking_level": "blocking",
                    "repair_owner": "editor",
                    "finding_type": "unsupported_claim",
                    "artifact_id": "audited_brief",
                    "summary": "Revenue claim is not supported by the ledger.",
                },
            ]
        }),
        encoding="utf-8",
    )

    payload = ingest_feedback(
        workspace=ws,
        feedback_path=audit,
        source="audit",
        repo_workdir=ROOT,
    )

    issues = payload["feedback_issues"]["issues"]
    # Only the real finding is ingested; the advisory proposal is dropped.
    assert [issue["metadata"]["source_finding_id"] for issue in issues] == ["AUDIT_001"]
    assert all(
        issue["metadata"].get("finding_type") != "semantic_support_proposal" for issue in issues
    )


def test_feedback_plan_creates_plan_and_marks_open_issues_planned(tmp_path):
    ws = _write_workspace(tmp_path)
    feedback = tmp_path / "feedback.txt"
    feedback.write_text("The audited brief needs a repair pass.\n", encoding="utf-8")
    ingest_feedback(
        workspace=ws,
        feedback_path=feedback,
        source="human",
        stage_id="analyst",
        artifact_id="audited_brief",
        category="clarity",
        severity="blocking",
        repo_workdir=ROOT,
    )

    payload = plan_feedback(workspace=ws, repo_workdir=ROOT)

    issue = payload["feedback_issues"]["issues"][0]
    plan = payload["repair_plan"]["repair_plans"][0]
    assert issue["status"] == "planned"
    assert plan["target_stage"] == "analyst"
    assert plan["target_artifacts"] == ["audited_brief"]
    assert plan["allowed_decision"] == "delegate_repair"
    assert plan["issue_ids"] == [issue["issue_id"]]
    assert _plan_path(ws).exists()

    event_types = [event["event_type"] for event in _events(ws)]
    assert "feedback_issue_created" in event_types
    assert "feedback_issue_planned" in event_types
    assert "repair_plan_created" in event_types


def test_feedback_ingest_rejects_invalid_explicit_refs(tmp_path):
    ws = _write_workspace(tmp_path)
    feedback = tmp_path / "feedback.txt"
    feedback.write_text("Bad mapping.\n", encoding="utf-8")

    with pytest.raises(RuntimeStateError) as excinfo:
        ingest_feedback(
            workspace=ws,
            feedback_path=feedback,
            source="human",
            stage_id="future-stage",
            repo_workdir=ROOT,
        )

    payload = excinfo.value.to_dict()
    assert payload["ok"] is False
    assert "Unknown feedback stage" in payload["error"]
    assert not _issues_path(ws).exists()


def test_feedback_ingest_bad_existing_contract_does_not_initialize_runtime(tmp_path):
    ws = _write_uninitialized_workspace(tmp_path)
    out = ws / "output" / "intermediate"
    out.mkdir(parents=True)
    _issues_path(ws).write_text(
        json.dumps({"schema_version": "bad", "issues": []}),
        encoding="utf-8",
    )
    feedback = tmp_path / "feedback.txt"
    feedback.write_text("Bad existing feedback state.\n", encoding="utf-8")

    with pytest.raises(RuntimeStateError) as excinfo:
        ingest_feedback(
            workspace=ws,
            feedback_path=feedback,
            source="human",
            stage_id="analyst",
            artifact_id="audited_brief",
            category="citation_error",
            severity="blocking",
            repo_workdir=ROOT,
        )

    payload = excinfo.value.to_dict()
    assert payload["ok"] is False
    assert "feedback_issues.json has an unsupported schema_version" in json.dumps(payload)
    assert not (ws / "output" / "intermediate" / "runtime_manifest.json").exists()
    assert not (ws / "output" / "intermediate" / "event_log.jsonl").exists()


def test_feedback_ingest_event_failure_leaves_feedback_file_unwritten(tmp_path, monkeypatch):
    ws = _write_workspace(tmp_path)
    feedback = tmp_path / "feedback.txt"
    feedback.write_text("The audited brief needs a clearer citation.\n", encoding="utf-8")

    def fail_append(*args, **kwargs):
        raise RuntimeStateError("event append failed")

    monkeypatch.setattr(feedback_state, "append_event", fail_append)

    with pytest.raises(RuntimeStateError, match="event append failed"):
        ingest_feedback(
            workspace=ws,
            feedback_path=feedback,
            source="human",
            stage_id="analyst",
            artifact_id="audited_brief",
            category="citation_error",
            severity="blocking",
            repo_workdir=ROOT,
        )

    assert not _issues_path(ws).exists()


def test_feedback_validate_rejects_repair_plan_referencing_missing_issue(tmp_path):
    ws = _write_uninitialized_workspace(tmp_path)
    out = ws / "output" / "intermediate"
    out.mkdir(parents=True)
    _issues_path(ws).write_text(
        json.dumps({
            "schema_version": "multi-agent-brief-feedback-issues/v1",
            "created_at": "2026-06-08T00:00:00+00:00",
            "updated_at": "2026-06-08T00:00:00+00:00",
            "issues": [],
        }),
        encoding="utf-8",
    )
    _plan_path(ws).write_text(
        json.dumps({
            "schema_version": "multi-agent-brief-repair-plan/v1",
            "created_at": "2026-06-08T00:00:00+00:00",
            "updated_at": "2026-06-08T00:00:00+00:00",
            "repair_plans": [
                {
                    "repair_plan_id": "rp_bad",
                    "created_at": "2026-06-08T00:00:00+00:00",
                    "updated_at": "2026-06-08T00:00:00+00:00",
                    "target_stage": "analyst",
                    "target_artifacts": ["audited_brief"],
                    "issue_ids": ["missing_issue"],
                    "allowed_decision": "delegate_repair",
                    "repair_scope": "minimal",
                    "instructions": [],
                    "requires_human_review": False,
                    "status": "planned",
                    "fingerprint": "bad",
                }
            ],
        }),
        encoding="utf-8",
    )

    result = validate_feedback_workspace(workspace=ws, repo_workdir=ROOT)

    assert result["ok"] is False
    assert "missing issues" in " ".join(result["errors"])


def test_feedback_plan_event_failure_leaves_feedback_state_unchanged(tmp_path, monkeypatch):
    ws = _write_workspace(tmp_path)
    feedback = tmp_path / "feedback.txt"
    feedback.write_text("The audited brief needs a repair pass.\n", encoding="utf-8")
    ingest_feedback(
        workspace=ws,
        feedback_path=feedback,
        source="human",
        stage_id="analyst",
        artifact_id="audited_brief",
        category="clarity",
        severity="blocking",
        repo_workdir=ROOT,
    )
    before = json.loads(_issues_path(ws).read_text(encoding="utf-8"))

    def fail_append(*args, **kwargs):
        raise RuntimeStateError("event append failed")

    monkeypatch.setattr(feedback_state, "append_event", fail_append)

    with pytest.raises(RuntimeStateError, match="event append failed"):
        plan_feedback(workspace=ws, repo_workdir=ROOT)

    after = json.loads(_issues_path(ws).read_text(encoding="utf-8"))
    assert after == before
    assert not _plan_path(ws).exists()


def test_state_check_feedback_blocks_only_current_stage(tmp_path):
    ws = _write_workspace(tmp_path)
    feedback = tmp_path / "feedback.txt"
    feedback.write_text("The analyst draft needs repair before continuing.\n", encoding="utf-8")
    ingest_feedback(
        workspace=ws,
        feedback_path=feedback,
        source="human",
        stage_id="analyst",
        artifact_id="audited_brief",
        category="clarity",
        severity="blocking",
        repo_workdir=ROOT,
    )

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    assert state["workflow_state"]["current_stage"] == "doctor"
    assert state["workflow_state"]["blocked"] is False

    _advance_to_analyst(ws)

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    workflow = state["workflow_state"]
    assert workflow["current_stage"] == "analyst"
    assert workflow["blocked"] is True
    assert "blocking feedback issues without a repair plan" in workflow["blocking_reason"]


def test_planned_blocking_issue_rejects_continue_until_resolved(tmp_path):
    ws = _write_workspace(tmp_path)
    feedback = tmp_path / "feedback.txt"
    feedback.write_text("The analyst draft needs repair before continuing.\n", encoding="utf-8")
    ingest_feedback(
        workspace=ws,
        feedback_path=feedback,
        source="human",
        stage_id="analyst",
        artifact_id="audited_brief",
        category="clarity",
        severity="blocking",
        repo_workdir=ROOT,
    )
    plan_feedback(workspace=ws, repo_workdir=ROOT)
    issues = json.loads(_issues_path(ws).read_text(encoding="utf-8"))["issues"]
    plans = json.loads(_plan_path(ws).read_text(encoding="utf-8"))["repair_plans"]
    issue_id = issues[0]["issue_id"]
    repair_plan_id = plans[0]["repair_plan_id"]

    _advance_to_analyst(ws)
    (_intermediate(ws) / "audited_brief.md").write_text("# Audited brief\n", encoding="utf-8")

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    workflow = state["workflow_state"]
    assert workflow["current_stage"] == "analyst"
    assert workflow["blocked"] is True
    assert "unresolved blocking feedback issues" in workflow["blocking_reason"]

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_stage_transaction(
            workspace=ws,
            stage_id="analyst",
            reason="skip repair",
            repo_workdir=ROOT,
        )
    payload = excinfo.value.to_dict()
    assert payload["error_code"] == "E_ILLEGAL_TRANSITION"
    assert "unresolved blocking feedback issues" in " ".join(payload["details"]["blocking_reasons"])

    resolved = resolve_feedback(
        workspace=ws,
        issue_id=issue_id,
        repair_plan_id=repair_plan_id,
        reason="Repair was handled by runtime subagent.",
        repo_workdir=ROOT,
    )
    assert resolved["feedback_issues"]["issues"][0]["status"] == "resolved"
    assert resolved["repair_plan"]["repair_plans"][0]["status"] == "completed"
    event_types = [event["event_type"] for event in _events(ws)]
    assert "feedback_issue_resolved" in event_types
    assert "repair_plan_completed" in event_types

    # Completing the stage succeeds once the blocking feedback issue is resolved.
    complete_stage_transaction(
        workspace=ws,
        stage_id="analyst",
        reason="repair resolved",
        repo_workdir=ROOT,
    )


def test_resolving_one_issue_does_not_complete_shared_repair_plan(tmp_path):
    ws = _write_workspace(tmp_path)
    feedback_a = tmp_path / "feedback-a.txt"
    feedback_b = tmp_path / "feedback-b.txt"
    feedback_a.write_text("The analyst draft needs clearer citations.\n", encoding="utf-8")
    feedback_b.write_text("The analyst draft has confusing wording.\n", encoding="utf-8")

    for feedback_path, category in (
        (feedback_a, "citation_error"),
        (feedback_b, "clarity"),
    ):
        ingest_feedback(
            workspace=ws,
            feedback_path=feedback_path,
            source="human",
            stage_id="analyst",
            artifact_id="audited_brief",
            category=category,
            severity="blocking",
            repo_workdir=ROOT,
        )

    planned = plan_feedback(workspace=ws, repo_workdir=ROOT)
    plan = planned["repair_plan"]["repair_plans"][0]
    issue_ids = plan["issue_ids"]
    assert len(issue_ids) == 2
    assert plan["status"] == "planned"

    partial = resolve_feedback(
        workspace=ws,
        issue_id=issue_ids[0],
        repair_plan_id=plan["repair_plan_id"],
        reason="First issue resolved.",
        repo_workdir=ROOT,
    )
    partial_plan = partial["repair_plan"]["repair_plans"][0]
    statuses = {
        issue["issue_id"]: issue["status"]
        for issue in partial["feedback_issues"]["issues"]
    }
    assert statuses[issue_ids[0]] == "resolved"
    assert statuses[issue_ids[1]] == "planned"
    assert partial_plan["status"] == "planned"
    assert "completed_at" not in partial_plan
    assert "completion_reason" not in partial_plan
    event_types = [event["event_type"] for event in _events(ws)]
    assert "feedback_issue_resolved" in event_types
    assert "repair_plan_completed" not in event_types

    completed = resolve_feedback(
        workspace=ws,
        issue_id=issue_ids[1],
        repair_plan_id=plan["repair_plan_id"],
        reason="All shared plan issues resolved.",
        repo_workdir=ROOT,
    )
    completed_plan = completed["repair_plan"]["repair_plans"][0]
    assert completed_plan["status"] == "completed"
    assert completed_plan["completion_reason"] == "All shared plan issues resolved."
    event_types = [event["event_type"] for event in _events(ws)]
    assert event_types.count("repair_plan_completed") == 1


def test_missing_delta_audit_report_is_not_blocking_without_active_repair(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(runtime="operator", workspace=ws, repo_workdir=ROOT)

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)

    registry = state["artifact_registry"]["artifacts"]
    assert registry["delta_audit_report"]["status"] == "expected"
    assert state["workflow_state"]["blocked"] is False


def test_feedback_commands_do_not_modify_stage_output_artifacts(tmp_path):
    ws = _write_workspace(tmp_path)
    feedback = tmp_path / "feedback.txt"
    feedback.write_text("The audited brief needs a repair pass.\n", encoding="utf-8")

    ingest_feedback(
        workspace=ws,
        feedback_path=feedback,
        source="human",
        stage_id="analyst",
        artifact_id="audited_brief",
        category="clarity",
        severity="blocking",
        repo_workdir=ROOT,
    )
    plan_feedback(workspace=ws, repo_workdir=ROOT)

    assert not (ws / "output" / "brief.md").exists()
    assert not (ws / "output" / "intermediate" / "candidate_claims.json").exists()
    assert not (ws / "output" / "intermediate" / "screened_candidates.json").exists()
    assert not (ws / "output" / "intermediate" / "claim_ledger.json").exists()
    assert not (ws / "output" / "intermediate" / "audited_brief.md").exists()
    assert not (ws / "output" / "intermediate" / "audit_report.json").exists()
    assert not (ws / "output" / "intermediate" / "delta_audit_report.json").exists()


def test_delegate_repair_cannot_target_non_current_stage(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(runtime="operator", workspace=ws, repo_workdir=ROOT)

    with pytest.raises(RuntimeStateError) as excinfo:
        record_decision(
            workspace=ws,
            stage_id="analyst",
            decision="delegate_repair",
            reason="future repair",
            repo_workdir=ROOT,
        )

    payload = excinfo.value.to_dict()
    assert "does not match current stage" in payload["error"]
