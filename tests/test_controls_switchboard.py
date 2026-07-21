"""Retired-surface guards for the `controls`, `state` and operator-`run` public CLIs.

LD2-3 removes `controls/switchboard.py` along with the legacy runtime-state stack,
so the switchboard projection tests that drove it are gone. The typed-rejection
probes below are live contracts, not residue: they hold three fail-closed stubs to
`legacy_workspace_unsupported` with zero writes.

The legacy-workspace precondition comes from `write_legacy_control_files` rather
than the retired runtime-state writers. `controls/contract.py` survives, so the
`controls` command group keeps its parser registration and typed rejection.
"""

from __future__ import annotations

from functools import partial
from pathlib import Path

from multi_agent_brief.cli.main import main
from tests.helpers import write_legacy_control_files, write_workspace_files_under

ROOT = Path(__file__).resolve().parent.parent


_write_workspace_files = partial(
    write_workspace_files_under,
    config_text="""
project:
  name: "Control Switchboard Test"
  company: "Demo Holdings Ltd"
  industry: "testing"
  language: "en"
  audience: "management"
report:
  cadence: "weekly"
input:
  path: "input"
output:
  path: "output"
""".strip(),
    user_text="# User\n\nNeed management-ready brief with consumer pain point coverage.\n",
    sources_text="""
source_strategy:
  enabled_providers:
    - manual
manual:
  enabled: true
  sources: []
""".strip(),
    include_input_dir=True,
)


def _write_workspace(tmp_path: Path) -> Path:
    return write_legacy_control_files(_write_workspace_files(tmp_path))


def _snapshot_workspace_files(ws: Path) -> dict[str, bytes]:
    return {
        path.relative_to(ws).as_posix(): path.read_bytes()
        for path in ws.rglob("*")
        if path.is_file()
    }


def test_retired_controls_cli_fails_closed_without_writes(tmp_path, capsys):
    ws = _write_workspace(tmp_path)

    retired_argv = [
        ["controls", "build-switchboard", "--workspace", str(ws), "--repo-workdir", str(ROOT), "--json"],
        ["controls", "show", "--workspace", str(ws), "--json"],
        [
            "controls", "select", "--workspace", str(ws),
            "--control", "quality_gates", "--selection", "enable",
            "--reason", "Use gates.", "--json",
        ],
        ["controls", "validate", "--workspace", str(ws), "--strict", "--json"],
    ]
    for argv in retired_argv:
        before_files = _snapshot_workspace_files(ws)
        rc = main(argv)
        output = capsys.readouterr().out

        assert rc == 1, argv
        assert output == "legacy_workspace_unsupported\n", argv
        assert _snapshot_workspace_files(ws) == before_files, argv


def test_retired_state_cli_fails_closed_without_writes(tmp_path, capsys):
    ws = _write_workspace(tmp_path)

    retired_argv = [
        ["state", "init", "--runtime", "operator", "--workspace", str(ws), "--repo-workdir", str(ROOT), "--reset-state"],
        ["state", "check", "--workspace", str(ws), "--repo-workdir", str(ROOT), "--json"],
    ]
    for argv in retired_argv:
        before_files = _snapshot_workspace_files(ws)
        rc = main(argv)
        output = capsys.readouterr().out

        assert rc == 1, argv
        assert output == "legacy_workspace_unsupported\n", argv
        assert _snapshot_workspace_files(ws) == before_files, argv


def test_retired_operator_run_cli_fails_closed_without_writes(tmp_path, capsys):
    ws = _write_workspace(tmp_path)

    retired_argv = [
        ["run", "--runtime", "operator", "--workspace", str(ws), "--repo-workdir", str(ROOT)],
        ["run", "--runtime", "operator", "--workspace", str(ws), "--skip-doctor", "--repo-workdir", str(ROOT)],
    ]
    for argv in retired_argv:
        before_files = _snapshot_workspace_files(ws)
        rc = main(argv)
        output = capsys.readouterr().out

        assert rc == 1, argv
        assert output == "legacy_workspace_unsupported\n", argv
        assert _snapshot_workspace_files(ws) == before_files, argv
