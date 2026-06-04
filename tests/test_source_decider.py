"""Tests for source decider (llm_decide execution)."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
import yaml

from multi_agent_brief.sources.decider import (
    build_search_queries,
    generate_source_candidates,
    load_source_discovery,
    merge_candidates_to_sources,
)


@pytest.fixture
def workspace_with_sources(tmp_path: Path):
    """Create a minimal workspace with sources.yaml."""
    sources = {
        "source_strategy": {
            "profile": "llm_decide",
            "decision_mode": "agent_decide",
            "requires_agent_resolution": True,
        },
        "source_discovery": {
            "company": "测试公司",
            "industry": "technology",
            "role": "research_analyst",
            "audience": "management",
            "focus_areas": ["竞品动态", "政策法规"],
            "cadence": "weekly",
            "max_source_age_days": 14,
            "language": "zh",
            "source_profile": "llm_decide",
        },
        "manual": {"enabled": True, "sources": []},
        "rss": {"enabled": False, "feeds": []},
    }
    sources_path = tmp_path / "sources.yaml"
    with open(sources_path, "w", encoding="utf-8") as f:
        yaml.dump(sources, f, allow_unicode=True)
    return tmp_path


def test_load_source_discovery(workspace_with_sources: Path):
    discovery = load_source_discovery(workspace_with_sources / "sources.yaml")
    assert discovery["company"] == "测试公司"
    assert discovery["industry"] == "technology"
    assert "竞品动态" in discovery["focus_areas"]


def test_load_source_discovery_missing(tmp_path: Path):
    discovery = load_source_discovery(tmp_path / "nonexistent.yaml")
    assert discovery == {}


def test_build_search_queries():
    discovery = {
        "company": "网易游戏",
        "industry": "technology",
        "focus_areas": ["竞品分析", "游戏版号"],
        "cadence": "weekly",
    }
    queries = build_search_queries(discovery)
    assert len(queries) >= 3
    assert any("网易游戏" in q for q in queries)
    assert any("竞品分析" in q for q in queries)


def test_build_search_queries_no_focus():
    discovery = {"company": "TestCo", "industry": "finance"}
    queries = build_search_queries(discovery)
    assert len(queries) >= 2


def test_generate_source_candidates(workspace_with_sources: Path):
    discovery = load_source_discovery(workspace_with_sources / "sources.yaml")
    candidates = generate_source_candidates(discovery)

    assert candidates["metadata"]["company"] == "测试公司"
    assert candidates["metadata"]["status"] == "pending_review"
    assert "recommended_sources" in candidates
    assert "template_sources" in candidates


def test_generate_source_candidates_with_search_results():
    discovery = {"company": "TestCo", "industry": "finance", "language": "en"}
    search_results = [
        {
            "query": "TestCo news",
            "results": [
                {"title": "TestCo Announces Q2 Results", "url": "https://testco.com/news", "snippet": "..."},
                {"title": "Finance News", "url": "https://reuters.com/finance", "snippet": "..."},
            ],
        }
    ]
    candidates = generate_source_candidates(discovery, search_results)
    assert len(candidates["recommended_sources"]) == 2
    assert candidates["recommended_sources"][0]["category"] == "company_official"


def test_merge_candidates_to_sources(workspace_with_sources: Path):
    sources_path = workspace_with_sources / "sources.yaml"
    discovery = load_source_discovery(sources_path)
    candidates = generate_source_candidates(discovery)

    # Add some enabled candidates with URLs
    candidates["recommended_sources"] = [
        {"name": "Official Blog", "url": "https://testco.com/blog", "category": "company_official", "enabled": True},
        {"name": "Tech News", "url": "https://technews.com", "category": "industry_media", "enabled": True},
        {"name": "Disabled Source", "url": "https://disabled.com", "category": "industry_media", "enabled": False},
    ]

    candidates_path = workspace_with_sources / "source_candidates.yaml"
    with open(candidates_path, "w", encoding="utf-8") as f:
        yaml.dump(candidates, f, allow_unicode=True)

    result = merge_candidates_to_sources(sources_path, candidates_path)

    # Both company_official and industry_media now go to manual (not RSS).
    # Only explicitly verified rss_feed sources go to rss.feeds.
    assert result["added_manual"] == 2
    assert result["added_rss"] == 0
    assert result["total_enabled"] == 2
    assert result["total_disabled"] == 1

    # Verify sources.yaml was updated
    updated = yaml.safe_load(sources_path.read_text(encoding="utf-8"))
    assert len(updated["manual"]["sources"]) == 2
    assert len(updated["rss"]["feeds"]) == 0
    # After fix: merge should NOT auto-enable web_search
    assert updated["web_search"]["enabled"] is False


def test_merge_candidates_idempotent(workspace_with_sources: Path):
    """Merging same candidates twice should not duplicate."""
    sources_path = workspace_with_sources / "sources.yaml"
    discovery = load_source_discovery(sources_path)
    candidates = generate_source_candidates(discovery)
    candidates["recommended_sources"] = [
        {"name": "Official", "url": "https://testco.com", "category": "company_official", "enabled": True},
    ]

    candidates_path = workspace_with_sources / "source_candidates.yaml"
    with open(candidates_path, "w", encoding="utf-8") as f:
        yaml.dump(candidates, f, allow_unicode=True)

    merge_candidates_to_sources(sources_path, candidates_path)
    merge_candidates_to_sources(sources_path, candidates_path)

    updated = yaml.safe_load(sources_path.read_text(encoding="utf-8"))
    assert len(updated["manual"]["sources"]) == 1  # not duplicated
