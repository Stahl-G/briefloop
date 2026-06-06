"""Tests for Policy & Regulatory Risk Module."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

# Import registry first to trigger auto-registration
from multi_agent_brief.analysis_modules.registry import (
    MODULE_REGISTRY,
    load_enabled_modules,
    register_module,
)

from multi_agent_brief.analysis_modules.policy_regulatory.module import (
    PolicyRegulatoryModule,
)
from multi_agent_brief.analysis_modules.policy_regulatory.schemas import (
    ApplicabilityQuestion,
    PolicyCoverageReport,
    PolicyEvidencePack,
    PolicyEvent,
    RiskItem,
)


@pytest.fixture(autouse=True)
def _ensure_policy_regulatory_registered():
    """Ensure policy_regulatory module is registered for each test."""
    if "policy_regulatory" not in MODULE_REGISTRY:
        register_module("policy_regulatory", PolicyRegulatoryModule)
    yield


class TestPolicyRegulatoryModule:
    """Test PolicyRegulatoryModule."""

    def test_module_name(self):
        """Module has correct name."""
        module = PolicyRegulatoryModule()
        assert module.name == "policy_regulatory"

    def test_validate_config_empty(self):
        """Empty config is valid."""
        module = PolicyRegulatoryModule()
        errors = module.validate_config({})
        assert errors == []

    def test_validate_config_valid_risk_types(self):
        """Valid risk_types config passes."""
        module = PolicyRegulatoryModule()
        errors = module.validate_config({
            "risk_types": ["compliance", "operational"]
        })
        assert errors == []

    def test_validate_config_invalid_risk_types(self):
        """Invalid risk_types config fails."""
        module = PolicyRegulatoryModule()
        errors = module.validate_config({
            "risk_types": "not_a_list"
        })
        assert len(errors) == 1
        assert "must be a list" in errors[0]

    def test_validate_config_unknown_risk_type(self):
        """Unknown risk type fails."""
        module = PolicyRegulatoryModule()
        errors = module.validate_config({
            "risk_types": ["compliance", "unknown_type"]
        })
        assert len(errors) == 1
        assert "Unknown risk type" in errors[0]

    def test_registry_auto_registration(self):
        """Module is auto-registered in registry."""
        assert "policy_regulatory" in MODULE_REGISTRY
        assert MODULE_REGISTRY["policy_regulatory"] == PolicyRegulatoryModule

    def test_load_enabled_modules(self):
        """Module loads when enabled in config."""
        config = {
            "modules": {
                "policy_regulatory": {
                    "enabled": True,
                }
            }
        }
        modules = load_enabled_modules(config)
        assert len(modules) == 1
        assert modules[0].name == "policy_regulatory"

    def test_load_disabled_modules(self):
        """Module does not load when disabled."""
        config = {
            "modules": {
                "policy_regulatory": {
                    "enabled": False,
                }
            }
        }
        modules = load_enabled_modules(config)
        assert len(modules) == 0

    def test_load_no_modules_config(self):
        """No modules loads when config has no modules section."""
        modules = load_enabled_modules({})
        assert len(modules) == 0

    def test_load_none_config(self):
        """None config returns empty list."""
        modules = load_enabled_modules(None)
        assert len(modules) == 0


class TestPolicyEvent:
    """Test PolicyEvent schema."""

    def test_to_dict(self):
        """to_dict returns correct structure."""
        event = PolicyEvent(
            event_id="POLICY_0001",
            jurisdiction="US",
            authority="SEC",
            instrument_name="New Disclosure Rule",
            publication_date="2024-01-15",
            effective_date="2024-07-01",
            affected_entities=["public_companies"],
            core_change="Enhanced disclosure requirements",
            compliance_deadline="2024-12-31",
            source_refs=["src_001"],
            limitations=["Limited to US-listed companies"],
            epistemic_type="FACT",
        )
        d = event.to_dict()
        assert d["event_id"] == "POLICY_0001"
        assert d["jurisdiction"] == "US"
        assert d["authority"] == "SEC"
        assert d["epistemic_type"] == "FACT"


class TestRiskItem:
    """Test RiskItem schema."""

    def test_to_dict(self):
        """to_dict returns correct structure."""
        risk = RiskItem(
            risk_id="RISK_0001",
            event_id="POLICY_0001",
            risk_type="compliance",
            severity="high",
            likelihood="likely",
            affected_entities=["company_a"],
            mitigation_notes="Update disclosure procedures",
            applicability_status="CONFIRMED",
            source_refs=["src_001"],
        )
        d = risk.to_dict()
        assert d["risk_id"] == "RISK_0001"
        assert d["risk_type"] == "compliance"
        assert d["severity"] == "high"
        assert d["applicability_status"] == "CONFIRMED"


class TestApplicabilityQuestion:
    """Test ApplicabilityQuestion schema."""

    def test_to_dict(self):
        """to_dict returns correct structure."""
        question = ApplicabilityQuestion(
            question_id="Q_0001",
            event_id="POLICY_0001",
            question="Does the new rule apply to private companies?",
            context="The rule mentions public companies explicitly.",
            priority="high",
        )
        d = question.to_dict()
        assert d["question_id"] == "Q_0001"
        assert d["priority"] == "high"


class TestPolicyEvidencePack:
    """Test PolicyEvidencePack schema."""

    def test_to_dict_empty(self):
        """Empty pack to_dict returns correct structure."""
        pack = PolicyEvidencePack()
        d = pack.to_dict()
        assert d["events"] == []
        assert d["risks"] == []
        assert d["applicability_questions"] == []

    def test_to_dict_with_data(self):
        """Pack with data to_dict returns correct structure."""
        event = PolicyEvent(
            event_id="POLICY_0001",
            jurisdiction="US",
            authority="SEC",
            instrument_name="Test Rule",
        )
        risk = RiskItem(
            risk_id="RISK_0001",
            event_id="POLICY_0001",
            risk_type="compliance",
            severity="medium",
            likelihood="possible",
        )
        question = ApplicabilityQuestion(
            question_id="Q_0001",
            event_id="POLICY_0001",
            question="Test question?",
        )
        pack = PolicyEvidencePack(
            events=[event],
            risks=[risk],
            applicability_questions=[question],
            metadata={"test": True},
        )
        d = pack.to_dict()
        assert len(d["events"]) == 1
        assert len(d["risks"]) == 1
        assert len(d["applicability_questions"]) == 1
        assert d["metadata"]["test"] is True


class TestPolicyCoverageReport:
    """Test PolicyCoverageReport schema."""

    def test_to_dict(self):
        """to_dict returns correct structure."""
        report = PolicyCoverageReport(
            total_events=5,
            jurisdictions_covered=["US", "EU"],
            authorities_covered=["SEC", "ESMA"],
            risk_types_covered=["compliance", "operational"],
            coverage_gaps=["No Asia-Pacific coverage"],
            metadata={"test": True},
        )
        d = report.to_dict()
        assert d["total_events"] == 5
        assert len(d["jurisdictions_covered"]) == 2
        assert len(d["coverage_gaps"]) == 1
