"""Shared source metadata validation helpers."""

from __future__ import annotations

from typing import Any, Mapping

SOURCE_CATEGORY_FIELD = "source_category"
VALID_SOURCE_CATEGORIES = {
    "clin" + "ical_registry",
    "company_press_release",
    "industry_database",
    "market_report",
    "news_media",
    "other",
    "peer_reviewed_paper",
    "preprint",
    "regulator",
}


def non_empty_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def source_url_error(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        return "must be a string"
    stripped = value.strip()
    if not stripped:
        return None
    if not stripped.startswith(("http://", "https://")):
        return "must be an http(s) URL"
    return None


def source_category_error(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        return "must be a string"
    if value.strip() not in VALID_SOURCE_CATEGORIES:
        return f"must be one of {sorted(VALID_SOURCE_CATEGORIES)}"
    return None


def has_valid_source_url(record: Mapping[str, Any]) -> bool:
    value = record.get("source_url")
    return non_empty_text(value) and source_url_error(value) is None


def source_category_missing(record: Mapping[str, Any]) -> bool:
    return not non_empty_text(record.get(SOURCE_CATEGORY_FIELD))


def local_file_without_url_missing_identity(
    record: Mapping[str, Any],
    *,
    default_source_type: str | None = None,
) -> str | None:
    source_type = record.get("source_type")
    if isinstance(source_type, str):
        normalized_source_type = source_type.strip() or default_source_type
    elif source_type is None:
        normalized_source_type = default_source_type
    else:
        return None
    if not normalized_source_type:
        return None
    if normalized_source_type != "local_file":
        return None
    if has_valid_source_url(record):
        return None
    if source_category_missing(record):
        return SOURCE_CATEGORY_FIELD
    if not (
        non_empty_text(record.get("source_title")) or non_empty_text(record.get("source_name"))
    ):
        return "source_title_or_source_name"
    return None
