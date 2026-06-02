"""CLI scraper source provider (stub for Phase 1)."""
from __future__ import annotations

import shutil
from typing import Any

from multi_agent_brief.sources.base import SourceItem, SourceProvider, SourceQuery


class CliProvider(SourceProvider):
    """Local CLI scraper source provider. Phase 1 stub."""

    name = "cli"
    source_type = "cli"

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        if not config.get("enabled"):
            return []
        errors: list[str] = []
        scrapers = config.get("scrapers", [])
        for i, scraper in enumerate(scrapers):
            command = scraper.get("command", "")
            if command and not shutil.which(command):
                errors.append(f"cli.scrapers[{i}] '{scraper.get('name', '?')}': command '{command}' not found in PATH")
        return errors

    def collect(self, query: SourceQuery, config: dict[str, Any]) -> list[SourceItem]:
        if not config.get("enabled"):
            return []
        # Phase 1 stub
        return []
