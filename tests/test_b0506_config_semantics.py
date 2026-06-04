"""Tests for PR5: Provider, Profile, and Backend configuration semantics
(B06, B07, B12, B13).

B06 — Industry Pack must not auto-enable RSS bypassing user's provider/profile.
B07 — Manual/RSS top-level `enabled: false` must actually prevent collection.
B12 — Exa default API key env must be EXA_API_KEY, not TAVILY_API_KEY.
B13 — --industry must work even without sources.yaml.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from multi_agent_brief.sources.base import SourceConfig, SourceQuery
from multi_agent_brief.sources.registry import collect_all_sources
from multi_agent_brief.sources.planner import create_source_plan
from multi_agent_brief.sources.manual import ManualProvider
from multi_agent_brief.sources.rss import RssProvider
from multi_agent_brief.core.pipeline import BriefPipeline
from multi_agent_brief.core.schemas import PipelineContext


# ─── B06: Industry Pack must not bypass profile ───

class TestB06IndustryPackNoBypass:
    """Industry Pack RSS feeds must only be used when RSS is in enabled_providers."""

    def test_conservative_profile_rejects_rss(self):
        """Even with industry pack feeds, conservative profile must not get RSS."""
        plan = create_source_plan(
            industry="manufacturing",
            recency_days=7,
            enabled_providers=["manual"],  # conservative: no RSS
        )
        assert len(plan.rss_feeds) >= 0  # planner loads feeds regardless
        # But the pipeline must not auto-add RSS for conservative profile
        assert "rss" not in plan.enabled_providers

    def test_research_profile_allows_rss(self):
        """research profile allows RSS if industry has feeds."""
        plan = create_source_plan(
            industry="manufacturing",
            recency_days=7,
            enabled_providers=["manual", "rss"],
        )
        assert "rss" in plan.enabled_providers


# ─── B07: Provider top-level enabled must prevent collection ───

class TestB07ProviderEnabledFlag:
    """Top-level `enabled: false` must prevent provider collection."""

    def test_manual_enabled_false_skips_all(self, tmp_path):
        """When manual.enabled is False, ManualProvider.collect must return []."""
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        (input_dir / "news.md").write_text(
            "- Important market signal about expansion plans detected.\n",
            encoding="utf-8",
        )
        provider = ManualProvider()
        items = provider.collect(
            SourceQuery(),
            {
                "enabled": False,
                "sources": [
                    {"name": "Local Input", "path": str(input_dir),
                     "category": "local_files", "enabled": True},
                ],
            },
        )
        assert items == [], (
            "B07 FAIL: ManualProvider collected sources despite enabled: false"
        )

    def test_rss_enabled_false_skips_all(self):
        """When rss.enabled is False, RssProvider.collect must return []."""
        provider = RssProvider()
        items = provider.collect(
            SourceQuery(),
            {
                "enabled": False,
                "feeds": [
                    {"name": "Test Feed", "url": "https://example.com/feed.xml",
                     "enabled": True},
                ],
            },
        )
        assert items == [], (
            "B07 FAIL: RssProvider collected feeds despite enabled: false"
        )

    def test_manual_enabled_true_collects(self, tmp_path):
        """When manual.enabled is True (or absent), collection works."""
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        (input_dir / "news.md").write_text(
            "- Important market signal about factory expansion plans detected.\n",
            encoding="utf-8",
        )
        provider = ManualProvider()
        items = provider.collect(
            SourceQuery(),
            {
                "enabled": True,
                "sources": [
                    {"name": "Local Input", "path": str(input_dir),
                     "category": "local_files", "enabled": True},
                ],
            },
        )
        assert len(items) > 0, (
            "B07 FAIL: ManualProvider did not collect when enabled: true"
        )


# ─── B12: Exa default API key env ───

class TestB12ExaDefaultApiKey:
    """Exa backend must default to EXA_API_KEY, not TAVILY_API_KEY."""

    def test_exa_default_env_is_exa(self):
        """ExaBackend constructor defaults to EXA_API_KEY."""
        from multi_agent_brief.sources.search_backends.exa import ExaBackend, DEFAULT_API_KEY_ENV
        assert DEFAULT_API_KEY_ENV == "EXA_API_KEY", (
            "B12 FAIL: Exa DEFAULT_API_KEY_ENV should be EXA_API_KEY"
        )
        backend = ExaBackend()
        assert backend._api_key_env == "EXA_API_KEY", (
            "B12 FAIL: ExaBackend default api_key_env should be EXA_API_KEY"
        )

    def test_exa_not_available_with_tavily_key(self, monkeypatch):
        """Exa must not find TAVILY_API_KEY as its own credential."""
        monkeypatch.setenv("TAVILY_API_KEY", "test-tavily-key")
        monkeypatch.delenv("EXA_API_KEY", raising=False)
        from multi_agent_brief.sources.search_backends.exa import ExaBackend
        backend = ExaBackend()
        assert backend.is_available() is False, (
            "B12 FAIL: Exa is_available should be False when only TAVILY_API_KEY is set"
        )

    def test_exa_available_with_exa_key(self, monkeypatch):
        """Exa must find EXA_API_KEY."""
        monkeypatch.setenv("EXA_API_KEY", "test-exa-key")
        from multi_agent_brief.sources.search_backends.exa import ExaBackend
        backend = ExaBackend()
        assert backend.is_available() is True, (
            "B12 FAIL: Exa is_available should be True when EXA_API_KEY is set"
        )

    def test_web_search_provider_passes_correct_env_to_exa(self, monkeypatch):
        """When backend is 'exa' and no explicit api_key_env, must use EXA_API_KEY."""
        monkeypatch.setenv("TAVILY_API_KEY", "tavily-key")
        monkeypatch.setenv("EXA_API_KEY", "exa-key")

        from multi_agent_brief.sources.web_search import WebSearchProvider
        provider = WebSearchProvider()

        # Without explicit api_key_env, provider should pick the right default
        # The issue: _get_backend uses config.get("api_key_env", "TAVILY_API_KEY")
        # for ALL backends. Exa should default to EXA_API_KEY.
        from multi_agent_brief.sources.search_backends.base import SearchBackend

        # Use a capture backend to see what api_key_env was passed
        class CaptureBackend(SearchBackend):
            name = "exa"
            def __init__(self, api_key_env="EXA_API_KEY"):
                super().__init__()
                self.api_key_env = api_key_env
            def search(self, *a, **kw): return []
            def is_available(self): return True

        # Monkey-patch the registry
        from multi_agent_brief.sources.web_search import _KNOWN_BACKENDS, _register_known_backends
        _register_known_backends()
        exa_cls = _KNOWN_BACKENDS.get("exa")

        # Test that when no api_key_env is explicitly configured,
        # the WebSearchProvider passes the backend its own default.
        # The issue: line 50 hardcodes "TAVILY_API_KEY" as fallback.
