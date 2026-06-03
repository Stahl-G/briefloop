"""Web search source provider with pluggable backends."""
from __future__ import annotations

import hashlib
from typing import Any

from multi_agent_brief.sources.base import SourceItem, SourceProvider, SourceQuery
from multi_agent_brief.sources.search_backends.base import SearchBackend, SearchResult


# Registry of known backends that can be auto-instantiated from config.
_KNOWN_BACKENDS: dict[str, type[SearchBackend]] = {}


def _register_known_backends() -> None:
    """Lazily register known backend classes."""
    if _KNOWN_BACKENDS:
        return
    from multi_agent_brief.sources.search_backends.tavily import TavilyBackend
    _KNOWN_BACKENDS["tavily"] = TavilyBackend


class WebSearchProvider(SourceProvider):
    """Web search provider using pluggable search backends."""

    name = "web_search"
    source_type = "web_search"

    def __init__(self, backend: SearchBackend | None = None) -> None:
        self._backend = backend

    def _get_backend(self, config: dict[str, Any]) -> SearchBackend:
        if self._backend is not None:
            return self._backend
        backend_name = config.get("backend") or ""
        if not backend_name:
            raise RuntimeError("web_search is enabled but no backend is configured.")

        _register_known_backends()
        cls = _KNOWN_BACKENDS.get(backend_name)
        if cls is not None:
            api_key_env = config.get("api_key_env", "TAVILY_API_KEY")
            return cls(api_key_env=api_key_env)

        raise NotImplementedError(
            f"web_search backend '{backend_name}' is not available. "
            "Supported backends: tavily. Or inject a SearchBackend implementation."
        )

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        if not config.get("enabled"):
            return []
        backend_name = config.get("backend") or ""
        if not backend_name:
            return ["web_search is enabled but no backend is configured"]

        _register_known_backends()
        if backend_name not in _KNOWN_BACKENDS:
            return [f"web_search: unknown backend '{backend_name}'. Supported: {', '.join(_KNOWN_BACKENDS)}"]

        try:
            backend = self._get_backend(config)
        except (RuntimeError, NotImplementedError) as exc:
            return [str(exc)]
        if not backend.is_available():
            api_key_env = config.get("api_key_env", "TAVILY_API_KEY")
            return [f"web_search: backend '{backend_name}' requires env var {api_key_env} to be set"]
        return []

    def collect(self, query: SourceQuery, config: dict[str, Any]) -> list[SourceItem]:
        if not config.get("enabled"):
            return []

        backend = self._get_backend(config)
        if not backend.is_available():
            return []

        backend_name = backend.name
        all_items: list[SourceItem] = []
        max_results = config.get("max_results", 20)

        # Build search queries from query keywords or config
        queries = self._build_queries(query, config)

        for q, domains in queries:
            results = backend.search(q, max_results=max_results, domains=domains)
            for r in results:
                item = self._result_to_source_item(r, q, backend_name)
                all_items.append(item)

        return all_items

    def _build_queries(self, query: SourceQuery, config: dict[str, Any]) -> list[tuple[str, list[str] | None]]:
        """Build search queries from the query object and config.

        Returns list of (query_string, domains_or_none) tuples.
        Preserves individual search_tasks — does NOT collapse into one giant query.
        """
        queries: list[tuple[str, list[str] | None]] = []

        # Prefer config search_tasks — each task is a separate query with its own domains
        for task in config.get("search_tasks", []):
            q = task.get("query", "")
            if q:
                domains = task.get("domains") or None
                queries.append((q, domains))

        # If no search_tasks, use query.keywords as individual queries
        if not queries and query.keywords:
            for kw in query.keywords:
                if kw.strip():
                    queries.append((kw.strip(), None))

        # Fallback: generic query
        if not queries:
            queries.append(("latest news", None))

        return queries

    def _result_to_source_item(self, result: SearchResult, query: str, backend_name: str) -> SourceItem:
        """Convert a SearchResult to a SourceItem."""
        from multi_agent_brief.sources.base import _utc_now_iso

        raw = f"{result.url}|{result.title}"
        digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10].upper()
        source_id = f"WS_{digest}"
        retrieved_at = _utc_now_iso()
        metadata: dict[str, Any] = {
            "query": query,
            "backend": backend_name,
            "retrieved_at": retrieved_at,
            "date_status": result.metadata.get("date_status", "missing_published_at"),
            "source_temporality": result.metadata.get("source_temporality", "retrieved_only"),
        }
        metadata.update(result.metadata)
        return SourceItem(
            source_id=source_id,
            source_name=result.source_name or "web_search",
            source_type="web_search",
            title=result.title,
            content=result.snippet,
            url=result.url,
            published_at=result.published_at,
            retrieved_at=retrieved_at,
            reliability="medium",
            metadata=metadata,
        )
