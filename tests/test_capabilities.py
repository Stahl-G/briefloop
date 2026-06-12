"""Tests for the Capability Center: models, catalog, detect, and CI gate."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from multi_agent_brief.capabilities.catalog import CAPABILITIES, get_capability, list_capabilities
from multi_agent_brief.capabilities.detect import (
    assess_capability,
    check_cli_tool,
    check_env_var,
    detect_readiness,
)
from multi_agent_brief.capabilities.models import (
    CapabilityOption,
    CapabilitySpec,
    CapabilityStatus,
    RequirementResult,
)


class TestCapabilitySpecModels:
    """Data model basics."""

    def test_capability_spec_has_required_fields(self):
        cap = CapabilitySpec(
            id="test",
            name={"en": "Test"},
            summary={"en": "A test"},
            category="source",
            provider_name="test",
        )
        assert cap.id == "test"
        assert cap.visibility == "standard"
        assert cap.maturity == "stable"

    def test_capability_option_defaults(self):
        opt = CapabilityOption(id="opt1", name="Option 1", description="desc")
        assert opt.enabled is False
        assert opt.dependencies == []

    def test_requirement_result_fields(self):
        rr = RequirementResult(requirement="env_var", status="OK", message="set")
        assert rr.status == "OK"


class TestCatalog:
    """Built-in catalog integrity."""

    def test_capabilities_are_unique(self):
        ids = [c.id for c in CAPABILITIES]
        assert len(ids) == len(set(ids)), f"Duplicate IDs: {[i for i in ids if ids.count(i) > 1]}"

    def test_get_capability_returns_spec(self):
        cap = get_capability("manual")
        assert cap is not None
        assert cap.id == "manual"
        assert cap.visibility == "core"

    def test_get_capability_unknown_returns_none(self):
        assert get_capability("nonexistent") is None

    def test_list_all_capabilities(self):
        all_caps = list_capabilities()
        assert len(all_caps) >= 14

    def test_list_filter_by_category(self):
        source_caps = list_capabilities(category="source")
        assert all(c.category == "source" for c in source_caps)
        assert len(source_caps) >= 8

    def test_list_filter_by_visibility(self):
        core = list_capabilities(visibility="core")
        assert all(c.visibility == "core" for c in core)
        assert len(core) >= 3

    def test_all_capabilities_have_names(self):
        for cap in CAPABILITIES:
            assert "en" in cap.name, f"{cap.id} missing English name"
            assert "zh" in cap.name, f"{cap.id} missing Chinese name"

    def test_all_capabilities_have_valid_category(self):
        valid = {"source", "processing", "output", "integration", "analysis"}
        for cap in CAPABILITIES:
            assert cap.category in valid, f"{cap.id} has invalid category: {cap.category}"

    def test_web_search_has_all_backends(self):
        ws = get_capability("web_search")
        assert ws is not None
        backend_ids = {o.id for o in ws.options}
        assert backend_ids == {"tavily", "exa", "brave", "firecrawl", "serper"}


class TestDetect:
    """Readiness detection."""

    def test_check_env_var_set(self, monkeypatch):
        monkeypatch.setenv("TEST_VAR_XYZ", "value")
        r = check_env_var("TEST_VAR_XYZ")
        assert r.status == "OK"

    def test_check_env_var_missing(self, monkeypatch):
        monkeypatch.delenv("TEST_VAR_XYZ_MISSING", raising=False)
        r = check_env_var("TEST_VAR_XYZ_MISSING")
        assert r.status == "ERROR"

    def test_check_cli_tool_found(self):
        r = check_cli_tool("python")
        assert r.status == "OK"

    def test_check_cli_tool_missing(self):
        r = check_cli_tool("nonexistent_tool_xyz_12345")
        assert r.status == "WARN"

    def test_detect_readiness_unknown_capability(self):
        results = detect_readiness("nonexistent")
        assert len(results) == 1
        assert results[0].status == "ERROR"

    def test_detect_readiness_manual_has_no_requirements(self):
        results = detect_readiness("manual")
        assert len(results) == 0

    def test_assess_capability_manual(self):
        status = assess_capability("manual", enabled_providers={"manual"})
        assert status.state == "ENABLED_READY"

    def test_assess_capability_not_enabled(self):
        status = assess_capability("web_search", enabled_providers={"manual"})
        assert status.state == "AVAILABLE"

    def test_assess_capability_enabled_needs_setup(self, monkeypatch):
        monkeypatch.delenv("TAVILY_API_KEY", raising=False)
        monkeypatch.delenv("EXA_API_KEY", raising=False)
        monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
        monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
        monkeypatch.delenv("SERPER_API_KEY", raising=False)
        status = assess_capability("web_search", enabled_providers={"web_search"})
        assert status.state == "ENABLED_NEEDS_SETUP"

    def test_assess_capability_runtime_tool_ready_without_api_key(self, tmp_path, monkeypatch):
        monkeypatch.delenv("TAVILY_API_KEY", raising=False)
        monkeypatch.delenv("EXA_API_KEY", raising=False)
        monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
        monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
        monkeypatch.delenv("SERPER_API_KEY", raising=False)
        (tmp_path / "sources.yaml").write_text(
            "source_strategy:\n"
            "  enabled_providers:\n"
            "    - web_search\n"
            "web_search:\n"
            "  enabled: true\n"
            "  mode: runtime_tool\n",
            encoding="utf-8",
        )

        status = assess_capability(
            "web_search",
            workspace_dir=tmp_path,
            enabled_providers={"web_search"},
        )

        assert status.state == "ENABLED_READY"
        assert "No search backend API key" not in status.notes

    def test_assess_capability_unknown(self):
        status = assess_capability("nonexistent")
        assert status.state == "UNAVAILABLE"


class TestCIGate:
    """CI gate: every user-facing provider must have a CapabilitySpec."""

    def test_check_capabilities_passes(self):
        from multi_agent_brief.sources.registry import PROVIDER_CLASSES

        skip_providers = {"cached_package"}
        provider_to_cap = {cap.provider_name: cap.id for cap in CAPABILITIES}

        for provider_name in PROVIDER_CLASSES:
            if provider_name in skip_providers:
                continue
            assert provider_name in provider_to_cap, (
                f"Provider '{provider_name}' not registered. "
                f"Add a CapabilitySpec in catalog.py."
            )

    def test_capability_ids_unique(self):
        ids = [c.id for c in CAPABILITIES]
        assert len(ids) == len(set(ids))


class TestFeaturesCommand:
    """CLI 'features' command tests."""

    def test_features_prints_table(self, capsys):
        from multi_agent_brief.cli.main import main
        assert main(["features"]) == 0
        out = capsys.readouterr().out
        assert "Source Providers" in out
        assert "Manual Inputs" in out

    def test_features_json_output(self, capsys):
        import json
        from multi_agent_brief.cli.main import main
        assert main(["features", "--json"]) == 0
        out = capsys.readouterr().out
        data = json.loads(out)
        assert isinstance(data, list)
        assert len(data) >= 14
        assert any(c["id"] == "manual" for c in data)

    def test_features_info_single(self, capsys):
        from multi_agent_brief.cli.main import main
        assert main(["features", "--info", "web_search"]) == 0
        out = capsys.readouterr().out
        assert "Web Search" in out
        assert "Tavily" in out
        assert "Options:" in out

    def test_features_info_json(self, capsys):
        import json
        from multi_agent_brief.cli.main import main
        assert main(["features", "--info", "mineru", "--json"]) == 0
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["id"] == "mineru"
        assert len(data["options"]) == 3

    def test_features_info_unknown_returns_error(self, capsys):
        from multi_agent_brief.cli.main import main
        assert main(["features", "--info", "nonexistent"]) == 1

    def test_features_with_workspace(self, tmp_path, capsys):
        from multi_agent_brief.cli.main import main
        ws = tmp_path / "ws"
        main([
            "init", str(ws),
            "--language", "en-US",
            "--company", "Test",
            "--industry", "mfg",
            "--title", "Brief",
            "--audience", "mgmt",
            "--cadence", "weekly",
            "--source-profile", "research",
        ])
        assert main(["features", str(ws)]) == 0
        out = capsys.readouterr().out
        # manual is enabled in research profile
        assert "Manual Inputs" in out


class TestRecommendCommand:
    """CLI 'recommend' command tests."""

    def test_recommend_with_text(self, capsys):
        from multi_agent_brief.cli.main import main
        assert main(["recommend", "--text", "Track competitors and earnings"]) == 0
        out = capsys.readouterr().out
        assert "market_competitor" in out
        assert "filing_resolver" in out

    def test_recommend_json_output(self, capsys):
        import json
        from multi_agent_brief.cli.main import main
        assert main(["recommend", "--text", "competitor analysis", "--json"]) == 0
        out = capsys.readouterr().out
        data = json.loads(out)
        assert "capabilities" in data
        assert len(data["capabilities"]) >= 1

    def test_recommend_no_match(self, capsys):
        from multi_agent_brief.cli.main import main
        assert main(["recommend", "--text", "hello world"]) == 0
        out = capsys.readouterr().out
        assert "No capability recommendations" in out

    def test_recommend_with_workspace(self, tmp_path, capsys):
        from multi_agent_brief.cli.main import main
        ws = tmp_path / "ws"
        main([
            "init", str(ws),
            "--language", "en-US",
            "--company", "Tesla",
            "--industry", "automotive",
            "--title", "Competitor Analysis",
            "--audience", "mgmt",
            "--cadence", "weekly",
            "--source-profile", "research",
        ])
        assert main(["recommend", str(ws)]) == 0
        out = capsys.readouterr().out
        assert "market_competitor" in out


class TestSetupCommand:
    """CLI 'setup' command tests."""

    def test_setup_dry_run(self, tmp_path, capsys):
        from multi_agent_brief.cli.main import main
        ws = tmp_path / "ws"
        main([
            "init", str(ws),
            "--language", "en-US",
            "--company", "Tesla",
            "--industry", "automotive",
            "--title", "Competitor Analysis",
            "--audience", "mgmt",
            "--cadence", "weekly",
            "--source-profile", "research",
        ])
        assert main(["setup", str(ws), "--dry-run"]) == 0
        out = capsys.readouterr().out
        assert "dry-run" in out.lower()

    def test_setup_applies_changes(self, tmp_path, capsys):
        from multi_agent_brief.cli.main import main
        ws = tmp_path / "ws"
        main([
            "init", str(ws),
            "--language", "en-US",
            "--company", "Tesla",
            "--industry", "automotive",
            "--title", "Competitor Analysis",
            "--audience", "mgmt",
            "--cadence", "weekly",
            "--source-profile", "research",
        ])
        assert main(["setup", str(ws)]) == 0
        out = capsys.readouterr().out
        assert "change(s) applied" in out

    def test_setup_nonexistent_workspace(self, capsys):
        from multi_agent_brief.cli.main import main
        assert main(["setup", "/nonexistent/path"]) == 1


class TestInitIntegration:
    """Init should show capability recommendations after workspace creation."""

    def test_init_shows_recommendations(self, tmp_path, capsys):
        from multi_agent_brief.cli.main import main
        ws = tmp_path / "ws"
        main([
            "init", str(ws),
            "--language", "en-US",
            "--company", "Tesla",
            "--industry", "automotive",
            "--title", "Competitor Analysis",
            "--audience", "mgmt",
            "--cadence", "weekly",
            "--source-profile", "research",
        ])
        out = capsys.readouterr().out
        assert "Recommended capabilities" in out
        assert "market_competitor" in out
        assert "multi-agent-brief setup" in out

    def test_init_focus_areas_trigger_recommendations(self, tmp_path, capsys):
        from multi_agent_brief.cli.main import main
        ws = tmp_path / "ws"
        # Default focus_areas include "competitor" and "market" which trigger market_competitor
        main([
            "init", str(ws),
            "--language", "en-US",
            "--company", "Test Corp",
            "--industry", "textiles",
            "--title", "Weekly Report",
            "--audience", "mgmt",
            "--cadence", "weekly",
            "--source-profile", "conservative",
        ])
        out = capsys.readouterr().out
        # Default focus_areas ["policy", "competitor", "market", "customer_demand"] trigger recommendations
        assert "Recommended capabilities" in out
        assert "market_competitor" in out

    def test_init_from_onboarding_shows_recommendations(self, tmp_path, capsys):
        import json
        from multi_agent_brief.cli.main import main
        ws = tmp_path / "ws"
        ob = {
            "target": str(ws),
            "company_or_org": "Apple",
            "industry_or_theme": "technology",
            "task_objective": "Track SEC filings and competitor movements",
            "audience_plain": "management team",
            "source_style_plain": "reliable research",
            "language_plain": "English",
            "cadence_plain": "weekly",
        }
        ob_path = tmp_path / "onboarding.json"
        ob_path.write_text(json.dumps(ob), encoding="utf-8")
        main(["init", "--from-onboarding", str(ob_path)])
        out = capsys.readouterr().out
        assert "Recommended capabilities" in out
        assert "filing_resolver" in out
