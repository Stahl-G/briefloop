"""Tests for Market & Competitor Intelligence schema contracts."""
from __future__ import annotations

import pytest

from multi_agent_brief.analysis_modules.market_competitor.schemas import (
    AnalysisCard,
    MarketEvent,
)


# ── MarketEvent ─────────────────────────────────────────────────────────────

def test_market_event_rejects_empty_claims():
    with pytest.raises(ValueError, match="supporting_claim_ids"):
        MarketEvent(
            event_id="EVT_BAD",
            entity_ids=["x"],
            event_type="other",
            supporting_claim_ids=[],
        )


# ── AnalysisCard ────────────────────────────────────────────────────────────

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
