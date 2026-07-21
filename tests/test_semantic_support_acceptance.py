"""Retired-surface guard for the `semantic-support` public CLI.

LD2-3 removes `runtime_state/semantic_support_acceptance.py` along with the rest of
the legacy stack, so the adjudication-ledger tests that drove it are gone. The
typed-rejection probe below is a live contract, not residue: it holds the
fail-closed stub to `legacy_workspace_unsupported` with zero writes.

The legacy-workspace precondition comes from `write_legacy_control_files`. The
guard answers before any semantic-support artifact is read, so the probe needs no
report/ledger fixture -- only a workspace the guard classifies as legacy.
"""

from __future__ import annotations

from functools import partial
from pathlib import Path

from multi_agent_brief.cli.main import main as cli_main
from tests.helpers import write_legacy_control_files, write_minimal_workspace_under

# The owning module is deleted by LD2-3, so this path is no longer re-exportable;
# the probe asserts the ledger is never materialized by the retired surface.
_LEDGER_RELATIVE = "output/intermediate/semantic_support_acceptance_ledger.json"

_workspace = partial(
    write_minimal_workspace_under,
    project_name="semantic-support-retired-surface",
    user_text="# Semantic support retired surface probe\n",
)


def _snapshot_workspace_files(ws: Path) -> dict[str, bytes]:
    return {
        path.relative_to(ws).as_posix(): path.read_bytes()
        for path in sorted(ws.rglob("*"))
        if path.is_file()
    }


def test_semantic_support_public_cli_retired_rejects_typed_without_writes(
    tmp_path: Path,
    capsys,
) -> None:
    ws = write_legacy_control_files(_workspace(tmp_path))
    capsys.readouterr()

    for argv in (
        ["semantic-support", "bind", "--workspace", str(ws), "--json"],
        [
            "semantic-support",
            "adjudicate",
            "--workspace",
            str(ws),
            "--proposal-id",
            "SAR-0001",
            "--decision",
            "accept",
            "--reason",
            "Human reviewer adjudicated this proposal.",
            "--json",
        ],
    ):
        before = _snapshot_workspace_files(ws)
        rc = cli_main(argv)
        assert rc == 1
        assert capsys.readouterr().out.strip() == "legacy_workspace_unsupported"
        assert _snapshot_workspace_files(ws) == before
        assert not (ws / _LEDGER_RELATIVE).exists()
