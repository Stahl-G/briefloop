"""Tests for CLI init --from-onboarding integration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from multi_agent_brief.cli.main import main
from multi_agent_brief.runtime_host_v2.codex import workspace_codex_adapter_loader
from multi_agent_brief.runtime_host_v2.initialization import initialize_or_open_runtime


def test_bootstrap_freezes_report_window_and_selector_capacity():
    """RunDirection freezes the reporting window and screening capacity at init."""
    from datetime import date

    from multi_agent_brief.workspace.init_profile import (
        InitProfile,
        build_controlstore_bootstrap,
    )

    profile = InitProfile(
        company="ExampleCo",
        task_objective="Track solar stocks weekly.",
        max_source_age_days=7,
        selector_max_items=20,
    )
    bootstrap = build_controlstore_bootstrap(
        profile,
        workspace_id="WS-TEST",
        run_id="RUN-TEST",
        report_date=date(2026, 8, 10),
    )
    direction = bootstrap.run_direction
    assert direction.report_window_start == "2026-08-03"
    assert direction.report_window_end == "2026-08-10"
    assert direction.selector_max_items == 20


def test_init_from_onboarding_creates_workspace(tmp_path: Path, capsys):
    onboarding = {
        "target": "exampleco-weekly",
        "company_or_org": "ExampleCo",
        "industry_or_theme": "manufacturing",
        "task_objective": "Track material manufacturing developments for ExampleCo management.",
        "audience_plain": "management team",
        "source_style_plain": "reliable, but include sector news",
        "output_style_plain": "executive brief, conclusion-first",
        "language_plain": "English",
        "cadence_plain": "weekly",
        "must_watch": ["ExampleCo", "policy", "competitors", "risk events"],
    }
    ob_path = tmp_path / "onboarding.json"
    ob_path.write_text(json.dumps(onboarding), encoding="utf-8")

    ws = tmp_path / "exampleco-weekly"
    rc = main(["init", str(ws), "--from-onboarding", str(ob_path), "--force"])
    output = capsys.readouterr().out
    assert rc == 0

    for name in (
        "config.yaml",
        "profile.yaml",
        "sources.yaml",
        "user.md",
        "audience_profile.md",
    ):
        assert (ws / name).exists(), f"{name} missing"
    input_readme = (ws / "input" / "README.md").read_text(encoding="utf-8")
    context_readme = (ws / "input" / "context" / "README.md").read_text(
        encoding="utf-8"
    )
    assert "prior weekly reports" in input_readme
    assert "input/context/" in input_readme
    assert "previous_weekly_reference.md" in context_readme
    assert "input/context" in output
    assert "prior weekly reports" in output
    assert (ws / "input" / "sources" / "README.md").exists()
    audience_profile = (ws / "audience_profile.md").read_text(encoding="utf-8")
    assert "ExampleCo" in audience_profile
    assert "Audience Profile" in audience_profile
    assert "not source evidence" in audience_profile

    sources = yaml.safe_load((ws / "sources.yaml").read_text(encoding="utf-8"))
    assert sources["source_strategy"]["profile"] == "llm_decide"
    # Industry is preserved in source_discovery for llm_decide mode
    assert "manufacturing" in sources.get("source_discovery", {}).get("industry", "")
    # Online search is recommended but defaults to configure_later unless explicitly enabled.
    assert sources["web_search"]["enabled"] is True
    assert sources["web_search"]["mode"] == "configure_later"
    assert "backend" not in sources["web_search"]
    assert "api_key_env" not in sources["web_search"]
    config = yaml.safe_load((ws / "config.yaml").read_text(encoding="utf-8"))
    assert config["selector"]["max_items"] == 20
    assert config["selector"]["max_items"] >= config["brief_quality"]["min_items"]


def test_init_from_onboarding_preserves_declined_online_search(tmp_path: Path):
    onboarding = {
        "target": "no-search-weekly",
        "company_or_org": "ExampleCo",
        "industry_or_theme": "manufacturing",
        "task_objective": "Track material manufacturing developments for ExampleCo management.",
        "audience_plain": "management team",
        "source_style_plain": "llm_decide",
        "output_style_plain": "executive brief",
        "language_plain": "English",
        "cadence_plain": "weekly",
        "search_backend_plain": "none",
        "tavily_enabled": False,
    }
    ob_path = tmp_path / "onboarding.json"
    ob_path.write_text(json.dumps(onboarding), encoding="utf-8")

    ws = tmp_path / "no-search-weekly"
    rc = main(["init", str(ws), "--from-onboarding", str(ob_path), "--force"])

    assert rc == 0
    sources = yaml.safe_load((ws / "sources.yaml").read_text(encoding="utf-8"))
    assert sources["source_strategy"]["profile"] == "llm_decide"
    assert "web_search" not in sources["source_strategy"]["enabled_providers"]
    assert sources["web_search"]["enabled"] is False
    assert sources["web_search"]["mode"] == "disabled"
    assert "backend" not in sources["web_search"]

    doctor_rc = main(["doctor", "--config", str(ws / "config.yaml")])
    assert doctor_rc == 0


@pytest.mark.parametrize(
    "search_selection",
    (
        {"search_backend_plain": "tavily"},
        {"tavily_enabled": True},
    ),
)
@pytest.mark.parametrize("source_age_days", (7, 30))
def test_init_from_onboarding_tavily_freezes_exact_human_topic(
    tmp_path: Path,
    search_selection: dict[str, object],
    source_age_days: int,
) -> None:
    topic = "grid-scale energy storage"
    onboarding = {
        "target": "tavily-weekly",
        "company_or_org": "ExampleCo",
        "industry_or_theme": topic,
        "task_objective": "Track public grid storage developments for management.",
        "audience_plain": "management team",
        "source_style_plain": "research",
        "output_style_plain": "executive brief",
        "language_plain": "English",
        "cadence_plain": "weekly",
        "source_age_days": source_age_days,
        **search_selection,
    }
    ob_path = tmp_path / "onboarding.json"
    ob_path.write_text(json.dumps(onboarding), encoding="utf-8")
    workspace = tmp_path / "tavily-weekly"

    assert main(["init", str(workspace), "--from-onboarding", str(ob_path)]) == 0
    initialized = initialize_or_open_runtime(
        workspace,
        adapter_loader=workspace_codex_adapter_loader(workspace),
    )
    route = next(
        item
        for item in initialized.verified.source_plan.routes
        if item.route_id == "web-search"
    )
    assert route.acquisition_spec is not None
    topic_queries = [
        task.query
        for task in route.acquisition_spec.tasks
        if topic in task.query
    ]
    assert topic_queries
    sources = yaml.safe_load((workspace / "sources.yaml").read_text(encoding="utf-8"))
    assert sources["web_search"]["recency_days"] == 7
    assert sources["web_search"]["initial_news_backfill"]["recency_days"] == 30


@pytest.mark.parametrize(
    "invalid_window",
    (None, 14, 90),
    ids=("missing", "unsupported-14", "unsupported-90"),
)
def test_init_from_onboarding_tavily_rejects_unsupported_window_before_writes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    invalid_window: int | None,
) -> None:
    onboarding: dict[str, object] = {
        "target": "tavily-weekly",
        "company_or_org": "ExampleCo",
        "industry_or_theme": "grid-scale energy storage",
        "task_objective": "Track public grid storage developments for management.",
        "audience_plain": "management team",
        "source_style_plain": "research",
        "output_style_plain": "executive brief",
        "language_plain": "English",
        "cadence_plain": "weekly",
        "search_backend_plain": "tavily",
    }
    if invalid_window is not None:
        onboarding["source_age_days"] = invalid_window
    ob_path = tmp_path / "onboarding.json"
    ob_path.write_text(json.dumps(onboarding), encoding="utf-8")
    workspace = tmp_path / "tavily-weekly"

    assert main(["init", str(workspace), "--from-onboarding", str(ob_path)]) == 1
    output = capsys.readouterr().out
    assert "explicit 7- or 30-day source window" in output
    assert "briefloop init <workspace> --web" in output
    assert not workspace.exists()


@pytest.mark.parametrize(("window_choice", "expected_days"), (("1", 7), ("2", 30)))
def test_interactive_onboarding_tavily_forces_supported_window_choice(
    window_choice: str,
    expected_days: int,
) -> None:
    from multi_agent_brief.cli.init_wizard import prompt_for_profile

    def answer(prompt: str) -> str:
        if prompt.startswith("Select language"):
            return "1"
        if prompt.startswith("Maximum source age"):
            return "90"
        if prompt.startswith("Source profile"):
            return "2"
        if prompt.startswith("Enable online search"):
            return "y"
        if prompt.startswith("How should live web search"):
            return "1"
        if prompt.startswith("Tavily source window"):
            return window_choice
        if "RAG" in prompt or prompt.startswith("Enable competitor"):
            return "n"
        return ""

    profile = prompt_for_profile(input_func=answer)

    assert profile.search_backend == "tavily"
    assert profile.tavily_enabled is True
    assert profile.max_source_age_days == expected_days


def test_init_from_onboarding_cli_workspace_overrides_target(tmp_path: Path):
    onboarding = {
        "target": "onboarding-target",
        "company_or_org": "TestCo",
        "industry_or_theme": "technology",
        "task_objective": "Track material technology developments for TestCo management.",
        "language_plain": "English",
        "cadence_plain": "weekly",
    }
    ob_path = tmp_path / "onboarding.json"
    ob_path.write_text(json.dumps(onboarding), encoding="utf-8")

    # CLI target "cli-target" should win over onboarding "onboarding-target"
    ws = tmp_path / "cli-target"
    rc = main(["init", str(ws), "--from-onboarding", str(ob_path), "--force"])
    assert rc == 0
    assert ws.exists()
    assert not (tmp_path / "onboarding-target").exists()


def test_init_from_onboarding_uses_onboarding_target_when_no_cli_target(tmp_path: Path):
    """When CLI target is the default, onboarding.target should be used."""
    onboarding = {
        "target": "auto-target",
        "company_or_org": "TestCo",
        "industry_or_theme": "technology",
        "task_objective": "Track material technology developments for TestCo management.",
        "language_plain": "English",
        "cadence_plain": "weekly",
    }
    ob_path = tmp_path / "onboarding.json"
    ob_path.write_text(json.dumps(onboarding), encoding="utf-8")

    # Pass CLI target as the default "brief-workspace" — onboarding.target should win
    rc = main(["init", "--from-onboarding", str(ob_path), "--force"])
    assert rc == 0
    ws = Path("auto-target")
    try:
        assert ws.exists()
        assert (ws / "config.yaml").exists()
    finally:
        import shutil

        shutil.rmtree(ws, ignore_errors=True)


def test_sources_decide_search_no_mock_residual(capsys, tmp_path: Path):
    """sources decide --search must not mention mock backend."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "config.yaml").write_text(
        "project:\n  name: test\ninput:\n  path: input\noutput:\n  path: output\n",
        encoding="utf-8",
    )
    sources_yaml = (
        "source_strategy:\n  profile: research\n  industry: manufacturing\n"
        "  enabled_providers: [manual]\nmanual:\n  enabled: true\n  sources: []\n"
        "source_discovery:\n  company: TestCo\n  industry: manufacturing\n"
        "  topics: [policy]\n  queries:\n    - test query\n"
    )
    (ws / "sources.yaml").write_text(sources_yaml, encoding="utf-8")
    before_files = {
        path.relative_to(ws).as_posix(): path.read_bytes()
        for path in ws.rglob("*")
        if path.is_file()
    }

    rc = main(["sources", "decide", "--config", str(ws / "config.yaml"), "--search"])
    captured = capsys.readouterr()
    # retired public `sources decide` surface; source decisions
    # now run inside the SQLite ControlStore runtime authority.
    assert rc == 1
    assert captured.out == "runtime_command_unsupported\n"
    assert "mock" not in captured.out.lower()
    after_files = {
        path.relative_to(ws).as_posix(): path.read_bytes()
        for path in ws.rglob("*")
        if path.is_file()
    }
    assert after_files == before_files


