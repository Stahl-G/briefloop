from __future__ import annotations

import json
from pathlib import Path

import pytest

from multi_agent_brief.improvement.contract import canonical_json, read_ledger_text, revision_sha256
from multi_agent_brief.improvement.state import (
    ImprovementLedgerError,
    approve_improvement,
    improvement_ledger_path,
    improvement_stats,
    validate_improvement_ledger,
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


def _ledger_text(ws: Path) -> str:
    path = improvement_ledger_path(ws)
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _events(ws: Path) -> list[dict]:
    path = ws / "output" / "intermediate" / "event_log.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_feedback_issue(
    ws: Path,
    *,
    run_id: str = "mabw-test-run",
    source: str = "human",
    category: str = "audience_mismatch",
    finding_type: str = "audience_mismatch",
    source_artifact: str = "",
) -> None:
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
                        "source": source,
                        "severity": "high",
                        "stage_id": "auditor",
                        "artifact_id": "audited_brief",
                        "category": category,
                        "summary": "Brief does not answer the executive audience question.",
                        "feedback_excerpt": "Brief does not answer the executive audience question.",
                        "raw_feedback_ref": "output/intermediate/quality_gate_report.json#finding:FINDING_001",
                        "source_artifact": source_artifact,
                        "supporting_context": [],
                        "metadata": {
                            "run_id": run_id,
                            "source_finding_id": "FINDING_001",
                            "finding_type": finding_type,
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


def _propose_and_approve(
    ws: Path,
    *,
    guidance: str = "Lead with the decision-relevant number when evidence supports it.",
    category: str = "audience_mismatch",
    scope: str = "brief",
) -> str:
    state = propose_improvement(
        workspace=ws,
        guidance=guidance,
        category=category,
        scope=scope,
        source_summary="Operator-created audience guidance proposal.",
    )
    entry_id = str(state["entry"]["entry_id"])
    approve_improvement(workspace=ws, entry_id=entry_id, approved_by="stahl")
    return entry_id


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


def test_propose_human_records_origin_runtime_when_runtime_state_exists(tmp_path):
    ws = _workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT, runtime="deepseek-v4-flash")

    state = propose_improvement(
        workspace=ws,
        guidance="Lead with the decision-relevant number when evidence supports it.",
        category="audience_mismatch",
        scope="brief",
        source_summary="Operator-created audience guidance proposal.",
    )

    evidence = state["entry"]["source_evidence"][0]
    assert evidence["source_type"] == "human_feedback"
    assert evidence["origin"] == {"origin_runtime": "deepseek-v4-flash"}
    assert state["event_recorded"] is True


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
            "source_item_id": "FINDING_001",
            "finding_type": "audience_mismatch",
            "blocking_level": "blocking",
            "issue_category": "audience_mismatch",
            "issue_source": "human",
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


def test_propose_from_issue_rejects_audit_gate_target_relevance_without_writing(tmp_path):
    ws = _workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_feedback_issue(
        ws,
        run_id="mabw-run-001",
        source="audit",
        category="audience_mismatch",
        finding_type="target_relevance",
        source_artifact="output/intermediate/quality_gate_report.json",
    )
    before_events = (ws / "output" / "intermediate" / "event_log.jsonl").read_text(encoding="utf-8")

    with pytest.raises(ImprovementLedgerError) as excinfo:
        propose_improvement(
            workspace=ws,
            from_issue="fi-0001",
            guidance="Put the target audience implication first.",
            category="audience_mismatch",
            scope="brief",
        )

    assert excinfo.value.details["reason_code"] == "target_relevance_rewrite_required"
    assert "rewrite" in excinfo.value.details["message"]
    assert not improvement_ledger_path(ws).exists()
    assert (ws / "output" / "intermediate" / "event_log.jsonl").read_text(encoding="utf-8") == before_events


def test_propose_from_issue_rejects_format_field_missing_without_writing(tmp_path):
    ws = _workspace(tmp_path)
    _write_feedback_issue(
        ws,
        run_id="mabw-run-001",
        source="audit",
        category="formatting",
        finding_type="format_field_missing",
        source_artifact="output/intermediate/audit_report.json",
    )

    with pytest.raises(ImprovementLedgerError) as excinfo:
        propose_improvement(
            workspace=ws,
            from_issue="fi-0001",
            guidance="Always include this field in the final brief.",
            category="structure",
            scope="brief",
        )

    assert excinfo.value.details["reason_code"] == "repair_only_finding_type"
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


