"""Tests for Editorial Governance Rule Packs."""

from __future__ import annotations

import pytest

from multi_agent_brief.audit.editorial_governance import (
    EditorialGovernanceConfig,
    check_business_advice,
    check_comparable_cases,
    check_factual_density,
    check_historical_analogies,
    check_must_preserve_facts,
    run_editorial_governance_checks,
)
from multi_agent_brief.core.claim_ledger import ClaimLedger
from multi_agent_brief.core.schemas import Claim


class TestFactualDensity:
    """Test factual density checks."""

    def test_high_density_passes(self):
        """High factual density passes check."""
        config = EditorialGovernanceConfig(min_claims_per_1000_chars=2.0)
        # 10 claims in 1000 chars = 10.0 density
        claims = [Claim(claim_id=f"C{i}", statement=f"Claim {i}", source_id="S1",
                       evidence_text="Evidence", claim_type="fact") for i in range(10)]
        markdown = "x" * 1000

        findings = check_factual_density(markdown, claims, config)
        assert len(findings) == 0

    def test_low_density_triggers_warning(self):
        """Low factual density triggers warning."""
        config = EditorialGovernanceConfig(min_claims_per_1000_chars=2.0)
        # 1 claim in 1000 chars = 1.0 density
        claims = [Claim(claim_id="C1", statement="Claim 1", source_id="S1",
                       evidence_text="Evidence", claim_type="fact")]
        markdown = "x" * 1000

        findings = check_factual_density(markdown, claims, config)
        assert len(findings) == 1
        assert findings[0].finding_id == "EDITORIAL_LOW_FACTUAL_DENSITY"
        assert findings[0].severity == "warning"

    def test_quiet_week_lower_threshold(self):
        """Quiet week lowers density threshold."""
        config = EditorialGovernanceConfig(
            min_claims_per_1000_chars=2.0,
            quiet_week=True,
            allow_quiet_week_exception=True,
        )
        # 1 claim in 1000 chars = 1.0 density, threshold lowered to 1.0
        claims = [Claim(claim_id="C1", statement="Claim 1", source_id="S1",
                       evidence_text="Evidence", claim_type="fact")]
        markdown = "x" * 1000

        findings = check_factual_density(markdown, claims, config)
        assert len(findings) == 0


class TestBusinessAdvice:
    """Test business advice checks."""

    def test_advice_with_evidence_passes(self):
        """Business advice with evidence passes check."""
        config = EditorialGovernanceConfig(require_evidence_for_advice=True)
        markdown = "According to [src:C1], companies should invest in AI."
        claims = []

        findings = check_business_advice(markdown, claims, config)
        assert len(findings) == 0

    def test_advice_without_evidence_triggers_finding(self):
        """Business advice without evidence triggers finding."""
        config = EditorialGovernanceConfig(require_evidence_for_advice=True)
        markdown = "Companies should invest in AI immediately."
        claims = []

        findings = check_business_advice(markdown, claims, config)
        assert len(findings) == 1
        assert findings[0].finding_id == "EDITORIAL_UNSUPPORTED_ADVICE"
        assert findings[0].severity == "high"

    def test_disabled_check_passes(self):
        """Disabled business advice check passes."""
        config = EditorialGovernanceConfig(require_evidence_for_advice=False)
        markdown = "Companies should invest in AI immediately."
        claims = []

        findings = check_business_advice(markdown, claims, config)
        assert len(findings) == 0


class TestComparableCases:
    """Test comparable case checks."""

    def test_analogy_with_applicability_passes(self):
        """Analogy with applicability reason passes check."""
        config = EditorialGovernanceConfig(require_applicability_for_analogies=True)
        claim = Claim(
            claim_id="C1",
            statement="Similar to Company A's expansion",
            source_id="S1",
            evidence_text="Evidence",
            claim_type="interpretation",
            epistemic_type="analogy",
            applicability_reason="Both operate in same market",
            limitations=["Different scale"],
        )

        findings = check_comparable_cases([claim], config)
        assert len(findings) == 0

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

    def test_analogy_without_limitations_triggers_finding(self):
        """Analogy without limitations triggers finding."""
        config = EditorialGovernanceConfig(require_limitations_for_analogies=True)
        claim = Claim(
            claim_id="C1",
            statement="Similar to Company A's expansion",
            source_id="S1",
            evidence_text="Evidence",
            claim_type="interpretation",
            epistemic_type="analogy",
            applicability_reason="Both operate in same market",
        )

        findings = check_comparable_cases([claim], config)
        assert len(findings) == 1
        assert "EDITORIAL_ANALOGY_NO_LIMITATIONS" in findings[0].finding_id


