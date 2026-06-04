"""Brave Search backend using the Brave Search API.

Uses Python stdlib urllib.request — no mandatory SDK dependency.
Reads API key from env var BRAVE_SEARCH_API_KEY by default, or a custom env var
specified via api_key_env in config.

API docs: https://brave.com/search/api/
"""
from __future__ import annotations

import json
import os
import urllib.request
from typing import Any

from multi_agent_brief.sources.search_backends.base import SearchBackend, SearchBackendError, SearchResult
from multi_agent_brief.sources.search_backends.capabilities import (
    BRAVE_CAPABILITIES,
    SearchBackendCapabilities,
)

BRAVE_WEB_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"
BRAVE_NEWS_SEARCH_URL = "https://api.search.brave.com/res/v1/news/search"
DEFAULT_API_KEY_ENV = "BRAVE_SEARCH_API_KEY"


def _extract_domain(url: str) -> str:
    """Extract domain from URL, safe for malformed URLs."""
    try:
        parts = url.split("/")
        if len(parts) >= 3:
            return parts[2]
    except (IndexError, AttributeError):
        pass
    return ""


def _strip_html_tags(text: str) -> str:
    """Strip HTML tags from text (Brave descriptions may contain <strong> etc.)."""
    import re
    return re.sub(r"<[^>]+>", "", text).strip()


class BraveBackend(SearchBackend):
    """Brave Search web search backend.

    Reads API key from environment variable (default: BRAVE_SEARCH_API_KEY).
    No API key is ever printed or stored in metadata.
    """

    name = "brave"

    def __init__(self, api_key_env: str = DEFAULT_API_KEY_ENV) -> None:
        self._api_key_env = api_key_env

    @staticmethod
    def capabilities() -> SearchBackendCapabilities:
        return BRAVE_CAPABILITIES

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

        # Determine search mode: "web" or "news"
        vertical = kwargs.get("vertical", "web")

        # Build query parameters
        params: dict[str, str] = {
            "q": query,
            "count": str(min(max_results, 20)),  # Brave max is 20 per request
        }

        # Optional parameters
        country = kwargs.get("country")
        if country:
            params["country"] = country

        search_lang = kwargs.get("search_lang")
        if search_lang:
            params["search_lang"] = search_lang

        freshness = kwargs.get("freshness")
        if freshness:
            params["freshness"] = freshness

        # Build URL
        if vertical == "news":
            base_url = BRAVE_NEWS_SEARCH_URL
        else:
            base_url = BRAVE_WEB_SEARCH_URL

        query_string = "&".join(f"{k}={urllib.request.quote(v)}" for k, v in params.items())
        url = f"{base_url}?{query_string}"

        req = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
                "X-Subscription-Token": api_key,
            },
            method="GET",
        )

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                # Handle gzip encoding
                if resp.headers.get("Content-Encoding") == "gzip":
                    import gzip
                    data = json.loads(gzip.decompress(resp.read()).decode("utf-8"))
                else:
                    data = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            raise SearchBackendError(
                f"Brave search failed: {type(exc).__name__}: {exc}",
                backend="brave",
            ) from exc

        results: list[SearchResult] = []

        if vertical == "news":
            for item in data.get("results", []):
                results.append(self._parse_news_result(item, query))
        else:
            for item in data.get("web", {}).get("results", []):
                results.append(self._parse_web_result(item, query))

        return results[:max_results]

    def _parse_web_result(self, item: dict[str, Any], query: str) -> SearchResult:
        """Parse a Brave web search result into SearchResult."""
        description = _strip_html_tags(item.get("description", ""))
        age = item.get("age", "")
        profile = item.get("profile", {})
        source_name = profile.get("long_name") or _extract_domain(item.get("url", ""))

        return SearchResult(
            title=item.get("title", ""),
            url=item.get("url", ""),
            snippet=description,
            published_at=age,  # Brave provides human-readable age, not ISO
            source_name=source_name,
            metadata={
                "backend": "brave",
                "query": query,
                "vertical": "web",
                "date_status": "published_at_present" if age else "missing_published_at",
                "source_temporality": "published" if age else "retrieved_only",
                "evidence_quality": "snippet",
                "profile_name": profile.get("name"),
                "extra_snippets": item.get("extra_snippets"),
            },
        )

    def _parse_news_result(self, item: dict[str, Any], query: str) -> SearchResult:
        """Parse a Brave news search result into SearchResult."""
        description = _strip_html_tags(item.get("description", ""))
        age = item.get("age", "")
        page_age = item.get("page_age", "")
        meta_url = item.get("meta_url", {})
        source_name = meta_url.get("hostname") or _extract_domain(item.get("url", ""))

        # Prefer page_age (ISO) over age (human-readable)
        published_at = page_age or age

        return SearchResult(
            title=item.get("title", ""),
            url=item.get("url", ""),
            snippet=description,
            published_at=published_at,
            source_name=source_name,
            metadata={
                "backend": "brave",
                "query": query,
                "vertical": "news",
                "date_status": "published_at_present" if published_at else "missing_published_at",
                "source_temporality": "published" if published_at else "retrieved_only",
                "evidence_quality": "snippet",
                "age": age,
            },
        )
