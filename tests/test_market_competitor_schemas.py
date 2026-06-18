"""Tests for Market & Competitor Intelligence schema contracts."""
from __future__ import annotations

import pytest

from multi_agent_brief.analysis_modules.market_competitor.schemas import (
    AnalysisCard,
    CompetitorEntity,
    CompetitorMatrix,
    CompetitorMatrixCell,
    CompetitorUniverse,
    CoverageReport,
    EVENT_TYPES,
    FINDING_TYPES,
    MarketEvent,
    ModuleConfig,
    Watchlist,
    WatchlistItem,
)
from multi_agent_brief.contracts.schemas.analysis_pack import (
    AnalysisCardContract,
    MarketEventContract,
)


# ── CompetitorEntity ────────────────────────────────────────────────────────

def test_competitor_entity_construction():
    e = CompetitorEntity(entity_id="target_co", name="Target Co", aliases=["TC"])
    assert e.entity_id == "target_co"
    assert e.name == "Target Co"
    assert "TC" in e.aliases
    assert e.relation == "direct_competitor"
    assert e.priority == "primary"


# ── CompetitorUniverse ──────────────────────────────────────────────────────

def test_universe_match_name_exact():
    target = CompetitorEntity(entity_id="target", name="Target Inc")
    comp = CompetitorEntity(entity_id="comp_a", name="Competitor A")
    u = CompetitorUniverse(target=target, entities=[comp])
    assert u.match_name("Competitor A") == "comp_a"
    assert u.match_name("Target Inc") == "target"


def test_universe_match_name_alias():
    target = CompetitorEntity(entity_id="target", name="Target", aliases=["TC"])
    u = CompetitorUniverse(target=target, entities=[])
    assert u.match_name("tc") == "target"


def test_universe_match_name_no_match():
    u = CompetitorUniverse(
        target=CompetitorEntity(entity_id="t", name="T"),
        entities=[],
    )
    assert u.match_name("Unknown") is None


def test_universe_to_dict():
    u = CompetitorUniverse(
        target=CompetitorEntity(entity_id="t", name="Target"),
        market_scope={"geographies": ["US"]},
        entities=[CompetitorEntity(entity_id="c1", name="Comp 1")],
        enabled=True,
    )
    d = u.to_dict()
    assert d["target"]["entity_id"] == "t"
    assert d["enabled"] is True
    assert len(d["entities"]) == 1


# ── MarketEvent ─────────────────────────────────────────────────────────────

def test_market_event_construction():
    ev = MarketEvent(
        event_id="EVT_001",
        entity_ids=["comp_a"],
        event_type="capacity_expansion",
        dimension="capacity",
        status="announced",
        geography="US",
        summary="New factory announced.",
        supporting_claim_ids=["CLAIM_001"],
    )
    assert ev.source_count == 1
    assert ev.confidence == "medium"
    assert ev.change_status == "new"


def test_market_event_rejects_empty_claims():
    with pytest.raises(ValueError, match="supporting_claim_ids"):
        MarketEvent(
            event_id="EVT_BAD",
            entity_ids=["x"],
            event_type="other",
            supporting_claim_ids=[],
        )


def test_market_event_contract_enum_matches_runtime_event_types():
    schema = MarketEventContract.json_schema()
    assert set(schema["properties"]["event_type"]["enum"]) == set(EVENT_TYPES)

    for event_type in EVENT_TYPES:
        assert MarketEventContract.is_valid({
            "event_id": "EVT_TEST",
            "entity_ids": ["entity"],
            "event_type": event_type,
        })

    violations = MarketEventContract.validate({
        "event_id": "EVT_BAD",
        "entity_ids": ["entity"],
        "event_type": "not_a_runtime_event_type",
    })
    assert any(violation.field == "event_type" for violation in violations)


# ── AnalysisCard ────────────────────────────────────────────────────────────

