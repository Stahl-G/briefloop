"""Tests for Editorial Governance Rule Packs."""

from __future__ import annotations

from multi_agent_brief.audit.editorial_governance import (
    EditorialGovernanceConfig,
    check_business_advice,
    check_comparable_cases,
)
from multi_agent_brief.core.schemas import Claim


class TestBusinessAdvice:
    """Test business advice checks."""

    def test_advice_without_evidence_triggers_finding(self):
        """Business advice without evidence triggers finding."""
        config = EditorialGovernanceConfig(require_evidence_for_advice=True)
        markdown = "Companies should invest in AI immediately."
        claims = []

        findings = check_business_advice(markdown, claims, config)
        assert len(findings) == 1
        assert findings[0].finding_id == "EDITORIAL_UNSUPPORTED_ADVICE"
        assert findings[0].severity == "high"


class TestComparableCases:
    """Test comparable case checks."""

    def test_analogy_without_applicability_triggers_finding(self):
        """Analogy without applicability reason triggers finding."""
        config = EditorialGovernanceConfig(require_applicability_for_analogies=True)
        claim = Claim(
            claim_id="C1",
            statement="Similar to Company A's expansion",
            source_id="S1",
            evidence_text="Evidence",
            claim_type="interpretation",
            epistemic_type="analogy",
        )

        findings = check_comparable_cases([claim], config)
        # Should trigger both applicability and limitations findings
        assert len(findings) == 2
        applicability_findings = [f for f in findings if "APPLICABILITY" in f.finding_id]
        assert len(applicability_findings) == 1
