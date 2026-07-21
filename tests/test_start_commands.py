"""Tests for briefloop start / handoff launcher."""
from __future__ import annotations

import json
import re
from functools import partial
from pathlib import Path

import yaml
import pytest

from multi_agent_brief.cli.main import main
from multi_agent_brief.orchestrator.fact_layer_import import require_fast_rerun_handoff_ready
from multi_agent_brief.orchestrator_contract import contract_references_exist
from multi_agent_brief.orchestrator_contract import resolve_repo_workdir
from multi_agent_brief.audience_memory import AUDIENCE_MEMORY_FILES
from multi_agent_brief.controls.contract import CONTROL_SWITCHBOARD_FILES
from multi_agent_brief.quality_gates.contract import QUALITY_GATE_STATE_FILES
from multi_agent_brief.provenance.contract import PROVENANCE_STATE_FILES
from tests.helpers import write_legacy_control_files, sha256_file as _sha256_file
from tests.helpers import write_workspace_files_under


ROOT = Path(__file__).resolve().parent.parent


_write_workspace = partial(
    write_workspace_files_under,
    config_text="""
project:
  name: "Test Brief"
  company: "TestCo"
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
    user_text="# Test User Profile\n\nCompany: TestCo\n",
    sources_text="""
source_strategy:
  profile: "conservative"
  enabled_providers:
    - "manual"
manual:
  enabled: true
  sources: []
""".strip(),
    include_input_dir=True,
)




def _snapshot_workspace_bytes(ws: Path) -> dict[str, bytes]:
    return {
        path.relative_to(ws).as_posix(): path.read_bytes()
        for path in ws.rglob("*")
        if path.is_file()
    }




















# ---------------------------------------------------------------------------
# Help and identity tests
# ---------------------------------------------------------------------------

def test_start_help_shows_runtime_options(capsys):
    """start --help must show runtime choices and launcher identity."""
    try:
        main(["start", "--help"])
    except SystemExit:
        pass
    captured = capsys.readouterr()
    output = captured.out
    assert "launcher" in output.lower() or "handoff" in output.lower()
    assert "--runtime" in output
    assert "--recipe" in output
    assert "hermes" in output
    assert "claude" in output
    assert "operator" in output
    assert "manual" not in output
    assert "{hermes,claude,opencode,codex,codebuddy,operator}" in output
    assert "--workspace" in output


def test_start_help_does_not_claim_to_generate_briefs(capsys):
    """start help must not present itself as a brief generator."""
    try:
        main(["start", "--help"])
    except SystemExit:
        pass
    captured = capsys.readouterr()
    output = captured.out
    assert "generate" not in output.lower() or "never generates" in output.lower()


def test_handoff_help_shows_config_required(capsys):
    try:
        main(["handoff", "--help"])
    except SystemExit:
        pass
    captured = capsys.readouterr()
    output = captured.out
    assert "--config" in output
    assert "--runtime" in output


# ---------------------------------------------------------------------------
# start — no workspace
# ---------------------------------------------------------------------------

def test_start_no_workspace_in_non_workspace_dir(tmp_path, monkeypatch, capsys):
    """start without --workspace in a non-workspace dir should give guidance."""
    monkeypatch.chdir(tmp_path)
    rc = main(["start", "--runtime", "operator", "--skip-doctor"])
    assert rc == 1
    captured = capsys.readouterr()
    output = captured.out
    assert "No workspace found" in output or "briefloop init" in output




# ---------------------------------------------------------------------------
# start — with workspace
# ---------------------------------------------------------------------------



























# ---------------------------------------------------------------------------
# start — runtime variants
# ---------------------------------------------------------------------------









def test_run_fast_rerun_recipe_requires_fact_layer_import(tmp_path):
    ws = _write_workspace(tmp_path)

    with pytest.raises(ValueError) as excinfo:
        require_fast_rerun_handoff_ready(ws)
    message = str(excinfo.value)
    assert "E_FAST_RERUN_IMPORT_REQUIRED" in message
    assert (
        "briefloop state import-fact-layer --workspace <workspace> "
        "--archive <output/runs/run_id> "
        "--runtime <hermes|claude|opencode|codex|codebuddy|operator>"
    ) in message
    assert not (ws / "output" / "intermediate" / "agent_handoff.json").exists()














def test_start_rejects_historical_runtime_without_writes(tmp_path):
    ws = _write_workspace(tmp_path)
    with pytest.raises(SystemExit):
        main([
            "start",
            "--workspace", str(ws),
            "--runtime", "manual",
            "--skip-doctor",
            "--venv", str(tmp_path / ".venv" / "bin" / "activate"),
        ])
    assert not (ws / "output").exists()


# ---------------------------------------------------------------------------
# handoff
# ---------------------------------------------------------------------------



def test_handoff_no_config_fails(tmp_path):
    rc = main(["handoff", "--runtime", "operator", "--config", str(tmp_path / "nonexistent" / "config.yaml"), "--skip-doctor"])
    assert rc != 0


# ---------------------------------------------------------------------------
# build_handoff direct unit tests
# ---------------------------------------------------------------------------















# ---------------------------------------------------------------------------
# write_handoff_artifacts
# ---------------------------------------------------------------------------





# ---------------------------------------------------------------------------
# run command — launcher identity
# ---------------------------------------------------------------------------

def test_run_help_does_not_contain_deprecated(capsys):
    """run --help must not contain deprecated/prepare/deterministic pipeline language."""
    try:
        main(["run", "--help"])
    except SystemExit:
        pass
    output = capsys.readouterr().out
    assert "deprecated" not in output.lower()
    assert "deterministic pipeline" not in output.lower()
    assert "never generates" not in output.lower()


def test_run_requires_explicit_runtime_without_writes(tmp_path):
    ws = _write_workspace(tmp_path)
    argv = [
        "run",
        "--workspace", str(ws),
        "--skip-doctor",
        "--venv", str(tmp_path / ".venv" / "bin" / "activate"),
    ]
    with pytest.raises(SystemExit):
        main(argv)
    assert not (ws / "output").exists()






def test_prepare_output_points_to_run(tmp_path, capsys):
    """prepare is a retired public path: typed rejection with zero writes."""
    ws = _write_workspace(tmp_path)
    before = _snapshot_workspace_bytes(ws)
    # retired public `prepare` launcher and its legacy guidance text.
    rc = main(["prepare", "--config", str(ws / "config.yaml")])
    assert rc == 1
    assert capsys.readouterr().out == "runtime_command_unsupported\n"
    assert _snapshot_workspace_bytes(ws) == before


def test_retired_launcher_public_paths_reject_without_writes(tmp_path, monkeypatch, capsys):
    """Bounded rejection matrix for the retired run/start/handoff launcher surface."""
    venv = str(tmp_path / ".venv" / "bin" / "activate")

    def assert_rejected(ws: Path, argv: list[str], expected: str) -> None:
        before = _snapshot_workspace_bytes(ws)
        assert main(argv) == 1
        assert capsys.readouterr().out == expected
        assert _snapshot_workspace_bytes(ws) == before

    # retired public `start` launcher (explicit --workspace).
    ws_start = _write_workspace(tmp_path / "start-flag")
    assert_rejected(
        ws_start,
        ["start", "--runtime", "operator", "--workspace", str(ws_start), "--skip-doctor", "--venv", venv],
        "runtime_command_unsupported\n",
    )
    # retired `start` CWD workspace auto-detection.
    ws_start_cwd = _write_workspace(tmp_path / "start-cwd")
    monkeypatch.chdir(ws_start_cwd)
    assert_rejected(
        ws_start_cwd,
        ["start", "--runtime", "operator", "--skip-doctor", "--venv", venv],
        "[start] runtime_command_unsupported\n",
    )
    # retired non-codex `run` runtime adapters (operator/claude).
    ws_run = _write_workspace(tmp_path / "run-operator")
    assert_rejected(
        ws_run,
        ["run", "--runtime", "operator", "--workspace", str(ws_run), "--skip-doctor", "--venv", venv],
        "[run] runtime_adapter_unsupported\n",
    )
    ws_rerun = _write_workspace(tmp_path / "run-fast-rerun")
    assert_rejected(
        ws_rerun,
        [
            "run", "--runtime", "claude", "--recipe", "fast-rerun",
            "--workspace", str(ws_rerun), "--skip-doctor", "--venv", venv,
        ],
        "[run] runtime_adapter_unsupported\n",
    )
    # retired `run --skip-doctor` launcher path for the codex runtime.
    ws_codex = _write_workspace(tmp_path / "run-codex")
    assert_rejected(
        ws_codex,
        ["run", "--runtime", "codex", "--workspace", str(ws_codex), "--skip-doctor", "--venv", venv],
        "[run] runtime_command_unsupported\n",
    )
    # retired public `handoff` generator command.
    ws_handoff = _write_workspace(tmp_path / "handoff")
    assert_rejected(
        ws_handoff,
        ["handoff", "--config", str(ws_handoff / "config.yaml"), "--runtime", "hermes", "--skip-doctor", "--venv", venv],
        "runtime_command_unsupported\n",
    )
    # legacy JSON control-plane workspaces are refused by every command.
    ws_legacy = _write_workspace(tmp_path / "legacy")
    write_legacy_control_files(ws_legacy)
    assert_rejected(
        ws_legacy,
        ["run", "--runtime", "claude", "--workspace", str(ws_legacy), "--skip-doctor", "--venv", venv],
        "legacy_workspace_unsupported\n",
    )


# ---------------------------------------------------------------------------
# onboard command discoverability
# ---------------------------------------------------------------------------

def test_onboard_help_exists(capsys):
    """onboard --help must exist as a discoverable command."""
    try:
        main(["onboard", "--help"])
    except SystemExit:
        pass
    output = capsys.readouterr().out
    assert "onboard" in output
    assert "onboarding" in output.lower()


def test_init_help_mentions_onboard(capsys):
    """init --help must reference onboard as the first step."""
    try:
        main(["init", "--help"])
    except SystemExit:
        pass
    output = capsys.readouterr().out
    assert "onboard" in output


def test_run_no_workspace_mentions_onboard(tmp_path, capsys):
    """run without a workspace must suggest onboard as the first path."""
    rc = main(["run", "--runtime", "operator", "--workspace", str(tmp_path / "no-such-ws"), "--skip-doctor"])
    assert rc == 1
    captured = capsys.readouterr()
    output = captured.out
    assert "briefloop onboard" in output
    assert "briefloop init" in output
    assert "--from-onboarding onboarding.json" in output


def test_init_demo_mentions_onboard(tmp_path, capsys):
    """init --demo must say it's a demo and point to onboard for real projects."""
    ws = tmp_path / "demo-ws"
    rc = main(["init", str(ws), "--demo", "--force"])
    assert rc == 0
    captured = capsys.readouterr()
    output = captured.out
    assert "demo" in output.lower()
    assert "briefloop onboard" in output
    assert "input/context" in output
    assert "example brief Markdown" in output
    input_readme = (ws / "input" / "README.md").read_text(encoding="utf-8")
    context_readme = (ws / "input" / "context" / "README.md").read_text(
        encoding="utf-8"
    )
    assert "prior weekly reports" in input_readme
    assert "input/context/" in input_readme
    assert "previous_weekly_reference.md" in context_readme