def test_init_from_onboarding_aliases_accepted(tmp_path: Path):
    """Agent-generated onboarding.json with short field names must work."""
    onboarding = {
        "company": "Canadian Solar",
        "industry": "光伏",
        "task_objective": "跟踪加拿大太阳能行业政策、诉讼与法规变化，输出管理层周报。",
        "title": "美国光储市场周报",
        "audience": "总裁办",
        "language": "zh-CN",
        "cadence": "weekly",
        "source_style": "reliable research",
        "output_style": "executive brief",
        "focus_areas": ["政策", "诉讼", "法规变化"],
        "forbidden_sources": [],
        "tavily_enabled": False,
    }
    ob_path = tmp_path / "onboarding.json"
    ob_path.write_text(json.dumps(onboarding), encoding="utf-8")
    ws = tmp_path / "ws"

    rc = main(["init", str(ws), "--from-onboarding", str(ob_path)])
    assert rc == 0, f"init should succeed even with aliased field names, got rc={rc}"
    assert (ws / "config.yaml").exists()
    assert (ws / "user.md").exists()
    assert (ws / "audience_profile.md").exists()


def test_init_from_onboarding_title_alias_does_not_pollute_task_objective(
    tmp_path: Path,
):
    onboarding = {
        "company": "Example Solar",
        "industry": "美国光储市场",
        "title": "美国光储市场周报",
        "task_objective": (
            "制作 Example Solar 美国光储市场周报，聚焦 AI 数据中心清洁能源需求和"
            "美国 HJT 异质结技术动态，输出专业严谨的中文简报供管理层决策参考"
        ),
        "audience": "管理层",
        "language": "zh-CN",
        "cadence": "weekly",
    }
    ob_path = tmp_path / "onboarding.json"
    ob_path.write_text(json.dumps(onboarding, ensure_ascii=False), encoding="utf-8")
    ws = tmp_path / "ws"

    rc = main(["init", str(ws), "--from-onboarding", str(ob_path)])

    assert rc == 0
    config = yaml.safe_load((ws / "config.yaml").read_text(encoding="utf-8"))
    assert config["project"]["name"] == "美国光储市场周报"
    assert config["project"]["name"] != onboarding["task_objective"]
    user_md = (ws / "user.md").read_text(encoding="utf-8")
    assert onboarding["task_objective"] in user_md


