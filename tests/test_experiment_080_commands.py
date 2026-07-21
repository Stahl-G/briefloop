"""Retired-surface guards for the MABW-080 and finalize public CLI surfaces.

LD2-3 retires the MABW-080 experiment library with the legacy runtime-state stack,
so the case/run-record tests that drove it are gone. `cli/experiments_commands.py`
survives as a fail-closed stub, and the probe below is that stub's live contract
together with the `finalize` and `state finalize-complete` surfaces.

Two rejection faces are covered deliberately: a fresh workspace answers
`runtime_command_unsupported`, a legacy workspace answers
`legacy_workspace_unsupported`. The guard replies before any case archive is
read, so the probe needs no 080 fixture -- only the two workspace shapes.
"""

from __future__ import annotations

from pathlib import Path

from multi_agent_brief.cli.main import main
from tests.helpers import write_legacy_control_files


def _write_scaffold_workspace(ws: Path) -> Path:
    ws.mkdir(parents=True)
    (ws / "input").mkdir()
    (ws / "config.yaml").write_text(
        "project:\n"
        "  name: \"080 Retired Surface Workspace\"\n"
        "input:\n"
        "  path: \"input\"\n"
        "output:\n"
        "  path: \"output\"\n",
        encoding="utf-8",
    )
    (ws / "sources.yaml").write_text("manual:\n  sources: []\n", encoding="utf-8")
    (ws / "user.md").write_text("# User\n\nRetired surface probe.\n", encoding="utf-8")
    return ws


def _workspace_file_bytes(ws: Path) -> dict[str, bytes]:
    return {
        path.relative_to(ws).as_posix(): path.read_bytes()
        for path in sorted(ws.rglob("*"))
        if path.is_file()
    }


def test_experiments_080_public_cli_workspace_surfaces_are_retired(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    case_dir.mkdir()

    fresh_ws = _write_scaffold_workspace(tmp_path / "fresh-workspace")
    fresh_before = _workspace_file_bytes(fresh_ws)

    scaffold_rc = main([
        "experiments",
        "080",
        "scaffold-condition",
        "--case",
        str(case_dir),
        "--condition",
        "baseline",
        "--workspace",
        str(fresh_ws),
        "--runtime",
        "codex",
        "--json",
    ])

    assert scaffold_rc == 1
    assert capsys.readouterr().out == "runtime_command_unsupported\n"
    assert _workspace_file_bytes(fresh_ws) == fresh_before

    legacy_ws = write_legacy_control_files(
        _write_scaffold_workspace(tmp_path / "legacy-workspace")
    )
    legacy_before = _workspace_file_bytes(legacy_ws)
    run_record_output = tmp_path / "memory.run_record.json"

    register_rc = main([
        "experiments",
        "080",
        "register-run",
        "--case",
        str(case_dir),
        "--condition",
        "memory",
        "--workspace",
        str(legacy_ws),
        "--output",
        str(run_record_output),
        "--json",
    ])

    assert register_rc == 1
    assert capsys.readouterr().out == "legacy_workspace_unsupported\n"
    assert _workspace_file_bytes(legacy_ws) == legacy_before
    assert not run_record_output.exists()

    finalize_rc = main(["finalize", "--config", str(legacy_ws / "config.yaml")])

    assert finalize_rc == 1
    assert capsys.readouterr().out == "legacy_workspace_unsupported\n"
    assert _workspace_file_bytes(legacy_ws) == legacy_before

    complete_rc = main([
        "state",
        "finalize-complete",
        "--workspace",
        str(legacy_ws),
        "--reason",
        "retired surface must not write",
        "--json",
    ])

    assert complete_rc == 1
    assert capsys.readouterr().out == "legacy_workspace_unsupported\n"
    assert _workspace_file_bytes(legacy_ws) == legacy_before