def test_propose_supersedes_approved_materializable_entry(tmp_path):
    ws = _workspace(tmp_path)
    _propose_and_approve(ws, guidance="Lead with the key number first.")

    state = propose_improvement(
        workspace=ws,
        guidance="Lead with the key number, then explain what changed.",
        category="audience_mismatch",
        scope="brief",
        source_summary="Operator-created replacement guidance.",
        supersedes="AG-0001",
    )

    assert state["entry"]["entry_id"] == "AG-0002"
    assert state["entry"]["supersedes_id"] == "AG-0001"
    assert state["warnings"] == []
    approved = approve_improvement(workspace=ws, entry_id="AG-0002", approved_by="stahl")
    assert approved["entry"]["supersedes_id"] == "AG-0001"
    stats = improvement_stats(workspace=ws)
    assert stats["approved_count"] == 2
    assert stats["superseded_count"] == 1
    assert stats["eligible_for_materialization_count"] == 1


def test_propose_supersedes_rejects_invalid_targets(tmp_path):
    ws = _workspace(tmp_path)
    propose_improvement(
        workspace=ws,
        guidance="Lead with the key number first.",
        category="audience_mismatch",
        scope="brief",
        source_summary="Operator-created guidance.",
    )

    with pytest.raises(ImprovementLedgerError, match="currently approved"):
        propose_improvement(
            workspace=ws,
            guidance="Replacement guidance.",
            category="audience_mismatch",
            scope="brief",
            source_summary="Operator-created guidance.",
            supersedes="AG-0001",
        )
    with pytest.raises(ImprovementLedgerError, match="Unknown supersedes target"):
        propose_improvement(
            workspace=ws,
            guidance="Replacement guidance.",
            category="audience_mismatch",
            scope="brief",
            source_summary="Operator-created guidance.",
            supersedes="AG-9999",
        )

    nonmaterializable_base = tmp_path / "nonmaterializable"
    nonmaterializable_base.mkdir()
    ws2 = _workspace(nonmaterializable_base)
    _append_approved_legacy_feedback_issue_entry(
        ws2,
        entry_id="AG-0001",
        finding_type="format_field_missing",
        issue_category="formatting",
    )
    with pytest.raises(ImprovementLedgerError, match="materializable"):
        propose_improvement(
            workspace=ws2,
            guidance="Replacement guidance.",
            category="audience_mismatch",
            scope="brief",
            source_summary="Operator-created guidance.",
            supersedes="AG-0001",
        )


def test_duplicate_and_forked_supersession_warnings(tmp_path):
    ws = _workspace(tmp_path)
    _propose_and_approve(ws, guidance="Lead with the key number first.")

    duplicate = propose_improvement(
        workspace=ws,
        guidance="Lead with the decision-relevant number first.",
        category="audience_mismatch",
        scope="brief",
        source_summary="Operator-created guidance.",
    )
    assert duplicate["warnings"][0]["code"] == "possible_duplicate_active_guidance"
    assert duplicate["warnings"][0]["entry_id"] == "AG-0001"

    replacement = propose_improvement(
        workspace=ws,
        guidance="Replacement guidance.",
        category="audience_mismatch",
        scope="brief",
        source_summary="Operator-created guidance.",
        supersedes="AG-0001",
    )
    approve_improvement(workspace=ws, entry_id=replacement["entry"]["entry_id"], approved_by="stahl")

    fork = propose_improvement(
        workspace=ws,
        guidance="Another replacement guidance.",
        category="audience_mismatch",
        scope="brief",
        source_summary="Operator-created guidance.",
        supersedes="AG-0001",
    )
    assert any(item["code"] == "supersedes_target_already_superseded" for item in fork["warnings"])


def test_revert_superseder_warns_about_reexposed_entry(tmp_path):
    ws = _workspace(tmp_path)
    _propose_and_approve(ws, guidance="Original guidance.")
    replacement = propose_improvement(
        workspace=ws,
        guidance="Replacement guidance.",
        category="audience_mismatch",
        scope="brief",
        source_summary="Operator-created guidance.",
        supersedes="AG-0001",
    )
    approve_improvement(workspace=ws, entry_id=replacement["entry"]["entry_id"], approved_by="stahl")

    reverted = revert_improvement(
        workspace=ws,
        entry_id=replacement["entry"]["entry_id"],
        reverted_by="stahl",
        reason="No longer desired.",
    )

    assert reverted["warnings"] == [{
        "code": "supersession_reexposes_entry",
        "entry_id": "AG-0001",
        "message": "Reverting AG-0002 re-exposes AG-0001.",
    }]
    assert improvement_stats(workspace=ws)["eligible_for_materialization_count"] == 1


