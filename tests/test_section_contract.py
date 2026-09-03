"""Unit tests for the frozen reader-skeleton section contract."""

from __future__ import annotations

from multi_agent_brief.quality_gates.section_contract import (
    required_section_findings,
)

_INTENTS = [
    "executive_summary",
    "market_reaction_divergence",
    "investment_view_calendar",
    "earnings_valuation",
]


def test_missing_intent_is_blocking_and_gap_disclosure_is_the_escape() -> None:
    brief = "# Brief\n\n## 摘要\n\nBody.\n"
    findings = required_section_findings(
        brief,
        required_intents=_INTENTS,
        report_date="2026-08-25",
    )
    missing = [f for f in findings if f["finding_type"] == "required_section_missing"]
    assert {f["metadata"]["section_intent"] for f in missing} == {
        "market_reaction_divergence",
        "investment_view_calendar",
        "earnings_valuation",
    }
    assert all(f["blocking_level"] == "blocking" for f in missing)
    assert all(f["repair_owner"] == "editor" for f in missing)

    disclosed = (
        "# Brief\n\n## 摘要\n\nBody.\n\n## 覆盖缺口\n\n"
        "- 市场反应：无窗口内证据。\n"
        "- 催化剂日历：无已知日期。\n"
        "- 估值：无冻结倍数。\n"
    )
    findings = required_section_findings(
        disclosed,
        required_intents=_INTENTS,
        report_date="2026-08-25",
    )
    assert [f["finding_type"] for f in findings] == []


def test_valuation_obligation_needs_core_ticker_and_multiple() -> None:
    brief = (
        "# Brief\n\n## 摘要\n\nBody.\n\n## 市场反应\n\nBody.\n\n"
        "## 催化剂\n\n2026-12-04 生效。\n\n## 估值\n\n同业比较见数据表。\n"
    )
    findings = required_section_findings(
        brief,
        required_intents=_INTENTS,
        report_date="2026-08-25",
        core_ticker="DEMO",
        core_multiple_available=True,
        peer_multiples_count=5,
    )
    assert [f["finding_type"] for f in findings] == [
        "valuation_discussion_missing"
    ]

    fixed = brief.replace(
        "同业比较见数据表。",
        "DEMO 的 P/S TTM 显著低于同业，市场定价与财报表现背离。",
    )
    assert (
        required_section_findings(
            fixed,
            required_intents=_INTENTS,
            report_date="2026-08-25",
            core_ticker="DEMO",
            core_multiple_available=True,
            peer_multiples_count=5,
        )
        == []
    )


def test_no_intents_means_no_findings() -> None:
    assert (
        required_section_findings(
            "# Brief\n",
            required_intents=[],
            report_date="2026-08-25",
        )
        == []
    )


def test_unknown_intent_falls_back_to_id_alias_matching() -> None:
    findings = required_section_findings(
        "# Brief\n\n## Custom Watch\n\nBody.\n",
        required_intents=["custom_watch"],
        report_date="2026-08-25",
    )
    assert findings == []
    findings = required_section_findings(
        "# Brief\n\n## Unrelated\n\nBody.\n",
        required_intents=["custom_watch"],
        report_date="2026-08-25",
    )
    assert [f["finding_type"] for f in findings] == ["required_section_missing"]


