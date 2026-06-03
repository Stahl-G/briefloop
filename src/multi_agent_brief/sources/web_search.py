"""Web search source provider with pluggable backends."""
from __future__ import annotations

import hashlib
from typing import Any

from multi_agent_brief.sources.base import SourceItem, SourceProvider, SourceQuery
from multi_agent_brief.sources.search_backends.base import SearchBackend, SearchResult
from multi_agent_brief.sources.search_backends.mock import MockSearchBackend


class WebSearchProvider(SourceProvider):
    """Web search provider using pluggable search backends."""

    name = "web_search"
    source_type = "web_search"

    def __init__(self, backend: SearchBackend | None = None) -> None:
        self._backend = backend

    def _get_backend(self, config: dict[str, Any]) -> SearchBackend:
        if self._backend is not None:
            return self._backend
        # Auto-select backend based on config
        backend_name = config.get("backend", "mock")
        if backend_name == "mock":
            return MockSearchBackend()
        # Future: tavily, serpapi, browser backends
        return MockSearchBackend()

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        if not config.get("enabled"):
            return []
        backend = self._get_backend(config)
        if not backend.is_available():
            return [f"web_search: backend '{backend.name}' is not available"]
        return []

    def collect(self, query: SourceQuery, config: dict[str, Any]) -> list[SourceItem]:
        if not config.get("enabled"):
            return []

        backend = self._get_backend(config)
        if not backend.is_available():
            return []

        all_items: list[SourceItem] = []
        max_results = config.get("max_results", 20)

        # Build search queries from query keywords or config
        queries = self._build_queries(query, config)

        for q in queries:
            try:
                results = backend.search(q, max_results=max_results)
                for r in results:
                    item = self._result_to_source_item(r, q)
                    all_items.append(item)
            except Exception:
                # Backend failures are non-fatal
                pass

        return all_items

    def _build_queries(self, query: SourceQuery, config: dict[str, Any]) -> list[str]:
        """Build search queries from the query object and config."""
        queries: list[str] = []

        # Use query keywords if provided
        if query.keywords:
            queries.append(" ".join(query.keywords))

        # Use pre-defined queries from config
        for task in config.get("search_tasks", []):
            q = task.get("query", "")
            if q:
                queries.append(q)

        # Fallback: generic query
        if not queries:
            queries.append("latest news")

        return queries

    def _result_to_source_item(self, result: SearchResult, query: str) -> SourceItem:
        """Convert a SearchResult to a SourceItem."""
        raw = f"{result.url}|{result.title}"
        digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10].upper()
        source_id = f"WS_{digest}"
        return SourceItem(
            source_id=source_id,
            source_name=result.source_name or "web_search",
            source_type="web_search",
            title=result.title,
            content=result.snippet,
            url=result.url,
            published_at=result.published_at,
            reliability="medium",
            metadata={"query": query, "backend": self._get_backend({}).name},
        )
