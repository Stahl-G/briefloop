from __future__ import annotations

import json
from pathlib import Path

from multi_agent_brief.cli.main import main
from multi_agent_brief.improvement.state import improvement_ledger_path
from multi_agent_brief.orchestrator.runtime_state import initialize_runtime_state


ROOT = Path(__file__).resolve().parent.parent


def _workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "config.yaml").write_text(
        """
project:
  name: "Improvement CLI Test"
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


def _ledger_text(ws: Path) -> str:
    path = improvement_ledger_path(ws)
    return path.read_text(encoding="utf-8") if path.exists() else ""


def test_improve_propose_list_show_validate_stats_json(tmp_path, capsys):
    ws = _workspace(tmp_path)

    rc = main([
        "improve",
        "propose",
        "--workspace",
        str(ws),
        "--guidance",
        "Lead with the decision-relevant number when evidence supports it.",
        "--category",
        "audience_mismatch",
        "--scope",
        "brief",
        "--source-summary",
        "Operator-created audience guidance proposal.",
        "--json",
    ])

    assert rc == 0
    proposed = json.loads(capsys.readouterr().out)
    assert proposed["entry"]["entry_id"] == "AG-0001"
    assert proposed["event_recorded"] is False

    assert main(["improve", "list", "--workspace", str(ws), "--json"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed["entry_count"] == 1
    assert listed["current_entries"][0]["entry_id"] == "AG-0001"

    assert main(["improve", "show", "--workspace", str(ws), "--entry-id", "AG-0001", "--json"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["current"]["status"] == "proposed"
    assert len(shown["revisions"]) == 1

    assert main(["improve", "validate", "--workspace", str(ws), "--json"]) == 0
    validation = json.loads(capsys.readouterr().out)
    assert validation["ok"] is True

    assert main(["improve", "stats", "--workspace", str(ws), "--json"]) == 0
    stats = json.loads(capsys.readouterr().out)
    assert stats["approved_count"] == 0
    assert stats["eligible_for_materialization_count"] == 0


def test_improve_propose_requires_source_summary(tmp_path, capsys):
    ws = _workspace(tmp_path)

    rc = main([
        "improve",
        "propose",
        "--workspace",
        str(ws),
        "--guidance",
        "Lead with the decision-relevant number when evidence supports it.",
        "--category",
        "audience_mismatch",
        "--scope",
        "brief",
        "--json",
    ])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert "source-summary" in payload["error"]
    assert not improvement_ledger_path(ws).exists()


def test_improve_propose_rejects_source_summary_with_from_issue(tmp_path, capsys):
    ws = _workspace(tmp_path)

    rc = main([
        "improve",
        "propose",
        "--workspace",
        str(ws),
        "--guidance",
        "Lead with the decision-relevant number when evidence supports it.",
        "--category",
        "audience_mismatch",
        "--scope",
        "brief",
        "--from-issue",
        "fi-0001",
        "--source-summary",
        "Operator-created audience guidance proposal.",
        "--json",
    ])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert "mutually exclusive" in payload["error"]
    assert not improvement_ledger_path(ws).exists()


def test_improve_approve_reject_revert_cli_boundaries(tmp_path, capsys):
    ws = _workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)

    assert main([
        "improve",
        "propose",
        "--workspace",
        str(ws),
        "--guidance",
        "Lead with the decision-relevant number when evidence supports it.",
        "--category",
        "audience_mismatch",
        "--scope",
        "brief",
        "--source-summary",
        "Operator-created audience guidance proposal.",
        "--json",
    ]) == 0
    capsys.readouterr()

    assert main([
        "improve",
        "approve",
        "--workspace",
        str(ws),
        "--entry-id",
        "AG-0001",
        "--by",
        "stahl",
        "--json",
    ]) == 0
    approved = json.loads(capsys.readouterr().out)
    assert approved["entry"]["status"] == "approved"
    assert approved["event_recorded"] is True

    rc = main([
        "improve",
        "reject",
        "--workspace",
        str(ws),
        "--entry-id",
        "AG-0001",
        "--by",
        "stahl",
        "--reason",
        "Too late.",
        "--json",
    ])
    assert rc == 1
    assert "failed validation" in json.loads(capsys.readouterr().out)["error"]

    assert main([
        "improve",
        "revert",
        "--workspace",
        str(ws),
        "--entry-id",
        "AG-0001",
        "--by",
        "stahl",
        "--reason",
        "No longer desired.",
        "--json",
    ]) == 0
    reverted = json.loads(capsys.readouterr().out)
    assert reverted["entry"]["status"] == "reverted"


def test_improve_validate_is_read_only_for_corrupt_ledger(tmp_path, capsys):
    ws = _workspace(tmp_path)
    path = improvement_ledger_path(ws)
    path.parent.mkdir(parents=True)
    path.write_text("{not json}\n", encoding="utf-8")
    before = _ledger_text(ws)

    rc = main(["improve", "validate", "--workspace", str(ws), "--json"])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert _ledger_text(ws) == before
    assert not (ws / "output" / "intermediate" / "event_log.jsonl").exists()


def test_improve_cli_does_not_materialize_memory_or_handoff(tmp_path, capsys):
    ws = _workspace(tmp_path)
    assert main([
        "improve",
        "propose",
        "--workspace",
        str(ws),
        "--guidance",
        "Lead with the decision-relevant number when evidence supports it.",
        "--category",
        "audience_mismatch",
        "--scope",
        "brief",
        "--source-summary",
        "Operator-created audience guidance proposal.",
        "--json",
    ]) == 0
    capsys.readouterr()
    assert main([
        "improve",
        "approve",
        "--workspace",
        str(ws),
        "--entry-id",
        "AG-0001",
        "--by",
        "stahl",
        "--json",
    ]) == 0
    capsys.readouterr()

    forbidden = [
        ws / "improvement" / "memory.md",
        ws / "output" / "intermediate" / "improvement_memory_snapshot.md",
        ws / "audience_profile.md",
        ws / "output" / "intermediate" / "audience_profile_snapshot.md",
        ws / "output" / "intermediate" / "agent_handoff.md",
        ws / "output" / "intermediate" / "agent_handoff.json",
    ]
    assert all(not path.exists() for path in forbidden)


def test_run_start_handoff_sources_do_not_reference_improvement_ledger():
    for rel_path in (
        "src/multi_agent_brief/cli/run_commands.py",
        "src/multi_agent_brief/cli/start_commands.py",
    ):
        text = (ROOT / rel_path).read_text(encoding="utf-8")
        assert "improvement/ledger.jsonl" not in text
        assert "improvement_memory_snapshot" not in text
