from __future__ import annotations

import json
from pathlib import Path

import pytest

from multi_agent_brief.improvement.contract import read_ledger_text
from multi_agent_brief.improvement.state import (
    ImprovementLedgerError,
    approve_improvement,
    improvement_ledger_path,
    improvement_stats,
    propose_improvement,
    reject_improvement,
    revert_improvement,
)
from multi_agent_brief.feedback.feedback_state import ingest_feedback
from multi_agent_brief.orchestrator.runtime_state import RuntimeStateError, initialize_runtime_state


ROOT = Path(__file__).resolve().parent.parent


def _workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "config.yaml").write_text(
        """
project:
  name: "Improvement Test"
  company: "Demo Holdings"
  industry: "testing"
  language: "en"
  audience: "management"
report:
  cadence: "weekly"
input:
  path: "input"
output:
  path: "output"
""".strip(),
        encoding="utf-8",
    )
    (ws / "sources.yaml").write_text("manual:\n  sources: []\n", encoding="utf-8")
    (ws / "user.md").write_text("# User\n\nNeed concise management guidance.\n", encoding="utf-8")
    (ws / "input").mkdir()
    return ws


def _ledger_lines(ws: Path) -> list[dict]:
    path = improvement_ledger_path(ws)
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _events(ws: Path) -> list[dict]:
    path = ws / "output" / "intermediate" / "event_log.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_feedback_issue(ws: Path, *, run_id: str = "mabw-test-run") -> None:
    path = ws / "output" / "intermediate" / "feedback_issues.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": "multi-agent-brief-feedback-issues/v1",
                "created_at": "2026-06-10T00:00:00Z",
                "updated_at": "2026-06-10T00:00:00Z",
                "issues": [
                    {
                        "issue_id": "fi-0001",
                        "source": "audit",
                        "severity": "high",
                        "stage_id": "auditor",
                        "artifact_id": "audited_brief",
                        "category": "audience_mismatch",
                        "summary": "Brief does not answer the executive audience question.",
                        "feedback_excerpt": "Brief does not answer the executive audience question.",
                        "raw_feedback_ref": "output/intermediate/quality_gate_report.json#finding:FINDING_001",
                        "source_artifact": "output/intermediate/quality_gate_report.json",
                        "supporting_context": [],
                        "metadata": {
                            "run_id": run_id,
                            "source_finding_id": "FINDING_001",
                            "finding_type": "target_relevance",
                            "blocking_level": "blocking",
                        },
                        "status": "open",
                        "created_at": "2026-06-10T00:00:00Z",
                        "updated_at": "2026-06-10T00:00:00Z",
                        "fingerprint": "abc",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_propose_human_creates_ledger_without_runtime_event(tmp_path):
    ws = _workspace(tmp_path)

    state = propose_improvement(
        workspace=ws,
        guidance="Lead with the decision-relevant number when evidence supports it.",
        category="audience_mismatch",
        scope="brief",
        source_summary="Operator-created audience guidance proposal.",
    )

    assert state["entry"]["entry_id"] == "AG-0001"
    assert state["entry"]["status"] == "proposed"
    assert state["event_recorded"] is False
    assert state["event_reason"] == "no_runtime_state"
    assert not (ws / "output" / "intermediate" / "event_log.jsonl").exists()
    assert read_ledger_text(improvement_ledger_path(ws).read_text(encoding="utf-8")).current_entries["AG-0001"]["status"] == "proposed"


def test_propose_requires_source_summary_for_human_evidence(tmp_path):
    ws = _workspace(tmp_path)

    with pytest.raises(ImprovementLedgerError, match="source-summary"):
        propose_improvement(
            workspace=ws,
            guidance="Lead with the decision-relevant number when evidence supports it.",
            category="audience_mismatch",
            scope="brief",
        )

    assert not improvement_ledger_path(ws).exists()


def test_propose_rejects_source_summary_with_from_issue(tmp_path):
    ws = _workspace(tmp_path)

    with pytest.raises(ImprovementLedgerError, match="mutually exclusive"):
        propose_improvement(
            workspace=ws,
            from_issue="fi-0001",
            source_summary="Operator-created audience guidance proposal.",
            guidance="Lead with the decision-relevant number when evidence supports it.",
            category="audience_mismatch",
            scope="brief",
        )

    assert not improvement_ledger_path(ws).exists()


def test_propose_from_issue_freezes_minimal_feedback_evidence(tmp_path):
    ws = _workspace(tmp_path)
    _write_feedback_issue(ws, run_id="mabw-run-001")

    state = propose_improvement(
        workspace=ws,
        from_issue="fi-0001",
        guidance="Put the audience implication before methodology details.",
        category="audience_mismatch",
        scope="executive_summary",
    )

    evidence = state["entry"]["source_evidence"][0]
    assert evidence == {
        "source_type": "feedback_issue",
        "summary": "Brief does not answer the executive audience question.",
        "run_id": "mabw-run-001",
        "issue_id": "fi-0001",
        "origin": {
            "control_file": "quality_gate_report.json",
            "source_item_id": "FINDING_001",
            "finding_type": "target_relevance",
            "blocking_level": "blocking",
        },
    }
    assert state["entry"]["change"]["guidance_text"] == "Put the audience implication before methodology details."


def test_propose_from_real_feedback_issue_uses_original_run_after_reset(tmp_path):
    ws = _workspace(tmp_path)
    feedback = tmp_path / "feedback.txt"
    feedback.write_text("The brief does not answer the executive audience question.", encoding="utf-8")
    ingest_feedback(
        workspace=ws,
        feedback_path=feedback,
        source="human",
        stage_id="auditor",
        artifact_id="audited_brief",
        category="audience_mismatch",
        severity="high",
        repo_workdir=ROOT,
    )
    issues_path = ws / "output" / "intermediate" / "feedback_issues.json"
    issues_payload = json.loads(issues_path.read_text(encoding="utf-8"))
    issue = issues_payload["issues"][0]
    old_run_id = issue["metadata"]["run_id"]

    # Simulate a pre-fix feedback issue that lacks persisted run_id, then reset.
    issue["metadata"].pop("run_id")
    issues_path.write_text(json.dumps(issues_payload, ensure_ascii=False), encoding="utf-8")
    reset_state = initialize_runtime_state(workspace=ws, repo_workdir=ROOT, reset_state=True)
    assert reset_state["manifest"]["run_id"] != old_run_id

    state = propose_improvement(
        workspace=ws,
        from_issue=issue["issue_id"],
        guidance="Put the audience implication before methodology details.",
        category="audience_mismatch",
        scope="brief",
    )

    evidence = state["entry"]["source_evidence"][0]
    assert evidence["run_id"] == old_run_id
    assert evidence["run_id"] != reset_state["manifest"]["run_id"]


def test_propose_from_issue_rejects_invalid_feedback_contract(tmp_path):
    ws = _workspace(tmp_path)
    path = ws / "output" / "intermediate" / "feedback_issues.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({
            "schema_version": "bad",
            "issues": [
                {
                    "issue_id": "fi-0001",
                    "source": "human",
                    "severity": "high",
                    "stage_id": "auditor",
                    "artifact_id": "audited_brief",
                    "category": "audience_mismatch",
                    "summary": "Looks valid but schema is wrong.",
                    "metadata": {"run_id": "mabw-old"},
                    "status": "open",
                }
            ],
        }),
        encoding="utf-8",
    )

    with pytest.raises(ImprovementLedgerError, match="failed contract validation"):
        propose_improvement(
            workspace=ws,
            from_issue="fi-0001",
            guidance="Put the audience implication before methodology details.",
            category="audience_mismatch",
            scope="brief",
        )

    assert not improvement_ledger_path(ws).exists()


