"""Tests for source decider (llm_decide execution)."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from multi_agent_brief.sources.decider import (
    build_search_queries,
    build_daily_news_search_tasks,
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


def test_build_daily_news_search_tasks_customized_from_user_need():
    discovery = {
        "company": "Example Solar",
        "industry": "solar manufacturing",
        "task_objective": "Track US policy and HJT capacity signals",
        "focus_areas": ["IRA tariffs", "HJT capacity"],
        "audience": "management",
        "language": "en",
    }

    tasks = build_daily_news_search_tasks(
        discovery,
        days=7,
        daily_max_results=20,
        report_date="2026-06-09",
    )

    assert len(tasks) == 7
    assert all(task["max_results"] == 20 for task in tasks)
    assert tasks[0]["date_window_start"] == "2026-06-02"
    assert tasks[-1]["date_window_end"] == "2026-06-09"
    assert all("after:" in task["query"] and "before:" in task["query"] for task in tasks)
    assert all(task["source_intent"] == "initial_daily_news_backfill" for task in tasks)
    joined = "\n".join(task["query"] for task in tasks)
    assert "Example Solar" in joined
    assert "solar manufacturing" in joined
    assert "IRA tariffs" in joined
    assert "HJT capacity" in joined


def test_build_daily_news_search_tasks_uses_user_selected_domains():
    discovery = {
        "company": "ExampleCo",
        "industry": "industrial technology",
        "focus_areas": ["supply chain"],
        "language": "en",
        "news_source_selection": {
            "preferred_domains": [
                "https://news.example.com/latest",
                "www.industry.example.org",
            ],
            "excluded_domains": ["spam.example.net"],
        },
    }

    tasks = build_daily_news_search_tasks(
        discovery,
        days=2,
        daily_max_results=20,
        report_date="2026-06-09",
    )

    assert len(tasks) == 2
    assert all(
        task["domains"] == ["news.example.com", "industry.example.org"]
        for task in tasks
    )
    assert all(task["preferred_domains"] == ["news.example.com", "industry.example.org"] for task in tasks)
    assert all(task["excluded_domains"] == ["spam.example.net"] for task in tasks)


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


def test_generate_source_candidates_preserves_daily_backfill_metadata():
    discovery = {"company": "TestCo", "industry": "finance", "language": "en"}
    search_results = [
        {
            "query": "TestCo finance news after:2026-06-02 before:2026-06-03",
            "metadata": {
                "source_intent": "initial_daily_news_backfill",
                "date_window_start": "2026-06-02",
                "date_window_end": "2026-06-03",
            },
            "results": [
                {
                    "title": "TestCo policy update",
                    "url": "https://example.com/testco-policy",
                    "snippet": "A policy update affected TestCo.",
                    "published_at": "2026-06-02",
                    "source_name": "Example News",
                },
            ],
        }
    ]

    candidates = generate_source_candidates(discovery, search_results)
    source = candidates["recommended_sources"][0]
    assert source["search_intent"] == "initial_daily_news_backfill"
    assert source["date_window_start"] == "2026-06-02"
    assert source["date_window_end"] == "2026-06-03"
    assert source["published_at"] == "2026-06-02"
    assert source["source_name"] == "Example News"


def test_generate_source_candidates_filters_excluded_domains():
    discovery = {"company": "TestCo", "industry": "finance", "language": "en"}
    search_results = [
        {
            "query": "TestCo finance news",
            "metadata": {
                "source_intent": "initial_daily_news_backfill",
                "excluded_domains": ["spam.example.com"],
            },
            "results": [
                {
                    "title": "Spam result",
                    "url": "https://spam.example.com/testco",
                    "snippet": "Low quality result.",
                },
                {
                    "title": "Useful result",
                    "url": "https://news.example.org/testco",
                    "snippet": "Useful source.",
                },
            ],
        }
    ]

    candidates = generate_source_candidates(discovery, search_results)

    sources = candidates["recommended_sources"]
    assert len(sources) == 1
    assert sources[0]["name"] == "Useful result"


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


def test_merge_candidates_preserves_daily_backfill_source_metadata(workspace_with_sources: Path):
    sources_path = workspace_with_sources / "sources.yaml"
    candidates = {
        "metadata": {},
        "recommended_sources": [
            {
                "name": "Daily Source",
                "url": "https://example.com/daily-source",
                "category": "industry_media",
                "published_at": "2026-06-02",
                "source_name": "Example News",
                "search_intent": "initial_daily_news_backfill",
                "date_window_start": "2026-06-02",
                "date_window_end": "2026-06-03",
                "enabled": True,
            }
        ],
    }
    candidates_path = workspace_with_sources / "source_candidates.yaml"
    candidates_path.write_text(yaml.dump(candidates, allow_unicode=True), encoding="utf-8")

    merge_candidates_to_sources(sources_path, candidates_path)

    updated = yaml.safe_load(sources_path.read_text(encoding="utf-8"))
    source = updated["manual"]["sources"][0]
    assert source["published_at"] == "2026-06-02"
    assert source["source_name"] == "Example News"
    assert source["search_intent"] == "initial_daily_news_backfill"
    assert source["date_window_start"] == "2026-06-02"
    assert source["date_window_end"] == "2026-06-03"


def test_merge_candidates_normalizes_yaml_null_list_fields(tmp_path: Path):
    """YAML empty list fields parse as None and must not crash merge."""
    sources_path = tmp_path / "sources.yaml"
    sources = {
        "source_strategy": {
            "profile": "llm_decide",
            "enabled_providers": None,
        },
        "source_discovery": {
            "company": "DemoCo",
            "industry": "technology",
            "language": "en",
        },
        "manual": {"enabled": True, "sources": None},
        "rss": {"enabled": True, "feeds": None},
        "web_search": {
            "enabled": True,
            "mode": "external_api",
            "backend": "tavily",
            "search_tasks": None,
        },
        "filing_resolver": {
            "enabled": True,
            "tickers": None,
            "filing_types": None,
        },
    }
    sources_path.write_text(yaml.dump(sources, allow_unicode=True), encoding="utf-8")

    candidates_path = tmp_path / "source_candidates.yaml"
    candidates = {
        "metadata": {},
        "recommended_sources": [
            {
                "name": "DemoCo Official",
                "url": "https://example.com/democo",
                "category": "company_official",
                "enabled": True,
            },
            {
                "name": "Demo RSS",
                "url": "https://example.com/rss.xml",
                "category": "rss_feed",
                "enabled": True,
            },
        ],
        "filing_sources": [
            {
                "name": "DemoCo filings",
                "provider": "filing_resolver",
                "tickers": ["DEMO"],
                "filing_types": ["20-F"],
                "enabled": True,
            }
        ],
        "local_social_listening_tasks": [
            {
                "query": "DemoCo discussion",
                "market": "US",
                "language": "en",
                "platform_group": "public_web",
                "signal_type": "consumer_discussion",
                "enabled": True,
            }
        ],
    }
    candidates_path.write_text(yaml.dump(candidates, allow_unicode=True), encoding="utf-8")

    result = merge_candidates_to_sources(sources_path, candidates_path)

    assert result["added_manual"] == 1
    assert result["added_rss"] == 1
    assert result["added_filing"] == 1
    assert result["added_local"] == 1

    updated = yaml.safe_load(sources_path.read_text(encoding="utf-8"))
    assert updated["manual"]["sources"][0]["url"] == "https://example.com/democo"
    assert updated["rss"]["feeds"][0]["url"] == "https://example.com/rss.xml"
    assert updated["filing_resolver"]["tickers"] == [{"ticker": "DEMO"}]
    assert "20-F" in updated["filing_resolver"]["filing_types"]
    assert updated["web_search"]["search_tasks"][0]["query"] == "DemoCo discussion"
    assert updated["source_strategy"]["enabled_providers"] == [
        "manual",
        "rss",
        "web_search",
        "filing_resolver",
    ]


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


# ---- filing_sources integration tests ----


def test_generate_source_candidates_includes_filing_sources():
    """generate_source_candidates should include filing_sources when company is set."""
    discovery = {"company": "Acme Corp", "industry": "manufacturing", "language": "en"}
    candidates = generate_source_candidates(discovery)
    assert "filing_sources" in candidates
    fs = candidates["filing_sources"]
    assert len(fs) == 1
    assert fs[0]["provider"] == "filing_resolver"
    assert "Acme Corp" in fs[0]["name"]
    assert fs[0]["tickers"] == ["Acme Corp"]
    assert fs[0]["enabled"] is True


def test_generate_source_candidates_no_filing_when_no_company():
    """filing_sources should be absent when company is empty."""
    discovery = {"industry": "technology", "language": "en"}
    candidates = generate_source_candidates(discovery)
    assert "filing_sources" not in candidates


def test_merge_candidates_merges_filing_sources(workspace_with_sources: Path):
    """Merging candidates with filing_sources should enable filing_resolver provider."""
    sources_path = workspace_with_sources / "sources.yaml"
    discovery = load_source_discovery(sources_path)
    candidates = generate_source_candidates(discovery)
    # The fixture has company="测试公司", so filing_sources should be present
    assert "filing_sources" in candidates

    candidates_path = workspace_with_sources / "source_candidates.yaml"
    with open(candidates_path, "w", encoding="utf-8") as f:
        yaml.dump(candidates, f, allow_unicode=True)

    result = merge_candidates_to_sources(sources_path, candidates_path)

    assert result["added_filing"] == 1

    updated = yaml.safe_load(sources_path.read_text(encoding="utf-8"))
    assert "filing_resolver" in updated
    assert updated["filing_resolver"]["enabled"] is True
    assert {"company_name": "测试公司"} in updated["filing_resolver"]["tickers"]
    assert "10-K" in updated["filing_resolver"]["filing_types"]
    assert "filing_resolver" in updated["source_strategy"]["enabled_providers"]


def test_merge_candidates_filing_sources_disabled(workspace_with_sources: Path):
    """Disabled filing_sources should not be merged."""
    sources_path = workspace_with_sources / "sources.yaml"
    discovery = load_source_discovery(sources_path)
    candidates = generate_source_candidates(discovery)
    # Disable the filing source
    for fs in candidates.get("filing_sources", []):
        fs["enabled"] = False

    candidates_path = workspace_with_sources / "source_candidates.yaml"
    with open(candidates_path, "w", encoding="utf-8") as f:
        yaml.dump(candidates, f, allow_unicode=True)

    result = merge_candidates_to_sources(sources_path, candidates_path)

    assert result["added_filing"] == 0

    updated = yaml.safe_load(sources_path.read_text(encoding="utf-8"))
    # filing_resolver should NOT be added to enabled_providers
    assert "filing_resolver" not in updated.get("source_strategy", {}).get("enabled_providers", [])


def test_merge_candidates_filing_idempotent(workspace_with_sources: Path):
    """Merging same filing_sources twice should not duplicate tickers."""
    sources_path = workspace_with_sources / "sources.yaml"
    discovery = load_source_discovery(sources_path)
    candidates = generate_source_candidates(discovery)

    candidates_path = workspace_with_sources / "source_candidates.yaml"
    with open(candidates_path, "w", encoding="utf-8") as f:
        yaml.dump(candidates, f, allow_unicode=True)

    merge_candidates_to_sources(sources_path, candidates_path)
    merge_candidates_to_sources(sources_path, candidates_path)

    updated = yaml.safe_load(sources_path.read_text(encoding="utf-8"))
    assert updated["filing_resolver"]["tickers"].count({"company_name": "测试公司"}) == 1


def test_merge_candidates_filing_merges_tickers(workspace_with_sources: Path):
    """Merging filing_sources with additional tickers should append, not replace."""
    sources_path = workspace_with_sources / "sources.yaml"

    # First merge: just the default from discovery
    discovery = load_source_discovery(sources_path)
    candidates = generate_source_candidates(discovery)
    candidates_path = workspace_with_sources / "source_candidates.yaml"
    with open(candidates_path, "w", encoding="utf-8") as f:
        yaml.dump(candidates, f, allow_unicode=True)
    merge_candidates_to_sources(sources_path, candidates_path)

    # Second merge: add another ticker
    candidates2 = generate_source_candidates(discovery)
    candidates2["filing_sources"].append({
        "name": "Peer Corp — SEC EDGAR filings",
        "provider": "filing_resolver",
        "tickers": ["PEER"],
        "filing_types": ["10-K"],
        "category": "company_official",
        "enabled": True,
    })
    candidates2_path = workspace_with_sources / "source_candidates2.yaml"
    with open(candidates2_path, "w", encoding="utf-8") as f:
        yaml.dump(candidates2, f, allow_unicode=True)
    merge_candidates_to_sources(sources_path, candidates2_path)

    updated = yaml.safe_load(sources_path.read_text(encoding="utf-8"))
    tickers = updated["filing_resolver"]["tickers"]
    assert {"company_name": "测试公司"} in tickers
    assert {"ticker": "PEER"} in tickers


def test_merge_candidates_filing_handles_existing_mapping_tickers(workspace_with_sources: Path):
    """Existing canonical filing_resolver mappings remain idempotent."""
    sources_path = workspace_with_sources / "sources.yaml"
    sources = yaml.safe_load(sources_path.read_text(encoding="utf-8"))
    sources["filing_resolver"] = {
        "enabled": True,
        "tickers": [{"ticker": "PEER"}],
        "filing_types": ["10-K"],
    }
    sources_path.write_text(yaml.dump(sources, allow_unicode=True), encoding="utf-8")

    candidates = {
        "metadata": {},
        "filing_sources": [
            {
                "name": "Peer Corp — SEC EDGAR filings",
                "provider": "filing_resolver",
                "tickers": ["PEER"],
                "filing_types": ["10-Q"],
                "category": "company_official",
                "enabled": True,
            }
        ],
    }
    candidates_path = workspace_with_sources / "source_candidates.yaml"
    candidates_path.write_text(yaml.dump(candidates, allow_unicode=True), encoding="utf-8")

    result = merge_candidates_to_sources(sources_path, candidates_path)

    updated = yaml.safe_load(sources_path.read_text(encoding="utf-8"))
    assert result["added_filing"] == 0
    assert updated["filing_resolver"]["tickers"] == [{"ticker": "PEER"}]
    assert "10-Q" in updated["filing_resolver"]["filing_types"]


def test_merge_candidates_filing_normalizes_existing_string_tickers(workspace_with_sources: Path):
    """Legacy string ticker entries are normalized during merge."""
    sources_path = workspace_with_sources / "sources.yaml"
    sources = yaml.safe_load(sources_path.read_text(encoding="utf-8"))
    sources["filing_resolver"] = {
        "enabled": True,
        "tickers": ["FSLR"],
        "filing_types": ["10-K"],
    }
    sources_path.write_text(yaml.dump(sources, allow_unicode=True), encoding="utf-8")

    candidates = {
        "metadata": {},
        "filing_sources": [
            {
                "name": "First Solar — SEC EDGAR filings",
                "provider": "filing_resolver",
                "tickers": ["FSLR"],
                "filing_types": ["10-Q"],
                "category": "company_official",
                "enabled": True,
            }
        ],
    }
    candidates_path = workspace_with_sources / "source_candidates.yaml"
    candidates_path.write_text(yaml.dump(candidates, allow_unicode=True), encoding="utf-8")

    result = merge_candidates_to_sources(sources_path, candidates_path)

    updated = yaml.safe_load(sources_path.read_text(encoding="utf-8"))
    assert result["added_filing"] == 0
    assert updated["filing_resolver"]["tickers"] == [{"ticker": "FSLR"}]
    assert "10-Q" in updated["filing_resolver"]["filing_types"]
