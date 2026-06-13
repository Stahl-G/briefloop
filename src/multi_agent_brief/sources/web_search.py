"""Web search source provider with pluggable backends."""
from __future__ import annotations

import hashlib
import os
from contextlib import contextmanager
from typing import Any

from multi_agent_brief.core.env import KNOWN_WORKSPACE_ENV_KEYS, known_env_key_is_set, read_workspace_env_key
from multi_agent_brief.sources.base import SourceItem, SourceProvider, SourceQuery
from multi_agent_brief.sources.search_backends.base import SearchBackend, SearchResult


# Registry of known backends that can be auto-instantiated from config.
_KNOWN_BACKENDS: dict[str, type[SearchBackend]] = {}
WEB_SEARCH_MODES = {"disabled", "runtime_tool", "external_api", "configure_later"}


def _register_known_backends() -> None:
    """Lazily register known backend classes."""
    if _KNOWN_BACKENDS:
        return
    from multi_agent_brief.sources.search_backends.tavily import TavilyBackend
    _KNOWN_BACKENDS["tavily"] = TavilyBackend
    from multi_agent_brief.sources.search_backends.exa import ExaBackend
    _KNOWN_BACKENDS["exa"] = ExaBackend
    from multi_agent_brief.sources.search_backends.brave import BraveBackend
    _KNOWN_BACKENDS["brave"] = BraveBackend
    from multi_agent_brief.sources.search_backends.firecrawl import FirecrawlBackend
    _KNOWN_BACKENDS["firecrawl"] = FirecrawlBackend
    from multi_agent_brief.sources.search_backends.serper import SerperBackend
    _KNOWN_BACKENDS["serper"] = SerperBackend


def backend_api_key_env(backend: SearchBackend, config: dict[str, Any] | None = None) -> str:
    """Return the env var name a backend uses for its API key."""
    if config and config.get("api_key_env"):
        return str(config["api_key_env"])
    return str(getattr(backend, "_api_key_env", ""))


