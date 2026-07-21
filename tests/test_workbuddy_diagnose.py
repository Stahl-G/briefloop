"""Retired-surface guard for the `workbuddy diagnose` public CLI.

LD2-3 removes `workbuddy/diagnose.py` along with the legacy runtime-state stack,
so the Run Card projection tests that drove it are gone. The typed-rejection
probe below is a live contract, not residue: it holds the fail-closed stub to
`legacy_workspace_unsupported` with zero writes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from multi_agent_brief.cli.main import main
from tests.helpers import write_legacy_control_files


def _workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "input").mkdir()
    (ws / "output" / "intermediate").mkdir(parents=True)
    (ws / "config.yaml").write_text("project:\n  name: Test\n", encoding="utf-8")
    (ws / "sources.yaml").write_text(
        "source_strategy:\n"
        "  profile: conservative\n"
        "  enabled_providers:\n"
        "    - manual\n"
        "manual:\n"
        "  enabled: true\n"
        "  sources:\n"
        "    - name: Local\n"
        "      path: input/\n",
        encoding="utf-8",
    )
    return ws


def _workspace_file_bytes(ws: Path) -> dict[str, bytes]:
    return {
        path.relative_to(ws).as_posix(): path.read_bytes()
        for path in ws.rglob("*")
        if path.is_file()
    }


@pytest.mark.parametrize("json_mode", [True, False], ids=["json", "text"])
def test_workbuddy_diagnose_public_cli_is_retired_with_zero_writes(
    tmp_path: Path,
    capsys,
    json_mode: bool,
) -> None:
    ws = _workspace(tmp_path)
    write_legacy_control_files(ws)
    before_files = _workspace_file_bytes(ws)

    argv = ["workbuddy", "diagnose", "--workspace", str(ws)]
    if json_mode:
        argv.append("--json")
    rc = main(argv)

    assert rc == 1
    assert capsys.readouterr().out.strip() == "legacy_workspace_unsupported"
    assert _workspace_file_bytes(ws) == before_files
