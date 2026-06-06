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
    assert plan.jobs[0].schedule == "0 7 * * *"
    assert "public, citable signals" in plan.jobs[0].prompt
    assert plan.jobs[1].context_from == [plan.jobs[0].name]
    assert all("multi-agent-brief-hermes" in job.skills for job in plan.jobs)
    assert all(job.workdir == str(tmp_path.resolve()) for job in plan.jobs)


def test_hermes_skill_contains_cron_rules():
    skill = render_hermes_skill()
    assert "name: multi-agent-brief-hermes" in skill
    assert "Daily scout jobs collect source packages only" in skill
    assert "multi-agent-brief doctor" in skill
    assert "subagent workflow" in skill
    assert "multi-agent-brief finalize" in skill
    assert "Reader-facing artifacts must not contain `[src:CLAIM_ID]`" in skill
    assert "multi-agent-brief prepare" not in skill
    assert "generate-brief" in skill


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
