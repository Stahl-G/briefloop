"""Tavily search backend using the Tavily Search API.

Uses Python stdlib urllib.request — no mandatory Tavily SDK dependency.
Reads API key from env var TAVILY_API_KEY by default, or a custom env var
specified via api_key_env in config.
"""
from __future__ import annotations

import json
import os
import urllib.request
from typing import Any

from multi_agent_brief.sources.search_backends.base import SearchBackend, SearchBackendError, SearchResult
from multi_agent_brief.sources.search_backends.capabilities import (
    TAVILY_CAPABILITIES,
    SearchBackendCapabilities,
)

TAVILY_API_URL = "https://api.tavily.com/search"
DEFAULT_API_KEY_ENV = "TAVILY_API_KEY"


def _extract_domain(url: str) -> str:
    """Extract domain from URL, safe for malformed URLs."""
    try:
        parts = url.split("/")
        if len(parts) >= 3:
            return parts[2]
    except (IndexError, AttributeError):
        pass
    return ""


class TavilyBackend(SearchBackend):
    """Tavily web search backend.

    Reads API key from environment variable (default: TAVILY_API_KEY).
    No API key is ever printed or stored in metadata.
    """

    name = "tavily"

    def __init__(self, api_key_env: str = DEFAULT_API_KEY_ENV) -> None:
        self._api_key_env = api_key_env

    @staticmethod
    def capabilities() -> SearchBackendCapabilities:
        return TAVILY_CAPABILITIES

    def is_available(self) -> bool:
        return bool(os.environ.get(self._api_key_env))

    def search(
        self,
        query: str,
        max_results: int = 10,
        *,
        domains: list[str] | None = None,
        **kwargs: Any,
    ) -> list[SearchResult]:
        api_key = os.environ.get(self._api_key_env, "")
        if not api_key:
            return []

        topic = kwargs.get("topic", "news")
        search_depth = kwargs.get("search_depth", "basic")
        days = kwargs.get("days")

        payload: dict[str, Any] = {
            "api_key": api_key,
            "query": query,
            "max_results": max_results,
            "topic": topic,
            "search_depth": search_depth,
            "include_answer": False,
            "include_raw_content": False,
        }
        if days:
            payload["days"] = days
        if domains:
            payload["include_domains"] = domains

        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            TAVILY_API_URL,
            data=body,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            raise SearchBackendError(
                f"Tavily search failed: {type(exc).__name__}: {exc}",
                backend="tavily",
            ) from exc

        results: list[SearchResult] = []
        for item in data.get("results", []):
            raw_published = (item.get("published_date") or "").strip()
            has_published = bool(raw_published)
            results.append(
                SearchResult(
                    title=item.get("title", ""),
                    url=item.get("url", ""),
                    snippet=item.get("content", ""),
                    published_at=raw_published,
                    source_name=_extract_domain(item.get("url", "")),
                    metadata={
                        "backend": "tavily",
                        "query": query,
                        "date_status": "published_at_present" if has_published else "missing_published_at",
                        "source_temporality": "published" if has_published else "retrieved_only",
                        "evidence_quality": "snippet",
                        "vertical": topic,
                        "raw_score": item.get("score"),
                        "has_raw_content": bool(item.get("raw_content")),
                    },
                )
            )
        return results[:max_results]
