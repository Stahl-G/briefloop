"""Firecrawl search backend using the Firecrawl Search API.

Uses Python stdlib urllib.request — no mandatory SDK dependency.
Reads API key from env var FIRECRAWL_API_KEY by default, or a custom env var
specified via api_key_env in config.

API docs: https://docs.firecrawl.dev/api-reference/endpoint/search
"""
from __future__ import annotations

import json
import os
import urllib.request
from typing import Any

from multi_agent_brief.sources.search_backends.base import SearchBackend, SearchBackendError, SearchResult
from multi_agent_brief.sources.search_backends.capabilities import (
    FIRECRAWL_CAPABILITIES,
    SearchBackendCapabilities,
)

FIRECRAWL_SEARCH_URL = "https://api.firecrawl.dev/v2/search"
DEFAULT_API_KEY_ENV = "FIRECRAWL_API_KEY"
MAX_CONTENT_LENGTH = 2000  # Truncate long markdown/content to this length


def _extract_domain(url: str) -> str:
    """Extract domain from URL, safe for malformed URLs."""
    try:
        parts = url.split("/")
        if len(parts) >= 3:
            return parts[2]
    except (IndexError, AttributeError):
        pass
    return ""


class FirecrawlBackend(SearchBackend):
    """Firecrawl search + extract backend.

    Reads API key from environment variable (default: FIRECRAWL_API_KEY).
    No API key is ever printed or stored in metadata.
    """

    name = "firecrawl"

    def __init__(self, api_key_env: str = DEFAULT_API_KEY_ENV) -> None:
        self._api_key_env = api_key_env

    @staticmethod
    def capabilities() -> SearchBackendCapabilities:
        return FIRECRAWL_CAPABILITIES

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

        # Build request payload
        payload: dict[str, Any] = {
            "query": query,
            "limit": min(max_results, 100),
            "sources": [{"type": "web"}],
        }

        if domains:
            payload["includeDomains"] = domains

        # Scrape options for full content extraction
        scrape_markdown = kwargs.get("scrape_markdown", False)
        if scrape_markdown:
            payload["scrapeOptions"] = {
                "formats": [{"type": "markdown"}],
                "onlyMainContent": True,
            }

        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            FIRECRAWL_SEARCH_URL,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            raise SearchBackendError(
                f"Firecrawl search failed: {type(exc).__name__}: {exc}",
                backend="firecrawl",
            ) from exc

        if not data.get("success"):
            message = str(data.get("error") or "success=false")
            raise SearchBackendError(
                f"Firecrawl search failed: {message}",
                backend="firecrawl",
            )

        results: list[SearchResult] = []
        for item in data.get("data", {}).get("web", []):
            results.append(self._parse_web_result(item, query))

        return results[:max_results]

    def _parse_web_result(self, item: dict[str, Any], query: str) -> SearchResult:
        """Parse a Firecrawl web result into SearchResult."""
        url = item.get("url", "")
        title = item.get("title", "")
        description = item.get("description", "")
        markdown = item.get("markdown")

        # Build snippet: prefer markdown (full text), fall back to description
        snippet, evidence_quality = _build_snippet(description, markdown)

        return SearchResult(
            title=title,
            url=url,
            snippet=snippet,
            published_at="",  # Firecrawl doesn't provide published_at
            source_name=_extract_domain(url),
            metadata={
                "backend": "firecrawl",
                "query": query,
                "vertical": "web",
                "date_status": "missing_published_at",
                "source_temporality": "retrieved_only",
                "evidence_quality": evidence_quality,
                "has_markdown": markdown is not None,
            },
        )


def _build_snippet(description: str, markdown: str | None) -> tuple[str, str]:
    """Build the best available snippet and determine evidence quality.

    Returns (snippet, evidence_quality).
    """
    if markdown:
        # Full text available — truncate safely
        cleaned = markdown.strip()
        if len(cleaned) > MAX_CONTENT_LENGTH:
            cleaned = cleaned[:MAX_CONTENT_LENGTH] + "..."
        return cleaned, "full_text"

    if description:
        return description.strip(), "snippet"

    return "", "snippet"
