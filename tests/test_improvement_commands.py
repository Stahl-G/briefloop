from __future__ import annotations

import json
from functools import partial
from pathlib import Path

import pytest

from multi_agent_brief.cli.main import main
from multi_agent_brief.improvement.memory import rebuild_improvement_memory
from multi_agent_brief.improvement.state import (
    ImprovementLedgerError,
    approve_improvement,
    improvement_ledger_path,
    improvement_stats,
    list_improvements,
    propose_improvement,
    reject_improvement,
    revert_improvement,
    show_improvement,
    validate_improvement_ledger,
)
from multi_agent_brief.orchestrator.runtime_state import initialize_runtime_state
from tests.helpers import write_minimal_workspace_under


ROOT = Path(__file__).resolve().parent.parent


_workspace = partial(
    write_minimal_workspace_under,
    project_name="Improvement CLI Test",
    user_text="# User\n\nNeed concise management guidance.\n",
    include_input_dir=True,
    input_path="input",
    output_path="output",
)


def _ledger_text(ws: Path) -> str:
    path = improvement_ledger_path(ws)
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _workspace_file_bytes(ws: Path) -> dict[str, bytes]:
    return {
        path.relative_to(ws).as_posix(): path.read_bytes()
        for path in sorted(ws.rglob("*"))
        if path.is_file()
    }


_RETIRED_IMPROVE_COMMANDS = [
    pytest.param(
        [
            "improve",
            "propose",
            "--guidance",
            "Lead with the decision-relevant number when evidence supports it.",
            "--category",
            "audience_mismatch",
            "--scope",
            "brief",
            "--source-summary",
            "Operator-created audience guidance proposal.",
        ],
        id="propose",
    ),
    pytest.param(["improve", "list"], id="list"),
    pytest.param(["improve", "show", "--entry-id", "AG-0001"], id="show"),
    pytest.param(
        ["improve", "approve", "--entry-id", "AG-0001", "--by", "stahl"],
        id="approve",
    ),
    pytest.param(
        ["improve", "reject", "--entry-id", "AG-0001", "--by", "stahl", "--reason", "Too late."],
        id="reject",
    ),
    pytest.param(
        ["improve", "revert", "--entry-id", "AG-0001", "--by", "stahl", "--reason", "No longer desired."],
        id="revert",
    ),
    pytest.param(["improve", "stats"], id="stats"),
    pytest.param(["improve", "validate"], id="validate"),
    pytest.param(["improve", "rebuild"], id="rebuild"),
]


@pytest.mark.parametrize("command", _RETIRED_IMPROVE_COMMANDS)
def test_improve_cli_public_surface_is_retired_without_writes(tmp_path, capsys, command):
    ws = _workspace(tmp_path)
    before = _workspace_file_bytes(ws)

    rc = main([*command, "--workspace", str(ws)])

    # LEGACY-DELETE: retired public `improve` CLI; the Improvement Ledger is
    # driven through the deterministic improvement.state/memory seams below.
    assert rc == 1
    assert capsys.readouterr().out == "runtime_command_unsupported\n"
    assert _workspace_file_bytes(ws) == before


def test_improve_propose_list_show_validate_stats_json(tmp_path):
    ws = _workspace(tmp_path)

    proposed = propose_improvement(
        workspace=ws,
        guidance="Lead with the decision-relevant number when evidence supports it.",
        category="audience_mismatch",
        scope="brief",
        source_summary="Operator-created audience guidance proposal.",
    )
    assert proposed["entry"]["entry_id"] == "AG-0001"
    assert proposed["event_recorded"] is False

    listed = list_improvements(workspace=ws)
    assert listed["entry_count"] == 1
    assert listed["current_entries"][0]["entry_id"] == "AG-0001"

    shown = show_improvement(workspace=ws, entry_id="AG-0001")
    assert shown["current"]["status"] == "proposed"
    assert len(shown["revisions"]) == 1

    validation = validate_improvement_ledger(workspace=ws)
    assert validation["ok"] is True

    stats = improvement_stats(workspace=ws)
    assert stats["approved_count"] == 0
    assert stats["eligible_for_materialization_count"] == 0


@pytest.mark.parametrize("historical_runtime", ["auto", "manual"])
def test_improve_propose_rejects_historical_runtime_without_writes(
    tmp_path,
    historical_runtime,
):
    ws = _workspace(tmp_path)
    initialize_runtime_state(runtime="operator", workspace=ws, repo_workdir=ROOT)
    manifest_path = ws / "output/intermediate/runtime_manifest.json"
    event_log_path = ws / "output/intermediate/event_log.jsonl"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["runtime"] = historical_runtime
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest_before = manifest_path.read_bytes()
    event_log_before = event_log_path.read_bytes()

    with pytest.raises(ImprovementLedgerError, match="canonical runtime identity"):
        propose_improvement(
            workspace=ws,
            guidance="Lead with the decision-relevant number when evidence supports it.",
            category="audience_mismatch",
            scope="brief",
            source_summary="Operator-created audience guidance proposal.",
        )

    assert not improvement_ledger_path(ws).exists()
    assert manifest_path.read_bytes() == manifest_before
    assert event_log_path.read_bytes() == event_log_before