def test_raw_gate_report_cannot_be_used_as_direct_issue_input(tmp_path):
    ws = _workspace(tmp_path)
    (ws / "output" / "intermediate").mkdir(parents=True)
    (ws / "output" / "intermediate" / "quality_gate_report.json").write_text(
        json.dumps({"findings": [{"finding_id": "FINDING_001"}]}),
        encoding="utf-8",
    )

    with pytest.raises(ImprovementLedgerError, match="feedback_issues.json"):
        propose_improvement(
            workspace=ws,
            from_issue="FINDING_001",
            guidance="Put audience implication first.",
            category="audience_mismatch",
            scope="brief",
        )

    assert not improvement_ledger_path(ws).exists()


def test_transitions_append_revisions_and_runtime_events_when_state_exists(tmp_path):
    ws = _workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)

    proposed = propose_improvement(
        workspace=ws,
        guidance="Lead with the decision-relevant number when evidence supports it.",
        category="audience_mismatch",
        scope="brief",
        source_summary="Operator-created audience guidance proposal.",
    )
    approved = approve_improvement(workspace=ws, entry_id="AG-0001", approved_by="stahl")
    reverted = revert_improvement(workspace=ws, entry_id="AG-0001", reverted_by="stahl", reason="No longer desired.")

    lines = _ledger_lines(ws)
    assert [item["status"] for item in lines] == ["proposed", "approved", "reverted"]
    assert approved["entry"]["change"] == proposed["entry"]["change"]
    assert approved["entry"]["source_evidence"] == proposed["entry"]["source_evidence"]
    assert reverted["entry"]["previous_revision_sha256"]
    assert [event["event_type"] for event in _events(ws)] == [
        "run_initialized",
        "improvement_proposed",
        "improvement_approved",
        "improvement_reverted",
    ]


