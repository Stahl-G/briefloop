"""Tests for the Source Provider system."""
from __future__ import annotations

from pathlib import Path

import pytest

from multi_agent_brief.sources.base import SourceConfig, SourceItem, SourceQuery, SOURCE_PROFILES
from multi_agent_brief.sources.manual import ManualProvider
from multi_agent_brief.sources.rss import RssProvider
from multi_agent_brief.sources.web_search import WebSearchProvider
from multi_agent_brief.sources.api_news import NewsApiProvider
from multi_agent_brief.sources.mcp_provider import McpProvider
from multi_agent_brief.sources.normalizer import normalize_source_item, dedupe_sources, filter_by_recency
from multi_agent_brief.sources.registry import load_sources_config, collect_all_sources, validate_all_providers
from multi_agent_brief.sources.doctor import run_doctor, format_doctor_report


# --- SourceConfig ---

def test_source_config_from_dict():
    data = {
        "source_strategy": {"profile": "research", "enabled_providers": ["manual", "rss"]},
        "manual": {"enabled": True, "sources": [{"name": "Test", "path": "input/"}]},
        "rss": {"enabled": False},
    }
    config = SourceConfig.from_dict(data)
    assert config.profile == "research"
    assert config.enabled_providers == ["manual", "rss"]
    assert config.manual["enabled"] is True


def test_source_config_defaults():
    config = SourceConfig()
    assert config.profile == "research"
    assert config.enabled_providers == ["manual"]


def test_source_profiles_defined():
    assert "conservative" in SOURCE_PROFILES
    assert "research" in SOURCE_PROFILES
    assert "aggressive_signal" in SOURCE_PROFILES


# --- ManualProvider ---

def test_manual_provider_loads_local_files(tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "news.md").write_text("- Solar demand grew 10% in Q1.\n- New tariff announced.\n", encoding="utf-8")

    provider = ManualProvider()
    config = {"sources": [{"name": "Test", "path": str(input_dir)}]}
    query = SourceQuery()
    items = provider.collect(query, config)

    assert len(items) == 1
    assert items[0].source_type == "local_file"
    assert "Solar demand" in items[0].content


