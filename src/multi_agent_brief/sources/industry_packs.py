"""Industry-specific source presets for SourcePlanner."""
from __future__ import annotations

from typing import Any


INDUSTRY_PACKS: dict[str, dict[str, Any]] = {
    "solar": {
        "name": "Solar / Renewable Energy",
        "rss_feeds": [
            {"name": "PV Tech", "url": "https://www.pv-tech.org/feed/", "reliability": "medium", "category": "industry_news"},
            {"name": "PV Magazine", "url": "https://www.pv-magazine.com/feed/", "reliability": "medium", "category": "industry_news"},
            {"name": "Solar Power World", "url": "https://www.solarpowerworldonline.com/feed/", "reliability": "medium", "category": "industry_news"},
            {"name": "Greentech Media", "url": "https://www.greentechmedia.com/feed", "reliability": "medium", "category": "industry_news"},
        ],
        "search_tasks": [
            {"query": "solar policy tariff regulation update", "topic": "policy", "domains": ["pv-tech.org", "pv-magazine.com"]},
            {"query": "solar module price market", "topic": "market", "domains": ["pv-tech.org", "pv-magazine.com"]},
            {"query": "solar earnings revenue guidance", "topic": "earnings", "domains": []},
            {"query": "solar manufacturing capacity expansion plant", "topic": "competitor", "domains": ["pv-tech.org"]},
            {"query": "solar technology efficiency record TOPCon perovskite", "topic": "technology", "domains": ["pv-tech.org", "pv-magazine.com"]},
            {"query": "battery storage energy storage demand", "topic": "market", "domains": ["pv-tech.org"]},
        ],
    },
    "technology": {
        "name": "Internet / Technology",
        "rss_feeds": [
            {"name": "TechCrunch", "url": "https://techcrunch.com/feed/", "reliability": "medium", "category": "tech_news"},
            {"name": "The Verge", "url": "https://www.theverge.com/rss/index.xml", "reliability": "medium", "category": "tech_news"},
        ],
        "search_tasks": [
            {"query": "technology earnings revenue guidance", "topic": "earnings", "domains": []},
            {"query": "AI artificial intelligence launch product", "topic": "competitor", "domains": []},
            {"query": "technology regulation policy antitrust", "topic": "policy", "domains": []},
            {"query": "semiconductor chip manufacturing capacity", "topic": "market", "domains": []},
        ],
    },
    "finance": {
        "name": "Finance / Banking",
        "rss_feeds": [],
        "search_tasks": [
            {"query": "financial earnings revenue bank", "topic": "earnings", "domains": []},
            {"query": "interest rate monetary policy central bank", "topic": "policy", "domains": []},
            {"query": "M&A acquisition investment deal", "topic": "capital", "domains": []},
        ],
    },
    "manufacturing": {
        "name": "Manufacturing / Industrial",
        "rss_feeds": [],
        "search_tasks": [
            {"query": "manufacturing PMI industrial production", "topic": "market", "domains": []},
            {"query": "supply chain logistics disruption", "topic": "competitor", "domains": []},
            {"query": "manufacturing tariff trade policy", "topic": "policy", "domains": []},
        ],
    },
    "policy_macro": {
        "name": "Policy / Macro Economics",
        "rss_feeds": [],
        "search_tasks": [
            {"query": "government policy regulation update", "topic": "policy", "domains": []},
            {"query": "GDP economic growth forecast", "topic": "market", "domains": []},
            {"query": "trade tariff sanctions", "topic": "policy", "domains": []},
        ],
    },
}


def get_industry_pack(industry: str) -> dict[str, Any] | None:
    """Get industry pack by name. Returns None if not found."""
    return INDUSTRY_PACKS.get(industry)


def list_industries() -> list[str]:
    """List all available industry pack names."""
    return list(INDUSTRY_PACKS.keys())
