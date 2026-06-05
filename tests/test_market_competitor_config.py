"""Tests for competitor universe config loading, saving, and merging."""
from __future__ import annotations

from pathlib import Path

import pytest

from multi_agent_brief.analysis_modules.market_competitor.config import (
    load_competitor_universe,
    save_competitor_universe,
    load_competitor_candidates,
    save_competitor_candidates,
    merge_candidates_to_universe,
    generate_universe_template,
    generate_candidates_template,
)
from multi_agent_brief.analysis_modules.market_competitor.schemas import CompetitorUniverse, CompetitorEntity


# ── load_competitor_universe ────────────────────────────────────────────────

def test_load_universe_empty_file(tmp_path: Path):
    p = tmp_path / "universe.yaml"
    p.write_text("")
    u = load_competitor_universe(p)
    assert not u.enabled
    assert u.entities == []


def test_load_universe_missing_file(tmp_path: Path):
    u = load_competitor_universe(tmp_path / "nope.yaml")
    assert not u.enabled


def test_load_universe_with_entities(tmp_path: Path):
    p = tmp_path / "universe.yaml"
    p.write_text("""target:
  entity_id: target_co
  name: Target Co
  aliases: []
  relation: direct_competitor
  priority: primary
  geographies: []
  technologies: []
market_scope:
  geographies:
    - US
entities:
  - entity_id: comp_a
    name: Competitor A
    relation: direct_competitor
    priority: primary
    geographies:
      - US
  - entity_id: comp_b
    name: Competitor B
    relation: adjacent_competitor
    priority: secondary
mode: weekly_monitor
enabled: true
""")
    u = load_competitor_universe(p)
    assert u.enabled is True
    assert u.target.entity_id == "target_co"
    assert len(u.entities) == 2
    assert u.entities[0].entity_id == "comp_a"
    assert u.entities[0].name == "Competitor A"


def test_load_universe_dedup_entity_ids(tmp_path: Path):
    p = tmp_path / "universe.yaml"
    p.write_text("""target:
  entity_id: target_co
  name: Target Co
  aliases: []
entities:
  - entity_id: comp_a
    name: Competitor A
  - entity_id: comp_a
    name: Competitor A Duplicate
enabled: true
""")
    u = load_competitor_universe(p)
    assert len(u.entities) == 1  # duplicate removed


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


# ── competitor_candidates ───────────────────────────────────────────────────

def test_load_candidates_missing_file(tmp_path: Path):
    c = load_competitor_candidates(tmp_path / "nope.yaml")
    assert c == []


def test_save_and_load_candidates(tmp_path: Path):
    p = tmp_path / "candidates.yaml"
    cands = [
        {"entity_id": "c1", "name": "Comp 1", "approved": False},
        {"entity_id": "c2", "name": "Comp 2", "approved": True},
    ]
    save_competitor_candidates(cands, p)
    loaded = load_competitor_candidates(p)
    assert len(loaded) == 2
    assert loaded[0]["entity_id"] == "c1"


# ── merge_candidates_to_universe ────────────────────────────────────────────

def test_merge_approved_candidates(tmp_path: Path):
    cands_path = tmp_path / "candidates.yaml"
    univ_path = tmp_path / "universe.yaml"

    save_competitor_candidates([
        {"entity_id": "comp_a", "name": "Comp A", "approved": True, "relation": "direct_competitor", "priority": "primary"},
        {"entity_id": "comp_b", "name": "Comp B", "approved": False},
    ], cands_path)

    save_competitor_universe(CompetitorUniverse(
        target=CompetitorEntity(entity_id="target", name="Target"),
        entities=[],
    ), univ_path)

    added = merge_candidates_to_universe(cands_path, univ_path)
    assert added == 1

    u = load_competitor_universe(univ_path)
    assert len(u.entities) == 1
    assert u.entities[0].entity_id == "comp_a"


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


# ── templates ───────────────────────────────────────────────────────────────

def test_generate_universe_template():
    t = generate_universe_template()
    assert "target" in t
    assert "entities" in t
    assert t["enabled"] is False
    assert t["mode"] == "weekly_monitor"


def test_generate_candidates_template():
    t = generate_candidates_template()
    assert "candidates" in t
    assert t["candidates"] == []
