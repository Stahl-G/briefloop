"""Tests for EntityEventEnricher and competitor search task generation."""
from __future__ import annotations

import pytest

from multi_agent_brief.analysis_modules.market_competitor.schemas import (
    CompetitorEntity,
    CompetitorUniverse,
)
from multi_agent_brief.analysis_modules.market_competitor.enricher import (
    EntityEventEnricher,
    generate_competitor_search_tasks,
    _match_event_type,
    _match_geography,
    _match_dimension,
)
from multi_agent_brief.core.schemas import Claim


# ── EntityEventEnricher ─────────────────────────────────────────────────────

def _make_universe(*entities: CompetitorEntity) -> CompetitorUniverse:
    return CompetitorUniverse(
        target=CompetitorEntity(entity_id="target", name="Target Co"),
        entities=list(entities),
        enabled=True,
    )


def test_enricher_exact_name_match():
    u = _make_universe(CompetitorEntity(entity_id="comp_a", name="Competitor A"))
    enricher = EntityEventEnricher(u)
    claim = Claim(
        claim_id="C1", statement="Competitor A announced new factory.", source_id="SRC_001",
        evidence_text="Competitor A will build a 5GW plant.", source_type="web_search",
    )
    enricher.enrich([claim])
    assert claim.metadata.get("entity_ids") == ["comp_a"]


def test_enricher_alias_match():
    u = _make_universe(CompetitorEntity(entity_id="comp_a", name="Comp A", aliases=["ACME"]))
    enricher = EntityEventEnricher(u)
    claim = Claim(
        claim_id="C1", statement="ACME launched new product.", source_id="SRC_001",
        evidence_text="ACME release.", source_type="web_search",
    )
    enricher.enrich([claim])
    assert "comp_a" in claim.metadata.get("entity_ids", [])


def test_enricher_no_match():
    u = _make_universe(CompetitorEntity(entity_id="comp_a", name="Competitor A"))
    enricher = EntityEventEnricher(u)
    claim = Claim(
        claim_id="C1", statement="Unrelated news about solar.", source_id="SRC_001",
        evidence_text="No match.", source_type="web_search",
    )
    enricher.enrich([claim])
    assert "entity_ids" not in claim.metadata


def test_enricher_event_type_tagging():
    u = _make_universe(CompetitorEntity(entity_id="comp_a", name="Comp A"))
    enricher = EntityEventEnricher(u)
    claim = Claim(
        claim_id="C1", statement="Comp A announced capacity expansion.", source_id="SRC_001",
        evidence_text="New 5GW factory.", source_type="web_search",
    )
    enricher.enrich([claim])
    assert claim.metadata.get("event_type") == "capacity_expansion"


def test_enricher_geography_tagging():
    u = _make_universe(CompetitorEntity(entity_id="comp_a", name="Comp A"))
    enricher = EntityEventEnricher(u)
    claim = Claim(
        claim_id="C1", statement="Comp A builds plant in Japan.", source_id="SRC_001",
        evidence_text="Japan factory.", source_type="web_search",
    )
    enricher.enrich([claim])
    assert claim.metadata.get("geography") == "Japan"


def test_enricher_dimension_tagging():
    u = _make_universe(CompetitorEntity(entity_id="comp_a", name="Comp A"))
    enricher = EntityEventEnricher(u)
    claim = Claim(
        claim_id="C1", statement="Comp A revenue grew 20%.", source_id="SRC_001",
        evidence_text="Earnings up.", source_type="web_search",
    )
    enricher.enrich([claim])
    assert claim.metadata.get("dimension") == "financials"


def test_enricher_empty_universe():
    u = _make_universe()
    enricher = EntityEventEnricher(u)
    claim = Claim(
        claim_id="C1", statement="Comp A news.", source_id="SRC_001",
        evidence_text="News.", source_type="web_search",
    )
    enricher.enrich([claim])
    assert "entity_ids" not in claim.metadata


def test_enricher_multiple_entities_in_one_claim():
    u = _make_universe(
        CompetitorEntity(entity_id="comp_a", name="Comp A"),
        CompetitorEntity(entity_id="comp_b", name="Comp B"),
    )
    enricher = EntityEventEnricher(u)
    claim = Claim(
        claim_id="C1", statement="Comp A partnered with Comp B.", source_id="SRC_001",
        evidence_text="Two companies.", source_type="web_search",
    )
    enricher.enrich([claim])
    ids = claim.metadata.get("entity_ids", [])
    assert "comp_a" in ids
    assert "comp_b" in ids


def test_enricher_target_entity_match():
    u = CompetitorUniverse(
        target=CompetitorEntity(entity_id="target", name="Target Co"),
        entities=[],
        enabled=True,
    )
    enricher = EntityEventEnricher(u)
    claim = Claim(
        claim_id="C1", statement="Target Co announced earnings.", source_id="SRC_001",
        evidence_text="Target Co.", source_type="web_search",
    )
    enricher.enrich([claim])
    assert claim.metadata.get("entity_ids") == ["target"]


# ── Search task generation ──────────────────────────────────────────────────

def test_generate_search_tasks():
    u = _make_universe(
        CompetitorEntity(entity_id="comp_a", name="Comp A", priority="primary"),
        CompetitorEntity(entity_id="comp_b", name="Comp B", priority="secondary"),
    )
    tasks = generate_competitor_search_tasks(u)
    # Only primary competitors get search tasks
    assert len(tasks) > 0
    queries = [t["query"] for t in tasks]
    assert any("Comp A" in q and "capacity" in q for q in queries)


def test_generate_search_tasks_no_primary():
    u = _make_universe(
        CompetitorEntity(entity_id="comp_b", name="Comp B", priority="secondary"),
    )
    tasks = generate_competitor_search_tasks(u)
    assert tasks == []


def test_generate_search_tasks_empty():
    u = _make_universe()
    tasks = generate_competitor_search_tasks(u)
    assert tasks == []
