"""MCP server source provider (stub for Phase 1)."""
from __future__ import annotations

from typing import Any

from multi_agent_brief.sources.base import SourceItem, SourceProvider, SourceQuery


class McpProvider(SourceProvider):
    """MCP server source provider. Phase 1 stub."""

    name = "mcp"
    source_type = "mcp"

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        if not config.get("enabled"):
            return []
        errors: list[str] = []
        servers = config.get("servers", [])
        if not servers:
            errors.append("mcp: enabled but no servers configured")
        return errors

    def collect(self, query: SourceQuery, config: dict[str, Any]) -> list[SourceItem]:
        if not config.get("enabled"):
            return []
        # Phase 1 stub
        return []
