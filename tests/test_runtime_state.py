"""Retired-surface guards for the `state` and `repair` public CLIs.

LD2-3 removes the legacy JSON runtime-state stack, so the transaction, workflow
and registry tests that drove it are gone. The typed-rejection probes below are
live contracts, not residue: they hold both fail-closed stubs to their typed
token with zero writes, across every retired subcommand.

Both rejection faces stay covered: a fresh workspace is refused without being
initialized, and a legacy workspace answers `legacy_workspace_unsupported`. The
legacy precondition comes from `write_legacy_control_files` rather than the
retired writers.
"""

from __future__ import annotations

from functools import partial
from pathlib import Path

import pytest

from multi_agent_brief.cli.main import main
from tests.helpers import write_legacy_control_files, write_workspace_files_under

ROOT = Path(__file__).resolve().parent.parent

_write_workspace = partial(
    write_workspace_files_under,
    config_text="""
project:
  name: "Runtime State Test"
output:
  path: "output"
input:
  path: "input"
""".strip(),
    user_text="# User\n",
    include_input_dir=True,
)


def _snapshot_workspace_files(ws: Path) -> dict[str, bytes]:
    return {
        path.relative_to(ws).as_posix(): path.read_bytes()
        for path in ws.rglob("*")
        if path.is_file()
    }


def _assert_retired_cli_typed_rejection_without_writes(ws: Path, capsys, argv: list[str]) -> None:
    before_files = _snapshot_workspace_files(ws)

    rc = main(argv)

    assert rc == 1
    assert capsys.readouterr().out.strip() == "legacy_workspace_unsupported"
    assert _snapshot_workspace_files(ws) == before_files


def test_state_check_strict_fresh_workspace_fails_without_initializing(tmp_path):
    ws = _write_workspace(tmp_path)

    rc = main([
        "state",
        "check",
        "--workspace",
        str(ws),
        "--repo-workdir",
        str(ROOT),
        "--strict",
        "--json",
    ])

    assert rc == 1
    assert not (ws / "output" / "intermediate" / "runtime_manifest.json").exists()


@pytest.mark.parametrize(
    "argv",
    [
        ["decide", "--stage", "doctor", "--decision", "continue", "--reason", "continue doctor", "--repo-workdir", str(ROOT), "--json"],
        ["stage-complete", "--stage", "doctor", "--reason", "doctor passed", "--repo-workdir", str(ROOT), "--json"],
        ["enrich-claim-metadata", "--from-source-evidence", "--repo-workdir", str(ROOT), "--json"],
        ["freeze-claim-ledger", "--repo-workdir", str(ROOT), "--json"],
        ["finalize-complete", "--reason", "reader artifacts finalized and clean", "--repo-workdir", str(ROOT), "--json"],
        ["show"],
        ["show", "--json"],
        ["import-fact-layer", "--runtime", "operator", "--archive", "output/runs/source-run/manifest.json", "--repo-workdir", str(ROOT), "--json"],
        ["check", "--strict", "--repo-workdir", str(ROOT), "--json"],
    ],
    ids=[
        "decide-json",
        "stage-complete-json",
        "enrich-claim-metadata-json",
        "freeze-claim-ledger-json",
        "finalize-complete-json",
        "show",
        "show-json",
        "import-fact-layer-json",
        "check-strict-json",
    ],
)
def test_retired_state_cli_rejects_legacy_workspace_without_writes(tmp_path, capsys, argv):
    ws = write_legacy_control_files(_write_workspace(tmp_path))

    _assert_retired_cli_typed_rejection_without_writes(
        ws,
        capsys,
        ["state", argv[0], "--workspace", str(ws), *argv[1:]],
    )


@pytest.mark.parametrize(
    "argv",
    [
        ["start", "--json"],
        [
            "supersede-stage",
            "--stage",
            "editor",
            "--artifact",
            "output/intermediate/audited_brief.md",
            "--reason",
            "operator accepted contaminated edit as new editor revision",
            "--json",
        ],
    ],
    ids=["start-json", "supersede-stage-json"],
)
def test_retired_repair_cli_rejects_legacy_workspace_without_writes(tmp_path, capsys, argv):
    ws = write_legacy_control_files(_write_workspace(tmp_path))

    _assert_retired_cli_typed_rejection_without_writes(
        ws,
        capsys,
        ["repair", argv[0], "--workspace", str(ws), "--repo-workdir", str(ROOT), *argv[1:]],
    )
