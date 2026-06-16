"""Deterministic source-provider join helpers.

Provider collection may become parallel, but hash-sensitive source packages
must not depend on completion order.  This module joins provider batches by
declared provider priority and stable item keys before dedupe.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from multi_agent_brief.sources.base import SourceItem
from multi_agent_brief.sources.normalizer import filter_by_recency, normalize_source_item


@dataclass
class SourceProviderBatch:
    """One provider's collected source items and visible errors."""

    provider: str
    provider_priority: int
    items: list[SourceItem] = field(default_factory=list)
    errors: list[dict[str, str]] = field(default_factory=list)


def join_source_provider_batches(
    batches: list[SourceProviderBatch],
    *,
    recency_days: int,
    report_date: str = "",
) -> tuple[list[SourceItem], list[dict[str, str]]]:
    """Join provider batches into stable source items and stable errors.

    Duplicate winners are chosen by provider priority, then provider-local item
    index, then a content-derived stable key.  Final item order is content-key
    sorted, so a future parallel collector can feed batches in any completion
    order without changing output identity.
    """

    candidates: list[tuple[int, int, tuple[str, ...], SourceItem]] = []
    errors: list[tuple[int, int, dict[str, str]]] = []

    for batch in batches:
        for error_index, error in enumerate(batch.errors):
            errors.append((batch.provider_priority, error_index, _normalize_error(batch.provider, error)))
        for item_index, item in enumerate(batch.items):
            normalized = normalize_source_item(item)
            if _is_error_or_placeholder(normalized):
                errors.append(
                    (
                        batch.provider_priority,
                        item_index,
                        {
                            "provider": normalized.source_type.replace("_error", "") or batch.provider,
                            "error_type": str(
                                normalized.metadata.get("error_type", "PlaceholderSource")
                            ),
                            "message": (
                                f"Source '{normalized.source_name}' is not usable:"
                                f" {normalized.content[:120]}"
                            ),
                        },
                    )
                )
                continue
            candidates.append(
                (
                    batch.provider_priority,
                    item_index,
                    _source_item_stable_key(normalized),
                    normalized,
                )
            )

    filtered = filter_by_recency(
        [candidate[-1] for candidate in candidates],
        recency_days,
        report_date=report_date,
    )
    filtered_ids = {id(item) for item in filtered}
    dedupe_candidates = [
        candidate for candidate in candidates if id(candidate[-1]) in filtered_ids
    ]

    winners: dict[str, tuple[int, int, tuple[str, ...], SourceItem]] = {}
    for candidate in sorted(dedupe_candidates, key=lambda item: (item[0], item[1], item[2])):
        dedupe_key = _dedupe_key(candidate[-1])
        winners.setdefault(dedupe_key, candidate)

    joined_items = [
        candidate[-1]
        for candidate in sorted(winners.values(), key=lambda item: item[2])
    ]
    joined_errors = [
        error
        for _, _, error in sorted(
            errors,
            key=lambda item: (
                item[0],
                item[1],
                item[2].get("provider", ""),
                item[2].get("error_type", ""),
                item[2].get("message", ""),
            ),
        )
    ]
    return joined_items, joined_errors


def source_join_digest(items: list[SourceItem], errors: list[dict[str, str]]) -> str:
    """Return a stable helper digest for joined source output.

    This is not the source artifact hash.  Source artifacts are still hashed
    from their actual file bytes, and ``SourceItem.to_dict()`` still includes
    top-level ``retrieved_at``.  Future canonical source-package writers must
    explicitly use a timestamp-normalized serialization before claiming
    timestamp-neutral artifact hashes.

    `retrieved_at` is intentionally excluded because it is collection-time
    metadata and must not make deterministic join identity depend on wall-clock
    timing.  Publication date remains hash-sensitive.
    """

    payload = {
        "errors": [
            {
                "provider": str(error.get("provider", "")),
                "error_type": str(error.get("error_type", "")),
                "message": str(error.get("message", "")),
            }
            for error in errors
        ],
        "items": [_source_item_digest_payload(item) for item in items],
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _normalize_error(provider: str, error: dict[str, str]) -> dict[str, str]:
    return {
        "provider": str(error.get("provider") or provider),
        "error_type": str(error.get("error_type", "")),
        "message": str(error.get("message", "")),
    }


def _is_error_or_placeholder(item: SourceItem) -> bool:
    """Return True if this SourceItem is an error or placeholder."""
    if item.metadata.get("error_type"):
        return True
    if item.metadata.get("requires_fetch"):
        return True
    if item.metadata.get("ingestion_status") == "placeholder":
        return True
    if item.metadata.get("filtered_reason"):
        return True
    if item.metadata.get("low_quality"):
        return True
    if item.source_type.endswith("_error"):
        return True
    return False


def _dedupe_key(item: SourceItem) -> str:
    return str(item.dedupe_key or item.title.lower()).strip().lower()


def _source_item_stable_key(item: SourceItem) -> tuple[str, ...]:
    return (
        _dedupe_key(item),
        str(item.source_type).strip().lower(),
        str(item.source_name).strip().lower(),
        str(item.title).strip().lower(),
        str(item.url).strip().lower(),
        str(item.source_id).strip().lower(),
    )


def _source_item_digest_payload(item: SourceItem) -> dict[str, Any]:
    return {
        "source_id": item.source_id,
        "source_name": item.source_name,
        "source_type": item.source_type,
        "title": item.title,
        "content": item.content,
        "url": item.url,
        "published_at": item.published_at,
        "language": item.language,
        "reliability": item.reliability,
        "dedupe_key": item.dedupe_key,
        "metadata": _stable_metadata(item.metadata),
    }


def _stable_metadata(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _stable_metadata(inner)
            for key, inner in sorted(value.items(), key=lambda item: str(item[0]))
            if str(key) != "retrieved_at"
        }
    if isinstance(value, list):
        return [_stable_metadata(item) for item in value]
    return value
