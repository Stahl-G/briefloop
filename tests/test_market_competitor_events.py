"""Tests for event_builder and renderer."""
from __future__ import annotations

from pathlib import Path

from multi_agent_brief.analysis_modules.market_competitor.schemas import (
    CompetitorEntity,
    CompetitorUniverse,
    MarketEvent,
)
from multi_agent_brief.analysis_modules.market_competitor.event_builder import build_events
from multi_agent_brief.analysis_modules.market_competitor.renderer import (
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

def test_build_events_status_inference():
    u = _make_universe(CompetitorEntity(entity_id="comp_a", name="Comp A"))
    c1 = _make_claim("C1", "Comp A under construction.", ["comp_a"], "capacity_expansion", "capacity")
    ledger = _make_ledger(c1)
    events = build_events(ledger, u)
    assert events[0].status == "under_construction"


# ── Renderer ────────────────────────────────────────────────────────────────

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
