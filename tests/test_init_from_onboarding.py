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
        "industry_or_theme": "renewable energy",
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
    assert sources["source_strategy"]["industry"] == "energy"
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
