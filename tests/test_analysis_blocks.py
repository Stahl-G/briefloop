"""Tests for the Analysis Block Contract (v0.5.3 PR 1)."""
from __future__ import annotations

import pytest

from multi_agent_brief.analysis_blocks.builder import build_analysis_blocks
from multi_agent_brief.analysis_blocks.renderer import render_analysis_blocks
from multi_agent_brief.analysis_blocks.schemas import AnalysisBlock, CaseApplicability
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

    def test_analogous_goes_to_case(self):
        claim = _make_claim("C002", epistemic_type="analogy", evidence_relation="analogous")
        ledger = ClaimLedger([claim])
        blocks = build_analysis_blocks(ledger)
        assert "C002" in blocks[0].case_claim_ids
        assert "C002" not in blocks[0].fact_claim_ids

    def test_hypothesis_goes_to_interpretation_and_to_verify(self):
        claim = _make_claim("C003", epistemic_type="hypothesis", evidence_relation="inferred")
        ledger = ClaimLedger([claim])
        blocks = build_analysis_blocks(ledger)
        assert "C003" in blocks[0].interpretation_claim_ids
        assert "C003" in blocks[0].to_verify_claim_ids

    def test_interpreted_goes_to_interpretation_only(self):
        claim = _make_claim("C004", epistemic_type="interpreted", evidence_relation="indirect")
        ledger = ClaimLedger([claim])
        blocks = build_analysis_blocks(ledger)
        assert "C004" in blocks[0].interpretation_claim_ids
        assert "C004" not in blocks[0].to_verify_claim_ids

    def test_action_without_evidence_downgraded_to_verify(self):
        claim = _make_claim("C005", epistemic_type="action", evidence_relation="inferred", evidence_text="")
        ledger = ClaimLedger([claim])
        blocks = build_analysis_blocks(ledger)
        assert "C005" in blocks[0].to_verify_claim_ids
        assert "C005" not in blocks[0].action_claim_ids

    def test_action_with_direct_evidence_goes_to_action(self):
        claim = _make_claim("C006", epistemic_type="action", evidence_relation="direct", evidence_text="strong evidence")
        ledger = ClaimLedger([claim])
        blocks = build_analysis_blocks(ledger)
        assert "C006" in blocks[0].action_claim_ids
        assert "C006" not in blocks[0].to_verify_claim_ids

    def test_observed_indirect_goes_to_interpretation_and_verify(self):
        claim = _make_claim("C007", epistemic_type="observed", evidence_relation="indirect")
        ledger = ClaimLedger([claim])
        blocks = build_analysis_blocks(ledger)
        assert "C007" in blocks[0].interpretation_claim_ids
        assert "C007" in blocks[0].to_verify_claim_ids
        assert "C007" not in blocks[0].fact_claim_ids

    def test_claims_with_limitations_appear_in_limitation_bucket(self):
        claim = _make_claim("C008", limitations=["Not local data"])
        ledger = ClaimLedger([claim])
        blocks = build_analysis_blocks(ledger)
        assert "C008" in blocks[0].limitation_claim_ids

    def test_multiple_topics_produce_multiple_blocks(self):
        c1 = _make_claim("C010", topic="policy")
        c2 = _make_claim("C011", topic="market")
        ledger = ClaimLedger([c1, c2])
        blocks = build_analysis_blocks(ledger)
        topics = {b.title for b in blocks}
        assert "Policy" in topics
        assert "Market" in topics


# ── Case Applicability tests (PR 3) ──────────────────────────────