def test_init_from_onboarding_canonical_brief_title_beats_title_alias(tmp_path: Path):
    onboarding = {
        "company": "Example Solar",
        "industry": "美国光储市场",
        "title": "Alias Title Should Lose",
        "brief_title": "美国光储市场周报",
        "task_objective": "制作 Example Solar 美国光储市场周报并供管理层决策参考",
        "audience": "管理层",
        "language": "zh-CN",
        "cadence": "weekly",
    }
    ob_path = tmp_path / "onboarding.json"
    ob_path.write_text(json.dumps(onboarding, ensure_ascii=False), encoding="utf-8")
    ws = tmp_path / "ws"

    rc = main(["init", str(ws), "--from-onboarding", str(ob_path)])

    assert rc == 0
    config = yaml.safe_load((ws / "config.yaml").read_text(encoding="utf-8"))
    assert config["project"]["name"] == "美国光储市场周报"


def test_init_from_onboarding_rejects_selector_below_quality_floor(
    tmp_path: Path, capsys
):
    onboarding = {
        "company": "Example Solar",
        "industry": "美国光储市场",
        "title": "美国光储市场周报",
        "task_objective": "制作 Example Solar 美国光储市场周报并供管理层决策参考",
        "audience": "管理层",
        "language": "zh-CN",
        "cadence": "weekly",
        "max_items_per_brief": 1,
    }
    ob_path = tmp_path / "onboarding.json"
    ob_path.write_text(json.dumps(onboarding, ensure_ascii=False), encoding="utf-8")
    ws = tmp_path / "ws"

    rc = main(["init", str(ws), "--from-onboarding", str(ob_path)])

    assert rc == 1
    out = capsys.readouterr().out
    assert "--selector-max-items must be at least 20" in out
    assert not (ws / "config.yaml").exists()


