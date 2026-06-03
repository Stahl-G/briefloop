"""Exa search backend using the Exa Search API.

Uses Python stdlib urllib.request — no mandatory SDK dependency.
Reads API key from env var EXA_API_KEY by default, or a custom env var
specified via api_key_env in config.

API docs: https://exa.ai/docs/reference/search
"""
from __future__ import annotations

import json
import os
import urllib.request
from typing import Any

from multi_agent_brief.sources.search_backends.base import SearchBackend, SearchResult
from multi_agent_brief.sources.search_backends.capabilities import (
    EXA_CAPABILITIES,
    SearchBackendCapabilities,
)

EXA_API_URL = "https://api.exa.ai/search"
DEFAULT_API_KEY_ENV = "EXA_API_KEY"


def _extract_domain(url: str) -> str:
    """Extract domain from URL, safe for malformed URLs."""
    try:
        parts = url.split("/")
        if len(parts) >= 3:
            return parts[2]
    except (IndexError, AttributeError):
        pass
    return ""


class ExaBackend(SearchBackend):
    """Exa semantic search backend.

    Reads API key from environment variable (default: EXA_API_KEY).
    No API key is ever printed or stored in metadata.
    """

    name = "exa"

    def __init__(self, api_key_env: str = DEFAULT_API_KEY_ENV) -> None:
        self._api_key_env = api_key_env

    @staticmethod
    def capabilities() -> SearchBackendCapabilities:
        return EXA_CAPABILITIES

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
            "numResults": min(max_results, 100),
            "type": kwargs.get("type", "auto"),
            "contents": {
                "highlights": True,
                "summary": True,
            },
        }

        if domains:
            payload["includeDomains"] = domains

        # Date filtering
        start_date = kwargs.get("start_published_date")
        end_date = kwargs.get("end_published_date")
        days = kwargs.get("days")
        if start_date:
            payload["startPublishedDate"] = start_date
        if end_date:
            payload["endPublishedDate"] = end_date
        elif days:
            # Convert days to ISO 8601 date
            from datetime import datetime, timezone, timedelta
            start = datetime.now(timezone.utc) - timedelta(days=days)
            payload["startPublishedDate"] = start.strftime("%Y-%m-%dT%H:%M:%SZ")

        # Category filter
        category = kwargs.get("category")
        if category:
            payload["category"] = category

        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            EXA_API_URL,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "x-api-key": api_key,
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception:
            return []

        results: list[SearchResult] = []
        for item in data.get("results", []):
            raw_published = (item.get("publishedDate") or "").strip()
            has_published = bool(raw_published)

            # Build snippet from summary, highlights, or text
            snippet = _build_snippet(item)

            # Determine evidence quality based on what content is available
            evidence_quality = "snippet"
            if item.get("highlights"):
                evidence_quality = "highlight"
            if item.get("text"):
                evidence_quality = "full_text"

            results.append(
                SearchResult(
                    title=item.get("title", ""),
                    url=item.get("url", ""),
                    snippet=snippet,
                    published_at=raw_published,
                    source_name=_extract_domain(item.get("url", "")),
                    metadata={
                        "backend": "exa",
                        "query": query,
                        "date_status": "published_at_present" if has_published else "missing_published_at",
                        "source_temporality": "published" if has_published else "retrieved_only",
                        "evidence_quality": evidence_quality,
                        "author": item.get("author"),
                        "highlights": item.get("highlights"),
                        "highlight_scores": item.get("highlightScores"),
                        "raw_score": None,  # Exa doesn't return a simple score in search
                        "cost_dollars": data.get("costDollars", {}).get("total"),
                    },
                )
            )
        return results[:max_results]


def _build_snippet(item: dict[str, Any]) -> str:
    """Build the best available snippet from an Exa result.

    Priority: summary > joined highlights > first 500 chars of text.
    """
    summary = (item.get("summary") or "").strip()
    if summary:
        return summary[:1000]

    highlights = item.get("highlights") or []
    if highlights:
        joined = " ... ".join(h.strip() for h in highlights if h.strip())
        if joined:
            return joined[:1000]

    text = (item.get("text") or "").strip()
    if text:
        return text[:500]

    return ""
