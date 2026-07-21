"""Retired-surface guard for the `improve` public CLI.

LD2-3 removes the `improvement/` package along with the legacy runtime-state
stack, so the Improvement Ledger tests that drove it are gone. The typed-rejection
probe below is a live contract, not residue: it holds the fail-closed stub to
`runtime_command_unsupported` with zero writes across every retired subcommand.
"""

from __future__ import annotations

from functools import partial
from pathlib import Path

import pytest

from multi_agent_brief.cli.main import main
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

    assert rc == 1
    assert capsys.readouterr().out == "runtime_command_unsupported\n"
    assert _workspace_file_bytes(ws) == before
