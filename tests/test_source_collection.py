"""Tests for the three-layer Source Collection architecture."""
from __future__ import annotations

from pathlib import Path

import pytest

from multi_agent_brief.core.pipeline import BriefPipeline
from multi_agent_brief.core.schemas import PipelineContext
from multi_agent_brief.sources.base import SourceConfig, SourceItem, SourceQuery
from multi_agent_brief.sources.planner import SourcePlan, SearchTask, create_source_plan
from multi_agent_brief.sources.industry_packs import get_industry_pack, list_industries
from multi_agent_brief.sources.web_search import WebSearchProvider
from multi_agent_brief.sources.search_backends.mock import MockSearchBackend
from multi_agent_brief.sources.cached_package import CachedPackageProvider
from multi_agent_brief.sources.registry import collect_all_sources


# --- SourcePlanner ---

def test_create_source_plan_solar():
    plan = create_source_plan(industry="solar", report_date="2026-06-02", recency_days=7)
    assert plan.industry == "solar"
    assert len(plan.search_tasks) > 0
    assert len(plan.rss_feeds) > 0
    assert any("pv-tech.org" in str(task.source_domains) for task in plan.search_tasks)


def test_create_source_plan_unknown_industry():
    plan = create_source_plan(industry="unknown", report_date="2026-06-02")
    assert plan.industry == "unknown"
    assert len(plan.search_tasks) == 0


def test_create_source_plan_with_extra_keywords():
    plan = create_source_plan(industry="solar", extra_keywords=["bifacial module"])
    assert any("bifacial" in task.query for task in plan.search_tasks)


# --- Industry Packs ---

def test_list_industries():
    industries = list_industries()
    assert "solar" in industries
    assert "technology" in industries


def test_get_industry_pack():
    pack = get_industry_pack("solar")
    assert pack is not None
    assert "rss_feeds" in pack
    assert "search_tasks" in pack


def test_get_industry_pack_unknown():
    pack = get_industry_pack("nonexistent")
    assert pack is None


# --- WebSearchProvider ---

def test_web_search_mock_backend():
    backend = MockSearchBackend()
    assert backend.is_available()
    results = backend.search("solar", max_results=2)
    assert len(results) == 2
    assert results[0].title  # not empty


def test_web_search_provider_collects():
    provider = WebSearchProvider(backend=MockSearchBackend())
    config = {"enabled": True}
    items = provider.collect(SourceQuery(keywords=["solar"]), config)
    assert len(items) > 0
    assert all(item.source_type == "web_search" for item in items)


def test_web_search_domain_filtering():
    """Search tasks with domains should be passed to backend."""
    provider = WebSearchProvider(backend=MockSearchBackend())
    config = {
        "enabled": True,
        "search_tasks": [
            {"query": "solar prices", "domains": ["pv-tech.org"]},
        ],
    }
    items = provider.collect(SourceQuery(), config)
    assert len(items) > 0


# --- CachedPackageProvider ---

def test_cached_package_reads_json(tmp_path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "daily.json").write_text('[{"title": "Solar news", "content": "Test content", "url": "https://example.com"}]', encoding="utf-8")

    provider = CachedPackageProvider()
    config = {"enabled": True, "paths": [str(cache_dir)], "formats": ["json"]}
    items = provider.collect(SourceQuery(), config)
    assert len(items) == 1
    assert items[0].title == "Solar news"
    assert items[0].source_type == "cached"


def test_cached_package_reads_markdown(tmp_path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "notes.md").write_text("- Solar demand grew 10 percent in the first quarter of 2026\n- A new policy was announced that affects the solar industry\n", encoding="utf-8")

    provider = CachedPackageProvider()
    config = {"enabled": True, "paths": [str(cache_dir)], "formats": ["md"]}
    items = provider.collect(SourceQuery(), config)
    assert len(items) == 2


def test_cached_package_disabled():
    provider = CachedPackageProvider()
    items = provider.collect(SourceQuery(), {"enabled": False})
    assert items == []


# --- Full pipeline integration ---

def test_pipeline_with_provider_sources(tmp_path):
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()

    # Create a local source file
    (input_dir / "news.md").write_text("- Solar industry expanded 15% in Q1.\n", encoding="utf-8")

    context = PipelineContext(
        project_name="Test Brief",
        input_dir=str(input_dir),
        output_dir=str(output_dir),
        report_date="2026-06-02",
        max_source_age_days=14,
    )

    # Attach a SourceConfig to trigger provider-based collection
    source_config = SourceConfig(
        profile="research",
        industry="solar",
        enabled_providers=["manual"],
        manual={"enabled": True, "sources": [{"name": "Test", "path": str(input_dir), "enabled": True}]},
    )
    context.metadata["source_config"] = source_config

    outputs = BriefPipeline().run(context)

    # Should have source-collection + 6 agents = 7 outputs
    assert len(outputs) == 7
    assert outputs[0].agent_name == "source-collection"
    assert "2" in outputs[0].summary or "solar" in outputs[0].artifacts.get("industry", "")


def test_pipeline_backward_compatible_local_only(tmp_path):
    """Without source_config, pipeline falls back to local files."""
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    (input_dir / "news.md").write_text("- Test signal for backward compat.\n", encoding="utf-8")

    context = PipelineContext(
        project_name="Test",
        input_dir=str(input_dir),
        output_dir=str(output_dir),
    )

    outputs = BriefPipeline().run(context)
    assert len(outputs) == 7  # source-collection + 6 agents
    assert outputs[0].agent_name == "source-collection"
