"""Tests for Comparable Case Applicability audit (v0.5.3 PR 3)."""
from __future__ import annotations

import pytest

from multi_agent_brief.analysis_blocks.builder import build_analysis_blocks
from multi_agent_brief.analysis_blocks.schemas import AnalysisBlock, CaseApplicability
from multi_agent_brief.audit.case_applicability import (
    CaseApplicabilityFinding,
    audit_case_applicability,
    format_case_applicability_report,
)
from multi_agent_brief.core.claim_ledger import ClaimLedger
from multi_agent_brief.core.schemas import Claim


def _make_claim(
    claim_id: str,
    *,
    epistemic_type: str = "observed",
    evidence_relation: str = "direct",
    evidence_text: str = "some evidence",
    applicability_reason: str = "",
    limitations: list[str] | None = None,
    metadata: dict | None = None,
    topic: str = "market",
) -> Claim:
    return Claim(
        claim_id=claim_id,
        statement=f"Statement for {claim_id}",
        source_id="SRC_TEST",
        evidence_text=evidence_text,
        epistemic_type=epistemic_type,
        evidence_relation=evidence_relation,
        applicability_reason=applicability_reason,
        limitations=limitations or [],
        metadata={"topic": topic, **(metadata or {})},
    )


# ── Rule 1: analogous must have applicability_reason ──────────────


class TestAnalogousApplicability:
    def test_analogous_without_reason_triggers_warning(self):
        claim = _make_claim("A001", epistemic_type="analogy", evidence_relation="analogous", applicability_reason="")
        ledger = ClaimLedger([claim])
        blocks = build_analysis_blocks(ledger)
        findings = audit_case_applicability(blocks, ledger)
        assert any(f.finding_type == "missing_applicability_reason" for f in findings)
        assert any(f.severity == "warning" for f in findings)

    def test_analogous_with_reason_passes(self):
        claim = _make_claim("A002", epistemic_type="analogy", evidence_relation="analogous", applicability_reason="Similar market structure")
        ledger = ClaimLedger([claim])
        blocks = build_analysis_blocks(ledger)
        findings = audit_case_applicability(blocks, ledger)
        assert not any(f.finding_type == "missing_applicability_reason" for f in findings)

    def test_direct_evidence_not_checked(self):
        claim = _make_claim("A003", epistemic_type="observed", evidence_relation="direct")
        ledger = ClaimLedger([claim])
        blocks = build_analysis_blocks(ledger)
        findings = audit_case_applicability(blocks, ledger)
        assert not any(f.finding_type == "missing_applicability_reason" for f in findings)


# ── Rule 2: single case can't support strong action ───────────────


class TestSingleCaseAction:
    def test_analogous_action_without_fact_triggers_fail(self):
        case_claim = _make_claim("B001", epistemic_type="analogy", evidence_relation="analogous", applicability_reason="Similar")
        action_claim = _make_claim("B002", epistemic_type="action", evidence_relation="analogous", evidence_text="Based on comparable")
        ledger = ClaimLedger([case_claim, action_claim])
        blocks = build_analysis_blocks(ledger)
        findings = audit_case_applicability(blocks, ledger)
        assert any(f.finding_type == "analogous_evidence_supports_action" for f in findings)
        assert any(f.severity == "fail" for f in findings)

    def test_action_with_direct_fact_passes(self):
        fact_claim = _make_claim("B003", epistemic_type="observed", evidence_relation="direct")
        case_claim = _make_claim("B004", epistemic_type="analogy", evidence_relation="analogous", applicability_reason="Similar")
        # action_claim needs to be classified as action by the builder
        # For it to end up in action_claim_ids, it needs epistemic_type=action + evidence_relation=direct + evidence_text
        action_claim = _make_claim("B005", epistemic_type="action", evidence_relation="direct", evidence_text="Direct support")
        ledger = ClaimLedger([fact_claim, case_claim, action_claim])
        blocks = build_analysis_blocks(ledger)
        # The block should have fact_claim_ids, so the check should pass
        block = blocks[0]
        assert "B003" in block.fact_claim_ids
        findings = audit_case_applicability(blocks, ledger)
        assert not any(f.finding_type == "analogous_evidence_supports_action" for f in findings)

    def test_no_action_claims_passs(self):
        case_claim = _make_claim("B006", epistemic_type="analogy", evidence_relation="analogous", applicability_reason="Similar")
        ledger = ClaimLedger([case_claim])
        blocks = build_analysis_blocks(ledger)
        findings = audit_case_applicability(blocks, ledger)
        assert not any(f.finding_type == "analogous_evidence_supports_action" for f in findings)


