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

def test_web_search_stub_returns_empty():
    provider = WebSearchProvider()
    config = {"enabled": True}
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
    items = collect_all_sources(config)
    assert len(items) == 1
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
