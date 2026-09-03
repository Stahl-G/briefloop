"""Deterministic reader-skeleton contract for report section intents.

The report pack freezes ``required_section_intents`` into
``RunDirection``.  This module checks the audited brief against that
contract using heading aliases only — it never judges the content of a
section.  Every missing intent can be satisfied either by a matching
section heading or by an explicit disclosure inside the limitations
(coverage-gap) section: a visible gap is lawful, an invisible one is not.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_MULTIPLE_TOKENS = (
    "P/S",
    "P/E",
    "EV/EBITDA",
    "EV/Sales",
    "市销率",
    "市盈率",
    "估值",
)

# intent id -> heading substrings (lowercased ASCII or CJK substrings)
SECTION_INTENT_ALIASES: dict[str, tuple[str, ...]] = {
    "executive_summary": ("executive summary", "摘要", "summary"),
    "primary_equity_comparison": (
        "primary equity",
        "主要同业",
        "美股同业",
        "主要可比",
        "primary comparison",
    ),
    "overseas_equity_comparison": (
        "overseas equity",
        "海外同业",
        "overseas comparison",
    ),
    "event_price_timeline": (
        "event timeline",
        "事件与交易日",
        "事件时间线",
        "事件映射",
        "events and trading",
    ),
    "earnings_valuation": (
        "earnings",
        "valuation",
        "财报",
        "估值",
        "业绩",
    ),
    "policy_input_dashboard": (
        "policy",
        "input price",
        "政策",
        "投入品",
    ),
    "capacity_asset_watch": ("capacity", "asset", "产能", "资产"),
    "sentiment_monitor": ("sentiment", "情绪"),
    "market_reaction_divergence": (
        "market reaction",
        "市场反应",
        "价格与叙事",
        "分歧",
        "price action",
    ),
    "investment_view_calendar": (
        "catalyst",
        "催化剂",
        "投资要点",
        "calendar",
        "日历",
        "要点与催化",
    ),
    "core_implications": ("implication", "启示"),
}

_LIMITATION_ALIASES = (
    "limitation",
    "coverage gap",
    "覆盖缺口",
    "数据说明",
    "局限性",
)


@dataclass(frozen=True)
class _Section:
    title: str
    body: str


def _sections(markdown: str) -> list[_Section]:
    sections: list[_Section] = []
    current_title = ""
    body: list[str] = []
    for line in markdown.splitlines():
        heading = _HEADING_RE.match(line)
        if heading is not None:
            if current_title or body:
                sections.append(_Section(current_title, "\n".join(body)))
            current_title = heading.group(2).strip()
            body = []
            continue
        body.append(line)
    if current_title or body:
        sections.append(_Section(current_title, "\n".join(body)))
    return sections


def _matches(title: str, aliases: Iterable[str]) -> bool:
    lowered = title.lower()
    return any(alias in lowered for alias in aliases)


def _intent_aliases(intent: str) -> tuple[str, ...]:
    return SECTION_INTENT_ALIASES.get(intent, (intent.replace("_", " "),))


def _section_for(sections: list[_Section], intent: str) -> _Section | None:
    aliases = _intent_aliases(intent)
    for section in sections:
        if _matches(section.title, aliases):
            return section
    return None


def _limitations_section(sections: list[_Section]) -> _Section | None:
    for section in sections:
        if _matches(section.title, _LIMITATION_ALIASES):
            return section
    return None


def sections_from_markdown(markdown: str) -> list[_Section]:
    return _sections(markdown)


def section_matching(
    sections: list[_Section], intent: str
) -> "_Section | None":
    return _section_for(sections, intent)


def limitations_section(sections: list[_Section]) -> "_Section | None":
    return _limitations_section(sections)


def _finding(
    *,
    finding_type: str,
    description: str,
    recommendation: str,
    intent: str,
) -> dict[str, object]:
    return {
        "finding_type": finding_type,
        "gate_id": "final_abstract_quality",
        "category": "required_section",
        "severity": "high",
        "blocking_level": "blocking",
        "repair_owner": "editor",
        "stage_id": "editor",
        "artifact_id": "audited_brief",
        "claim_id": None,
        "source_id": None,
        "description": description,
        "recommendation": recommendation,
        "metadata": {"section_intent": intent},
    }


def required_section_findings(
    markdown: str,
    *,
    required_intents: list[str] | tuple[str, ...],
    report_date: str,
    core_ticker: str | None = None,
    core_multiple_available: bool = False,
    peer_multiples_count: int = 0,
) -> list[dict[str, object]]:
    """Check the audited brief against the frozen section-intent contract."""

    if not required_intents:
        return []
    sections = _sections(markdown)
    findings: list[dict[str, object]] = []
    limitations = _limitations_section(sections)
    for intent in required_intents:
        aliases = _intent_aliases(intent)
        section = _section_for(sections, intent)
        if section is None:
            disclosed = limitations is not None and _matches(
                limitations.body.lower(), aliases
            )
            if not disclosed:
                findings.append(
                    _finding(
                        finding_type="required_section_missing",
                        description=(
                            f"Required section intent '{intent}' has no "
                            "matching section and is not disclosed as a "
                            "coverage gap."
                        ),
                        recommendation=(
                            "Add a section matching the intent (or its "
                            "aliases), or disclose the gap explicitly in "
                            "the coverage/limitations section."
                        ),
                        intent=intent,
                    )
                )
            continue
        # The catalyst calendar section is a placeholder: Python renders
        # the deterministic table into the reader copy at finalize, so
        # this contract checks only that the section exists.
        if (
            intent == "earnings_valuation"
            and core_ticker
            and core_multiple_available
            and peer_multiples_count >= 3
        ):
            body = section.body
            mentions_core = core_ticker.lower() in body.lower()
            mentions_multiple = any(
                token.lower() in body.lower() for token in _MULTIPLE_TOKENS
            )
            if not (mentions_core and mentions_multiple):
                findings.append(
                    _finding(
                        finding_type="valuation_discussion_missing",
                        description=(
                            "A valuation section is required when frozen "
                            "multiples exist for the core ticker and at "
                            "least three peers, and it must mention the "
                            "core ticker together with a multiple."
                        ),
                        recommendation=(
                            "Reference the core ticker and at least one "
                            "frozen multiple (e.g. P/S, P/E, EV/EBITDA) in "
                            "the earnings/valuation section."
                        ),
                        intent=intent,
                    )
                )
    return findings


__all__ = [
    "SECTION_INTENT_ALIASES",
    "limitations_section",
    "required_section_findings",
    "section_matching",
    "sections_from_markdown",
]
