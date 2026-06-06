from __future__ import annotations

import json
from pathlib import Path

import yaml

from multi_agent_brief.cli.main import main
from multi_agent_brief.hermes import build_hermes_cron_plan, render_hermes_skill


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

    assert plan.version == "v0.5.5"
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
    assert "scout" in weekly.prompt
    assert "auditor" in weekly.prompt
    assert "finalize" in weekly.prompt
    assert "/generate-brief" not in weekly.prompt

    # Monthly job
    monthly = plan.jobs[2]
    assert monthly.context_from == [plan.jobs[0].name]
    assert "Hermes-native delegated" in monthly.prompt
    assert "delegate_task" in monthly.prompt
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
    assert data["version"] == "v0.5.5"
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
