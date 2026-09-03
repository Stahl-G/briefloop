"""Tests for Comparable Case Applicability audit (v0.5.3 PR 3)."""
from __future__ import annotations

from multi_agent_brief.analysis_blocks.builder import build_analysis_blocks
from multi_agent_brief.audit.case_applicability import audit_case_applicability
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
