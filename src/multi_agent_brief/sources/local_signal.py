"""Local Signal Provider — reads local_signal_samples.jsonl as first-class SourceItems."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from multi_agent_brief.sources.base import SourceItem, SourceProvider, SourceQuery

logger = logging.getLogger(__name__)


class LocalSignalProvider(SourceProvider):
    """Provider that reads local_signal_samples.jsonl and returns SourceItems.

    This makes local signal samples first-class citizens in context.sources,
    allowing Scout to extract claims from them and Claim Ledger to record
    provenance metadata (platform, market, language, collected_at, etc.).
    """

    name = "local_signal"
    source_type = "local_signal"

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        """No validation needed — samples_path is checked at collect time."""
        return []

    def collect(self, query: SourceQuery, config: dict[str, Any]) -> list[SourceItem]:
        """Read local_signal_samples.jsonl and convert to SourceItems."""
        if not config.get("enabled", True):
            return []

        samples_path = config.get("samples_path", "")
        if not samples_path:
            return []

        path = Path(samples_path)
        if not path.exists():
            logger.info("local_signal: samples file not found: %s", samples_path)
            return []

        from multi_agent_brief.sources.local_signal_planner import parse_local_signal_samples

        sample_dicts = parse_local_signal_samples(path)
        if not sample_dicts:
            return []

        items: list[SourceItem] = []
        for d in sample_dicts:
            meta = d.get("metadata", {})
            source_id = _build_source_id(meta)
            items.append(SourceItem(
                source_id=source_id,
                source_name=d.get("source_name", "local_signal"),
                source_type="local_signal",
                title=d.get("title", ""),
                content=d.get("content", ""),
                url=d.get("url", ""),
                published_at=d.get("published_at", ""),
                language=meta.get("language", ""),
                reliability="low",
                metadata=meta,
            ))

        logger.info("local_signal: collected %d SourceItems from %s", len(items), samples_path)
        return items


def _build_source_id(meta: dict[str, Any]) -> str:
    """Build a deterministic source ID from sample metadata."""
    import hashlib

    parts = [
        meta.get("collector_task_id", ""),
        meta.get("platform", ""),
        meta.get("market", ""),
        meta.get("sample_type", ""),
    ]
    raw = "|".join(str(p) for p in parts)
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10].upper()
    return f"LS_{digest}"