@contextmanager
def temporary_workspace_api_key_env(
    backend: SearchBackend,
    config: dict[str, Any],
):
    """Expose an allowlisted workspace .env key only during backend calls."""
    api_key_env = backend_api_key_env(backend, config)
    workspace_dir = config.get("_workspace_dir") or config.get("workspace_dir") or ""
    if (
        not api_key_env
        or api_key_env not in KNOWN_WORKSPACE_ENV_KEYS
        or os.environ.get(api_key_env)
        or not workspace_dir
    ):
        yield
        return

    value = read_workspace_env_key(workspace_dir, api_key_env)
    if not value:
        yield
        return

    os.environ[api_key_env] = value
    try:
        yield
    finally:
        if os.environ.get(api_key_env) == value:
            os.environ.pop(api_key_env, None)


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
            # Only pass api_key_env if explicitly configured; otherwise each
            # backend uses its own default (e.g. EXA_API_KEY for Exa). (B12)
            api_key_env = config.get("api_key_env")
            if api_key_env:
                return cls(api_key_env=api_key_env)
            return cls()

        supported = ", ".join(sorted(_KNOWN_BACKENDS))
        raise NotImplementedError(
            f"web_search backend '{backend_name}' is not available. "
            f"Supported backends: {supported}. Or inject a SearchBackend implementation."
        )

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        if not config.get("enabled"):
            return []
        mode = str(config.get("mode") or "")
        if mode not in WEB_SEARCH_MODES:
            if mode in {"tavily", "exa", "brave", "firecrawl", "serper"}:
                return [
                    "web_search.mode must be one of disabled, runtime_tool, external_api, configure_later; "
                    f"got '{mode}'. Use mode: external_api with backend: {mode}."
                ]
            return [
                "web_search.mode must be one of disabled, runtime_tool, external_api, configure_later; "
                f"got '{mode or '<missing>'}'."
            ]
        if mode == "disabled":
            return ["web_search.enabled is true but mode is disabled. Set enabled: false or choose another mode."]
        if mode == "configure_later":
            return []
        if mode == "runtime_tool":
            if config.get("backend"):
                return ["web_search.mode runtime_tool must not configure backend; remove backend or use mode: external_api."]
            return []
        # external_api
        backend_name = config.get("backend") or ""
        if not backend_name:
            return ["web_search.mode external_api requires backend: tavily|exa|brave|firecrawl|serper."]

        _register_known_backends()
        if backend_name == "mock":
            return ["web_search mock backend has been removed; use mode: runtime_tool or a real external_api backend."]
        if backend_name not in _KNOWN_BACKENDS:
            return [f"web_search: unknown backend '{backend_name}'. Supported: {', '.join(_KNOWN_BACKENDS)}"]

        try:
            backend = self._get_backend(config)
        except (RuntimeError, NotImplementedError) as exc:
            return [str(exc)]
        if not backend.is_available():
            api_key_env = backend_api_key_env(backend, config)
            workspace_dir = config.get("_workspace_dir") or config.get("workspace_dir") or ""
            if api_key_env:
                if api_key_env in KNOWN_WORKSPACE_ENV_KEYS:
                    if known_env_key_is_set(api_key_env, workspace_dir):
                        return []
                elif os.environ.get(api_key_env):
                    return []
            key_hint = f"env var {api_key_env}" if api_key_env else "a configured API key"
            return [f"web_search: backend '{backend_name}' requires {key_hint}, but it is missing. Copy your workspace .env.example to .env and fill in the key, or export it in your shell."]
        return []

    def collect(self, query: SourceQuery, config: dict[str, Any]) -> list[SourceItem]:
        if not config.get("enabled"):
            return []
        if config.get("mode") == "runtime_tool":
            return []

        backend = self._get_backend(config)
        with temporary_workspace_api_key_env(backend, config):
            if not backend.is_available():
                return []

            backend_name = backend.name
            all_items: list[SourceItem] = []
            max_results = config.get("max_results", 20)
            recency_days = config.get("recency_days")

            # Build search queries from query keywords or config
            queries, task_meta = self._build_queries(query, config)

            for q, domains in queries:
                results = backend.search(q, max_results=max_results, domains=domains, days=recency_days)
                task_metadata = task_meta.get(q)
                for r in results:
                    item = self._result_to_source_item(r, q, backend_name, task_metadata=task_metadata)
                    all_items.append(item)

        return all_items

    def _build_queries(
        self, query: SourceQuery, config: dict[str, Any]
    ) -> tuple[list[tuple[str, list[str] | None]], dict[str, dict[str, Any]]]:
        """Build search queries from the query object and config.

        Returns:
            (queries, task_metadata_by_query) where queries is a list of
            (query_string, domains_or_none) tuples, and task_metadata_by_query
            maps each query string to its extra task metadata (topic, market,
            language, platform_group, signal_type) when present.
        """
        queries: list[tuple[str, list[str] | None]] = []
        task_meta: dict[str, dict[str, Any]] = {}

        # Prefer config search_tasks — each task is a separate query with its own domains
        for task in config.get("search_tasks", []):
            q = task.get("query", "")
            if q:
                domains = task.get("domains") or None
                queries.append((q, domains))
                # Preserve task metadata for propagation to SourceItems
                extra = {k: v for k, v in task.items() if k not in ("query", "domains")}
                if extra:
                    task_meta[q] = extra

        # If no search_tasks, use query.keywords as individual queries
        if not queries and query.keywords:
            for kw in query.keywords:
                if kw.strip():
                    queries.append((kw.strip(), None))

        # No queries at all → fail explicitly.  The old "latest news" silent
        # fallback is removed — set `allow_generic_fallback: true` in
        # sources.yaml web_search section to re-enable the generic catch-all.
        if not queries:
            if config.get("allow_generic_fallback"):
                queries.append(("latest news", None))
            else:
                raise RuntimeError(
                    "web_search has no search_tasks configured and no "
                    "keywords provided.  Run 'multi-agent-brief sources decide "
                    "--config <workspace>/config.yaml' to discover sources, or "
                    "add search_tasks manually in sources.yaml."
                )

        return queries, task_meta

    def _result_to_source_item(
        self,
        result: SearchResult,
        query: str,
        backend_name: str,
        task_metadata: dict[str, Any] | None = None,
    ) -> SourceItem:
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
        # Propagate task metadata (topic, market, language, etc.) to SourceItem
        if task_metadata:
            for key, value in task_metadata.items():
                metadata[f"task_{key}"] = value
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
