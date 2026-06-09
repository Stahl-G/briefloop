from __future__ import annotations

import json
from pathlib import Path

import yaml

from multi_agent_brief.cli.main import main
from multi_agent_brief.hermes import (
    build_hermes_cron_plan,
    render_hermes_prompt,
    render_hermes_setup_success,
    render_hermes_skill,
)


ROOT = Path(__file__).resolve().parent.parent
SOURCE_VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()


def _write_workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "input").mkdir()
    (ws / "config.yaml").write_text(
        """
project:
  name: "AI Agent Weekly"
  company: "ExampleCo"
  industry: "AI agents"
  language: "zh-CN"
  audience: "management"
report:
  cadence: "weekly,monthly"
input:
  path: "input"
output:
  path: "output"
""".strip(),
        encoding="utf-8",
    )
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


# ---------------------------------------------------------------------------
# Cron plan structure tests
# ---------------------------------------------------------------------------

def test_build_hermes_cron_plan_has_daily_weekly_monthly(tmp_path: Path):
    ws = _write_workspace(tmp_path)
    config = yaml.safe_load((ws / "config.yaml").read_text(encoding="utf-8"))

    plan = build_hermes_cron_plan(
        config=config,
        workspace=ws,
        repo_workdir=tmp_path,
        cadences=["weekly", "monthly"],
        deliver="feishu",
        profile="default",
    )

    assert plan.version == f"v{SOURCE_VERSION}"
    assert plan.cadences == ["weekly", "monthly"]
    assert len(plan.jobs) == 3

    # Daily job
    daily = plan.jobs[0]
    assert daily.schedule == "0 7 * * *"
    assert "daily source cache collection" in daily.prompt
    assert "YYYY-MM-DD.json" in daily.prompt

    # Weekly job
    weekly = plan.jobs[1]
    assert weekly.context_from == [plan.jobs[0].name]
    assert "Hermes-native delegated" in weekly.prompt
    assert "delegate_task" in weekly.prompt
    assert "Orchestrator main agent" in weekly.prompt
    assert "configs/orchestrator_contract.yaml" in weekly.prompt
    assert "retry_stage" in weekly.prompt
    assert "scout" in weekly.prompt
    assert "auditor" in weekly.prompt
    assert "multi-agent-brief gates check" in weekly.prompt
    assert "orchestrator_control_switchboard.json" in weekly.prompt
    assert "multi-agent-brief controls select" in weekly.prompt
    assert "Selection is not execution" in weekly.prompt
    assert "multi-agent-brief state check" in weekly.prompt
    assert "multi-agent-brief state decide" in weekly.prompt
    assert "multi-agent-brief provenance build" in weekly.prompt
    assert "not semantic proof" in weekly.prompt
    assert "finalize" in weekly.prompt
    assert weekly.prompt.index("multi-agent-brief controls select") < weekly.prompt.index("multi-agent-brief gates check")
    assert weekly.prompt.index("multi-agent-brief gates check") < weekly.prompt.index("multi-agent-brief finalize")
    assert "/generate-brief" not in weekly.prompt

    # Monthly job
    monthly = plan.jobs[2]
    assert monthly.context_from == [plan.jobs[0].name]
    assert "Hermes-native delegated" in monthly.prompt
    assert "delegate_task" in monthly.prompt
    assert "Orchestrator main agent" in monthly.prompt
    assert "configs/orchestrator_contract.yaml" in monthly.prompt
    assert "request_human_review" in monthly.prompt
    assert "month-level patterns" in monthly.prompt.lower()

    # Shared properties
    assert all("multi-agent-brief-hermes" in job.skills for job in plan.jobs)
    assert all(job.workdir == str(tmp_path.resolve()) for job in plan.jobs)


# ---------------------------------------------------------------------------
# Skill content tests — delegate_task native runtime
# ---------------------------------------------------------------------------