def test_manual_provider_loads_json(tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    import json
    (input_dir / "data.json").write_text(json.dumps({
        "source_url": "https://example.com",
        "published_at": "2026-06-01",
        "items": ["Item one", "Item two"],
    }), encoding="utf-8")

    provider = ManualProvider()
    config = {"sources": [{"name": "JSON Source", "path": str(input_dir)}]}
    items = provider.collect(SourceQuery(), config)

    assert len(items) == 1
    assert "Item one" in items[0].content
    assert items[0].url == "https://example.com"


def test_manual_provider_url_entry():
    provider = ManualProvider()
    config = {"sources": [{"name": "PV Magazine", "url": "https://www.pv-magazine.com/"}]}
    items = provider.collect(SourceQuery(), config)

    assert len(items) == 1
    assert items[0].source_type == "manual_url"
    assert items[0].url == "https://www.pv-magazine.com/"


def test_manual_provider_skips_disabled():
    provider = ManualProvider()
    config = {"sources": [{"name": "Disabled", "path": "/nonexistent", "enabled": False}]}
    items = provider.collect(SourceQuery(), config)
    assert items == []


def test_manual_provider_validate_config():
    provider = ManualProvider()
    errors = provider.validate_config({"sources": [{"name": "", "path": ""}]})
    assert len(errors) == 2  # missing name and missing path/url


# --- RssProvider ---

def test_rss_provider_validate_config():
    provider = RssProvider()
    errors = provider.validate_config({"feeds": [{"name": "", "url": ""}]})
    assert len(errors) == 2


def test_rss_provider_skips_disabled():
    provider = RssProvider()
    config = {"feeds": [{"name": "Test", "url": "http://example.com/feed", "enabled": False}]}
    items = provider.collect(SourceQuery(), config)
    assert items == []


# --- Stubs ---

def test_web_search_returns_mock_results():
    provider = WebSearchProvider()
    config = {"enabled": True, "backend": "mock"}
    items = provider.collect(SourceQuery(), config)
    assert len(items) > 0
    assert items[0].source_type == "web_search"


def test_web_search_disabled_returns_empty():
    provider = WebSearchProvider()
    config = {"enabled": False}
    items = provider.collect(SourceQuery(), config)
    assert items == []


def test_news_api_stub_returns_empty():
    provider = NewsApiProvider()
    config = {"enabled": True, "providers": []}
    items = provider.collect(SourceQuery(), config)
    assert items == []


def test_mcp_stub_returns_empty():
    provider = McpProvider()
    config = {"enabled": True, "servers": []}
    items = provider.collect(SourceQuery(), config)
    assert items == []


# --- Normalizer ---

def test_normalize_source_item():
    item = SourceItem(
        source_id="", source_name="Test", source_type="manual",
        title="  Hello World  ", content="  content  ", url="",
    )
    normalized = normalize_source_item(item)
    assert normalized.title == "Hello World"
    assert normalized.content == "content"
    assert normalized.dedupe_key  # should be generated
    assert normalized.source_id  # should be generated


def test_dedupe_sources():
    items = [
        SourceItem(source_id="A", source_name="A", source_type="manual", title="T1", content="C1", dedupe_key="key1"),
        SourceItem(source_id="B", source_name="B", source_type="manual", title="T2", content="C2", dedupe_key="key1"),
        SourceItem(source_id="C", source_name="C", source_type="manual", title="T3", content="C3", dedupe_key="key2"),
    ]
    result = dedupe_sources(items)
    assert len(result) == 2


def test_filter_by_recency():
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    items = [
        SourceItem(source_id="A", source_name="A", source_type="manual", title="Recent", content="C",
                   published_at=now.isoformat()),
        SourceItem(source_id="B", source_name="B", source_type="manual", title="Old", content="C",
                   published_at=(now - timedelta(days=30)).isoformat()),
        SourceItem(source_id="C", source_name="C", source_type="manual", title="NoDate", content="C"),
    ]
    result = filter_by_recency(items, 14)
    assert len(result) == 2  # Recent + NoDate


# --- Registry ---

def test_load_sources_config(tmp_path):
    sources_path = tmp_path / "sources.yaml"
    sources_path.write_text("""
source_strategy:
  profile: conservative
  enabled_providers:
    - manual
manual:
  enabled: true
  sources:
    - name: Test
      path: input/
""", encoding="utf-8")

    config = load_sources_config(sources_path)
    assert config.profile == "conservative"
    assert config.enabled_providers == ["manual"]


def test_validate_all_providers_passes():
    config = SourceConfig(
        profile="research",
        enabled_providers=["manual"],
        manual={"enabled": True, "sources": [{"name": "Test", "path": "input/"}]},
    )
    errors = validate_all_providers(config)
    assert errors == []


def test_collect_all_sources_manual(tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "test.md").write_text("- A solar factory expanded capacity.\n", encoding="utf-8")

    config = SourceConfig(
        enabled_providers=["manual"],
        manual={"enabled": True, "sources": [{"name": "Test", "path": str(input_dir)}]},
    )
    items, errors = collect_all_sources(config)
    assert len(items) == 1
    assert errors == []
    assert "solar" in items[0].content.lower()


# --- Doctor ---

def test_doctor_missing_config():
    results = run_doctor(config_path="/nonexistent/config.yaml")
    assert any(r.status == "ERROR" for r in results)


def test_doctor_with_valid_config(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("project:\n  name: Test\n", encoding="utf-8")
    (tmp_path / "sources.yaml").write_text("""
source_strategy:
  profile: research
  enabled_providers:
    - manual
manual:
  enabled: true
  sources:
    - name: Test
      path: input/
""", encoding="utf-8")

    results = run_doctor(config_path=config_path)
    report = format_doctor_report(results)
    assert "Source configuration check" in report
    # Should have OK for config found, profile, providers, etc.
    assert any(r.status == "OK" for r in results)


# --- P1: WebSearch source_id stability ---

def test_web_search_source_id_stable():
    """Same search result should produce same source_id across calls."""
    from multi_agent_brief.sources.search_backends.mock import MockSearchBackend

    provider = WebSearchProvider(backend=MockSearchBackend())
    config = {"enabled": True}
    query = SourceQuery(keywords=["solar"])

    items1 = provider.collect(query, config)
    items2 = provider.collect(query, config)

    ids1 = [item.source_id for item in items1]
    ids2 = [item.source_id for item in items2]
    assert ids1 == ids2
    # Should use SHA1-based format
    assert all(sid.startswith("WS_") for sid in ids1)


# --- P1: Decider merge should not auto-enable web_search ---

def test_merge_does_not_auto_enable_web_search(tmp_path):
    """merge_candidates_to_sources should not enable web_search by default."""
    import yaml
    from multi_agent_brief.sources.decider import merge_candidates_to_sources

    sources = {
        "source_strategy": {"profile": "research", "enabled_providers": ["manual"]},
        "manual": {"enabled": True, "sources": []},
        "rss": {"enabled": False, "feeds": []},
        "web_search": {"enabled": False, "max_results": 20, "recency_days": 7},
    }
    sources_path = tmp_path / "sources.yaml"
    with open(sources_path, "w", encoding="utf-8") as f:
        yaml.dump(sources, f)

    candidates = {
        "metadata": {"status": "pending_review"},
        "recommended_sources": [
            {"name": "Tech News", "url": "https://technews.com", "category": "industry_media", "enabled": True},
        ],
    }
    candidates_path = tmp_path / "source_candidates.yaml"
    with open(candidates_path, "w", encoding="utf-8") as f:
        yaml.dump(candidates, f)

    merge_candidates_to_sources(sources_path, candidates_path)

    updated = yaml.safe_load(sources_path.read_text(encoding="utf-8"))
    # web_search should NOT be auto-enabled
    assert updated["web_search"]["enabled"] is False
    # web_search should NOT be in enabled_providers
    assert "web_search" not in updated["source_strategy"].get("enabled_providers", [])


# --- P2: Provider errors are captured ---

def test_collect_all_sources_captures_provider_errors(tmp_path):
    """Failed providers should be recorded, not silently swallowed."""
    from unittest.mock import MagicMock
    from multi_agent_brief.sources.base import SourceProvider, SourceQuery
    from multi_agent_brief.sources.registry import collect_all_sources

    class FailingProvider(SourceProvider):
        name = "failing"
        source_type = "test"
        def validate_config(self, config):
            return []
        def collect(self, query, config):
            raise ConnectionError("Network timeout")

    config = SourceConfig(
        enabled_providers=["failing"],
    )

    # Monkey-patch the registry to include our failing provider
    import multi_agent_brief.sources.registry as reg
    old_registry = reg.PROVIDER_CLASSES.copy()
    reg.PROVIDER_CLASSES["failing"] = FailingProvider
    try:
        items, errors = collect_all_sources(config)
        assert items == []
        assert len(errors) == 1
        assert errors[0]["provider"] == "failing"
        assert errors[0]["error_type"] == "ConnectionError"
        assert "timeout" in errors[0]["message"]
    finally:
        reg.PROVIDER_CLASSES.clear()
        reg.PROVIDER_CLASSES.update(old_registry)

# --- P1: WebSearch backend errors propagate to registry errors ---

def test_web_search_backend_error_captured_by_registry():
    """Backend exceptions should propagate through to collect_all_sources errors."""
    from multi_agent_brief.sources.search_backends.base import SearchBackend, SearchResult
    from multi_agent_brief.sources.registry import collect_all_sources

    class FailingSearchBackend(SearchBackend):
        name = "failing_search"
        def search(self, query, max_results=10, **kwargs):
            raise ConnectionError("API rate limit exceeded")
        def is_available(self):
            return True

    from multi_agent_brief.sources.web_search import WebSearchProvider
    import multi_agent_brief.sources.registry as reg

    # Temporarily use our failing backend via WebSearchProvider
    provider = WebSearchProvider(backend=FailingSearchBackend())
    old_cls = reg.PROVIDER_CLASSES.get("web_search")
    reg.PROVIDER_CLASSES["web_search"] = lambda: provider

    config = SourceConfig(
        enabled_providers=["web_search"],
        web_search={"enabled": True},
    )
    try:
        items, errors = collect_all_sources(config)
        assert items == []
        assert len(errors) == 1
        assert errors[0]["provider"] == "web_search"
        assert errors[0]["error_type"] == "ConnectionError"
        assert "rate limit" in errors[0]["message"]
    finally:
        if old_cls:
            reg.PROVIDER_CLASSES["web_search"] = old_cls


# --- P2: Domain filtering ---

def test_web_search_passes_domains_to_backend():
    """search_tasks with domains should be forwarded to the backend."""
    from multi_agent_brief.sources.search_backends.mock import MockSearchBackend

    backend = MockSearchBackend()
    provider = WebSearchProvider(backend=backend)
    config = {
        "enabled": True,
        "search_tasks": [
            {"query": "solar prices", "domains": ["pv-tech.org", "reuters.com"]},
        ],
    }
    items = provider.collect(SourceQuery(), config)
    assert len(items) > 0
    # Verify the backend received the domains
    assert backend.last_domains == ["pv-tech.org", "reuters.com"]


def test_web_search_no_domains_passes_none():
    """search_tasks without domains should pass domains=None."""
    from multi_agent_brief.sources.search_backends.mock import MockSearchBackend

    backend = MockSearchBackend()
    provider = WebSearchProvider(backend=backend)
    config = {"enabled": True}
    items = provider.collect(SourceQuery(keywords=["solar"]), config)
    assert len(items) > 0
    assert backend.last_domains is None


# --- P2: Doctor web_search message ---

def test_doctor_warns_mock_backend_for_web_search(tmp_path):
    """Doctor should warn about mock backend when web_search is enabled."""
    from multi_agent_brief.sources.doctor import run_doctor, format_doctor_report
    import yaml

    config_path = tmp_path / "config.yaml"
    config_path.write_text("project:\n  name: Test\n", encoding="utf-8")

    sources = {
        "source_strategy": {"profile": "research", "enabled_providers": ["web_search"]},
        "web_search": {"enabled": True, "backend": "mock"},
    }
    (tmp_path / "sources.yaml").write_text(yaml.dump(sources), encoding="utf-8")

    results = run_doctor(config_path=config_path)
    assert any("mock backend" in r.message.lower() for r in results)


def test_doctor_ok_for_real_backend(tmp_path):
    """Doctor should report OK for a non-mock backend."""
    from multi_agent_brief.sources.doctor import run_doctor
    import yaml

    config_path = tmp_path / "config.yaml"
    config_path.write_text("project:\n  name: Test\n", encoding="utf-8")

    sources = {
        "source_strategy": {"profile": "research", "enabled_providers": ["web_search"]},
        "web_search": {"enabled": True, "backend": "tavily"},
    }
    (tmp_path / "sources.yaml").write_text(yaml.dump(sources), encoding="utf-8")

    results = run_doctor(config_path=config_path)
    assert any("tavily" in r.message.lower() and r.status == "OK" for r in results)
