"""Source Decider: resolve llm_decide profile into concrete source candidates.

Reads source_discovery from sources.yaml, searches for relevant sources,
and generates source_candidates.yaml for user review before merging.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

try:
    import yaml
except ModuleNotFoundError:
    yaml = None  # type: ignore[assignment]

from multi_agent_brief.sources.base import SourceConfig


def _load_yaml(path: Path) -> dict[str, Any]:
    """Load YAML file, return empty dict if unavailable."""
    if yaml is None:
        raise RuntimeError("PyYAML is required: pip install pyyaml")
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _save_yaml(path: Path, data: dict[str, Any]) -> None:
    """Save dict as YAML."""
    if yaml is None:
        raise RuntimeError("PyYAML is required: pip install pyyaml")
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)


def load_source_discovery(sources_path: Path) -> dict[str, Any]:
    """Extract source_discovery section from sources.yaml."""
    data = _load_yaml(sources_path)
    return data.get("source_discovery", {})


def build_search_queries(discovery: dict[str, Any]) -> list[str]:
    """Build web search queries from source_discovery fields."""
    company = discovery.get("company", "")
    industry = discovery.get("industry", "")
    focus_areas = discovery.get("focus_areas", [])
    cadence = discovery.get("cadence", "weekly")

    queries = []

    # Industry-level query
    if industry:
        queries.append(f"{industry} industry news recent")

    # Company-level query
    if company:
        queries.append(f"{company} official announcements news")

    # Focus area queries
    if isinstance(focus_areas, str):
        focus_areas = [a.strip() for a in focus_areas.split(",") if a.strip()]
    for area in focus_areas[:5]:  # cap at 5 focus areas
        if company:
            queries.append(f"{company} {area}")
        elif industry:
            queries.append(f"{industry} {area}")

    return queries


def generate_source_candidates(
    discovery: dict[str, Any],
    search_results: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Generate source_candidates.yaml content from discovery + search results.

    Args:
        discovery: source_discovery section from sources.yaml
        search_results: list of {"query": str, "results": [{"title", "url", "snippet"}]}
    """
    company = discovery.get("company", "")
    industry = discovery.get("industry", "")
    language = discovery.get("language", "zh")
    max_age = discovery.get("max_source_age_days", 14)

    candidates: dict[str, Any] = {
        "metadata": {
            "company": company,
            "industry": industry,
            "language": language,
            "max_source_age_days": max_age,
            "generated_by": "source_decider",
            "status": "pending_review",
        },
        "recommended_sources": [],
    }

    # Categorize search results into source tiers
    tier_map = {
        "official": "company_official",
        "gov": "government_regulator",
        "research": "research_institution",
        "news": "industry_media",
        "default": "industry_media",
    }

    if search_results:
        for sr in search_results:
            query = sr.get("query", "")
            for result in sr.get("results", []):
                url = result.get("url", "")
                title = result.get("title", "")
                snippet = result.get("snippet", "")

                # Simple tier classification based on URL patterns
                tier = "industry_media"
                if any(kw in url for kw in [".gov", "gov.cn", "regulator"]):
                    tier = "government_regulator"
                elif any(kw in url for kw in ["research", "report", "analysis", "journal"]):
                    tier = "research_institution"
                elif company and company.lower() in url.lower():
                    tier = "company_official"

                candidates["recommended_sources"].append({
                    "name": title[:80],
                    "url": url,
                    "category": tier,
                    "query": query,
                    "snippet": snippet[:200],
                    "enabled": True,
                })

    # Add template entries for common source types
    template_sources = _get_template_sources(industry, language)
    candidates["template_sources"] = template_sources

    return candidates