def test_hermes_skill_uses_delegate_task_runtime():
    skill = render_hermes_skill()
    assert "delegate_task" in skill
    assert "Hermes-native delegated" in skill
    assert "Orchestrator main agent" in skill
    assert "configs/orchestrator_contract.yaml" in skill
    assert "configs/stage_specs.yaml" in skill
    assert "configs/artifact_contracts.yaml" in skill
    assert "retry_stage" in skill
    assert "request_human_review" in skill
    assert "block_run" in skill
    assert "feedback ingest/plan/resolve/show/validate" in skill
    assert "gates check/show/validate" in skill
    assert "provenance build/show/validate" in skill
    assert "feedback_issues.json" in skill
    assert "repair_plan.json" in skill
    assert "quality_gate_report.json" in skill
    assert "provenance_graph.json" in skill
    assert "orchestrator_control_switchboard.json" in skill
    assert "control_selections.json" in skill
    assert "controls select" in skill
    assert "Selection is not execution" in skill
    assert "multi-agent-brief gates check --workspace <workspace>" in skill
    assert "multi-agent-brief state check --workspace <workspace> --strict" in skill
    assert "multi-agent-brief state decide --workspace <workspace> --stage auditor --decision continue" in skill
    assert "finalize` is not a quality-gate executor" in skill
    assert "not semantic proof" in skill
    assert "scout" in skill
    assert "screener" in skill
    assert "claim-ledger" in skill
    assert "analyst" in skill
    assert "editor" in skill
    assert "auditor" in skill
    assert "multi-agent-brief finalize" in skill


def test_hermes_skill_keeps_users_inside_hermes():
    skill = render_hermes_skill()
    assert "/generate-brief" not in skill
    assert "Claude Code" not in skill
    assert "multi-agent-brief prepare" not in skill


def test_hermes_skill_has_setup_workflow():
    skill = render_hermes_skill()
    assert "Setup Workflow" in skill
    assert "Project is cloned and ready" in skill
    assert "I can continue generating the brief inside Hermes" in skill
    assert "delegate_task children" in skill.lower()


def test_hermes_skill_has_daily_cache_workflow():
    skill = render_hermes_skill()
    assert "Daily Source Cache Workflow" in skill
    assert "YYYY-MM-DD.json" in skill
    assert "hermes_daily_cache" in skill


def test_hermes_skill_has_delegation_sequence():
    skill = render_hermes_skill()
    assert "## Hermes-native Delegated Brief Workflow" in skill
    assert "Scout child" in skill
    assert "Screener child" in skill
    assert "Claim-ledger child" in skill
    assert "Analyst child" in skill
    assert "Editor child" in skill
    assert "Auditor child" in skill
    assert "Parent Orchestration" in skill



def test_hermes_skill_no_prepare_reference():
    skill = render_hermes_skill()
    assert "multi-agent-brief prepare" not in skill


# ---------------------------------------------------------------------------
# Prompt and setup success tests
# ---------------------------------------------------------------------------

def test_hermes_prompt_keeps_user_inside_hermes():
    prompt = render_hermes_prompt(
        workspace="/tmp/test-ws",
        repo_workdir="/tmp/test-repo",
        venv_path="/tmp/test-repo/.venv/bin/activate",
    )
    assert "delegate_task" in prompt
    assert "Hermes" in prompt
    assert "Orchestrator main agent" in prompt
    assert "configs/orchestrator_contract.yaml" in prompt
    assert "configs/stage_specs.yaml" in prompt
    assert "configs/artifact_contracts.yaml" in prompt
    assert "retry_stage" in prompt
    assert "request_human_review" in prompt
    assert "block_run" in prompt
    assert "multi-agent-brief feedback ingest" in prompt
    assert "feedback show" in prompt
    assert "quality_gate_report.json" in prompt
    assert "provenance_graph.json" in prompt
    assert "orchestrator_control_switchboard.json" in prompt
    assert "control_selections.json" in prompt
    assert "multi-agent-brief controls select" in prompt
    assert "Selection is not execution" in prompt
    resolved_ws = str(Path("/tmp/test-ws").resolve())
    assert f"multi-agent-brief controls select --workspace {resolved_ws}" in prompt
    assert f"multi-agent-brief gates check --workspace {resolved_ws}" in prompt
    assert f"multi-agent-brief state check --workspace {resolved_ws} --strict" in prompt
    assert f"multi-agent-brief state decide --workspace {resolved_ws} --stage auditor --decision continue" in prompt
    assert f"multi-agent-brief provenance build --workspace {resolved_ws}" in prompt
    assert f"multi-agent-brief provenance validate --workspace {resolved_ws}" in prompt
    assert prompt.index("multi-agent-brief controls select") < prompt.index("multi-agent-brief gates check")
    assert prompt.index("multi-agent-brief gates check") < prompt.index("multi-agent-brief finalize")
    assert prompt.index("multi-agent-brief finalize") < prompt.index("multi-agent-brief provenance build")
    assert "feedback_issues.json" in prompt
    assert "scout" in prompt
    assert "screener" in prompt
    assert "claim-ledger" in prompt
    assert "analyst" in prompt
    assert "editor" in prompt
    assert "auditor" in prompt
    assert "multi-agent-brief finalize" in prompt
    assert "/generate-brief" not in prompt
    assert "Claude Code" not in prompt


