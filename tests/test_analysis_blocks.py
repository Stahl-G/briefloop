"""Tests for the Analysis Block Contract (v0.5.3 PR 1)."""
from __future__ import annotations

from multi_agent_brief.analysis_blocks.builder import build_analysis_blocks
from multi_agent_brief.analysis_blocks.renderer import render_analysis_blocks
from multi_agent_brief.core.claim_ledger import ClaimLedger
from multi_agent_brief.core.schemas import Claim


def _make_claim(
    claim_id: str,
    *,
    epistemic_type: str = "observed",
    evidence_relation: str = "direct",
    evidence_text: str = "some evidence",
    limitations: list[str] | None = None,
    metadata: dict | None = None,
    topic: str = "market",
    applicability_reason: str = "",
) -> Claim:
    return Claim(
        claim_id=claim_id,
        statement=f"Statement for {claim_id}",
        source_id="SRC_TEST",
        evidence_text=evidence_text,
        epistemic_type=epistemic_type,
        evidence_relation=evidence_relation,
        limitations=limitations or [],
        metadata={"topic": topic, **(metadata or {})},
        applicability_reason=applicability_reason,
    )


# ── Builder classification tests ──────────────────────────────────


class TestBuilderClassification:
    """PR 1 acceptance criteria: claims go to the right bucket."""

    def test_observed_direct_goes_to_fact(self):
        claim = _make_claim("C001", epistemic_type="observed", evidence_relation="direct")
        ledger = ClaimLedger([claim])
        blocks = build_analysis_blocks(ledger)
        assert len(blocks) == 1
        assert "C001" in blocks[0].fact_claim_ids
        assert "C001" not in blocks[0].interpretation_claim_ids


# ── Renderer tests ────────────────────────────────────────────────


class TestRenderer:
    def test_renderer_produces_markdown(self):
        claim = _make_claim("R001", epistemic_type="observed", evidence_relation="direct")
        ledger = ClaimLedger([claim])
        blocks = build_analysis_blocks(ledger)
        md = render_analysis_blocks(blocks, ledger)
        assert "## Market" in md
        assert "Fact" in md
        assert "[src:R001]" in md
