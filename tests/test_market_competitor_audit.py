"""Tests for MarketCompetitorAuditor — 6 specialist audit checks."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from multi_agent_brief.analysis_modules.market_competitor.auditor import (
    MarketCompetitorAuditor,
    _check_capacity_status,
    _check_comparison_evidence,
    _check_coverage_gap,
    _check_market_trend,
    _check_metric_basis,
    _check_single_source,
)
from multi_agent_brief.core.claim_ledger import ClaimLedger
from multi_agent_brief.core.schemas import Claim, PipelineContext


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

def test_comparison_both_sides_pass():
    c1 = _make_claim("C1", "A leads.", ["comp_a"])
    c2 = _make_claim("C2", "B gaining.", ["comp_b"])
    ledger = ClaimLedger([c1, c2])
    cards = [{
        "analysis_id": "A1", "headline": "A vs B gap narrowing",
        "observation": "comparison", "supporting_claim_ids": ["C1", "C2"],
    }]
    idx, findings = _check_comparison_evidence(cards, ledger, 0, [])
    assert len(findings) == 0


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


# ── capacity_status_missing ─────────────────────────────────────────────────

def test_capacity_status_present_pass():
    events = [{"event_id": "E1", "event_type": "capacity_expansion", "status": "announced"}]
    idx, findings = _check_capacity_status(events, 0, [])
    assert len(findings) == 0


def test_capacity_status_missing_fails():
    events = [{"event_id": "E1", "event_type": "capacity_expansion", "status": ""}]
    idx, findings = _check_capacity_status(events, 0, [])
    assert len(findings) >= 1
    assert findings[0].finding_type == "capacity_status_missing"


# ── metric_basis_missing ────────────────────────────────────────────────────

def test_metric_basis_present_pass():
    c1 = _make_claim("C1", "Revenue 1 billion.", evidence="annual FY2024 revenue 1 billion USD")
    ledger = ClaimLedger([c1])
    cards = [{
        "analysis_id": "A1", "headline": "Revenue up 20% FY2024",
        "observation": "Revenue 1 billion.", "supporting_claim_ids": ["C1"],
    }]
    idx, findings = _check_metric_basis(cards, ledger, 0, [])
    assert len(findings) == 0


def test_metric_basis_missing_fails():
    c1 = _make_claim("C1", "Revenue 1 billion.", evidence="Revenue was high.")
    ledger = ClaimLedger([c1])
    cards = [{
        "analysis_id": "A1", "headline": "Revenue 1 billion", "observation": "Revenue 1 billion.",
        "supporting_claim_ids": ["C1"],
    }]
    idx, findings = _check_metric_basis(cards, ledger, 0, [])
    assert len(findings) >= 1
    assert findings[0].finding_type == "metric_basis_missing"


# ── unsupported_market_trend ────────────────────────────────────────────────

def test_market_trend_two_sources_pass():
    cards = [{
        "analysis_id": "A1", "headline": "Market trend upward",
        "observation": "Market shifting higher.", "supporting_claim_ids": ["C1", "C2"],
    }]
    idx, findings = _check_market_trend(cards, 0, [])
    assert len(findings) == 0


def test_market_trend_one_source_fails():
    cards = [{
        "analysis_id": "A1", "headline": "Market trend upward",
        "observation": "Market shifting.", "supporting_claim_ids": ["C1"],
    }]
    idx, findings = _check_market_trend(cards, 0, [])
    assert len(findings) >= 1
    assert findings[0].finding_type == "unsupported_market_trend"


# ── single_source_interpretation ────────────────────────────────────────────

def test_single_source_low_confidence_pass():
    cards = [{
        "analysis_id": "A1", "finding_type": "risk", "headline": "Risk warning",
        "observation": "Risk noted.", "supporting_claim_ids": ["C1"], "confidence": "low",
    }]
    idx, findings = _check_single_source(cards, 0, [])
    assert len(findings) == 0


def test_single_source_medium_confidence_fails():
    cards = [{
        "analysis_id": "A1", "finding_type": "risk", "headline": "Risk warning",
        "observation": "Risk.", "supporting_claim_ids": ["C1"], "confidence": "medium",
    }]
    idx, findings = _check_single_source(cards, 0, [])
    assert len(findings) >= 1
    assert findings[0].finding_type == "single_source_interpretation"


# ── competitor_coverage_gap ─────────────────────────────────────────────────

def test_coverage_gap_found(tmp_path: Path):
    mc_dir = tmp_path / "intermediate" / "market_competitor"
    mc_dir.mkdir(parents=True)
    (mc_dir / "coverage_report.json").write_text(json.dumps({
        "primary_competitors_total": 3,
        "primary_competitors_with_recent_evidence": 1,
        "missing_entities": ["comp_b", "comp_c"],
    }))
    ctx = PipelineContext(
        project_name="test", input_dir=str(tmp_path), output_dir=str(tmp_path),
    )
    idx, findings = _check_coverage_gap(ctx, 0, [])
    assert len(findings) >= 1
    assert findings[0].finding_type == "competitor_coverage_gap"


def test_coverage_gap_none(tmp_path: Path):
    mc_dir = tmp_path / "intermediate" / "market_competitor"
    mc_dir.mkdir(parents=True)
    (mc_dir / "coverage_report.json").write_text(json.dumps({
        "primary_competitors_total": 3,
        "primary_competitors_with_recent_evidence": 3,
        "missing_entities": [],
    }))
    ctx = PipelineContext(
        project_name="test", input_dir=str(tmp_path), output_dir=str(tmp_path),
    )
    idx, findings = _check_coverage_gap(ctx, 0, [])
    assert len(findings) == 0