def test_hermes_setup_next_step_is_hermes_native():
    text = render_hermes_setup_success(
        repo="/tmp/test-repo",
        venv="/tmp/test-repo/.venv",
        workspace="/tmp/test-ws",
        version=f"v{SOURCE_VERSION}",
        doctor_status="passed",
    )
    assert "multi-agent-brief hermes prompt" in text
    assert "multi-agent-brief hermes install-skill" in text
    assert "Orchestrator main agent" in text
    assert "delegate" in text.lower()
    assert "/generate-brief" not in text
    assert "Claude Code" not in text


def test_hermes_prompt_contains_artifact_paths():
    prompt = render_hermes_prompt(
        workspace="/tmp/test-ws",
        repo_workdir="/tmp/test-repo",
        venv_path="/tmp/test-repo/.venv/bin/activate",
    )
    assert "candidate_claims.json" in prompt
    assert "screened_candidates.json" in prompt
    assert "claim_ledger.json" in prompt
    assert "audited_brief.md" in prompt
    assert "audit_report.json" in prompt
    assert "output/brief.md" in prompt


def test_hermes_prompt_contains_doctor_and_sources():
    prompt = render_hermes_prompt(
        workspace="/tmp/test-ws",
        repo_workdir="/tmp/test-repo",
        venv_path="/tmp/test-repo/.venv/bin/activate",
    )
    assert "multi-agent-brief doctor" in prompt
    assert "multi-agent-brief sources decide" in prompt
    assert "multi-agent-brief inputs classify" in prompt


# ---------------------------------------------------------------------------
# CLI tests
# ---------------------------------------------------------------------------

def test_cli_hermes_cron_plan_writes_json_and_markdown(tmp_path: Path):
    ws = _write_workspace(tmp_path)
    out = tmp_path / "plan.json"
    md = tmp_path / "plan.md"

    result = main([
        "hermes",
        "cron-plan",
        "--config",
        str(ws / "config.yaml"),
        "--repo-workdir",
        str(tmp_path),
        "--cadence",
        "weekly,monthly",
        "--deliver",
        "feishu",
        "--output",
        str(out),
        "--markdown",
        str(md),
    ])

    assert result == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["version"] == f"v{SOURCE_VERSION}"
    assert data["cadences"] == ["weekly", "monthly"]
    assert len(data["jobs"]) == 3
    assert "Hermes Cron Plan" in md.read_text(encoding="utf-8")


def test_cli_hermes_skill_writes_file(tmp_path: Path):
    output = tmp_path / "multi-agent-brief-hermes" / "SKILL.md"
    result = main(["hermes", "skill", "--output", str(output)])
    assert result == 0
    assert output.exists()
    assert "Skills" not in output.read_text(encoding="utf-8").splitlines()[0]
    assert "multi-agent-brief-hermes" in output.read_text(encoding="utf-8")


