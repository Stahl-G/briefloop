"""Retired-surface guards for the `feedback` and `state` public CLIs.

LD2-3 removes the `feedback/` package and the legacy runtime-state stack, so the
feedback-issue and workflow-transition tests that drove them are gone. The
typed-rejection probes below are live contracts, not residue: they hold both
fail-closed stubs to `legacy_workspace_unsupported` with zero writes.

The legacy-workspace precondition comes from `write_legacy_control_files` rather
than the retired runtime-state writers.
"""

from __future__ import annotations

from functools import partial
from pathlib import Path

import pytest

from multi_agent_brief.cli.main import main
from tests.helpers import write_legacy_control_files, write_workspace_files_under

ROOT = Path(__file__).resolve().parent.parent


_write_workspace_files = partial(
    write_workspace_files_under,
    config_text="""
project:
  name: "Feedback Test"
output:
  path: "output"
input:
  path: "input"
""".strip(),
    user_text="# User\n",
    include_input_dir=True,
)


def _write_workspace(tmp_path: Path) -> Path:
    return write_legacy_control_files(_write_workspace_files(tmp_path))


def _issues_path(ws: Path) -> Path:
    return ws / "output" / "intermediate" / "feedback_issues.json"


def _plan_path(ws: Path) -> Path:
    return ws / "output" / "intermediate" / "repair_plan.json"


def _workspace_file_bytes(ws: Path) -> dict[str, bytes]:
    return {
        path.relative_to(ws).as_posix(): path.read_bytes()
        for path in sorted(ws.rglob("*"))
        if path.is_file()
    }


@pytest.mark.parametrize("action", ["ingest", "plan", "resolve", "validate"])
def test_feedback_public_cli_is_retired_with_typed_rejection(tmp_path, capsys, action):
    ws = _write_workspace(tmp_path)
    feedback = tmp_path / "feedback.txt"
    feedback.write_text("Retired surface probe.\n", encoding="utf-8")
    args = ["feedback", action, "--workspace", str(ws), "--repo-workdir", str(ROOT), "--json"]
    if action == "ingest":
        args += ["--feedback", str(feedback), "--source", "human"]
    if action == "resolve":
        args += ["--issue-id", "issue_retired", "--repair-plan-id", "rp_retired", "--reason", "probe"]
    before = _workspace_file_bytes(ws)

    rc = main(args)

    assert rc == 1
    assert capsys.readouterr().out.strip() == "legacy_workspace_unsupported"
    assert _workspace_file_bytes(ws) == before
    assert not _issues_path(ws).exists()
    assert not _plan_path(ws).exists()


@pytest.mark.parametrize(
    "action_args",
    [
        ["stage-complete", "--stage", "analyst", "--reason", "probe"],
        ["decide", "--stage", "analyst", "--decision", "delegate_repair", "--reason", "probe"],
    ],
)
def test_state_public_cli_is_retired_with_typed_rejection(tmp_path, capsys, action_args):
    ws = _write_workspace(tmp_path)
    before = _workspace_file_bytes(ws)

    rc = main([
        "state",
        *action_args,
        "--workspace",
        str(ws),
        "--repo-workdir",
        str(ROOT),
        "--json",
    ])

    assert rc == 1
    assert capsys.readouterr().out.strip() == "legacy_workspace_unsupported"
    assert _workspace_file_bytes(ws) == before