def test_analysis_card_construction():
    ac = AnalysisCard(
        analysis_id="ANL_001",
        finding_type="relative_position_change",
        headline="Competitor A strengthening",
        observation="Announced new capacity and supply agreement.",
        supporting_claim_ids=["C1", "C2"],
    )
    assert ac.confidence == "medium"
    assert len(ac.supporting_claim_ids) == 2


def test_analysis_card_rejects_empty_claims():
    with pytest.raises(ValueError, match="supporting_claim_ids"):
        AnalysisCard(
            analysis_id="BAD",
            finding_type="risk",
            headline="x",
            observation="y",
            supporting_claim_ids=[],
        )


def test_analysis_card_single_source_requires_low_confidence():
    """Single-source interpretations MUST set confidence='low'."""
    with pytest.raises(ValueError, match="confidence='low'"):
        AnalysisCard(
            analysis_id="BAD",
            finding_type="risk",
            headline="x",
            observation="y",
            supporting_claim_ids=["C1"],
            confidence="medium",
        )


def test_analysis_card_single_source_low_confidence_accepted():
    ac = AnalysisCard(
        analysis_id="OK",
        finding_type="evidence_gap",
        headline="x",
        observation="y",
        supporting_claim_ids=["C1"],
        confidence="low",
    )
    assert ac.confidence == "low"


def test_analysis_card_contract_enum_matches_runtime_finding_types():
    schema = AnalysisCardContract.json_schema()
    assert set(schema["properties"]["finding_type"]["enum"]) == set(FINDING_TYPES)

    for finding_type in FINDING_TYPES:
        assert AnalysisCardContract.is_valid({
            "analysis_id": "ANL_TEST",
            "finding_type": finding_type,
            "headline": "Headline",
            "observation": "Observation",
        })

    violations = AnalysisCardContract.validate({
        "analysis_id": "ANL_BAD",
        "finding_type": "not_a_runtime_finding_type",
        "headline": "Headline",
        "observation": "Observation",
    })
    assert any(violation.field == "finding_type" for violation in violations)


# ── ModuleConfig ────────────────────────────────────────────────────────────

def test_module_config_from_dict():
    cfg = ModuleConfig.from_dict({"enabled": True, "max_events": 15})
    assert cfg.enabled is True
    assert cfg.max_events == 15
    assert cfg.mode == "weekly_monitor"  # default preserved


def test_module_config_default():
    cfg = ModuleConfig.from_dict(None)
    assert cfg.enabled is False
    assert cfg.max_events == 20
    assert len(cfg.dimensions) > 0
    assert len(cfg.event_types) > 0


# ── CoverageReport / Watchlist / CompetitorMatrix ───────────────────────────

def test_coverage_report_to_dict():
    cr = CoverageReport(
        primary_competitors_total=5,
        primary_competitors_with_recent_evidence=3,
        missing_entities=["comp_d"],
        undercovered_dimensions=["price"],
    )
    d = cr.to_dict()
    assert d["primary_competitors_total"] == 5
    assert d["primary_competitors_with_recent_evidence"] == 3


def test_watchlist_to_dict():
    wl = Watchlist(
        items=[
            WatchlistItem(
                item_id="W1",
                description="Verify plant construction",
                entity_id="comp_a",
            )
        ]
    )
    d = wl.to_dict()
    assert len(d["items"]) == 1
    assert d["items"][0]["status"] == "open"


def test_competitor_matrix_to_dict():
    matrix = CompetitorMatrix(
        entities=["target", "comp_a"],
        dimensions=["capacity", "technology"],
        cells=[
            CompetitorMatrixCell(
                entity_id="comp_a",
                dimension="capacity",
                summary="5GW announced",
                evidence_claim_ids=["C1"],
            )
        ],
        report_date="2026-06-05",
    )
    d = matrix.to_dict()
    assert len(d["cells"]) == 1
    assert d["cells"][0]["entity_id"] == "comp_a"
    assert d["report_date"] == "2026-06-05"
