"""Source item normalization and deduplication."""
from __future__ import annotations

import hashlib
import re
from typing import Any

from multi_agent_brief.sources.base import SourceItem


def normalize_source_item(item: SourceItem) -> SourceItem:
    """Normalize a source item: trim fields, generate dedupe_key if missing."""
    item.title = item.title.strip()
    item.content = item.content.strip()
    item.url = item.url.strip()
    item.published_at = item.published_at.strip()
    item.language = item.language.strip().lower()

    if not item.dedupe_key:
        item.dedupe_key = _make_dedupe_key(item)

    if not item.source_id:
        item.source_id = _make_source_id(item.source_name, item.title)

    return item


def _make_dedupe_key(item: SourceItem) -> str:
    if item.url:
        return item.url.lower().strip()
    return hashlib.sha1(
        f"{item.source_name}|{item.title}".encode("utf-8")
    ).hexdigest()[:16]


def _make_source_id(source_name: str, title: str) -> str:
    raw = f"{source_name}|{title}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]
    prefix = "".join(ch for ch in source_name.upper() if ch.isalnum())[:8] or "SRC"
    return f"{prefix}_{digest.upper()}"


def dedupe_sources(items: list[SourceItem]) -> list[SourceItem]:
    """Remove duplicate sources by dedupe_key, keeping the first occurrence."""
    seen: set[str] = set()
    result: list[SourceItem] = []
    for item in items:
        key = item.dedupe_key or item.title.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def filter_by_recency(items: list[SourceItem], recency_days: int) -> list[SourceItem]:
    """Keep items that are within recency_days or have no parseable date."""
    if recency_days <= 0:
        return items
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    result: list[SourceItem] = []
    for item in items:
        if not item.published_at:
            result.append(item)
            continue
        try:
            pub = datetime.fromisoformat(item.published_at.replace("Z", "+00:00"))
            age = (now - pub).days
            if age <= recency_days:
                result.append(item)
        except (ValueError, TypeError):
            result.append(item)
    return result
