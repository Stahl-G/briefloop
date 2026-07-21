"""Retired-surface guards for the operator-runtime and `state` public CLIs.

LD2-3 removes the `improvement/` package, `orchestrator/handoff.py`, and the
legacy runtime-state stack, so the improvement-memory tests that drove them are
gone. The typed-rejection matrix below is a live contract, not residue: it covers
three distinct rejection surfaces, including `[run] runtime_adapter_unsupported`
on `run` -- an active command whose adapter face must keep failing closed.

The legacy-workspace precondition comes from `write_legacy_control_files` rather
than the retired runtime-state writers.
"""

from __future__ import annotations

from functools import partial
from pathlib import Path

import pytest

from multi_agent_brief.cli.main import main
from tests.helpers import write_legacy_control_files, write_minimal_workspace_under

ROOT = Path(__file__).resolve().parent.parent

_workspace = partial(
    write_minimal_workspace_under,
    project_name="Improvement Memory Test",
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


_RETIRED_RUNTIME_COMMANDS = [
    pytest.param(
        ["run", "--runtime", "operator", "--skip-doctor"],
        "[run] runtime_adapter_unsupported\n",
        False,
        id="run-operator",
    ),
    pytest.param(
        ["start", "--runtime", "operator", "--skip-doctor"],
        "runtime_command_unsupported\n",
        False,
        id="start-operator",
    ),
    pytest.param(
        ["state", "check", "--strict"],
        "legacy_workspace_unsupported\n",
        True,
        id="state-check-legacy-workspace",
    ),
]


@pytest.mark.parametrize(
    ("command", "expected_output", "legacy_workspace"),
    _RETIRED_RUNTIME_COMMANDS,
)
def test_retired_runtime_public_surfaces_rejected_without_writes(
    tmp_path,
    capsys,
    command,
    expected_output,
    legacy_workspace,
):
    ws = _workspace(tmp_path)
    if legacy_workspace:
        write_legacy_control_files(ws)
    before = _workspace_file_bytes(ws)

    rc = main([*command, "--workspace", str(ws)])

    assert rc == 1
    assert capsys.readouterr().out == expected_output
    assert _workspace_file_bytes(ws) == before
