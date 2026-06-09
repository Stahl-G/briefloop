"""Tests for multi-agent-brief start / handoff launcher."""
from __future__ import annotations

import json
from pathlib import Path

import yaml

from multi_agent_brief.cli.main import main
from multi_agent_brief.cli.start_commands import (
    CONTRACT_REFERENCES,
    VALID_RUNTIMES,
    build_handoff,
    render_handoff_cli,
    write_handoff_artifacts,
)
from multi_agent_brief.orchestrator_contract import contract_references_exist
from multi_agent_brief.orchestrator_contract import resolve_repo_workdir
from multi_agent_brief.orchestrator.runtime_state import RUNTIME_STATE_FILES
from multi_agent_brief.audience_memory import AUDIENCE_MEMORY_FILES
from multi_agent_brief.feedback.feedback_contract import FEEDBACK_STATE_FILES
from multi_agent_brief.quality_gates.contract import QUALITY_GATE_STATE_FILES
from multi_agent_brief.provenance.contract import PROVENANCE_STATE_FILES


ROOT = Path(__file__).resolve().parent.parent


def _write_workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "input").mkdir()
    (ws / "config.yaml").write_text(
        """
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
        encoding="utf-8",
    )
    (ws / "user.md").write_text("# Test User Profile\n\nCompany: TestCo\n", encoding="utf-8")
    (ws / "sources.yaml").write_text(
        """
source_strategy:
  profile: "conservative"
  enabled_providers:
    - "manual"
manual:
  enabled: true
  sources: []
""".strip(),
        encoding="utf-8",
    )
    return ws


def _assert_orchestrator_contract_handoff(data: dict[str, object]) -> None:
    text = "\n".join(
        str(data.get(key, ""))
        for key in ("next_steps", "prompt", "notes")
    )
    assert data["contract_references"] == CONTRACT_REFERENCES
    assert data["runtime_state_files"] == RUNTIME_STATE_FILES
    assert data["audience_memory_files"] == AUDIENCE_MEMORY_FILES
    assert data["feedback_state_files"] == FEEDBACK_STATE_FILES
    assert data["quality_gate_state_files"] == QUALITY_GATE_STATE_FILES
    assert data["provenance_state_files"] == PROVENANCE_STATE_FILES
    for rel_path in data["runtime_state_files"].values():
        assert not Path(str(rel_path)).is_absolute()
    for rel_path in data["audience_memory_files"].values():
        assert not Path(str(rel_path)).is_absolute()
        assert rel_path not in data["expected_artifacts"]
    for rel_path in data["feedback_state_files"].values():
        assert not Path(str(rel_path)).is_absolute()
        assert rel_path not in data["expected_artifacts"]
    for rel_path in data["quality_gate_state_files"].values():
        assert not Path(str(rel_path)).is_absolute()
        assert rel_path not in data["expected_artifacts"]
    for rel_path in data["provenance_state_files"].values():
        assert not Path(str(rel_path)).is_absolute()
        assert rel_path not in data["expected_artifacts"]
    for rel_path in data["expected_artifacts"]:
        assert not Path(str(rel_path)).is_absolute()
    assert "Orchestrator main agent" in text or "Orchestrator main-agent" in text
    assert "configs/orchestrator_contract.yaml" in text
    assert "configs/stage_specs.yaml" in text
    assert "configs/artifact_contracts.yaml" in text
    assert "configs/policy_packs/default.yaml" in text
    assert "runtime_manifest.json" in text
    assert "workflow_state.json" in text
    assert "audience_profile_snapshot.md" in text
    assert "feedback_issues.json" in text
    assert "repair_plan.json" in text
    assert "quality_gate_report.json" in text
    assert "provenance_graph.json" in text
    assert "retry_stage" in text
    assert "request_human_review" in text
    assert "block_run" in text
    repo = Path(str(data["repo_workdir"]))
    assert contract_references_exist(repo)
    for rel_path in data["contract_references"].values():
        assert (repo / str(rel_path)).exists()


def _write_packaged_contract_base(tmp_path: Path) -> Path:
    package_base = tmp_path / "multi_agent_brief"
    for rel_path in CONTRACT_REFERENCES.values():
        target = package_base / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("placeholder: true\n", encoding="utf-8")
    (package_base / "__init__.py").write_text("", encoding="utf-8")
    return package_base


def test_resolve_repo_workdir_falls_back_to_packaged_contracts(tmp_path):
    package_base = _write_packaged_contract_base(tmp_path)

    resolved = resolve_repo_workdir(package_base)

    assert resolved == package_base.resolve()
    assert contract_references_exist(resolved)


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
    assert "hermes" in output
    assert "claude" in output
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
    rc = main(["start", "--skip-doctor"])
    assert rc == 1
    captured = capsys.readouterr()
    output = captured.out
    assert "No workspace found" in output or "multi-agent-brief init" in output


def test_start_auto_detects_workspace_in_cwd(tmp_path, monkeypatch):
    """start without --workspace should detect workspace if CWD is one."""
    ws = _write_workspace(tmp_path)
    monkeypatch.chdir(ws)
    rc = main(["start", "--skip-doctor"])
    assert rc == 0
    assert (ws / "output" / "intermediate" / "agent_handoff.md").exists()
    json_path = ws / "output" / "intermediate" / "agent_handoff.json"
    assert json_path.exists()
    assert (ws / "output" / "intermediate" / "runtime_manifest.json").exists()
    assert (ws / "output" / "intermediate" / "workflow_state.json").exists()
    assert (ws / "output" / "intermediate" / "artifact_registry.json").exists()
    assert (ws / "output" / "intermediate" / "event_log.jsonl").exists()
    assert (ws / "audience_profile.md").exists()
    assert (ws / "output" / "intermediate" / "audience_profile_snapshot.md").exists()
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert Path(data["repo_workdir"]).resolve() == ROOT
    _assert_orchestrator_contract_handoff(data)


# ---------------------------------------------------------------------------
# start — with workspace
# ---------------------------------------------------------------------------

def test_start_with_workspace_generates_handoff(tmp_path):
    ws = _write_workspace(tmp_path)
    rc = main([
        "start",
        "--workspace", str(ws),
        "--skip-doctor",
        "--venv", str(tmp_path / ".venv" / "bin" / "activate"),
    ])
    assert rc == 0

    md = ws / "output" / "intermediate" / "agent_handoff.md"
    js = ws / "output" / "intermediate" / "agent_handoff.json"
    assert md.exists()
    assert js.exists()
    assert (ws / "output" / "intermediate" / "runtime_manifest.json").exists()
    assert (ws / "output" / "intermediate" / "audience_profile_snapshot.md").exists()

    data = json.loads(js.read_text(encoding="utf-8"))
    assert data["runtime"] == "hermes"
    _assert_orchestrator_contract_handoff(data)


def test_start_does_not_generate_brief(tmp_path):
    """start must NOT generate brief.md or claim_ledger.json."""
    ws = _write_workspace(tmp_path)
    rc = main([
        "start",
        "--workspace", str(ws),
        "--skip-doctor",
        "--venv", str(tmp_path / ".venv" / "bin" / "activate"),
    ])
    assert rc == 0
    assert not (ws / "output" / "brief.md").exists()
    assert not (ws / "output" / "intermediate" / "claim_ledger.json").exists()
    assert not (ws / "output" / "intermediate" / "audited_brief.md").exists()
    assert not (ws / "output" / "intermediate" / "candidate_claims.json").exists()
    assert not (ws / "output" / "intermediate" / "screened_candidates.json").exists()
    assert not (ws / "output" / "intermediate" / "audit_report.json").exists()
    assert not (ws / "output" / "intermediate" / "feedback_issues.json").exists()
    assert not (ws / "output" / "intermediate" / "repair_plan.json").exists()
    assert not (ws / "output" / "intermediate" / "delta_audit_report.json").exists()
    assert not (ws / "output" / "intermediate" / "quality_gate_report.json").exists()
    assert not (ws / "output" / "intermediate" / "provenance_graph.json").exists()


# ---------------------------------------------------------------------------
# start — runtime variants
# ---------------------------------------------------------------------------

def test_start_hermes_handoff_contains_delegate_task(tmp_path):
    ws = _write_workspace(tmp_path)
    rc = main([
        "start",
        "--workspace", str(ws),
        "--runtime", "hermes",
        "--skip-doctor",
        "--venv", str(tmp_path / ".venv" / "bin" / "activate"),
    ])
    assert rc == 0
    data = json.loads((ws / "output" / "intermediate" / "agent_handoff.json").read_text(encoding="utf-8"))
    assert "delegate_task" in data["prompt"]
    assert "scout" in data["prompt"]
    assert "auditor" in data["prompt"]
    assert "multi-agent-brief finalize" in data["prompt"]
    _assert_orchestrator_contract_handoff(data)


def test_start_hermes_output_no_generate_brief(tmp_path, capsys):
    """start --runtime hermes must not mention /generate-brief in CLI output or handoff."""
    ws = _write_workspace(tmp_path)
    rc = main([
        "start",
        "--workspace", str(ws),
        "--runtime", "hermes",
        "--skip-doctor",
        "--venv", str(tmp_path / ".venv" / "bin" / "activate"),
    ])
    assert rc == 0
    captured = capsys.readouterr()
    cli_output = captured.out
    assert "/generate-brief" not in cli_output

    data = json.loads((ws / "output" / "intermediate" / "agent_handoff.json").read_text(encoding="utf-8"))
    assert "/generate-brief" not in data["prompt"]


def test_start_claude_output_contains_generate_brief(tmp_path, capsys):
    """start --runtime claude must mention /generate-brief."""
    ws = _write_workspace(tmp_path)
    rc = main([
        "start",
        "--workspace", str(ws),
        "--runtime", "claude",
        "--skip-doctor",
        "--venv", str(tmp_path / ".venv" / "bin" / "activate"),
    ])
    assert rc == 0
    captured = capsys.readouterr()
    assert "/generate-brief" in captured.out


def test_start_manual_handoff_contains_artifact_contract(tmp_path):
    ws = _write_workspace(tmp_path)
    rc = main([
        "start",
        "--workspace", str(ws),
        "--runtime", "manual",
        "--skip-doctor",
        "--venv", str(tmp_path / ".venv" / "bin" / "activate"),
    ])
    assert rc == 0
    data = json.loads((ws / "output" / "intermediate" / "agent_handoff.json").read_text(encoding="utf-8"))
    assert "candidate_claims.json" in data["prompt"]
    assert "multi-agent-brief finalize" in data["prompt"]
    _assert_orchestrator_contract_handoff(data)


# ---------------------------------------------------------------------------
# handoff
# ---------------------------------------------------------------------------

def test_handoff_with_config_generates_artifacts(tmp_path):
    ws = _write_workspace(tmp_path)
    rc = main([
        "handoff",
        "--config", str(ws / "config.yaml"),
        "--runtime", "hermes",
        "--skip-doctor",
        "--venv", str(tmp_path / ".venv" / "bin" / "activate"),
    ])
    assert rc == 0
    assert (ws / "output" / "intermediate" / "agent_handoff.md").exists()
    assert (ws / "output" / "intermediate" / "agent_handoff.json").exists()
    assert (ws / "output" / "intermediate" / "runtime_manifest.json").exists()
    assert (ws / "output" / "intermediate" / "workflow_state.json").exists()
    assert (ws / "output" / "intermediate" / "artifact_registry.json").exists()
    assert (ws / "output" / "intermediate" / "event_log.jsonl").exists()
    assert (ws / "output" / "intermediate" / "audience_profile_snapshot.md").exists()


def test_handoff_no_config_fails(tmp_path):
    rc = main(["handoff", "--config", str(tmp_path / "nonexistent" / "config.yaml"), "--skip-doctor"])
    assert rc != 0


# ---------------------------------------------------------------------------
# build_handoff direct unit tests
# ---------------------------------------------------------------------------

def test_build_handoff_hermes_has_delegate_task(tmp_path):
    ws = _write_workspace(tmp_path)
    handoff = build_handoff(
        workspace=ws,
        repo_workdir=ROOT,
        runtime="hermes",
        venv="/tmp/.venv/bin/activate",
        run_doctor=False,
    )
    assert handoff.runtime == "hermes"
    assert "delegate_task" in handoff.prompt
    assert "scout" in handoff.prompt
    assert "auditor" in handoff.prompt
    assert "/generate-brief" not in handoff.prompt
    _assert_orchestrator_contract_handoff(handoff.to_dict())


def test_build_handoff_claude_has_generate_brief(tmp_path):
    ws = _write_workspace(tmp_path)
    handoff = build_handoff(
        workspace=ws,
        repo_workdir=ROOT,
        runtime="claude",
        venv="/tmp/.venv/bin/activate",
        run_doctor=False,
    )
    assert "/generate-brief" in handoff.prompt
    _assert_orchestrator_contract_handoff(handoff.to_dict())


def test_build_handoff_unknown_runtime_raises(tmp_path):
    ws = _write_workspace(tmp_path)
    try:
        build_handoff(
            workspace=ws,
            repo_workdir=ROOT,
            runtime="nonexistent",
            run_doctor=False,
        )
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "Unknown runtime" in str(e)


def test_build_handoff_all_runtimes_valid(tmp_path):
    """Every declared valid runtime must build without error."""
    ws = _write_workspace(tmp_path)
    for runtime in VALID_RUNTIMES:
        handoff = build_handoff(
            workspace=ws,
            repo_workdir=ROOT,
            runtime=runtime,
            run_doctor=False,
        )
        # auto resolves to hermes in v0.5.5
        if runtime == "auto":
            assert handoff.runtime == "hermes"
        else:
            assert handoff.runtime == runtime
        assert len(handoff.expected_artifacts) >= 2
        assert len(handoff.prompt) > 50
        _assert_orchestrator_contract_handoff(handoff.to_dict())


# ---------------------------------------------------------------------------
# write_handoff_artifacts
# ---------------------------------------------------------------------------

def test_write_handoff_artifacts_writes_both_files(tmp_path):
    ws = _write_workspace(tmp_path)
    handoff = build_handoff(
        workspace=ws,
        repo_workdir=ROOT,
        runtime="hermes",
        run_doctor=False,
    )
    md_path, json_path = write_handoff_artifacts(handoff, ws)
    assert md_path.suffix == ".md"
    assert json_path.suffix == ".json"
    assert md_path.exists()
    assert json_path.exists()
    md_content = md_path.read_text(encoding="utf-8")
    assert "# Agent Handoff" in md_content
    assert "## Contract References" in md_content
    assert "## Runtime State Files" in md_content
    assert "## Audience Memory Files" in md_content
    assert "## Feedback State Files" in md_content
    assert "## Provenance State Files" in md_content
    assert "`orchestrator_contract`: `configs/orchestrator_contract.yaml`" in md_content
    assert "`runtime_manifest`: `output/intermediate/runtime_manifest.json`" in md_content
    assert "`audience_profile_snapshot`: `output/intermediate/audience_profile_snapshot.md`" in md_content
    assert "`feedback_issues`: `output/intermediate/feedback_issues.json`" in md_content
    assert "`provenance_graph`: `output/intermediate/provenance_graph.json`" in md_content
    assert "delegate_task" in md_content
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["runtime"] == "hermes"
    _assert_orchestrator_contract_handoff(data)


def test_render_handoff_cli_contains_runtime(tmp_path):
    ws = _write_workspace(tmp_path)
    handoff = build_handoff(
        workspace=ws,
        repo_workdir=ROOT,
        runtime="opencode",
        run_doctor=False,
    )
    output = render_handoff_cli(handoff)
    assert "opencode" in output
    assert str(ws.resolve()) in output


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


def test_run_default_auto_resolves_to_hermes(tmp_path):
    """Default run (--runtime auto) must resolve to hermes handoff."""
    ws = _write_workspace(tmp_path)
    rc = main([
        "run",
        "--workspace", str(ws),
        "--skip-doctor",
        "--venv", str(tmp_path / ".venv" / "bin" / "activate"),
    ])
    assert rc == 0
    data = json.loads((ws / "output" / "intermediate" / "agent_handoff.json").read_text(encoding="utf-8"))
    assert data["runtime"] == "hermes"
    assert "delegate_task" in data["prompt"]
    assert "/generate-brief" not in data["prompt"]
    _assert_orchestrator_contract_handoff(data)


def test_run_claude_contains_generate_brief(tmp_path):
    """run --runtime claude must contain /generate-brief."""
    ws = _write_workspace(tmp_path)
    rc = main([
        "run",
        "--workspace", str(ws),
        "--runtime", "claude",
        "--skip-doctor",
        "--venv", str(tmp_path / ".venv" / "bin" / "activate"),
    ])
    assert rc == 0
    data = json.loads((ws / "output" / "intermediate" / "agent_handoff.json").read_text(encoding="utf-8"))
    assert "/generate-brief" in data["prompt"]


def test_run_does_not_generate_brief(tmp_path):
    """run must NOT generate brief.md or claim_ledger.json."""
    ws = _write_workspace(tmp_path)
    rc = main([
        "run",
        "--workspace", str(ws),
        "--skip-doctor",
        "--venv", str(tmp_path / ".venv" / "bin" / "activate"),
    ])
    assert rc == 0
    assert not (ws / "output" / "brief.md").exists()
    assert not (ws / "output" / "intermediate" / "claim_ledger.json").exists()
    assert not (ws / "output" / "intermediate" / "provenance_graph.json").exists()


def test_prepare_output_points_to_run(capsys):
    """prepare must only point to multi-agent-brief run, nothing else."""
    try:
        main(["prepare", "--config", "/tmp/nonexistent/config.yaml"])
    except SystemExit:
        pass
    output = capsys.readouterr().out + capsys.readouterr().err
    assert "multi-agent-brief run --workspace <workspace>" in output
    assert "/generate-brief" not in output
    assert "Python pipeline" not in output
    assert "deterministic pipeline" not in output


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
    rc = main(["run", "--workspace", str(tmp_path / "no-such-ws"), "--skip-doctor"])
    assert rc == 1
    captured = capsys.readouterr()
    output = captured.out
    assert "multi-agent-brief onboard" in output
    assert "multi-agent-brief init" in output
    assert "--from-onboarding onboarding.json" in output


def test_init_demo_mentions_onboard(tmp_path, capsys):
    """init --demo must say it's a demo and point to onboard for real projects."""
    ws = tmp_path / "demo-ws"
    rc = main(["init", str(ws), "--demo", "--force"])
    assert rc == 0
    captured = capsys.readouterr()
    output = captured.out
    assert "demo" in output.lower()
    assert "multi-agent-brief onboard" in output