def test_validate_and_stats_use_product_definition_materialization(tmp_path):
    ws = _workspace(tmp_path)
    propose_improvement(
        workspace=ws,
        guidance="Lead with the decision-relevant number when evidence supports it.",
        category="audience_mismatch",
        scope="brief",
        source_summary="Operator-created audience guidance proposal.",
    )
    approve_improvement(workspace=ws, entry_id="AG-0001", approved_by="stahl")
    _append_approved_legacy_feedback_issue_entry(
        ws,
        entry_id="AG-0002",
        finding_type="format_field_missing",
        issue_category="formatting",
    )
    propose_improvement(
        workspace=ws,
        guidance="Use the best-fitting audience convention when no narrower category applies.",
        category="other",
        scope="brief",
        source_summary="Operator-created audience guidance proposal.",
    )
    approve_improvement(workspace=ws, entry_id="AG-0003", approved_by="stahl")

    validation = validate_improvement_ledger(workspace=ws)
    by_entry = {item["entry_id"]: item for item in validation["materialization_diagnostics"]}

    assert validation["ok"] is True
    assert by_entry["AG-0001"]["materializable"] is True
    assert by_entry["AG-0002"]["materializable"] is False
    assert by_entry["AG-0002"]["requires_product_definition_review"] is True
    assert by_entry["AG-0002"]["non_materializable_reason"] == "repair_only_finding_type"
    assert by_entry["AG-0003"]["materializable"] is True
    assert by_entry["AG-0003"]["reason_code"] == "category_other"

    ledger_before = _ledger_text(ws)
    stats = improvement_stats(workspace=ws)
    assert stats["approved_count"] == 3
    assert stats["eligible_for_materialization_count"] == 2
    assert stats["counts_by_category"]["other"] == 1
    assert _ledger_text(ws) == ledger_before


def test_validate_and_stats_reject_legacy_feedback_issue_without_origin(tmp_path):
    ws = _workspace(tmp_path)
    _append_approved_legacy_feedback_issue_entry(
        ws,
        entry_id="AG-0001",
        finding_type="",
        issue_category="",
        include_origin=False,
    )

    validation = validate_improvement_ledger(workspace=ws)
    diagnostic = validation["materialization_diagnostics"][0]

    assert validation["ok"] is True
    assert diagnostic["entry_id"] == "AG-0001"
    assert diagnostic["materializable"] is False
    assert diagnostic["requires_product_definition_review"] is True
    assert diagnostic["non_materializable_reason"] == "missing_feedback_issue_product_definition_origin"
    assert improvement_stats(workspace=ws)["eligible_for_materialization_count"] == 0


def test_validate_warns_when_non_materializable_superseder_suppresses_target(tmp_path):
    ws = _workspace(tmp_path)
    _propose_and_approve(ws, guidance="Original materializable guidance.")
    _append_approved_legacy_feedback_issue_entry(
        ws,
        entry_id="AG-0002",
        finding_type="format_field_missing",
        issue_category="formatting",
        supersedes_id="AG-0001",
    )

    validation = validate_improvement_ledger(workspace=ws)
    by_entry = {item["entry_id"]: item for item in validation["materialization_diagnostics"]}
    warning = next(
        item
        for item in validation["materialization_diagnostics"]
        if item["reason_code"] == "non_materializable_superseder_suppresses_target"
    )

    assert by_entry["AG-0001"]["materializable"] is False
    assert by_entry["AG-0001"]["non_materializable_reason"] == "superseded_by_active_guidance"
    assert by_entry["AG-0002"]["materializable"] is False
    assert warning["entry_id"] == "AG-0002"
    assert warning["supersedes_id"] == "AG-0001"
    assert improvement_stats(workspace=ws)["eligible_for_materialization_count"] == 0


def test_damaged_ledger_no_final_newline_rejects_all_transition_writes(tmp_path):
    for transition in ("propose", "approve", "reject", "revert"):
        base = tmp_path / transition
        base.mkdir()
        ws = _workspace(base)
        if transition == "propose":
            _write_valid_proposed_ledger(ws, final_newline=False)
        elif transition in {"approve", "reject"}:
            _write_valid_proposed_ledger(ws, final_newline=False)
        else:
            _write_valid_approved_ledger(ws, final_newline=False)
        before = _ledger_text(ws)

        with pytest.raises(ImprovementLedgerError, match="failed validation"):
            _run_transition(ws, transition)

        assert _ledger_text(ws) == before


