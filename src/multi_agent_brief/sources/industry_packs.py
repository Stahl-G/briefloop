"""Industry-specific source presets for SourcePlanner."""
from __future__ import annotations

from typing import Any


INDUSTRY_PACKS: dict[str, dict[str, Any]] = {
    "manufacturing": {
        "name": "Manufacturing / Industrial",
        "rss_feeds": [],
        "search_tasks": [
            {"query": "manufacturing PMI industrial production", "topic": "market", "domains": []},
            {"query": "supply chain logistics disruption", "topic": "competitor", "domains": []},
            {"query": "manufacturing tariff trade policy", "topic": "policy", "domains": []},
        ],
    },
    "banking": {
        "name": "Banking",
        "rss_feeds": [],
        "search_tasks": [
            {"query": "bank earnings net interest margin regulation", "topic": "earnings", "domains": []},
            {"query": "central bank interest rate policy banking", "topic": "policy", "domains": []},
            {"query": "bank capital adequacy credit risk", "topic": "capital", "domains": []},
        ],
    },
    "fund": {
        "name": "Fund / Asset Management",
        "rss_feeds": [],
        "search_tasks": [
            {"query": "asset management fund flows market update", "topic": "market", "domains": []},
            {"query": "investment fund regulation disclosure", "topic": "policy", "domains": []},
            {"query": "private equity venture capital fundraising deal", "topic": "capital", "domains": []},
        ],
    },
    "internet": {
        "name": "Internet / Technology",
        "rss_feeds": [],
        "search_tasks": [
            {"query": "internet platform product launch competition", "topic": "competitor", "domains": []},
            {"query": "technology company earnings revenue guidance", "topic": "earnings", "domains": []},
            {"query": "AI internet regulation antitrust policy", "topic": "policy", "domains": []},
        ],
    },
    "general": {
        "name": "General Research",
        "rss_feeds": [],
        "search_tasks": [
            {"query": "business policy market update", "topic": "market", "domains": []},
            {"query": "company earnings guidance update", "topic": "earnings", "domains": []},
            {"query": "regulation policy industry update", "topic": "policy", "domains": []},
        ],
    },
}


def get_industry_pack(industry: str) -> dict[str, Any] | None:
    """Get industry pack by name. Returns None if not found."""
    return INDUSTRY_PACKS.get(industry)


def list_industries() -> list[str]:
    """List all available industry pack names."""
    return list(INDUSTRY_PACKS.keys())
