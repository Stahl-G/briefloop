"""Tests for Policy & Regulatory Risk Module audit checks."""

from __future__ import annotations

import pytest

from multi_agent_brief.analysis_modules.policy_regulatory.audit import (
    audit_policy_events,
)
from multi_agent_brief.analysis_modules.policy_regulatory.schemas import (
    ApplicabilityQuestion,
    PolicyEvidencePack,
    PolicyEvent,
    RiskItem,
)


class TestAuditPolicyEvents:
    """Test audit_policy_events function."""

    def test_no_events_no_findings(self):
        """Empty evidence pack produces no findings."""
        pack = PolicyEvidencePack()
        findings = audit_policy_events(pack)
        assert findings == []

    def test_source_missing_finding(self):
        """Event without source_refs produces warning."""
        event = PolicyEvent(
            event_id="POLICY_0001",
            jurisdiction="US",
            authority="SEC",
            instrument_name="Test Rule",
            source_refs=[],
        )
        pack = PolicyEvidencePack(events=[event])
        findings = audit_policy_events(pack)

        source_findings = [f for f in findings if "SOURCE_MISSING" in f.finding_id]
        assert len(source_findings) == 1
        assert source_findings[0].severity == "warning"

    def test_effective_date_missing_finding(self):
        """Event without effective_date produces warning."""
        event = PolicyEvent(
            event_id="POLICY_0001",
            jurisdiction="US",
            authority="SEC",
            instrument_name="Test Rule",
            effective_date="",
            source_refs=["src_001"],
        )
        pack = PolicyEvidencePack(events=[event])
        findings = audit_policy_events(pack)

        date_findings = [f for f in findings if "EFFECTIVE_DATE_MISSING" in f.finding_id]
        assert len(date_findings) == 1
        assert date_findings[0].severity == "warning"

    def test_jurisdiction_missing_finding(self):
        """Event without jurisdiction produces high severity finding."""
        event = PolicyEvent(
            event_id="POLICY_0001",
            jurisdiction="",
            authority="SEC",
            instrument_name="Test Rule",
            source_refs=["src_001"],
        )
        pack = PolicyEvidencePack(events=[event])
        findings = audit_policy_events(pack)

        jurisdiction_findings = [f for f in findings if "JURISDICTION_MISSING" in f.finding_id]
        assert len(jurisdiction_findings) == 1
        assert jurisdiction_findings[0].severity == "high"
        assert jurisdiction_findings[0].blocking_level == "analyst_blocking"

    def test_applicability_overclaim_finding(self):
        """Risk with CONFIRMED status but HYPOTHESIS event produces high severity finding."""
        event = PolicyEvent(
            event_id="POLICY_0001",
            jurisdiction="US",
            authority="SEC",
            instrument_name="Test Rule",
            source_refs=["src_001"],
            epistemic_type="HYPOTHESIS",
        )
        risk = RiskItem(
            risk_id="RISK_0001",
            event_id="POLICY_0001",
            risk_type="compliance",
            severity="medium",
            likelihood="possible",
            applicability_status="CONFIRMED",
        )
        pack = PolicyEvidencePack(events=[event], risks=[risk])
        findings = audit_policy_events(pack)

        overclaim_findings = [f for f in findings if "APPLICABILITY_OVERCLAIM" in f.finding_id]
        assert len(overclaim_findings) == 1
        assert overclaim_findings[0].severity == "high"

    def test_compliance_advice_no_basis_finding(self):
        """Compliance risk with mitigation but no sources produces high severity finding."""
        event = PolicyEvent(
            event_id="POLICY_0001",
            jurisdiction="US",
            authority="SEC",
            instrument_name="Test Rule",
            source_refs=[],
        )
        risk = RiskItem(
            risk_id="RISK_0001",
            event_id="POLICY_0001",
            risk_type="compliance",
            severity="medium",
            likelihood="possible",
            mitigation_notes="Update procedures",
            source_refs=[],
        )
        pack = PolicyEvidencePack(events=[event], risks=[risk])
        findings = audit_policy_events(pack)

        advice_findings = [f for f in findings if "COMPLIANCE_ADVICE_NO_BASIS" in f.finding_id]
        assert len(advice_findings) == 1
        assert advice_findings[0].severity == "high"

    def test_critical_unconfirmed_finding(self):
        """Critical risk with non-CONFIRMED status produces warning."""
        risk = RiskItem(
            risk_id="RISK_0001",
            event_id="POLICY_0001",
            risk_type="compliance",
            severity="critical",
            likelihood="likely",
            applicability_status="TO_VERIFY",
        )
        pack = PolicyEvidencePack(risks=[risk])
        findings = audit_policy_events(pack)

        critical_findings = [f for f in findings if "CRITICAL_UNCONFIRMED" in f.finding_id]
        assert len(critical_findings) == 1
        assert critical_findings[0].severity == "warning"

    def test_high_priority_question_finding(self):
        """High priority applicability question produces low severity finding."""
        question = ApplicabilityQuestion(
            question_id="Q_0001",
            event_id="POLICY_0001",
            question="Is this applicable?",
            priority="high",
        )
        pack = PolicyEvidencePack(applicability_questions=[question])
        findings = audit_policy_events(pack)

        question_findings = [f for f in findings if "OPEN_QUESTION" in f.finding_id]
        assert len(question_findings) == 1
        assert question_findings[0].severity == "low"

    def test_valid_event_no_findings(self):
        """Valid event with all fields produces no findings."""
        event = PolicyEvent(
            event_id="POLICY_0001",
            jurisdiction="US",
            authority="SEC",
            instrument_name="Test Rule",
            effective_date="2030-01-01",  # Future date
            source_refs=["src_001"],
            epistemic_type="FACT",
        )
        pack = PolicyEvidencePack(events=[event])
        findings = audit_policy_events(pack)

        # No findings for valid event
        assert len(findings) == 0
