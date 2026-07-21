"""Retired-surface guard for the `repair` public CLI.

LD2-3 removes `repair/router.py` along with the legacy runtime-state stack, so the
route-mapping tests that drove it are gone. The typed-rejection probes below are
live contracts, not residue: they hold the fail-closed stub to
`legacy_workspace_unsupported` with zero writes.

The legacy-workspace precondition comes from `write_legacy_control_files` rather
than the retired runtime-state writers.
"""

from __future__ import annotations

from functools import partial
from pathlib import Path

import pytest

from multi_agent_brief.cli.main import main
from tests.helpers import write_legacy_control_files, write_minimal_workspace_under

_workspace = partial(
    write_minimal_workspace_under,
    project_name="repair-route-test",
    user_text="# Repair route test\n",
)


def _workspace_file_bytes(ws: Path) -> dict[str, bytes]:
    return {
        path.relative_to(ws).as_posix(): path.read_bytes()
        for path in ws.rglob("*")
        if path.is_file()
    }


@pytest.mark.parametrize(
    "argv",
    [
        ["repair", "route", "--workspace", "{ws}", "--json"],
        ["repair", "start", "--workspace", "{ws}", "--json"],
        ["repair", "complete", "--workspace", "{ws}", "--reason", "retired surface probe", "--json"],
    ],
    ids=["repair route", "repair start", "repair complete"],
)
def test_repair_public_cli_is_retired_with_zero_writes(tmp_path, capsys, argv):
    ws = write_legacy_control_files(_workspace(tmp_path))
    before = _workspace_file_bytes(ws)

    rc = main([arg.format(ws=ws) for arg in argv])

    assert rc == 1
    assert capsys.readouterr().out == "legacy_workspace_unsupported\n"
    assert _workspace_file_bytes(ws) == before


def test_repair_route_public_cli_rejects_without_writing_a_repair_plan(tmp_path, capsys):
    """The retired `repair route` surface never materializes a repair plan.

    Split out of the former route-mapping test: the `route_repair` assertions it
    carried tested a module LD2-3 deletes, while this half guards the stub.
    """

    ws = write_legacy_control_files(_workspace(tmp_path))
    before_files = _workspace_file_bytes(ws)

    rc = main(["repair", "route", "--workspace", str(ws), "--json"])
    output = capsys.readouterr().out

    assert rc == 1
    assert output.strip() == "legacy_workspace_unsupported"
    assert _workspace_file_bytes(ws) == before_files
    assert not (ws / "output" / "intermediate" / "repair_plan.json").exists()
