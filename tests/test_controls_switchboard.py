"""Tests for v0.6.7 Orchestrator control switchboard."""

from __future__ import annotations

import json
from functools import partial
from pathlib import Path

import pytest

import multi_agent_brief.controls.switchboard as switchboard_module
from multi_agent_brief.cli.main import main
from multi_agent_brief.controls.contract import CONTROL_SWITCHBOARD_FILES, ControlSwitchboardError
from multi_agent_brief.controls.switchboard import (
    build_control_switchboard,
    refresh_control_switchboard_if_stale,
    select_control,
    show_control_switchboard,
    validate_control_switchboard,
)
from multi_agent_brief.improvement.memory import freeze_improvement_memory_for_run
from multi_agent_brief.improvement.state import approve_improvement, propose_improvement
from multi_agent_brief.orchestrator.handoff import build_handoff, write_handoff_and_state
from multi_agent_brief.orchestrator.runtime_state import check_runtime_state, initialize_runtime_state
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


def _write_uninitialized_workspace(tmp_path: Path) -> Path:
    return _write_workspace_files(tmp_path)


def _run_operator_handoff(ws: Path, *, doctor_status: str = "not_run") -> None:
    """Drive the retired `run --runtime operator` flow through its direct deterministic seam."""
    handoff = build_handoff(workspace=ws, repo_workdir=ROOT, runtime="operator", run_doctor=False)
    handoff.doctor_status = doctor_status
    written = write_handoff_and_state(handoff=handoff, workspace=ws, repo_workdir=ROOT, prefix="[run]")
    assert written is not None


def _snapshot_workspace_files(ws: Path) -> dict[str, bytes]:
    return {
        path.relative_to(ws).as_posix(): path.read_bytes()
        for path in ws.rglob("*")
        if path.is_file()
    }


def _event_types(ws: Path) -> list[str]:
    return [event["event_type"] for event in _events(ws)]


