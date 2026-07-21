"""Retired-surface guard for the `experiments 080 scaffold-condition` public CLI.

LD2-3 retires the MABW-080 experiment library with the legacy runtime-state
stack, so the pilot-case tests that drove it are gone. `cli/experiments_commands.py`
survives as a fail-closed stub, and the typed-rejection probe below is that stub's
live contract: `runtime_command_unsupported` with zero writes.
"""

from __future__ import annotations

from pathlib import Path

from multi_agent_brief.cli.main import main

ROOT = Path(__file__).resolve().parent.parent
CASE_DIR = ROOT / "experiments" / "080" / "cases" / "solar_public_001"


def _write_scaffold_workspace(ws: Path) -> None:
    ws.mkdir(parents=True)
    (ws / "input").mkdir()
    (ws / "config.yaml").write_text(
        "project:\n"
        "  name: \"080 Public Pilot Condition Workspace\"\n"
        "language:\n"
        "  interface: \"en-US\"\n"
        "  output: \"en-US\"\n"
        "  source_handling: \"preserve_original\"\n"
        "input:\n"
        "  path: \"input\"\n"
        "output:\n"
        "  path: \"output\"\n"
        "report:\n"
        "  title: \"Synthetic Solar Fixture Brief\"\n"
        "  date: \"2026-06-18\"\n"
        "  max_source_age_days: 14\n"
        "  fail_on_stale_source: false\n",
        encoding="utf-8",
    )
    (ws / "sources.yaml").write_text("manual:\n  sources: []\n", encoding="utf-8")
    (ws / "user.md").write_text("# User\n\nPrepare a concise synthetic solar fixture brief.\n", encoding="utf-8")
    (ws / "audience_profile.md").write_text("# Audience\n\nManagement-facing public fixture reader.\n", encoding="utf-8")


def test_080_public_pilot_scaffold_condition_cli_is_retired(tmp_path, capsys):
    """Retired public scaffold-condition CLI: typed rejection, zero writes."""
    ws = tmp_path / "baseline-workspace"
    _write_scaffold_workspace(ws)
    before_files = {
        path.relative_to(ws).as_posix(): path.read_bytes()
        for path in ws.rglob("*")
        if path.is_file()
    }

    rc = main([
        "experiments",
        "080",
        "scaffold-condition",
        "--case",
        str(CASE_DIR),
        "--condition",
        "baseline",
        "--workspace",
        str(ws),
        "--runtime",
        "operator",
        "--repo-workdir",
        str(ROOT),
        "--json",
    ])

    assert rc == 1
    assert capsys.readouterr().out == "runtime_command_unsupported\n"
    after_files = {
        path.relative_to(ws).as_posix(): path.read_bytes()
        for path in ws.rglob("*")
        if path.is_file()
    }
    assert after_files == before_files
