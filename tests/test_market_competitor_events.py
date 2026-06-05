"""Tests for event_builder and renderer."""
from __future__ import annotations

import json
from pathlib import Path

from multi_agent_brief.analysis_modules.market_competitor.schemas import (
    CompetitorEntity,
    CompetitorUniverse,
    MarketEvent,
)
from multi_agent_brief.analysis_modules.market_competitor.event_builder import build_events
from multi_agent_brief.analysis_modules.market_competitor.renderer import (
    render_events_json,
    render_competitor_matrix,
    render_coverage_report,
    render_watchlist,
    render_evidence_pack,
    render_all,
)
from multi_agent_brief.core.claim_ledger import ClaimLedger
from multi_agent_brief.core.schemas import Claim


# ── Helpers ─────────────────────────────────────────────────────────────────

def _make_universe(*entities: CompetitorEntity) -> CompetitorUniverse:
    return CompetitorUniverse(
        target=CompetitorEntity(entity_id="target", name="Target Co"),
        entities=list(entities),
        enabled=True,
    )


def _make_claim(claim_id: str, statement: str, entity_ids: list[str] | None = None,
                event_type: str = "other", dimension: str = "other",
                geography: str = "", published_at: str = "") -> Claim:
    meta: dict = {}
    if entity_ids:
        meta["entity_ids"] = entity_ids
        meta["event_type"] = event_type
        meta["dimension"] = dimension
        if geography:
            meta["geography"] = geography
        if published_at:
            meta["published_at"] = published_at
    return Claim(
        claim_id=claim_id, statement=statement, source_id="S1",
        evidence_text=statement, source_type="web_search", metadata=meta,
    )


def _make_ledger(*claims: Claim) -> ClaimLedger:
    return ClaimLedger(list(claims))


# ── build_events ────────────────────────────────────────────────────────────

def test_build_events_single():
    u = _make_universe(CompetitorEntity(entity_id="comp_a", name="Comp A"))
    c1 = _make_claim("C1", "Comp A announced new factory.", ["comp_a"], "capacity_expansion", "capacity")
    ledger = _make_ledger(c1)
    events = build_events(ledger, u)
    assert len(events) == 1
    assert events[0].entity_ids == ["comp_a"]
    assert events[0].supporting_claim_ids == ["C1"]


def test_build_events_merge_same():
    u = _make_universe(CompetitorEntity(entity_id="comp_a", name="Comp A"))
    c1 = _make_claim("C1", "Comp A factory announcement.", ["comp_a"], "capacity_expansion", "capacity")
    c2 = _make_claim("C2", "Comp A capacity details.", ["comp_a"], "capacity_expansion", "capacity")
    ledger = _make_ledger(c1, c2)
    events = build_events(ledger, u)
    assert len(events) == 1
    assert set(events[0].supporting_claim_ids) == {"C1", "C2"}
    assert events[0].source_count == 2
    assert events[0].confidence == "high"


def test_build_events_different_entities():
    u = _make_universe(
        CompetitorEntity(entity_id="comp_a", name="Comp A"),
        CompetitorEntity(entity_id="comp_b", name="Comp B"),
    )
    c1 = _make_claim("C1", "Comp A news.", ["comp_a"], "capacity_expansion", "capacity")
    c2 = _make_claim("C2", "Comp B news.", ["comp_b"], "product_launch", "technology")
    ledger = _make_ledger(c1, c2)
    events = build_events(ledger, u)
    assert len(events) == 2


def test_build_events_skips_unmatched():
    u = _make_universe()
    c1 = _make_claim("C1", "General news.", None)
    ledger = _make_ledger(c1)
    events = build_events(ledger, u)
    assert events == []


def test_build_events_status_inference():
    u = _make_universe(CompetitorEntity(entity_id="comp_a", name="Comp A"))
    c1 = _make_claim("C1", "Comp A under construction.", ["comp_a"], "capacity_expansion", "capacity")
    ledger = _make_ledger(c1)
    events = build_events(ledger, u)
    assert events[0].status == "under_construction"


# ── Renderer ────────────────────────────────────────────────────────────────

def test_render_events_json(tmp_path: Path):
    ev = MarketEvent(event_id="EVT_001", entity_ids=["comp_a"], event_type="capacity_expansion",
                      dimension="capacity", supporting_claim_ids=["C1"])
    p = render_events_json([ev], tmp_path)
    assert p.exists()
    data = json.loads(p.read_text())
    assert data["count"] == 1
    assert data["events"][0]["entity_ids"] == ["comp_a"]


def test_render_competitor_matrix(tmp_path: Path):
    u = _make_universe(CompetitorEntity(entity_id="comp_a", name="Comp A"))
    ev = MarketEvent(event_id="EVT_001", entity_ids=["comp_a"], event_type="capacity_expansion",
                      dimension="capacity", supporting_claim_ids=["C1"], summary="5GW plant")
    p = render_competitor_matrix([ev], u, tmp_path)
    assert p.exists()
    data = json.loads(p.read_text())
    assert len(data["cells"]) >= 1


def test_render_coverage_report(tmp_path: Path):
    u = _make_universe(CompetitorEntity(entity_id="comp_a", name="Comp A", priority="primary"))
    p = render_coverage_report([], u, tmp_path)
    assert p.exists()
    data = json.loads(p.read_text())
    assert data["primary_competitors_total"] == 1
    assert data["primary_competitors_with_recent_evidence"] == 0
    assert "comp_a" in data["missing_entities"]


def test_render_watchlist(tmp_path: Path):
    p = render_watchlist(tmp_path)
    assert p.exists()
    data = json.loads(p.read_text())
    assert "items" in data


def test_render_evidence_pack(tmp_path: Path):
    ev = MarketEvent(event_id="EVT_001", entity_ids=["comp_a"], event_type="capacity_expansion",
                      dimension="capacity", supporting_claim_ids=["C1"], summary="Test")
    c1 = _make_claim("C1", "Test claim.", ["comp_a"], "capacity_expansion", "capacity")
    ledger = _make_ledger(c1)
    p = render_evidence_pack([ev], ledger, tmp_path)
    assert p.exists()
    data = json.loads(p.read_text())
    assert data["event_count"] == 1
    assert len(data["events"][0]["claims"]) == 1
    assert data["events"][0]["claims"][0]["claim_id"] == "C1"


def test_render_all(tmp_path: Path):
    u = _make_universe(CompetitorEntity(entity_id="comp_a", name="Comp A"))
    ev = MarketEvent(event_id="EVT_001", entity_ids=["comp_a"], event_type="capacity_expansion",
                      dimension="capacity", supporting_claim_ids=["C1"])
    c1 = _make_claim("C1", "Test.", ["comp_a"], "capacity_expansion", "capacity")
    paths = render_all([ev], _make_ledger(c1), u, tmp_path)
    assert len(paths) == 5
    for key in ("events", "competitor_matrix", "coverage_report", "watchlist", "evidence_pack"):
        assert key in paths
        assert Path(paths[key]).exists()