def _events(ws: Path) -> list[dict]:
    path = ws / "output" / "intermediate" / "event_log.jsonl"
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


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
    workflow_path.write_text(json.dumps(workflow, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def test_retired_controls_cli_fails_closed_without_writes(tmp_path, capsys):
    ws = _write_workspace(tmp_path)
    build_control_switchboard(workspace=ws, repo_workdir=ROOT)

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

        # retired public `controls` CLI over legacy JSON control state.
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

        # retired public `state` CLI over legacy JSON control state.
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

        # retired public `run --runtime operator` launcher.
        assert rc == 1, argv
        assert output == "legacy_workspace_unsupported\n", argv
        assert _snapshot_workspace_files(ws) == before_files, argv


def test_controls_build_show_validate_are_machine_readable(tmp_path):
    ws = _write_workspace(tmp_path)

    payload = build_control_switchboard(workspace=ws, repo_workdir=ROOT)

    assert payload["ok"] is True
    switchboard = payload["orchestrator_control_switchboard"]
    assert switchboard["schema_version"] == "multi-agent-brief-orchestrator-control-switchboard/v1"
    assert len(switchboard["controls"]) == 8
    assert {item["control_id"] for item in switchboard["controls"]} >= {
        "quality_gates",
        "local_signal_discovery",
        "consumer_pain_point_discovery",
    }
    for rel_path in CONTROL_SWITCHBOARD_FILES.values():
        assert not Path(rel_path).is_absolute()

    shown = show_control_switchboard(workspace=ws)
    assert shown["ok"] is True
    assert shown["orchestrator_control_switchboard"]["run_id"] == switchboard["run_id"]

    validation = validate_control_switchboard(workspace=ws)
    assert validation["ok"] is True
    assert "control_switchboard_built" in _event_types(ws)


def test_limitation_hygiene_control_uses_ledger_cli_contract(tmp_path):
    ws = _write_workspace(tmp_path)
    state = build_control_switchboard(workspace=ws, repo_workdir=ROOT)

    control = _control_by_id(state["orchestrator_control_switchboard"], "limitation_hygiene")

    assert "limitation-hygiene --ledger <workspace>/output/intermediate/claim_ledger.json" in control["execution_hint"]
    assert "--brief" not in control["execution_hint"]
    assert control["inputs"] == ["output/intermediate/claim_ledger.json"]


def test_build_switchboard_reuses_existing_runtime_state_without_reinitializing(tmp_path, monkeypatch):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(runtime="operator", workspace=ws, repo_workdir=ROOT)

    def fail_initialize_runtime_state(**_kwargs):
        raise AssertionError("build_control_switchboard must not reinitialize existing runtime state")

    state = build_control_switchboard(workspace=ws, repo_workdir=ROOT)

    assert state["orchestrator_control_switchboard"]["run_id"]


def test_switchboard_refresh_preserves_improvement_manifest(tmp_path):
    ws = _write_workspace(tmp_path)
    state = initialize_runtime_state(runtime="operator", workspace=ws, repo_workdir=ROOT)
    run_id = str(state["manifest"]["run_id"])
    entry_id = _propose_and_approve_improvement(ws)
    freeze_improvement_memory_for_run(workspace=ws, run_id=run_id)
    build_control_switchboard(workspace=ws, repo_workdir=ROOT)
    before = json.loads(
        (ws / "output" / "intermediate" / "runtime_manifest.json").read_text(encoding="utf-8")
    )["improvement"]

    (ws / "user.md").write_text(
        "# User\n\nNeed management-ready brief with updated context.\n",
        encoding="utf-8",
    )
    refreshed = refresh_control_switchboard_if_stale(workspace=ws, repo_workdir=ROOT)
    after = json.loads(
        (ws / "output" / "intermediate" / "runtime_manifest.json").read_text(encoding="utf-8")
    )["improvement"]

    assert before["materialized_entry_ids"] == [entry_id]
    assert refreshed is not None
    assert after == before


def test_controls_select_enable_does_not_execute_quality_gates(tmp_path):
    ws = _write_workspace(tmp_path)
    assert build_control_switchboard(workspace=ws, repo_workdir=ROOT)["ok"] is True

    payload = select_control(
        workspace=ws,
        control_id="quality_gates",
        selection="enable",
        reason="Use gates before finalize.",
        actor="orchestrator",
    )

    assert payload["ok"] is True
    selections = payload["control_selections"]["selections"]
    assert selections[0]["control_id"] == "quality_gates"
    assert selections[0]["selection"] == "enable"
    assert selections[0]["execution_ready"] is True
    assert selections[0]["executed"] is False
    assert payload["control_selections"]["switchboard_context_signature"] == payload["orchestrator_control_switchboard"]["context_signature"]
    assert selections[0]["switchboard_context_signature"] == payload["orchestrator_control_switchboard"]["context_signature"]
    assert not (ws / "output" / "intermediate" / "quality_gate_report.json").exists()
    assert "control_selection_recorded" in _event_types(ws)


def test_human_approval_control_enable_is_not_implicitly_ready(tmp_path):
    ws = _write_workspace(tmp_path)
    build_control_switchboard(workspace=ws, repo_workdir=ROOT)

    payload = select_control(
        workspace=ws,
        control_id="local_signal_discovery",
        selection="enable",
        reason="Consider local signal collection.",
        actor="orchestrator",
    )

    assert payload["ok"] is True
    selection = payload["control_selections"]["selections"][0]
    assert selection["approved_by_human"] is False
    assert selection["execution_ready"] is False
    assert selection["executed"] is False


def test_human_approval_control_records_explicit_approval_but_does_not_execute(tmp_path):
    ws = _write_workspace(tmp_path)
    build_control_switchboard(workspace=ws, repo_workdir=ROOT)

    payload = select_control(
        workspace=ws,
        control_id="consumer_pain_point_discovery",
        selection="enable",
        approved_by_human=True,
        human_approval_ref="approval:SYNTHETIC",
        reason="Human approved scoped review mining.",
        actor="orchestrator",
    )

    assert payload["ok"] is True
    selection = payload["control_selections"]["selections"][0]
    assert selection["approved_by_human"] is True
    assert selection["execution_ready"] is True
    assert selection["executed"] is False
    assert not (ws / "output" / "intermediate" / "local_signal_report.json").exists()


def test_human_approval_enable_requires_approval_ref(tmp_path):
    ws = _write_workspace(tmp_path)
    build_control_switchboard(workspace=ws, repo_workdir=ROOT)

    with pytest.raises(ControlSwitchboardError, match="Human approval reference is required"):
        select_control(
            workspace=ws,
            control_id="local_signal_discovery",
            selection="enable",
            approved_by_human=True,
            reason="Human approved local signal collection.",
            actor="orchestrator",
        )

    assert not (ws / "output" / "intermediate" / "control_selections.json").exists()


def test_controls_reject_unknown_control_and_invalid_selection(tmp_path):
    ws = _write_workspace(tmp_path)
    build_control_switchboard(workspace=ws, repo_workdir=ROOT)

    with pytest.raises(ControlSwitchboardError, match="Unknown control id"):
        select_control(
            workspace=ws,
            control_id="unknown_control",
            selection="enable",
            reason="Bad control.",
            actor="orchestrator",
        )

    with pytest.raises(ControlSwitchboardError, match="Invalid control selection"):
        select_control(
            workspace=ws,
            control_id="quality_gates",
            selection="execute",
            reason="Bad selection.",
            actor="orchestrator",
        )


def test_controls_strict_required_selection_does_not_block_runtime_state(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(runtime="operator", workspace=ws, repo_workdir=ROOT)
    out = ws / "output" / "intermediate"
    out.mkdir(parents=True, exist_ok=True)
    (out / "audited_brief.md").write_text("# Audited Brief\n", encoding="utf-8")

    assert build_control_switchboard(workspace=ws, repo_workdir=ROOT)["ok"] is True
    validation = validate_control_switchboard(workspace=ws, strict=True)

    assert validation["ok"] is False
    assert any("required selections" in error for error in validation["errors"])

    state = check_runtime_state(workspace=ws, repo_workdir=ROOT)
    assert state["workflow_state"]["current_stage"] == "doctor"
    assert state["workflow_state"]["blocked"] is False


def test_run_creates_switchboard_but_not_selections_or_gate_report(tmp_path):
    ws = _write_workspace(tmp_path)

    _run_operator_handoff(ws)

    assert (ws / "output" / "intermediate" / "orchestrator_control_switchboard.json").exists()
    assert not (ws / "output" / "intermediate" / "control_selections.json").exists()
    assert not (ws / "output" / "intermediate" / "quality_gate_report.json").exists()

    manifest = json.loads((ws / "output" / "intermediate" / "runtime_manifest.json").read_text(encoding="utf-8"))
    assert manifest["runtime"] == "operator"


def test_run_builds_switchboard_after_doctor_decision(tmp_path):
    ws = _write_workspace(tmp_path)

    _run_operator_handoff(ws, doctor_status="passed")

    events = _events(ws)
    doctor_decision_index = next(
        idx
        for idx, event in enumerate(events)
        if event["event_type"] == "decision_recorded"
        and event.get("stage_id") == "doctor"
    )
    switchboard_index = next(
        idx
        for idx, event in enumerate(events)
        if event["event_type"] == "control_switchboard_built"
    )
    assert doctor_decision_index < switchboard_index

    workflow = json.loads((ws / "output" / "intermediate" / "workflow_state.json").read_text(encoding="utf-8"))
    assert workflow["current_stage"] == "source-discovery"
    assert validate_control_switchboard(workspace=ws)["ok"] is True


def test_stale_switchboard_after_reset_must_be_rebuilt(tmp_path):
    ws = _write_workspace(tmp_path)

    old_switchboard = build_control_switchboard(workspace=ws, repo_workdir=ROOT)["orchestrator_control_switchboard"]

    initialize_runtime_state(runtime="operator", workspace=ws, repo_workdir=ROOT, reset_state=True)
    current_manifest = json.loads((ws / "output" / "intermediate" / "runtime_manifest.json").read_text(encoding="utf-8"))
    assert current_manifest["run_id"] != old_switchboard["run_id"]

    shown = show_control_switchboard(workspace=ws)
    assert shown["ok"] is False
    assert any("run_id does not match" in error and "build-switchboard" in error for error in shown["validation"]["errors"])

    validation = validate_control_switchboard(workspace=ws)
    assert validation["ok"] is False
    assert any("run_id does not match" in error and "build-switchboard" in error for error in validation["errors"])
    assert _events(ws)[-1]["event_type"] == "control_selection_validated"
    assert _events(ws)[-1]["run_id"] == current_manifest["run_id"]

    with pytest.raises(ControlSwitchboardError) as exc_info:
        select_control(
            workspace=ws,
            control_id="quality_gates",
            selection="enable",
            reason="Use gates.",
            actor="orchestrator",
        )
    assert any("run_id does not match" in error for error in exc_info.value.details["errors"])
    assert not (ws / "output" / "intermediate" / "control_selections.json").exists()
    assert "control_selection_recorded" not in _event_types(ws)

    rebuilt = build_control_switchboard(workspace=ws, repo_workdir=ROOT)["orchestrator_control_switchboard"]
    assert rebuilt["run_id"] == current_manifest["run_id"]
    selected = select_control(
        workspace=ws,
        control_id="quality_gates",
        selection="enable",
        reason="Use gates in rebuilt switchboard.",
        actor="orchestrator",
    )
    assert selected["control_selections"]["run_id"] == current_manifest["run_id"]


def test_reset_state_archives_old_control_selections(tmp_path):
    ws = _write_workspace(tmp_path)

    build_control_switchboard(workspace=ws, repo_workdir=ROOT)
    select_control(
        workspace=ws,
        control_id="quality_gates",
        selection="enable",
        reason="Use gates before finalize.",
        actor="orchestrator",
    )
    first_selections = json.loads((ws / "output" / "intermediate" / "control_selections.json").read_text(encoding="utf-8"))
    first_run_id = first_selections["run_id"]

    initialize_runtime_state(runtime="operator", workspace=ws, repo_workdir=ROOT, reset_state=True)
    build_control_switchboard(workspace=ws, repo_workdir=ROOT)

    assert not (ws / "output" / "intermediate" / "control_selections.json").exists()
    assert (ws / "output" / "intermediate" / f"control_selections.{first_run_id}.json").exists()
    payload = select_control(
        workspace=ws,
        control_id="quality_gates",
        selection="enable",
        reason="Use gates in the new run.",
        actor="orchestrator",
    )
    assert payload["control_selections"]["run_id"] == payload["orchestrator_control_switchboard"]["run_id"]


def test_state_check_refreshes_stale_switchboard_recommendations(tmp_path):
    ws = _write_workspace(tmp_path)

    build_control_switchboard(workspace=ws, repo_workdir=ROOT)
    switchboard_path = ws / "output" / "intermediate" / "orchestrator_control_switchboard.json"
    switchboard = json.loads(switchboard_path.read_text(encoding="utf-8"))
    assert _control_by_id(switchboard, "quality_gates")["recommendation"] == "recommended"

    out = ws / "output" / "intermediate"
    (out / "claim_ledger.json").write_text('{"claims": []}\n', encoding="utf-8")
    (out / "audited_brief.md").write_text("# Audited Brief\n", encoding="utf-8")

    check_runtime_state(workspace=ws, repo_workdir=ROOT)
    refreshed = json.loads(switchboard_path.read_text(encoding="utf-8"))
    assert _control_by_id(refreshed, "quality_gates")["recommendation"] == "required"
    assert _control_by_id(refreshed, "quality_gates")["selection_required"] is True


def test_switchboard_quality_gate_hint_uses_finalize_stage_when_current_stage_finalize(tmp_path):
    ws = _write_workspace(tmp_path)
    initialize_runtime_state(runtime="operator", workspace=ws, repo_workdir=ROOT)
    _set_current_stage(ws, "finalize")
    out = ws / "output"
    intermediate = out / "intermediate"
    out.mkdir(parents=True, exist_ok=True)
    intermediate.mkdir(parents=True, exist_ok=True)
    (out / "brief.md").write_text("# Reader Brief\n", encoding="utf-8")
    (intermediate / "claim_ledger.json").write_text('{"claims": []}\n', encoding="utf-8")

    switchboard = build_control_switchboard(workspace=ws, repo_workdir=ROOT)["orchestrator_control_switchboard"]
    control = _control_by_id(switchboard, "quality_gates")

    assert control["recommendation"] == "required"
    assert control["selection_required"] is True
    assert control["execution_hint"] == (
        "briefloop gates check --workspace <workspace> "
        "--stage finalize --brief <workspace>/output/brief.md"
    )
    assert control["inputs"] == ["output/brief.md", "output/intermediate/claim_ledger.json"]
    assert control["outputs"] == [
        "output/intermediate/gates/finalize_quality_gate_report.json",
        "output/intermediate/quality_gate_report.json",
    ]


def test_switchboard_refresh_archives_same_run_stale_control_selections(tmp_path):
    ws = _write_workspace(tmp_path)

    build_control_switchboard(workspace=ws, repo_workdir=ROOT)
    original = select_control(
        workspace=ws,
        control_id="quality_gates",
        selection="enable",
        reason="Use gates for the original context.",
        actor="orchestrator",
    )
    original_signature = original["orchestrator_control_switchboard"]["context_signature"]
    run_id = original["control_selections"]["run_id"]

    out = ws / "output" / "intermediate"
    (out / "claim_ledger.json").write_text('{"claims": []}\n', encoding="utf-8")
    (out / "audited_brief.md").write_text("# Audited Brief\n\nUpdated context.\n", encoding="utf-8")

    rebuilt = build_control_switchboard(workspace=ws, repo_workdir=ROOT)
    assert rebuilt["orchestrator_control_switchboard"]["context_signature"] != original_signature
    assert rebuilt["control_selections"] is None
    assert not (out / "control_selections.json").exists()
    assert list(out.glob(f"control_selections.{run_id}.stale*.json"))

    selected = select_control(
        workspace=ws,
        control_id="quality_gates",
        selection="enable",
        reason="Use gates for the refreshed context.",
        actor="orchestrator",
    )
    assert selected["control_selections"]["switchboard_context_signature"] == rebuilt["orchestrator_control_switchboard"]["context_signature"]


def test_control_selection_context_signature_must_match_switchboard(tmp_path):
    ws = _write_workspace(tmp_path)

    build_control_switchboard(workspace=ws, repo_workdir=ROOT)
    select_control(
        workspace=ws,
        control_id="quality_gates",
        selection="enable",
        reason="Use gates.",
        actor="orchestrator",
    )

    selections_path = ws / "output" / "intermediate" / "control_selections.json"
    selections = json.loads(selections_path.read_text(encoding="utf-8"))
    selections["switchboard_context_signature"] = "stale"
    selections["selections"][0]["switchboard_context_signature"] = "stale"
    selections_path.write_text(json.dumps(selections, ensure_ascii=False, indent=2), encoding="utf-8")

    validation = validate_control_switchboard(workspace=ws)

    assert validation["ok"] is False
    assert any("switchboard_context_signature" in error for error in validation["errors"])


def test_state_check_switchboard_refresh_is_idempotent(tmp_path):
    ws = _write_workspace(tmp_path)

    build_control_switchboard(workspace=ws, repo_workdir=ROOT)
    initial_count = _event_types(ws).count("control_switchboard_built")
    assert initial_count == 1

    check_runtime_state(workspace=ws, repo_workdir=ROOT)
    assert _event_types(ws).count("control_switchboard_built") == initial_count

    check_runtime_state(workspace=ws, repo_workdir=ROOT)
    assert _event_types(ws).count("control_switchboard_built") == initial_count


def test_state_check_reports_control_switchboard_warning_for_bad_json(tmp_path):
    ws = _write_workspace(tmp_path)

    build_control_switchboard(workspace=ws, repo_workdir=ROOT)
    switchboard_path = ws / "output" / "intermediate" / "orchestrator_control_switchboard.json"
    switchboard_path.write_text("{not-json", encoding="utf-8")

    payload = check_runtime_state(workspace=ws, repo_workdir=ROOT)

    assert "control_switchboard_warning" in payload
    assert "Invalid JSON orchestrator_control_switchboard.json" in payload["control_switchboard_warning"]["error"]
    events = _events(ws)
    assert events[-1]["event_type"] == "control_switchboard_warning"


def test_state_check_reports_control_selection_warning_for_bad_json(tmp_path):
    ws = _write_workspace(tmp_path)

    build_control_switchboard(workspace=ws, repo_workdir=ROOT)
    selections_path = ws / "output" / "intermediate" / "control_selections.json"
    selections_path.write_text("{not-json", encoding="utf-8")

    payload = check_runtime_state(workspace=ws, repo_workdir=ROOT)

    assert "control_switchboard_warning" in payload
    assert "Invalid JSON control_selections.json" in payload["control_switchboard_warning"]["error"]
    events = _events(ws)
    assert events[-1]["event_type"] == "control_switchboard_warning"
