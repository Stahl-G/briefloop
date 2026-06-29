"""Product-facing ReportPack entrypoint aliases.

The canonical ReportPack ids remain the values written to report_spec.yaml.
Aliases are only a user-facing command surface for `briefloop new` and related
listing/help output.
"""

from __future__ import annotations

from collections.abc import Iterable

CANONICAL_REPORT_PACK_IDS = (
    "market_weekly",
    "management_monthly",
    "evidence_extract",
    "solar_industry_periodic",
)

RECOMMENDED_REPORT_PACK_ENTRIES = {
    "market_weekly": "industry-weekly",
    "management_monthly": "management-monthly",
    "evidence_extract": "document-review",
    "solar_industry_periodic": "solar-periodic",
}

REPORT_PACK_ALIASES = {
    "industry_weekly": "market_weekly",
    "market_weekly": "market_weekly",
    "management_monthly": "management_monthly",
    "document_review": "evidence_extract",
    "evidence_extract": "evidence_extract",
    "solar_periodic": "solar_industry_periodic",
    "solar_industry_periodic": "solar_industry_periodic",
}


def normalize_report_pack_entry(value: str) -> str:
    return value.strip().lower().replace("-", "_")


def resolve_report_pack_id(value: str) -> str:
    normalized = normalize_report_pack_entry(value)
    return REPORT_PACK_ALIASES.get(normalized, normalized)


def hyphenated_pack_id(pack_id: str) -> str:
    return pack_id.replace("_", "-")


def aliases_for_report_pack(pack_id: str) -> list[str]:
    canonical = resolve_report_pack_id(pack_id)
    aliases = {
        hyphenated_pack_id(canonical),
        canonical,
    }
    recommended = RECOMMENDED_REPORT_PACK_ENTRIES.get(canonical)
    if recommended:
        aliases.add(recommended)
        aliases.add(normalize_report_pack_entry(recommended))
    return sorted(aliases)


def recommended_entries_for_pack_ids(pack_ids: Iterable[str]) -> list[str]:
    entries = []
    for pack_id in pack_ids:
        canonical = resolve_report_pack_id(pack_id)
        entries.append(RECOMMENDED_REPORT_PACK_ENTRIES.get(canonical, hyphenated_pack_id(canonical)))
    return sorted(set(entries))
