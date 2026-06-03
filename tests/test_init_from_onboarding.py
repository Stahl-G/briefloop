"""Tests for CLI init --from-onboarding integration."""
from __future__ import annotations

import json
from pathlib import Path

import yaml

from multi_agent_brief.cli.main import main


def test_init_from_onboarding_creates_workspace(tmp_path: Path):
    onboarding = {
        "target": "exampleco-weekly",
        "company_or_org": "ExampleCo",
        "industry_or_theme": "manufacturing",
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
    assert rc == 0

    for name in ("config.yaml", "profile.yaml", "sources.yaml", "user.md"):
        assert (ws / name).exists(), f"{name} missing"
    assert (ws / "input" / "README.md").exists()

    sources = yaml.safe_load((ws / "sources.yaml").read_text(encoding="utf-8"))
    assert sources["source_strategy"]["industry"] == "manufacturing"
    assert sources["web_search"]["enabled"] is False


def test_init_from_onboarding_cli_workspace_overrides_target(tmp_path: Path):
    onboarding = {
        "target": "onboarding-target",
        "company_or_org": "TestCo",
        "industry_or_theme": "technology",
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

    rc = main(["sources", "decide", "--config", str(ws / "config.yaml"), "--search"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "mock" not in captured.out.lower()
    assert "does not enable live search by default" in captured.out.lower() or "live web search" in captured.out.lower()