class TestHistoricalAnalogies:
    """Test historical analogy checks."""

    def test_historical_as_current_triggers_finding(self):
        """Historical analogy presented as current fact triggers finding."""
        config = EditorialGovernanceConfig(prevent_historical_as_current=True)
        markdown = "Historically, this week's performance has been strong."
        claims = []

        findings = check_historical_analogies(markdown, claims, config)
        assert len(findings) == 1
        assert findings[0].finding_id == "EDITORIAL_HISTORICAL_AS_CURRENT"
        assert findings[0].severity == "high"

    def test_historical_without_current_framing_passes(self):
        """Historical reference without current framing passes."""
        config = EditorialGovernanceConfig(prevent_historical_as_current=True)
        markdown = "Historically, performance has varied."
        claims = []

        findings = check_historical_analogies(markdown, claims, config)
        assert len(findings) == 0

    def test_disabled_check_passes(self):
        """Disabled historical check passes."""
        config = EditorialGovernanceConfig(prevent_historical_as_current=False)
        markdown = "Historically, this week's performance has been strong."
        claims = []

        findings = check_historical_analogies(markdown, claims, config)
        assert len(findings) == 0


class TestMustPreserveFacts:
    """Test must-preserve fact checks."""

    def test_preserved_fact_passes(self):
        """Must-preserve fact that is preserved passes."""
        config = EditorialGovernanceConfig(track_must_preserve_facts=True)
        original = Claim(
            claim_id="C1",
            statement="Critical fact",
            source_id="S1",
            evidence_text="Evidence",
            claim_type="fact",
            metadata={"must_preserve": True},
        )
        current = [original]

        findings = check_must_preserve_facts([original], current, config)
        assert len(findings) == 0

    def test_removed_fact_triggers_finding(self):
        """Removed must-preserve fact triggers finding."""
        config = EditorialGovernanceConfig(track_must_preserve_facts=True)
        original = Claim(
            claim_id="C1",
            statement="Critical fact",
            source_id="S1",
            evidence_text="Evidence",
            claim_type="fact",
            metadata={"must_preserve": True},
        )
        current = []  # Fact removed

        findings = check_must_preserve_facts([original], current, config)
        assert len(findings) == 1
        assert "EDITORIAL_MUST_PRESERVE_REMOVED" in findings[0].finding_id
        assert findings[0].severity == "high"

    def test_disabled_check_passes(self):
        """Disabled must-preserve check passes."""
        config = EditorialGovernanceConfig(track_must_preserve_facts=False)
        original = Claim(
            claim_id="C1",
            statement="Critical fact",
            source_id="S1",
            evidence_text="Evidence",
            claim_type="fact",
            metadata={"must_preserve": True},
        )
        current = []

        findings = check_must_preserve_facts([original], current, config)
        assert len(findings) == 0


class TestRunEditorialGovernanceChecks:
    """Test run_editorial_governance_checks function."""

    def test_all_checks_run(self):
        """All governance checks are run."""
        markdown = "Test markdown with some content."
        ledger = ClaimLedger()

        report = run_editorial_governance_checks(markdown, ledger)
        assert report.audit_status in ("pass", "warning", "fail")
        assert "governance_protocol" in report.metadata
        assert len(report.metadata["checks_run"]) == 5

    def test_passing_content(self):
        """Content that passes all checks."""
        # Create content with enough claims to pass density check
        # 10 claims in 1000 chars = 10.0 density (threshold is 2.0)
        markdown = "x" * 1000
        ledger = ClaimLedger()
        for i in range(10):
            ledger.add_claim(Claim(
                claim_id=f"C{i}",
                statement=f"Claim {i}",
                source_id="S1",
                evidence_text="Evidence",
                claim_type="fact",
            ))

        report = run_editorial_governance_checks(markdown, ledger)
        # Should pass with high density and no advice/analogies
        assert report.audit_status == "pass"
        assert report.audit_score == 100

    def test_failing_content(self):
        """Content that fails checks."""
        markdown = "Companies should invest immediately."
        ledger = ClaimLedger()

        report = run_editorial_governance_checks(markdown, ledger)
        assert report.audit_status in ("warning", "fail")
        assert len(report.findings) > 0
