"""Legacy control-switchboard internals and CX public-boundary tests."""

from __future__ import annotations

import hashlib
import json
from functools import partial
from pathlib import Path

import pytest

from multi_agent_brief.cli.main import main
from multi_agent_brief.controls.contract import CONTROL_SWITCHBOARD_FILES
from multi_agent_brief.controls.switchboard import (
    build_control_switchboard,
    refresh_control_switchboard_if_stale,
)
from multi_agent_brief.improvement.memory import freeze_improvement_memory_for_run
from multi_agent_brief.improvement.state import approve_improvement, propose_improvement
from multi_agent_brief.orchestrator.runtime_state import initialize_runtime_state
from tests.helpers import write_workspace_files_under


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
    ws = _write_workspace_files(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT, runtime="operator")
    return ws


def _workspace_bytes(ws: Path) -> dict[str, str]:
    return {
        path.relative_to(ws).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(ws.rglob("*"))
        if path.is_file()
    }


def _propose_and_approve_improvement(ws: Path) -> str:
    state = propose_improvement(
        workspace=ws,
        guidance="Start with the decision-relevant implication before implementation detail.",
        category="structure",
        scope="brief",
        source_summary="Synthetic public control-switchboard test preference.",
    )
    entry_id = str(state["entry"]["entry_id"])
    approve_improvement(workspace=ws, entry_id=entry_id, approved_by="reviewer")
    return entry_id


def _control_by_id(switchboard: dict, control_id: str) -> dict:
    for control in switchboard["controls"]:
        if control["control_id"] == control_id:
            return control
    raise AssertionError(f"control not found: {control_id}")


def _set_current_stage(ws: Path, stage_id: str) -> None:
    workflow_path = ws / "output" / "intermediate" / "workflow_state.json"
    workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    workflow["current_stage"] = stage_id
    workflow["blocked"] = False
    workflow["blocking_reason"] = ""
    workflow_path.write_text(
        json.dumps(workflow, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    ("command", "legacy_initialized", "expected_error"),
    [
        (
            ["controls", "build-switchboard", "--repo-workdir", str(ROOT), "--json"],
            True,
            "legacy_workspace_unsupported",
        ),
        (["controls", "show", "--json"], True, "legacy_workspace_unsupported"),
        (
            ["controls", "validate", "--strict", "--json"],
            True,
            "legacy_workspace_unsupported",
        ),
        (
            [
                "controls",
                "select",
                "--control",
                "quality_gates",
                "--selection",
                "enable",
                "--reason",
                "Use gates before finalize.",
                "--json",
            ],
            True,
            "legacy_workspace_unsupported",
        ),
        (
            [
                "run",
                "--runtime",
                "operator",
                "--skip-doctor",
                "--repo-workdir",
                str(ROOT),
            ],
            False,
            "[run] runtime_adapter_unsupported",
        ),
        (
            ["state", "init", "--runtime", "operator", "--repo-workdir", str(ROOT)],
            False,
            "runtime_command_unsupported",
        ),
        (
            ["state", "check", "--repo-workdir", str(ROOT)],
            True,
            "legacy_workspace_unsupported",
        ),
    ],
)
def test_closed_legacy_control_public_paths_fail_before_writes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    command: list[str],
    legacy_initialized: bool,
    expected_error: str,
) -> None:
    ws = _write_workspace_files(tmp_path)
    if legacy_initialized:
        initialize_runtime_state(workspace=ws, repo_workdir=ROOT, runtime="operator")
    before = _workspace_bytes(ws)

    rc = main([*command, "--workspace", str(ws)])

    assert rc == 1
    assert capsys.readouterr().out.strip() == expected_error
    assert _workspace_bytes(ws) == before
    assert not (ws / "briefloop.db").exists()
    if not legacy_initialized:
        assert not (ws / "output" / "intermediate" / "runtime_manifest.json").exists()
    assert not (
        ws / "output" / "intermediate" / "orchestrator_control_switchboard.json"
    ).exists()
    assert not (ws / "output" / "intermediate" / "control_selections.json").exists()


def test_limitation_hygiene_control_uses_ledger_cli_contract(tmp_path: Path) -> None:
    ws = _write_workspace(tmp_path)
    state = build_control_switchboard(workspace=ws, repo_workdir=ROOT)

    control = _control_by_id(
        state["orchestrator_control_switchboard"], "limitation_hygiene"
    )

    assert (
        "limitation-hygiene --ledger <workspace>/output/intermediate/claim_ledger.json"
        in control["execution_hint"]
    )
    assert "--brief" not in control["execution_hint"]
    assert control["inputs"] == ["output/intermediate/claim_ledger.json"]
    assert all(
        not Path(rel_path).is_absolute()
        for rel_path in CONTROL_SWITCHBOARD_FILES.values()
    )


def test_build_switchboard_reuses_existing_runtime_state_without_reinitializing(
    tmp_path: Path,
) -> None:
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(runtime="operator", workspace=ws, repo_workdir=ROOT)

    state = build_control_switchboard(workspace=ws, repo_workdir=ROOT)

    assert state["orchestrator_control_switchboard"]["run_id"]


def test_switchboard_refresh_preserves_improvement_manifest(tmp_path: Path) -> None:
    ws = _write_workspace(tmp_path)
    state = initialize_runtime_state(
        runtime="operator", workspace=ws, repo_workdir=ROOT
    )
    run_id = str(state["manifest"]["run_id"])
    entry_id = _propose_and_approve_improvement(ws)
    freeze_improvement_memory_for_run(workspace=ws, run_id=run_id)
    build_control_switchboard(workspace=ws, repo_workdir=ROOT)
    before = json.loads(
        (ws / "output" / "intermediate" / "runtime_manifest.json").read_text(
            encoding="utf-8"
        )
    )["improvement"]

    (ws / "user.md").write_text(
        "# User\n\nNeed management-ready brief with updated context.\n",
        encoding="utf-8",
    )
    refreshed = refresh_control_switchboard_if_stale(workspace=ws, repo_workdir=ROOT)
    after = json.loads(
        (ws / "output" / "intermediate" / "runtime_manifest.json").read_text(
            encoding="utf-8"
        )
    )["improvement"]

    assert before["materialized_entry_ids"] == [entry_id]
    assert refreshed is not None
    assert after == before


def test_switchboard_quality_gate_hint_uses_finalize_stage_when_current_stage_finalize(
    tmp_path: Path,
) -> None:
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(runtime="operator", workspace=ws, repo_workdir=ROOT)
    _set_current_stage(ws, "finalize")
    out = ws / "output"
    intermediate = out / "intermediate"
    out.mkdir(parents=True, exist_ok=True)
    intermediate.mkdir(parents=True, exist_ok=True)
    (out / "brief.md").write_text("# Reader Brief\n", encoding="utf-8")
    (intermediate / "claim_ledger.json").write_text(
        '{"claims": []}\n', encoding="utf-8"
    )

    switchboard = build_control_switchboard(
        workspace=ws,
        repo_workdir=ROOT,
    )["orchestrator_control_switchboard"]
    control = _control_by_id(switchboard, "quality_gates")

    assert control["recommendation"] == "required"
    assert control["selection_required"] is True
    assert control["execution_hint"] == (
        "briefloop gates check --workspace <workspace> "
        "--stage finalize --brief <workspace>/output/brief.md"
    )
    assert control["inputs"] == [
        "output/brief.md",
        "output/intermediate/claim_ledger.json",
    ]
    assert control["outputs"] == [
        "output/intermediate/gates/finalize_quality_gate_report.json",
        "output/intermediate/quality_gate_report.json",
    ]
