"""Serper search backend using the Serper API.

Uses Python stdlib urllib.request — no mandatory SDK dependency.
Reads API key from env var SERPER_API_KEY by default, or a custom env var
specified via api_key_env in config.

API docs: https://docs.serper.dev
"""
from __future__ import annotations

import json
import os
import urllib.request
from typing import Any

from multi_agent_brief.sources.search_backends.base import SearchBackend, SearchResult
from multi_agent_brief.sources.search_backends.capabilities import (
    SERPER_CAPABILITIES,
    SearchBackendCapabilities,
)

# Serper endpoint URLs
SERPER_BASE_URL = "https://google.serper.dev"
DEFAULT_API_KEY_ENV = "SERPER_API_KEY"

# Supported verticals and their endpoints
_VERTICAL_ENDPOINTS: dict[str, str] = {
    "search": f"{SERPER_BASE_URL}/search",
    "news": f"{SERPER_BASE_URL}/news",
    "scholar": f"{SERPER_BASE_URL}/scholar",
    "patents": f"{SERPER_BASE_URL}/patents",
}


def _extract_domain(url: str) -> str:
    """Extract domain from URL, safe for malformed URLs."""
    try:
        parts = url.split("/")
        if len(parts) >= 3:
            return parts[2]
    except (IndexError, AttributeError):
        pass
    return ""


class SerperBackend(SearchBackend):
    """Serper Google SERP backend.

    Reads API key from environment variable (default: SERPER_API_KEY).
    No API key is ever printed or stored in metadata.
    """

    name = "serper"

    def __init__(self, api_key_env: str = DEFAULT_API_KEY_ENV) -> None:
        self._api_key_env = api_key_env

    @staticmethod
    def capabilities() -> SearchBackendCapabilities:
        return SERPER_CAPABILITIES

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

        # Determine vertical (search, news, scholar, patents)
        vertical = kwargs.get("vertical", "search")
        endpoint = _VERTICAL_ENDPOINTS.get(vertical, _VERTICAL_ENDPOINTS["search"])

        # Build request payload
        payload: dict[str, Any] = {
            "q": query,
            "num": min(max_results, 100),
        }

        # Optional parameters
        gl = kwargs.get("gl")  # Country code
        if gl:
            payload["gl"] = gl

        hl = kwargs.get("hl")  # Language
        if hl:
            payload["hl"] = hl

        tbs = kwargs.get("tbs")  # Time-based search
        if tbs:
            payload["tbs"] = tbs

        page = kwargs.get("page")
        if page:
            payload["page"] = page

        # Add site: operator for domain filtering
        if domains and len(domains) == 1:
            payload["q"] = f"{query} site:{domains[0]}"

        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            endpoint,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "X-API-KEY": api_key,
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception:
            return []

        results: list[SearchResult] = []

        if vertical == "news":
            for item in data.get("news", []):
                results.append(self._parse_news_result(item, query))
        elif vertical == "scholar":
            for item in data.get("organic", []):
                results.append(self._parse_scholar_result(item, query))
        elif vertical == "patents":
            for item in data.get("organic", []):
                results.append(self._parse_patent_result(item, query))
        else:
            for item in data.get("organic", []):
                results.append(self._parse_search_result(item, query))

        return results[:max_results]

    def _parse_search_result(self, item: dict[str, Any], query: str) -> SearchResult:
        """Parse a Serper organic search result."""
        link = item.get("link", "")
        date = item.get("date", "")

        return SearchResult(
            title=item.get("title", ""),
            url=link,
            snippet=item.get("snippet", ""),
            published_at=date,
            source_name=_extract_domain(link),
            metadata={
                "backend": "serper",
                "query": query,
                "vertical": "search",
                "position": item.get("position"),
                "date_status": "published_at_present" if date else "missing_published_at",
                "source_temporality": "published" if date else "retrieved_only",
                "evidence_quality": "snippet",
                "sitelinks": item.get("sitelinks"),
            },
        )

    def _parse_news_result(self, item: dict[str, Any], query: str) -> SearchResult:
        """Parse a Serper news result."""
        link = item.get("link", "")
        date = item.get("date", "")

        return SearchResult(
            title=item.get("title", ""),
            url=link,
            snippet=item.get("snippet", ""),
            published_at=date,
            source_name=item.get("source") or _extract_domain(link),
            metadata={
                "backend": "serper",
                "query": query,
                "vertical": "news",
                "position": item.get("position"),
                "date_status": "published_at_present" if date else "missing_published_at",
                "source_temporality": "published" if date else "retrieved_only",
                "evidence_quality": "snippet",
            },
        )

    def _parse_scholar_result(self, item: dict[str, Any], query: str) -> SearchResult:
        """Parse a Serper scholar result."""
        link = item.get("link", "")
        year = item.get("year", "")

        return SearchResult(
            title=item.get("title", ""),
            url=link,
            snippet=item.get("snippet", ""),
            published_at=year,
            source_name=_extract_domain(link),
            metadata={
                "backend": "serper",
                "query": query,
                "vertical": "scholar",
                "position": item.get("position"),
                "date_status": "published_at_present" if year else "missing_published_at",
                "source_temporality": "published" if year else "retrieved_only",
                "evidence_quality": "snippet",
                "publication_info": item.get("publicationInfo"),
                "cited_by": item.get("citedBy"),
            },
        )

    def _parse_patent_result(self, item: dict[str, Any], query: str) -> SearchResult:
        """Parse a Serper patent result."""
        link = item.get("link", "")
        # Patents have multiple dates; prefer publicationDate
        date = item.get("publicationDate") or item.get("filingDate") or item.get("priorityDate", "")

        return SearchResult(
            title=item.get("title", ""),
            url=link,
            snippet=item.get("snippet", ""),
            published_at=date,
            source_name=_extract_domain(link),
            metadata={
                "backend": "serper",
                "query": query,
                "vertical": "patents",
                "position": item.get("position"),
                "date_status": "published_at_present" if date else "missing_published_at",
                "source_temporality": "published" if date else "retrieved_only",
                "evidence_quality": "snippet",
                "inventor": item.get("inventor"),
                "assignee": item.get("assignee"),
                "publication_number": item.get("publicationNumber"),
                "filing_date": item.get("filingDate"),
                "grant_date": item.get("grantDate"),
            },
        )