class TestCaseApplicability:
    def test_analogous_claim_populates_case_applicability(self):
        claim = _make_claim(
            "C020",
            epistemic_type="analogy",
            evidence_relation="analogous",
            applicability_reason="Similar market structure",
            metadata={
                "comparable_dimensions": ["market_size", "growth_rate"],
                "non_comparable_dimensions": ["regulatory_environment"],
                "local_verification_needed": True,
            },
        )
        ledger = ClaimLedger([claim])
        blocks = build_analysis_blocks(ledger)
        assert blocks[0].case_applicability is not None
        assert "market_size" in blocks[0].case_applicability.comparable_dimensions
        assert "regulatory_environment" in blocks[0].case_applicability.non_comparable_dimensions
        assert blocks[0].case_applicability.local_verification_needed is True
        assert blocks[0].applicability_note == "Similar market structure"

    def test_no_case_applicability_for_non_analogous(self):
        claim = _make_claim("C021", epistemic_type="observed", evidence_relation="direct")
        ledger = ClaimLedger([claim])
        blocks = build_analysis_blocks(ledger)
        assert blocks[0].case_applicability is None


# ── Confidence tests ──────────────────────────────────────────────


class TestConfidence:
    def test_all_direct_observed_gives_high_confidence(self):
        claims = [_make_claim(f"C{i}", epistemic_type="observed", evidence_relation="direct") for i in range(5)]
        ledger = ClaimLedger(claims)
        blocks = build_analysis_blocks(ledger)
        assert blocks[0].confidence == 1.0

    def test_all_hypothesis_gives_low_confidence(self):
        claims = [_make_claim(f"C{i}", epistemic_type="hypothesis", evidence_relation="inferred") for i in range(5)]
        ledger = ClaimLedger(claims)
        blocks = build_analysis_blocks(ledger)
        assert blocks[0].confidence == 0.2

    def test_mixed_claims_gives_medium_confidence(self):
        c1 = _make_claim("C1", epistemic_type="observed", evidence_relation="direct")
        c2 = _make_claim("C2", epistemic_type="hypothesis", evidence_relation="inferred")
        ledger = ClaimLedger([c1, c2])
        blocks = build_analysis_blocks(ledger)
        assert 0.2 < blocks[0].confidence < 1.0


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

    def test_renderer_management_audience(self):
        claim = _make_claim("R002", epistemic_type="observed", evidence_relation="direct")
        ledger = ClaimLedger([claim])
        blocks = build_analysis_blocks(ledger)
        md = render_analysis_blocks(blocks, ledger, audience="management")
        assert "What Happened" in md

    def test_renderer_research_audience(self):
        claim = _make_claim("R003", epistemic_type="observed", evidence_relation="direct")
        ledger = ClaimLedger([claim])
        blocks = build_analysis_blocks(ledger)
        md = render_analysis_blocks(blocks, ledger, audience="research")
        assert "Observed Facts" in md

    def test_renderer_chinese_language(self):
        claim = _make_claim("R004", epistemic_type="observed", evidence_relation="direct")
        ledger = ClaimLedger([claim])
        blocks = build_analysis_blocks(ledger)
        md = render_analysis_blocks(blocks, ledger, language="zh-CN")
        assert "事实" in md or "Fact" in md  # heading depends on audience

    def test_renderer_shows_no_fact_message_when_empty(self):
        claim = _make_claim("R005", epistemic_type="hypothesis", evidence_relation="inferred")
        ledger = ClaimLedger([claim])
        blocks = build_analysis_blocks(ledger)
        md = render_analysis_blocks(blocks, ledger)
        assert "No current-period direct fact found" in md

    def test_renderer_shows_empty_message_for_no_claims(self):
        md = render_analysis_blocks([], ClaimLedger())
        assert "No Reportable Signals" in md

    def test_renderer_shows_applicability_note(self):
        claim = _make_claim(
            "R006",
            epistemic_type="analogy",
            evidence_relation="analogous",
            applicability_reason="Similar market structure",
        )
        ledger = ClaimLedger([claim])
        blocks = build_analysis_blocks(ledger)
        md = render_analysis_blocks(blocks, ledger)
        assert "Similar market structure" in md

    def test_renderer_shows_verification_path(self):
        claim = _make_claim("R007", epistemic_type="hypothesis", evidence_relation="inferred")
        ledger = ClaimLedger([claim])
        blocks = build_analysis_blocks(ledger)
        blocks[0].verification_path = "Check Q3 earnings report"
        md = render_analysis_blocks(blocks, ledger)
        assert "Check Q3 earnings report" in md

    def test_renderer_limits_duplicate_limitations(self):
        claim = _make_claim(
            "R008",
            limitations=["Not local data", "Not local data", "Sample only"],
        )
        ledger = ClaimLedger([claim])
        blocks = build_analysis_blocks(ledger)
        md = render_analysis_blocks(blocks, ledger)
        # "Not local data" should appear only once
        assert md.count("Not local data") == 1

    def test_renderer_case_applicability_dimensions(self):
        claim = _make_claim(
            "R009",
            epistemic_type="analogy",
            evidence_relation="analogous",
            metadata={
                "comparable_dimensions": ["revenue_growth"],
                "non_comparable_dimensions": ["regulation"],
                "local_verification_needed": True,
            },
        )
        ledger = ClaimLedger([claim])
        blocks = build_analysis_blocks(ledger)
        md = render_analysis_blocks(blocks, ledger)
        assert "revenue_growth" in md
        assert "regulation" in md
        assert "Local verification needed" in md