def test_cli_hermes_sync_sources_enables_cached_package(tmp_path: Path):
    ws = _write_workspace(tmp_path)

    result = main(["hermes", "sync-sources", "--config", str(ws / "config.yaml")])

    assert result == 0
    data = yaml.safe_load((ws / "sources.yaml").read_text(encoding="utf-8"))
    assert "cached_package" in data["source_strategy"]["enabled_providers"]
    assert data["cached_package"]["enabled"] is True
    assert "input/hermes_cache" in data["cached_package"]["paths"]


def test_cli_hermes_install_skill(tmp_path: Path):
    target = tmp_path / "multi-agent-brief-hermes"
    result = main(["hermes", "install-skill", "--target", str(target)])
    assert result == 0
    assert (target / "SKILL.md").exists()
    content = (target / "SKILL.md").read_text(encoding="utf-8")
    assert "delegate_task" in content
    assert "multi-agent-brief-hermes" in content


def test_cli_hermes_prompt_generates_output(tmp_path: Path):
    ws = _write_workspace(tmp_path)
    result = main([
        "hermes", "prompt",
        "--config", str(ws / "config.yaml"),
        "--repo-workdir", str(tmp_path),
        "--venv", str(tmp_path / ".venv" / "bin" / "activate"),
    ])
    assert result == 0


def test_cli_hermes_prompt_output_contains_workflow_steps(capsys, tmp_path: Path):
    ws = _write_workspace(tmp_path)
    result = main([
        "hermes", "prompt",
        "--config", str(ws / "config.yaml"),
        "--repo-workdir", str(tmp_path),
        "--venv", str(tmp_path / ".venv" / "bin" / "activate"),
    ])
    assert result == 0
    captured = capsys.readouterr()
    output = captured.out
    assert "delegate_task" in output
    assert "Orchestrator main agent" in output
    assert "configs/orchestrator_contract.yaml" in output
    assert "retry_stage" in output
    assert "scout" in output
    assert "multi-agent-brief gates check" in output
    assert "multi-agent-brief state check" in output
    assert "multi-agent-brief state decide" in output
    assert "audience_profile_snapshot.md" in output
    assert "multi-agent-brief provenance build" in output
    assert output.index("multi-agent-brief gates check") < output.index("multi-agent-brief finalize")
    assert output.index("multi-agent-brief finalize") < output.index("multi-agent-brief provenance build")
    assert "multi-agent-brief finalize" in output
    assert "/generate-brief" not in output
    # Onboarding workflow path
    assert "chat-to-JSON onboarding" in output
    assert "Collect brief profile in chat" in output
    assert "multi-agent-brief init <workspace> --from-onboarding onboarding.json" in output
    assert "multi-agent-brief run --workspace <workspace>" in output
    # Plugin preferred path
    assert "Preferred" in output
    assert "Hermes Plugin" in output
    assert "integrations/hermes-plugin/mabw" in output


def test_hermes_skill_contains_onboarding_workflow():
    """Hermes SKILL.md must reference the plugin as preferred path and have fallback onboarding."""
    skill_path = Path(__file__).resolve().parent.parent / ".agents" / "hermes-skills" / "multi-agent-brief-hermes" / "SKILL.md"
    content = skill_path.read_text(encoding="utf-8")
    # Preferred path: plugin
    assert "Preferred Path" in content
    assert "Hermes Plugin" in content
    assert "integrations/hermes-plugin/mabw" in content
    assert "mabw_create_onboarding" in content
    assert "mabw_init_workspace" in content
    assert "mabw_run_handoff" in content
    # Fallback path: chat-to-JSON
    assert "Fallback" in content
    assert "chat-to-JSON" in content
    assert "Collect brief profile in chat" in content
    assert "multi-agent-brief init <workspace> --from-onboarding onboarding.json" in content
    assert "multi-agent-brief run --workspace <workspace>" in content
    assert "delegate_task" in content
    assert "gates check + state check/decide" in content
    assert "audience_profile_snapshot.md" in content
    assert "Do not treat `audience_profile.md` as evidence" in content
    assert "finalize` is not a quality-gate executor" in content
    assert "provenance build" in content
    assert "not semantic proof" in content
    assert "Orchestrator main agent" in content
    assert "configs/orchestrator_contract.yaml" in content
    assert "retry_stage" in content