def test_improve_propose_requires_source_summary(tmp_path):
    ws = _workspace(tmp_path)

    with pytest.raises(ImprovementLedgerError, match="source-summary"):
        propose_improvement(
            workspace=ws,
            guidance="Lead with the decision-relevant number when evidence supports it.",
            category="audience_mismatch",
            scope="brief",
        )

    assert not improvement_ledger_path(ws).exists()


def test_improve_propose_rejects_source_summary_with_from_issue(tmp_path):
    ws = _workspace(tmp_path)

    with pytest.raises(ImprovementLedgerError, match="mutually exclusive"):
        propose_improvement(
            workspace=ws,
            guidance="Lead with the decision-relevant number when evidence supports it.",
            category="audience_mismatch",
            scope="brief",
            from_issue="fi-0001",
            source_summary="Operator-created audience guidance proposal.",
        )

    assert not improvement_ledger_path(ws).exists()


def test_improve_approve_reject_revert_cli_boundaries(tmp_path):
    ws = _workspace(tmp_path)
    initialize_runtime_state(runtime="operator", workspace=ws, repo_workdir=ROOT)

    propose_improvement(
        workspace=ws,
        guidance="Lead with the decision-relevant number when evidence supports it.",
        category="audience_mismatch",
        scope="brief",
        source_summary="Operator-created audience guidance proposal.",
    )

    approved = approve_improvement(workspace=ws, entry_id="AG-0001", approved_by="stahl")
    assert approved["entry"]["status"] == "approved"
    assert approved["event_recorded"] is True

    with pytest.raises(ImprovementLedgerError, match="failed validation"):
        reject_improvement(
            workspace=ws,
            entry_id="AG-0001",
            rejected_by="stahl",
            reason="Too late.",
        )


def test_improve_propose_supersedes_cli_records_top_level_lineage(tmp_path):
    ws = _workspace(tmp_path)

    propose_improvement(
        workspace=ws,
        guidance="Lead with the decision-relevant number when evidence supports it.",
        category="audience_mismatch",
        scope="brief",
        source_summary="Operator-created audience guidance proposal.",
    )
    approve_improvement(workspace=ws, entry_id="AG-0001", approved_by="stahl")

    proposed = propose_improvement(
        workspace=ws,
        guidance="Lead with the replacement audience framing.",
        category="audience_mismatch",
        scope="brief",
        source_summary="Operator-created replacement guidance.",
        supersedes="AG-0001",
    )
    assert proposed["entry"]["entry_id"] == "AG-0002"
    assert proposed["entry"]["supersedes_id"] == "AG-0001"
    assert proposed["warnings"] == []

    reverted = revert_improvement(
        workspace=ws,
        entry_id="AG-0001",
        reverted_by="stahl",
        reason="No longer desired.",
    )
    assert reverted["entry"]["status"] == "reverted"


def test_improve_validate_is_read_only_for_corrupt_ledger(tmp_path):
    ws = _workspace(tmp_path)
    path = improvement_ledger_path(ws)
    path.parent.mkdir(parents=True)
    path.write_text("{not json}\n", encoding="utf-8")
    before = _ledger_text(ws)

    validation = validate_improvement_ledger(workspace=ws)

    assert validation["ok"] is False
    assert _ledger_text(ws) == before
    assert not (ws / "output" / "intermediate" / "event_log.jsonl").exists()


def test_improve_cli_does_not_materialize_memory_or_handoff(tmp_path):
    ws = _workspace(tmp_path)
    propose_improvement(
        workspace=ws,
        guidance="Lead with the decision-relevant number when evidence supports it.",
        category="audience_mismatch",
        scope="brief",
        source_summary="Operator-created audience guidance proposal.",
    )
    approve_improvement(workspace=ws, entry_id="AG-0001", approved_by="stahl")

    forbidden = [
        ws / "improvement" / "memory.md",
        ws / "output" / "intermediate" / "improvement_memory_snapshot.md",
        ws / "audience_profile.md",
        ws / "output" / "intermediate" / "audience_profile_snapshot.md",
        ws / "output" / "intermediate" / "agent_handoff.md",
        ws / "output" / "intermediate" / "agent_handoff.json",
    ]
    assert all(not path.exists() for path in forbidden)


def test_improve_rebuild_cli_projects_memory_without_runtime_state(tmp_path):
    ws = _workspace(tmp_path)
    propose_improvement(
        workspace=ws,
        guidance="Lead with the decision-relevant number when evidence supports it.",
        category="audience_mismatch",
        scope="brief",
        source_summary="Operator-created audience guidance proposal.",
    )
    approve_improvement(workspace=ws, entry_id="AG-0001", approved_by="stahl")

    payload = rebuild_improvement_memory(workspace=ws)

    assert payload["selected_entry_ids"] == ["AG-0001"]
    assert payload["eligible_count"] == 1
    assert payload["memory_path"] == "improvement/memory.md"
    assert (ws / "improvement" / "memory.md").exists()
    assert not (ws / "output" / "intermediate" / "improvement_memory_snapshot.md").exists()
    assert not (ws / "output" / "intermediate" / "runtime_manifest.json").exists()
    assert not (ws / "output" / "intermediate" / "agent_handoff.json").exists()