def test_direct_init_creates_audience_profile(tmp_path: Path):
    ws = tmp_path / "direct-ws"

    rc = main(
        [
            "init",
            str(ws),
            "--language",
            "en-US",
            "--company",
            "DirectCo",
            "--industry",
            "manufacturing",
            "--title",
            "DirectCo Weekly Brief",
            "--task-objective",
            "Track material manufacturing developments for DirectCo management.",
            "--audience",
            "management",
            "--cadence",
            "weekly",
            "--source-profile",
            "llm_decide",
            "--force",
        ]
    )

    assert rc == 0
    profile = (ws / "audience_profile.md").read_text(encoding="utf-8")
    assert "DirectCo" in profile
    assert "DirectCo Weekly Brief" in profile
    assert "Audience Profile" in profile


def test_direct_init_rejects_selector_below_quality_floor(tmp_path: Path, capsys):
    for value in ("0", "-1", "1"):
        ws = tmp_path / f"direct-ws-{value.replace('-', 'neg')}"

        rc = main(
            [
                "init",
                str(ws),
                "--language",
                "en-US",
                "--company",
                "DirectCo",
                "--role",
                "operator",
                "--industry",
                "manufacturing",
                "--title",
                "DirectCo Weekly Brief",
                "--task-objective",
                "Track material manufacturing developments for DirectCo management.",
                "--audience",
                "management",
                "--cadence",
                "weekly",
                "--source-profile",
                "research",
                "--selector-max-items",
                value,
                "--force",
            ]
        )

        assert rc == 1
        out = capsys.readouterr().out
        assert "--selector-max-items must be at least 20" in out
        assert not (ws / "config.yaml").exists()


def test_demo_init_creates_public_safe_audience_profile(tmp_path: Path):
    ws = tmp_path / "demo-ws"

    rc = main(["init", str(ws), "--demo", "--force"])

    assert rc == 0
    profile = (ws / "audience_profile.md").read_text(encoding="utf-8")
    assert "Synthetic Corp" in profile
    assert "public-safe" in profile
    assert "material non-public information" in profile