# ── Schema tests ──────────────────────────────────────────────────


class TestSchemas:
    def test_analysis_block_to_dict(self):
        block = AnalysisBlock(block_id="test", title="Test")
        d = block.to_dict()
        assert d["block_id"] == "test"
        assert d["fact_claim_ids"] == []

    def test_case_applicability_to_dict(self):
        ca = CaseApplicability(comparable_dimensions=["a"], local_verification_needed=True)
        d = ca.to_dict()
        assert d["comparable_dimensions"] == ["a"]
        assert d["local_verification_needed"] is True

    def test_analysis_block_with_case_applicability_to_dict(self):
        ca = CaseApplicability(market_context="US")
        block = AnalysisBlock(block_id="x", title="X", case_applicability=ca)
        d = block.to_dict()
        assert d["case_applicability"]["market_context"] == "US"


# ── CLI dispatch tests ────────────────────────────────────────────


class TestCLI:
    def test_analysis_blocks_cli(self, tmp_path):
        """CLI 'analysis-blocks' command writes JSON and returns 0."""
        import json

        from multi_agent_brief.cli.main import main

        # Create a minimal claim ledger
        claim = _make_claim("CLI001", epistemic_type="observed", evidence_relation="direct")
        ledger = ClaimLedger([claim])
        ledger_path = tmp_path / "claim_ledger.json"
        ledger.export_json(ledger_path)

        output_path = tmp_path / "analysis_blocks.json"
        rc = main(["analysis-blocks", "--ledger", str(ledger_path), "--output", str(output_path)])
        assert rc == 0
        assert output_path.exists()

        data = json.loads(output_path.read_text(encoding="utf-8"))
        assert isinstance(data, list)
        assert len(data) >= 1
        assert data[0]["block_id"] == "market"

    def test_analysis_blocks_cli_markdown_flag(self, tmp_path, capsys):
        """CLI 'analysis-blocks --markdown' prints markdown to stdout."""
        from multi_agent_brief.cli.main import main

        claim = _make_claim("CLI002", epistemic_type="observed", evidence_relation="direct")
        ledger = ClaimLedger([claim])
        ledger_path = tmp_path / "claim_ledger.json"
        ledger.export_json(ledger_path)

        rc = main(["analysis-blocks", "--ledger", str(ledger_path), "--output", str(tmp_path / "out.json"), "--markdown"])
        assert rc == 0
        captured = capsys.readouterr()
        assert "Fact" in captured.out or "What Happened" in captured.out

    def test_analysis_blocks_cli_missing_ledger(self, tmp_path, capsys):
        """CLI returns 1 when ledger file doesn't exist."""
        from multi_agent_brief.cli.main import main

        rc = main(["analysis-blocks", "--ledger", str(tmp_path / "missing.json"), "--output", str(tmp_path / "out.json")])
        assert rc == 1
