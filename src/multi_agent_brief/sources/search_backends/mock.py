"""Mock search backend for testing."""
from __future__ import annotations

from typing import Any

from multi_agent_brief.sources.search_backends.base import SearchBackend, SearchResult


class MockSearchBackend(SearchBackend):
    """Returns synthetic search results for testing."""

    name = "mock"

    def __init__(self, results: list[SearchResult] | None = None) -> None:
        self.last_domains: list[str] | None = None
        self._results = results or [
            SearchResult(
                title="Solar industry saw 15% capacity growth in Q1 2026",
                url="https://example.com/solar-q1-2026",
                snippet="Global solar manufacturing capacity expanded by 15% in Q1 2026, driven by Chinese and Indian manufacturers.",
                published_at="2026-04-01",
                source_name="Mock Industry News",
            ),
            SearchResult(
                title="New tariff proposal could impact solar imports",
                url="https://example.com/tariff-proposal",
                snippet="US Trade Representative proposed new tariff rates on imported solar modules, effective Q3 2026.",
                published_at="2026-05-15",
                source_name="Mock Policy News",
            ),
            SearchResult(
                title="Top solar installer reports record revenue",
                url="https://example.com/solar-earnings",
                snippet="Leading US solar installer reported Q1 revenue of $2.1B, up 22% year-over-year.",
                published_at="2026-05-01",
                source_name="Mock Earnings News",
            ),
        ]

    def search(self, query: str, max_results: int = 10, *, domains: list[str] | None = None, **kwargs: Any) -> list[SearchResult]:
        self.last_domains = domains
        return self._results[:max_results]

    def is_available(self) -> bool:
        return True
