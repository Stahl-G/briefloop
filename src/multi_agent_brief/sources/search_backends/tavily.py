"""Tavily search backend using the Tavily Search API.

Uses Python stdlib urllib.request — no mandatory Tavily SDK dependency.
Reads API key from env var TAVILY_API_KEY by default, or a custom env var
specified via api_key_env in config.
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.request
from urllib.parse import urlsplit
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from multi_agent_brief.sources.search_backends.base import (
    SearchBackend,
    SearchBackendError,
    SearchResponse,
    SearchResult,
)
from multi_agent_brief.sources.search_backends.capabilities import (
    TAVILY_CAPABILITIES,
    SearchBackendCapabilities,
)

TAVILY_API_URL = "https://api.tavily.com/search"
DEFAULT_API_KEY_ENV = "TAVILY_API_KEY"
TAVILY_RESPONSE_BYTE_CAP = 4 * 1024 * 1024
TAVILY_TIMEOUT_SECONDS = 30


class _TavilyResult(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    title: str
    url: str
    content: str
    raw_content: str | None = None
    published_date: str | None = None
    score: float | int | None = None

    @field_validator("url")
    @classmethod
    def _public_http_url(cls, value: str) -> str:
        if value != value.strip() or not value or len(value) > 2048:
            raise ValueError("invalid result URL")
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.hostname is None
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError("invalid result URL")
        return value


class _TavilyResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    results: list[_TavilyResult] = Field(max_length=100)


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
        return list(
            self.search_response(
                query,
                max_results=max_results,
                domains=domains,
                **kwargs,
            ).results
        )

    def search_response(
        self,
        query: str,
        max_results: int = 10,
        *,
        domains: list[str] | None = None,
        **kwargs: Any,
    ) -> SearchResponse:
        """Execute one bounded request and retain the exact response bytes."""

        api_key = os.environ.get(self._api_key_env, "")
        if not api_key:
            return SearchResponse(raw_response=b"", status_code=0, results=())

        topic = kwargs.get("topic", "news")
        search_depth = kwargs.get("search_depth", "basic")
        days = kwargs.get("days")
        time_range = kwargs.get("time_range")
        start_date = kwargs.get("start_date")
        end_date = kwargs.get("end_date")
        if (start_date is None) != (end_date is None):
            raise SearchBackendError(
                "Tavily search failed",
                backend="tavily",
            ) from None

        payload: dict[str, Any] = {
            "query": query,
            "max_results": max_results,
            "topic": topic,
            "search_depth": search_depth,
            "include_answer": False,
            "include_raw_content": "markdown",
            "auto_parameters": False,
        }
        if time_range:
            payload["time_range"] = time_range
        elif start_date is not None and end_date is not None:
            payload["start_date"] = start_date
            payload["end_date"] = end_date
        elif days:
            payload["days"] = days
        if domains:
            payload["include_domains"] = domains

        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            TAVILY_API_URL,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )

        failed = False
        status_code = 0
        raw_response = b""
        data: _TavilyResponse | None = None
        try:
            with urllib.request.urlopen(req, timeout=TAVILY_TIMEOUT_SECONDS) as resp:
                status_code = int(getattr(resp, "status", 200))
                raw_response = resp.read(TAVILY_RESPONSE_BYTE_CAP + 1)
            if (
                status_code != 200
                or not raw_response
                or len(raw_response) > TAVILY_RESPONSE_BYTE_CAP
            ):
                raise ValueError("invalid Tavily response")
            api_key_bytes = api_key.encode("utf-8")
            api_key_sha256 = hashlib.sha256(api_key_bytes).hexdigest().encode("ascii")
            if api_key_bytes in raw_response or api_key_sha256 in raw_response.lower():
                raise ValueError("unsafe Tavily response")
            decoded = json.loads(raw_response.decode("utf-8"))
            data = _TavilyResponse.model_validate(decoded, strict=True)
            if len(data.results) > max_results:
                raise ValueError("invalid Tavily result count")
        except Exception:
            failed = True
        if failed or data is None:
            raise SearchBackendError(
                "Tavily search failed",
                backend="tavily",
            ) from None

        results: list[SearchResult] = []
        for item in data.results:
            raw_published = (item.published_date or "").strip()
            has_published = bool(raw_published)
            raw_content_value = item.raw_content
            raw_content = (
                raw_content_value.strip()
                if isinstance(raw_content_value, str) and raw_content_value.strip()
                else None
            )
            projection = {
                "title": item.title,
                "url": str(item.url),
                "snippet": item.content,
                "raw_content": raw_content,
                "published_date": raw_published,
                "score": item.score,
            }
            results.append(
                SearchResult(
                    title=item.title,
                    url=str(item.url),
                    snippet=item.content,
                    raw_content=raw_content,
                    published_at=raw_published,
                    source_name=_extract_domain(str(item.url)),
                    raw_projection=projection,
                    metadata={
                        "backend": "tavily",
                        "query": query,
                        "date_status": "published_at_present"
                        if has_published
                        else "missing_published_at",
                        "source_temporality": "published"
                        if has_published
                        else "retrieved_only",
                        "evidence_quality": (
                            "partial_extract" if raw_content is not None else "snippet"
                        ),
                        "vertical": topic,
                        "raw_score": item.score,
                        "has_raw_content": raw_content is not None,
                    },
                )
            )
        return SearchResponse(
            raw_response=raw_response,
            status_code=status_code,
            results=tuple(results[:max_results]),
        )
