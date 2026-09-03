"""Tests for MarketCompetitorAuditor — 6 specialist audit checks."""
from __future__ import annotations

from multi_agent_brief.analysis_modules.market_competitor.auditor import (
    _check_comparison_evidence,
    _check_single_source,
)
from multi_agent_brief.core.claim_ledger import ClaimLedger
from multi_agent_brief.core.schemas import Claim


def _make_claim(claim_id: str, statement: str, entity_ids: list[str] | None = None,
                evidence: str = "") -> Claim:
    meta: dict = {}
    if entity_ids:
        meta["entity_ids"] = entity_ids
    return Claim(
        claim_id=claim_id, statement=statement, source_id="S1",
        evidence_text=evidence or statement, source_type="web_search",
        metadata=meta,
    )


# ── comparison_missing_entity_evidence ─────────────────────────────────────

def test_comparison_one_side_fails():
    c1 = _make_claim("C1", "A leads.", ["comp_a"])
    ledger = ClaimLedger([c1])
    cards = [{
        "analysis_id": "A1", "headline": "A vs B gap",
        "observation": "comparison with B", "supporting_claim_ids": ["C1"],
    }]
    idx, findings = _check_comparison_evidence(cards, ledger, 0, [])
    assert len(findings) >= 1
    assert findings[0].finding_type == "comparison_missing_entity_evidence"


# ── single_source_interpretation ────────────────────────────────────────────

def test_single_source_medium_confidence_fails():
    cards = [{
        "analysis_id": "A1", "finding_type": "risk", "headline": "Risk warning",
        "observation": "Risk.", "supporting_claim_ids": ["C1"], "confidence": "medium",
    }]
    idx, findings = _check_single_source(cards, 0, [])
    assert len(findings) >= 1
    assert findings[0].finding_type == "single_source_interpretation"