# ── Rule 3: missing verification_path ─────────────────────────────


class TestVerificationPath:
    def test_case_without_fact_and_without_path_triggers_warning(self):
        claim = _make_claim("C001", epistemic_type="analogy", evidence_relation="analogous", applicability_reason="Similar")
        ledger = ClaimLedger([claim])
        blocks = build_analysis_blocks(ledger)
        findings = audit_case_applicability(blocks, ledger)
        assert any(f.finding_type == "missing_verification_path" for f in findings)

    def test_case_with_fact_passes(self):
        case_claim = _make_claim("C002", epistemic_type="analogy", evidence_relation="analogous", applicability_reason="Similar")
        fact_claim = _make_claim("C003", epistemic_type="observed", evidence_relation="direct")
        ledger = ClaimLedger([case_claim, fact_claim])
        blocks = build_analysis_blocks(ledger)
        findings = audit_case_applicability(blocks, ledger)
        assert not any(f.finding_type == "missing_verification_path" for f in findings)

    def test_case_with_verification_path_passes(self):
        claim = _make_claim("C004", epistemic_type="analogy", evidence_relation="analogous", applicability_reason="Similar")
        ledger = ClaimLedger([claim])
        blocks = build_analysis_blocks(ledger)
        blocks[0].verification_path = "Check local Q3 data"
        findings = audit_case_applicability(blocks, ledger)
        assert not any(f.finding_type == "missing_verification_path" for f in findings)

    def test_no_case_claims_passes(self):
        claim = _make_claim("C005", epistemic_type="observed", evidence_relation="direct")
        ledger = ClaimLedger([claim])
        blocks = build_analysis_blocks(ledger)
        findings = audit_case_applicability(blocks, ledger)
        assert not any(f.finding_type == "missing_verification_path" for f in findings)


# ── Integration with builder ──────────────────────────────────────


class TestBuilderIntegration:
    def test_builder_and_audit_together(self):
        """Full pipeline: build blocks then audit."""
        claims = [
            _make_claim("I001", epistemic_type="observed", evidence_relation="direct", topic="earnings"),
            _make_claim("I002", epistemic_type="analogy", evidence_relation="analogous", applicability_reason="Similar sector", topic="earnings"),
            _make_claim("I003", epistemic_type="analogy", evidence_relation="analogous", applicability_reason="", topic="market"),
        ]
        ledger = ClaimLedger(claims)
        blocks = build_analysis_blocks(ledger)
        findings = audit_case_applicability(blocks, ledger)

        # I003 has no applicability_reason → warning
        assert any(f.claim_id == "I003" and f.finding_type == "missing_applicability_reason" for f in findings)
        # I001+I002 block has direct fact, so no action issue
        # I003 block has no fact → missing_verification_path warning
        assert any(f.block_id == "market" and f.finding_type == "missing_verification_path" for f in findings)


# ── Report formatting ─────────────────────────────────────────────


class TestReportFormat:
    def test_empty_findings(self):
        report = format_case_applicability_report([])
        assert "all checks passed" in report

    def test_report_with_findings(self):
        findings = [
            CaseApplicabilityFinding(
                finding_type="missing_applicability_reason",
                severity="warning",
                block_id="market",
                claim_id="X001",
                description="Missing reason",
                recommendation="Add reason",
            ),
        ]
        report = format_case_applicability_report(findings)
        assert "missing_applicability_reason" in report
        assert "WARNING" in report
        assert "1 fail(s), 1 warning(s)" in report or "0 fail(s), 1 warning(s)" in report
