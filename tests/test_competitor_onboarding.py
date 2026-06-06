"""Tests for competitor monitoring integration in init wizard and onboarding."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from multi_agent_brief.cli.init_wizard import InitProfile, build_config, _build_competitor_universe, create_workspace
from multi_agent_brief.onboarding.schema import OnboardingResult
from multi_agent_brief.onboarding.mapper import map_onboarding_to_profile
from multi_agent_brief.onboarding.io import load_onboarding_result, save_onboarding_result


# ── InitProfile fields ───────────────────────────────────────────────

class TestInitProfileFields:
    def test_competitor_defaults(self):
        profile = InitProfile()
        assert profile.competitor_module_enabled is False
        assert profile.competitor_names == []


# ── build_config ─────────────────────────────────────────────────────

class TestBuildConfig:
    def test_no_modules_when_disabled(self):
        profile = InitProfile()
        cfg = build_config(profile)
        assert "modules" not in cfg

    def test_modules_section_when_enabled(self):
        profile = InitProfile(competitor_module_enabled=True)
        cfg = build_config(profile)
        assert "modules" in cfg
        mc = cfg["modules"]["market_competitor"]
        assert mc["enabled"] is True
        assert mc["mode"] == "weekly_monitor"
        assert mc["universe_path"] == "competitor_universe.yaml"


# ── _build_competitor_universe ────────────────────────────────────────

class TestBuildCompetitorUniverse:
    def test_empty_when_disabled(self):
        profile = InitProfile()
        u = _build_competitor_universe(profile)
        assert u["entities"] == []
        assert u["enabled"] is False

    def test_entities_populated(self):
        profile = InitProfile(
            competitor_module_enabled=True,
            competitor_names=["Acme Corp", "Globex Inc"],
        )
        u = _build_competitor_universe(profile)
        assert u["enabled"] is True
        assert len(u["entities"]) == 2
        assert u["entities"][0]["name"] == "Acme Corp"
        assert u["entities"][0]["entity_id"] == "acme_corp"
        assert u["entities"][1]["name"] == "Globex Inc"
        assert u["entities"][1]["entity_id"] == "globex_inc"


# ── mapper ───────────────────────────────────────────────────────────

class TestMapperCompetitor:
    def test_competitor_preferences_enables_module(self):
        result = OnboardingResult(
            company_or_org="TestCo",
            competitor_preferences={"enabled": True, "names": ["Rival A", "Rival B"]},
        )
        profile = map_onboarding_to_profile(result)
        assert profile.competitor_module_enabled is True
        assert profile.competitor_names == ["Rival A", "Rival B"]

    def test_competitor_preferences_disabled(self):
        result = OnboardingResult(
            company_or_org="TestCo",
            competitor_preferences={"enabled": False},
        )
        profile = map_onboarding_to_profile(result)
        assert profile.competitor_module_enabled is False
        assert profile.competitor_names == []

    def test_empty_preferences(self):
        result = OnboardingResult(company_or_org="TestCo")
        profile = map_onboarding_to_profile(result)
        assert profile.competitor_module_enabled is False
        assert profile.competitor_names == []

    def test_competitor_config_written_to_workspace(self):
        profile = InitProfile(
            company="TestCo",
            competitor_module_enabled=True,
            competitor_names=["Alpha"],
        )
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "ws"
            create_workspace(target, profile, force=True)
            cfg = (target / "config.yaml").read_text(encoding="utf-8")
            assert "market_competitor" in cfg
            assert "enabled: true" in cfg
            u_text = (target / "competitor_universe.yaml").read_text(encoding="utf-8")
            assert "Alpha" in u_text
            assert "enabled: true" in u_text


# ── onboarding IO ────────────────────────────────────────────────────

class TestOnboardingIO:
    def test_competitor_preferences_roundtrip(self, tmp_path):
        result = OnboardingResult(
            company_or_org="TestCo",
            competitor_preferences={"enabled": True, "names": ["X", "Y"]},
        )
        path = tmp_path / "ob.json"
        save_onboarding_result(result, path)
        loaded = load_onboarding_result(path)
        assert loaded.competitor_preferences["enabled"] is True
        assert loaded.competitor_preferences["names"] == ["X", "Y"]
