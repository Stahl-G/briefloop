"""Unit tests for the price-vs-narrative divergence check (synthetic data)."""

from __future__ import annotations

from multi_agent_brief.core.claim_ledger import ClaimLedger
from multi_agent_brief.core.schemas import Claim
from multi_agent_brief.quality_gates.narrative_coherence import (
    price_narrative_findings,
)


def _ledger(*claim_types: str) -> ClaimLedger:
    claims = []
    for index, claim_type in enumerate(claim_types, start=1):
        claims.append(
            Claim.from_dict(
                {
                    "claim_id": f"CL-{index:04d}",
                    "statement": f"Synthetic statement {index}.",
                    "evidence_text": f"Synthetic evidence {index}.",
                    "source_id": f"SRC-{index}",
                    "claim_type": claim_type,
                    "confidence": "medium",
                    "requires_audit": False,
                    "created_by": "claim-ledger",
                    "used_in_sections": [],
                }
            )
        )
    return ClaimLedger(claims)


_BRIEF_WITHOUT_REACTION = (
    "# Brief\n\n## 摘要\n\n收入大幅增长 [src:CL-0001]。\n"
)
_BRIEF_WITH_MOVE = (
    "# Brief\n\n## 摘要\n\n收入大幅增长 [src:CL-0001]。\n\n"
    "## 市场反应与分歧\n\nTOYO 本周下跌 14.95%（行情快照）。\n"
)


def test_unaddressed_large_move_is_blocking() -> None:
    findings = price_narrative_findings(
        _BRIEF_WITHOUT_REACTION,
        core_ticker="TOYO",
        return_1w=-14.95,
        ledger=_ledger("fact"),
    )
    assert [f["finding_type"] for f in findings] == [
        "price_narrative_divergence_unaddressed"
    ]
    assert findings[0]["blocking_level"] == "blocking"
    assert findings[0]["repair_owner"] == "editor"


def test_move_stated_without_risk_or_gap_is_blocking() -> None:
    findings = price_narrative_findings(
        _BRIEF_WITH_MOVE,
        core_ticker="TOYO",
        return_1w=-14.95,
        ledger=_ledger("fact"),
    )
    assert [f["finding_type"] for f in findings] == [
        "price_move_cause_undisclosed"
    ]


def test_move_with_risk_citation_passes() -> None:
    brief = _BRIEF_WITH_MOVE.replace(
        "（行情快照）。",
        "（行情快照）；管理层提示政策不确定性影响出货 [src:CL-0002]。",
    )
    assert (
        price_narrative_findings(
            brief,
            core_ticker="TOYO",
            return_1w=-14.95,
            ledger=_ledger("fact", "risk"),
        )
        == []
    )


def test_move_with_explicit_gap_passes() -> None:
    brief = _BRIEF_WITH_MOVE.replace(
        "（行情快照）。",
        "（行情快照）；下跌原因无证据入账，不做推断。",
    )
    assert (
        price_narrative_findings(
            brief,
            core_ticker="TOYO",
            return_1w=-14.95,
            ledger=_ledger("fact"),
        )
        == []
    )


def test_below_threshold_or_missing_inputs_skip() -> None:
    assert (
        price_narrative_findings(
            _BRIEF_WITHOUT_REACTION,
            core_ticker="TOYO",
            return_1w=-5.0,
            ledger=_ledger("fact"),
        )
        == []
    )
    assert (
        price_narrative_findings(
            _BRIEF_WITHOUT_REACTION,
            core_ticker=None,
            return_1w=None,
            ledger=None,
        )
        == []
    )


def test_threshold_is_configurable() -> None:
    findings = price_narrative_findings(
        _BRIEF_WITHOUT_REACTION,
        core_ticker="TOYO",
        return_1w=-6.0,
        ledger=_ledger("fact"),
        threshold_pct=5.0,
    )
    assert [f["finding_type"] for f in findings] == [
        "price_narrative_divergence_unaddressed"
    ]


def test_other_company_same_magnitude_rise_cannot_satisfy_gate() -> None:
    """Review counter-example: FSLR +14.95% must not cover TOYO -14.95%."""

    brief = (
        "# Brief\n\n## 摘要\n\n收入增长 [src:CL-0001]。\n\n"
        "## 市场反应与分歧\n\nFSLR 本周上涨 14.95%（行情快照）。"
        "管理层提示政策风险 [src:CL-0002]。\n"
    )
    findings = price_narrative_findings(
        brief,
        core_ticker="TOYO",
        return_1w=-14.95,
        ledger=_ledger("fact", "risk"),
    )
    assert [f["finding_type"] for f in findings] == [
        "price_narrative_divergence_unaddressed"
    ]


def test_wrong_direction_or_missing_provenance_fails() -> None:
    base = "## 市场反应与分歧\n\nTOYO {}（{}）。风险提示 [src:CL-0002]。\n"
    wrong_direction = (
        "# Brief\n\n## 摘要\n\n收入 [src:CL-0001]。\n\n"
        + base.format("本周上涨 14.95%", "行情快照")
    )
    assert (
        price_narrative_findings(
            wrong_direction,
            core_ticker="TOYO",
            return_1w=-14.95,
            ledger=_ledger("fact", "risk"),
        )
        != []
    )
    no_provenance = (
        "# Brief\n\n## 摘要\n\n收入 [src:CL-0001]。\n\n"
        + base.format("本周下跌 14.95%", "市场综述")
    )
    assert (
        price_narrative_findings(
            no_provenance,
            core_ticker="TOYO",
            return_1w=-14.95,
            ledger=_ledger("fact", "risk"),
        )
        != []
    )


def test_signed_token_and_tolerance_variants_pass() -> None:
    signed = (
        "# Brief\n\n## 摘要\n\n收入 [src:CL-0001]。\n\n"
        "## 市场反应与分歧\n\nTOYO 周度收益 -14.9%（行情快照）；"
        "政策风险 [src:CL-0002]。\n"
    )
    assert (
        price_narrative_findings(
            signed,
            core_ticker="TOYO",
            return_1w=-14.95,
            ledger=_ledger("fact", "risk"),
        )
        == []
    )