def test_invalid_transition_writes_neither_ledger_revision_nor_event(tmp_path):
    ws = _workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    propose_improvement(
        workspace=ws,
        guidance="Lead with the decision-relevant number when evidence supports it.",
        category="audience_mismatch",
        scope="brief",
        source_summary="Operator-created audience guidance proposal.",
    )
    reject_improvement(workspace=ws, entry_id="AG-0001", rejected_by="stahl", reason="Not appropriate.")
    before_ledger = improvement_ledger_path(ws).read_text(encoding="utf-8")
    before_events = (ws / "output" / "intermediate" / "event_log.jsonl").read_text(encoding="utf-8")

    with pytest.raises(ImprovementLedgerError, match="failed validation"):
        approve_improvement(workspace=ws, entry_id="AG-0001", approved_by="stahl")

    assert improvement_ledger_path(ws).read_text(encoding="utf-8") == before_ledger
    assert (ws / "output" / "intermediate" / "event_log.jsonl").read_text(encoding="utf-8") == before_events


def test_event_append_failure_after_ledger_append_returns_warning(tmp_path, monkeypatch):
    ws = _workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)

    def fail_append_event(**_kwargs):
        raise RuntimeStateError("event append failed")

    monkeypatch.setattr("multi_agent_brief.improvement.state.append_event", fail_append_event)

    state = propose_improvement(
        workspace=ws,
        guidance="Lead with the decision-relevant number when evidence supports it.",
        category="audience_mismatch",
        scope="brief",
        source_summary="Operator-created audience guidance proposal.",
    )

    assert state["event_recorded"] is False
    assert state["event_reason"] == "event_append_failed"
    assert "event append failed" in state["event_error"]
    assert _ledger_lines(ws)[0]["status"] == "proposed"


def test_damaged_runtime_manifest_prevents_ledger_append(tmp_path):
    ws = _workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    (ws / "output" / "intermediate" / "runtime_manifest.json").write_text("{bad json", encoding="utf-8")

    with pytest.raises(ImprovementLedgerError, match="runtime_manifest.json is not valid JSON"):
        propose_improvement(
            workspace=ws,
            guidance="Lead with the decision-relevant number when evidence supports it.",
            category="audience_mismatch",
            scope="brief",
            source_summary="Operator-created audience guidance proposal.",
        )

    assert not improvement_ledger_path(ws).exists()


def test_damaged_event_log_prevents_ledger_append(tmp_path):
    ws = _workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    (ws / "output" / "intermediate" / "event_log.jsonl").write_text("{bad json}\n", encoding="utf-8")

    with pytest.raises(ImprovementLedgerError, match="event_log.jsonl contains invalid JSON"):
        propose_improvement(
            workspace=ws,
            guidance="Lead with the decision-relevant number when evidence supports it.",
            category="audience_mismatch",
            scope="brief",
            source_summary="Operator-created audience guidance proposal.",
        )

    assert not improvement_ledger_path(ws).exists()


def test_improve_write_requires_workspace_config(tmp_path):
    ws = tmp_path / "empty"
    ws.mkdir()

    with pytest.raises(ImprovementLedgerError, match="config.yaml"):
        propose_improvement(
            workspace=ws,
            guidance="Lead with the decision-relevant number when evidence supports it.",
            category="audience_mismatch",
            scope="brief",
            source_summary="Operator-created audience guidance proposal.",
        )

    assert not (ws / "improvement" / "ledger.jsonl").exists()


def test_stats_are_ledger_only_counts(tmp_path):
    ws = _workspace(tmp_path)
    propose_improvement(
        workspace=ws,
        guidance="Lead with the decision-relevant number when evidence supports it.",
        category="audience_mismatch",
        scope="brief",
        source_summary="Operator-created audience guidance proposal.",
    )
    approve_improvement(workspace=ws, entry_id="AG-0001", approved_by="stahl")

    stats = improvement_stats(workspace=ws)

    assert stats["approved_count"] == 1
    assert stats["eligible_for_materialization_count"] == 1
    assert "active_approved_count" not in stats
    assert stats["counts_by_category"] == {"audience_mismatch": 1}
    assert stats["counts_by_source_type"] == {"human_feedback": 1}


def test_approve_does_not_materialize_runtime_memory_or_handoff(tmp_path):
    ws = _workspace(tmp_path)
    propose_improvement(
        workspace=ws,
        guidance="Lead with the decision-relevant number when evidence supports it.",
        category="audience_mismatch",
        scope="brief",
        source_summary="Operator-created audience guidance proposal.",
    )
    approve_improvement(workspace=ws, entry_id="AG-0001", approved_by="stahl")

    assert not (ws / "improvement" / "memory.md").exists()
    assert not (ws / "output" / "intermediate" / "improvement_memory_snapshot.md").exists()
    assert not (ws / "audience_profile.md").exists()
    assert not (ws / "output" / "intermediate" / "audience_profile_snapshot.md").exists()
    assert not (ws / "output" / "intermediate" / "agent_handoff.json").exists()
