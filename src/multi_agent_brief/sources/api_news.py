"""News API source provider (stub for Phase 1)."""
from __future__ import annotations

import os
from typing import Any

from multi_agent_brief.sources.base import SourceItem, SourceProvider, SourceQuery


class NewsApiProvider(SourceProvider):
    """News API provider (NewsAPI, etc.). Phase 1 stub."""

    name = "api_news"
    source_type = "api"

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        if not config.get("enabled"):
            return []
        errors: list[str] = []
        for i, provider in enumerate(config.get("providers", [])):
            env_key = provider.get("api_key_env", "")
            if env_key and not os.environ.get(env_key):
                errors.append(f"api.providers[{i}] '{provider.get('name', '?')}': env var {env_key} not set")
        return errors

    def collect(self, query: SourceQuery, config: dict[str, Any]) -> list[SourceItem]:
        if not config.get("enabled"):
            return []
        # Phase 1 stub
        return []
