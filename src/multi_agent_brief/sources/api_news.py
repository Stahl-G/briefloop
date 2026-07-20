"""News API source provider using NewsAPI.org.

Endpoint: https://newsapi.org/v2/everything
Uses Python stdlib urllib.request — no mandatory NewsAPI SDK dependency.
Reads API key from env var NEWSAPI_API_KEY by default, or a custom env var
specified via api_key_env in config.
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from hashlib import sha1
from typing import Any

from multi_agent_brief.sources.base import SourceItem, SourceProvider, SourceQuery

NEWSAPI_EVERYTHING_URL = "https://newsapi.org/v2/everything"
DEFAULT_API_KEY_ENV = "NEWSAPI_API_KEY"


def _extract_domain(url: str) -> str:
    """Extract domain from URL, safe for malformed URLs."""
    try:
        parts = url.split("/")
        if len(parts) >= 3:
            return parts[2]
    except (IndexError, AttributeError):
        pass
    return ""


class NewsApiProvider(SourceProvider):
    """News API provider that searches NewsAPI.org for articles.

    Configuration (in sources.yaml under ``api:``):

    .. code-block:: yaml

        api:
          enabled: true
          providers:
            - name: newsapi
              api_key_env: NEWSAPI_API_KEY
    """

    name = "api_news"
    source_type = "api"

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        if not config.get("enabled"):
            return []
        errors: list[str] = []
        providers = config.get("providers", [])
        if not providers:
            errors.append("api: News API provider is enabled but no providers configured")
            return errors
        # Only validate providers whose name is 'newsapi' — other providers
        # (e.g. 'sec') in the same api: config section use different fields.
        seen_newsapi = False
        for i, provider in enumerate(providers):
            if provider.get("name") != "newsapi":
                continue
            seen_newsapi = True
            env_key = provider.get("api_key_env", DEFAULT_API_KEY_ENV)
            if not os.environ.get(env_key):
                errors.append(
                    f"api.providers[{i}] 'newsapi': "
                    f"env var {env_key} not set"
                )
        if not seen_newsapi:
            errors.append("api: News API provider enabled but no 'newsapi' entry in providers")
        return errors

    def collect(self, query: SourceQuery, config: dict[str, Any]) -> list[SourceItem]:
        if not config.get("enabled"):
            return []

        api_key_env = DEFAULT_API_KEY_ENV
        providers = config.get("providers", [])
        if providers:
            api_key_env = providers[0].get("api_key_env", DEFAULT_API_KEY_ENV)
        api_key = os.environ.get(api_key_env)
        if not api_key:
            return []

        # Build the search query
        keywords = query.keywords or []
        q = " ".join(keywords) if keywords else ""
        # Fall back to a user-configured query string
        if not q:
            q = config.get("default_query", config.get("query", ""))

        params: dict[str, Any] = {
            "apiKey": api_key,
            "q": q,
            "pageSize": min(query.max_results or 10, 100),
        }
        if query.start_date:
            params["from"] = query.start_date
        if query.end_date:
            params["to"] = query.end_date

        # Optional flags from config
        sort_by = config.get("sort_by")
        if sort_by:
            params["sortBy"] = sort_by
        language = config.get("language")
        if language:
            params["language"] = language
        domains = config.get("domains")
        if domains:
            params["domains"] = ",".join(domains) if isinstance(domains, list) else domains

        url = _build_url(params)
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            # Provider failures are non-fatal — registry catches these
            return []

        if data.get("status") != "ok":
            return []

        items: list[SourceItem] = []
        for article in data.get("articles", []):
            title = (article.get("title") or "").strip()
            description = (article.get("description") or "").strip()
            content = (article.get("content") or "").strip()
            published_at = (article.get("publishedAt") or "").strip()

            # Build content: prefer description, fall back to content snippet
            body = description or content or ""

            items.append(
                SourceItem(
                    source_id=_source_id(article),
                    source_name=article.get("source", {}).get("name", "NewsAPI") or "NewsAPI",
                    source_type="api",
                    title=title,
                    content=body,
                    url=(article.get("url") or "").strip(),
                    published_at=published_at,
                    language=language or "",
                    reliability="medium",
                    dedupe_key=article.get("url", "") or title,
                    metadata={
                        "backend": "newsapi",
                        "query": q,
                        "author": (article.get("author") or "").strip(),
                        "source_id": article.get("source", {}).get("id"),
                    },
                )
            )
        return items[: query.max_results or 100]


def _build_url(params: dict[str, Any]) -> str:
    """Build the full URL with query parameters."""
    query_string = "&".join(f"{k}={urllib.parse.quote(str(v), safe='')}" for k, v in params.items() if v)
    return f"{NEWSAPI_EVERYTHING_URL}?{query_string}"


def _source_id(article: dict[str, Any]) -> str:
    """Generate a stable source ID from the article URL."""
    url = (article.get("url") or "").strip()
    if url:
        return f"newsapi_{sha1(url.encode()).hexdigest()[:12]}"
    title = (article.get("title") or "").strip()
    return f"newsapi_{sha1(title.encode()).hexdigest()[:12]}"
