"""Tests for competitor universe config loading, saving, and merging."""
from __future__ import annotations

from pathlib import Path

from multi_agent_brief.analysis_modules.market_competitor.config import (
    load_competitor_universe,
    save_competitor_universe,
    save_competitor_candidates,
    merge_candidates_to_universe,
)
from multi_agent_brief.analysis_modules.market_competitor.schemas import CompetitorUniverse, CompetitorEntity


# ── save_competitor_universe -> load round-trip ─────────────────────────────

def test_universe_roundtrip(tmp_path: Path):
    u = CompetitorUniverse(
        target=CompetitorEntity(entity_id="t", name="Target"),
        entities=[CompetitorEntity(entity_id="c1", name="Comp 1", aliases=["C1"])],
        enabled=True,
    )
    p = tmp_path / "universe.yaml"
    save_competitor_universe(u, p)
    loaded = load_competitor_universe(p)
    assert loaded.enabled is True
    assert loaded.target.entity_id == "t"
    assert len(loaded.entities) == 1
    assert loaded.entities[0].name == "Comp 1"
    assert "C1" in loaded.entities[0].aliases


# ── merge_candidates_to_universe ────────────────────────────────────────────

def test_merge_no_duplicate_entities(tmp_path: Path):
    cands_path = tmp_path / "candidates.yaml"
    univ_path = tmp_path / "universe.yaml"

    save_competitor_universe(CompetitorUniverse(
        target=CompetitorEntity(entity_id="target", name="Target"),
        entities=[CompetitorEntity(entity_id="comp_a", name="Comp A")],
    ), univ_path)

    save_competitor_candidates([
        {"entity_id": "comp_a", "name": "Comp A V2", "approved": True},
    ], cands_path)

    added = merge_candidates_to_universe(cands_path, univ_path)
    assert added == 0  # already exists