def test_damaged_ledger_corrupt_middle_or_trailing_line_rejects_writes(tmp_path):
    valid = _valid_revision()
    cases = {
        "middle": canonical_json(valid) + "\n{bad json}\n" + canonical_json(_valid_revision(entry_id="AG-0002")) + "\n",
        "trailing": canonical_json(valid) + "\n{bad json}\n",
    }
    for name, text in cases.items():
        base = tmp_path / name
        base.mkdir()
        ws = _workspace(base)
        path = improvement_ledger_path(ws)
        path.parent.mkdir(parents=True)
        path.write_text(text, encoding="utf-8")

        with pytest.raises(ImprovementLedgerError, match="failed validation"):
            propose_improvement(
                workspace=ws,
                guidance="Lead with the decision-relevant number when evidence supports it.",
                category="audience_mismatch",
                scope="brief",
                source_summary="Operator-created audience guidance proposal.",
            )

        assert path.read_text(encoding="utf-8") == text


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


def _valid_revision(
    *,
    entry_id: str = "AG-0001",
    status: str = "proposed",
    revision: int = 1,
    previous_revision_sha256=None,
) -> dict:
    payload = {
        "schema_version": "multi-agent-brief-improvement-ledger/v1",
        "entry_id": entry_id,
        "revision": revision,
        "previous_revision_sha256": previous_revision_sha256,
        "created_at": "2026-06-10T00:00:00Z",
        "status": status,
        "level": 2,
        "target_kind": "audience_guidance",
        "change": {
            "category": "audience_mismatch",
            "scope": "brief",
            "guidance_text": "Lead with the decision-relevant number when evidence supports it.",
        },
        "source_evidence": [{
            "source_type": "human_feedback",
            "summary": "Operator-created audience guidance proposal.",
            "run_id": None,
            "issue_id": None,
        }],
    }
    if status == "approved":
        payload["approved_by"] = "stahl"
        payload["approved_at"] = "2026-06-10T00:01:00Z"
    return payload


def _write_valid_proposed_ledger(ws: Path, *, final_newline: bool = True) -> None:
    path = improvement_ledger_path(ws)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = canonical_json(_valid_revision())
    path.write_text(text + ("\n" if final_newline else ""), encoding="utf-8")


def _write_valid_approved_ledger(ws: Path, *, final_newline: bool = True) -> None:
    first = _valid_revision()
    second = _valid_revision(
        status="approved",
        revision=2,
        previous_revision_sha256=revision_sha256(first),
    )
    text = canonical_json(first) + "\n" + canonical_json(second)
    path = improvement_ledger_path(ws)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + ("\n" if final_newline else ""), encoding="utf-8")


def _append_approved_legacy_feedback_issue_entry(
    ws: Path,
    *,
    entry_id: str,
    finding_type: str,
    issue_category: str,
    include_origin: bool = True,
    supersedes_id: str | None = None,
) -> None:
    first = _valid_revision(entry_id=entry_id)
    first["supersedes_id"] = supersedes_id
    evidence = {
        "source_type": "feedback_issue",
        "summary": "Legacy feedback issue evidence.",
        "run_id": "mabw-legacy-run",
        "issue_id": "fi-legacy",
    }
    if include_origin:
        evidence["origin"] = {
            "finding_type": finding_type,
            "issue_category": issue_category,
            "issue_source": "audit",
            "control_file": "audit_report.json",
        }
    first["source_evidence"] = [evidence]
    second = _valid_revision(
        entry_id=entry_id,
        status="approved",
        revision=2,
        previous_revision_sha256=revision_sha256(first),
    )
    second["supersedes_id"] = supersedes_id
    second["source_evidence"] = first["source_evidence"]
    path = improvement_ledger_path(ws)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(canonical_json(first))
        handle.write("\n")
        handle.write(canonical_json(second))
        handle.write("\n")


def _run_transition(ws: Path, transition: str) -> None:
    if transition == "propose":
        propose_improvement(
            workspace=ws,
            guidance="Lead with the decision-relevant number when evidence supports it.",
            category="audience_mismatch",
            scope="brief",
            source_summary="Operator-created audience guidance proposal.",
        )
    elif transition == "approve":
        approve_improvement(workspace=ws, entry_id="AG-0001", approved_by="stahl")
    elif transition == "reject":
        reject_improvement(workspace=ws, entry_id="AG-0001", rejected_by="stahl", reason="Not appropriate.")
    elif transition == "revert":
        revert_improvement(workspace=ws, entry_id="AG-0001", reverted_by="stahl", reason="No longer desired.")
    else:
        raise AssertionError(f"unknown transition: {transition}")