def _get_template_sources(industry: str, language: str) -> list[dict[str, Any]]:
    """Get template source entries based on industry."""
    templates = {
        "finance": [
            {"name": "Industry regulator website", "category": "government_regulator", "enabled": True},
            {"name": "Stock exchange filings", "category": "company_official", "enabled": True},
            {"name": "Financial news outlet", "category": "industry_media", "enabled": True},
        ],
        "technology": [
            {"name": "Tech company blogs", "category": "company_official", "enabled": True},
            {"name": "Industry research reports", "category": "research_institution", "enabled": True},
            {"name": "Tech news media", "category": "industry_media", "enabled": True},
        ],
        "manufacturing": [
            {"name": "Industry association", "category": "industry_media", "enabled": True},
            {"name": "Trade publications", "category": "industry_media", "enabled": True},
            {"name": "Government policy portal", "category": "government_regulator", "enabled": True},
        ],
    }
    return templates.get(industry, templates.get("finance", []))


def merge_candidates_to_sources(
    sources_path: Path,
    candidates_path: Path,
    *,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Merge approved candidates into sources.yaml.

    Args:
        sources_path: path to sources.yaml
        candidates_path: path to source_candidates.yaml
        overwrite: if True, replace rss/web_search sections; if False, append

    Returns:
        Summary of changes
    """
    sources = _load_yaml(sources_path)
    candidates = _load_yaml(candidates_path)

    recommended = candidates.get("recommended_sources", [])
    enabled = [s for s in recommended if s.get("enabled", True)]

    # Group by category.
    # Only explicitly verified rss_feed sources go to rss.feeds.
    # All other URL categories (industry_media, research_institution,
    # government_regulator, company_official) go to manual sources as
    # URL entries — they are web pages, not RSS/Atom feeds.
    rss_feeds = []
    manual_sources = []

    for src in enabled:
        category = src.get("category", "")
        url = src.get("url", "")
        name = src.get("name", "")

        if not url:
            continue

        if category == "rss_feed":
            rss_feeds.append({"name": name, "url": url, "category": category, "enabled": True})
        else:
            # All other URL types go to manual sources (not RSS)
            manual_sources.append({"name": name, "url": url, "category": category, "enabled": True})

    # Merge into sources
    if "manual" not in sources:
        sources["manual"] = {"enabled": True, "sources": []}
    if "rss" not in sources:
        sources["rss"] = {"enabled": True, "feeds": []}

    existing_manual_urls = {s.get("url") for s in sources["manual"].get("sources", [])}
    existing_rss_urls = {f.get("url") for f in sources["rss"].get("feeds", [])}

    added_manual = 0
    added_rss = 0

    for src in manual_sources:
        if src["url"] not in existing_manual_urls:
            sources["manual"]["sources"].append(src)
            added_manual += 1

    for feed in rss_feeds:
        if feed["url"] not in existing_rss_urls:
            sources["rss"]["feeds"].append(feed)
            added_rss += 1

    # Ensure web_search section exists, but do NOT auto-enable it.
    # Only enable web_search if it was already enabled OR the user explicitly
    # set a real backend (not mock). Mock data must never leak into real reports
    # unless the user explicitly opted in with allow_mock_search: true.
    if not sources.get("web_search"):
        sources["web_search"] = {"enabled": False, "max_results": 20, "recency_days": 7}
    # Do not auto-enable web_search on merge.

    # Update source_strategy
    if "source_strategy" not in sources:
        sources["source_strategy"] = {"profile": "research", "enabled_providers": ["manual"]}
    providers = sources["source_strategy"].get("enabled_providers", [])
    if "rss" not in providers and added_rss > 0:
        providers.append("rss")
    # Only add web_search to enabled_providers if it is actually enabled
    if "web_search" not in providers and sources.get("web_search", {}).get("enabled"):
        providers.append("web_search")
    sources["source_strategy"]["enabled_providers"] = providers

    # Mark candidates as merged
    candidates["metadata"]["status"] = "merged"
    candidates["metadata"]["merged_manual"] = added_manual
    candidates["metadata"]["merged_rss"] = added_rss

    _save_yaml(sources_path, sources)
    _save_yaml(candidates_path, candidates)

    return {
        "added_manual": added_manual,
        "added_rss": added_rss,
        "total_enabled": len(enabled),
        "total_disabled": len(recommended) - len(enabled),
    }
