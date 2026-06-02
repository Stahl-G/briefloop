"""Web search source provider (stub for Phase 1)."""
from __future__ import annotations

from typing import Any

from multi_agent_brief.sources.base import SourceItem, SourceProvider, SourceQuery


class WebSearchProvider(SourceProvider):
    """LLM / search engine web search provider. Phase 1 stub."""

    name = "web_search"
    source_type = "web_search"

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        if not config.get("enabled"):
            return []
        return []  # No required config for stub

    def collect(self, query: SourceQuery, config: dict[str, Any]) -> list[SourceItem]:
        if not config.get("enabled"):
            return []
        # Phase 1 stub: web search not yet implemented
        return []
