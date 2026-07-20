from __future__ import annotations

import json
from pathlib import Path

from multi_agent_brief.cli.main import main
from multi_agent_brief.control_store import SQLiteControlStore


def test_public_safe_runtime_handoff_control_selection_and_finalize_e2e(
    tmp_path: Path,
    capsys,
) -> None:
    workspace = tmp_path / "public-safe-e2e"

    assert (
        main(
            [
                "new",
                "industry-weekly",
                str(workspace),
                "--web-search-mode",
                "disabled",
            ]
        )
        == 0
    )
    capsys.readouterr()
    public_input = workspace / "input" / "public-safe-source.md"
    public_input.write_text(
        "ExampleCo opened a synthetic public demo facility in June 2026.\n",
        encoding="utf-8",
    )

    assert (
        main(
            [
                "run",
                "--workspace",
                str(workspace),
                "--runtime",
                "codex",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert main(["runtime", "next", "--workspace", str(workspace)]) == 0
    action = json.loads(capsys.readouterr().out)
    assert action["schema_version"] == "briefloop.core_run_next_action.v2"
    assert action["effect_kind"] == "doctor_check"
    assert action["action_fingerprint"]

    intermediate = workspace / "output" / "intermediate"
    legacy_controls = (
        "agent_handoff.json",
        "runtime_manifest.json",
        "workflow_state.json",
        "artifact_registry.json",
        "event_log.jsonl",
        "finalize_report.json",
    )
    assert all(not (intermediate / name).exists() for name in legacy_controls)
    assert (workspace / "briefloop.db").is_file()
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        before_revision = store.current_revision
        snapshot = store.load_snapshot(action["run_id"])
    assert snapshot.run.runtime == "codex"
    assert snapshot.transactions[-1].committed_revision == before_revision

    database_before = (workspace / "briefloop.db").read_bytes()
    assert main(["finalize", "--config", str(workspace / "config.yaml")]) == 1
    assert capsys.readouterr().out == "runtime_command_unsupported\n"
    assert (workspace / "briefloop.db").read_bytes() == database_before
    assert all(not (intermediate / name).exists() for name in legacy_controls)


def test_demo_workspace_boots_into_codex_runtime(tmp_path: Path, capsys) -> None:
    workspace = tmp_path / "demo-codex"

    assert main(["init", str(workspace), "--demo", "--force"]) == 0
    capsys.readouterr()
    assert "controlstore_v2" in (workspace / "config.yaml").read_text(encoding="utf-8")

    assert main(["run", "--workspace", str(workspace), "--runtime", "codex"]) == 0
    capsys.readouterr()
    assert main(["runtime", "next", "--workspace", str(workspace)]) == 0
    action = json.loads(capsys.readouterr().out)
    assert action["schema_version"] == "briefloop.core_run_next_action.v2"
    assert action["effect_kind"] == "doctor_check"
    assert action["action_fingerprint"]
    assert (workspace / "briefloop.db").is_file()
