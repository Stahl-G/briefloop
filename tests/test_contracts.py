"""Tests for Contracts Package — schema registry, validation, and migration."""

from __future__ import annotations

import pytest

from multi_agent_brief.contracts.base import Contract, SchemaRegistry
from multi_agent_brief.contracts.errors import ContractError, FieldViolation
from multi_agent_brief.contracts.schemas.source_item import SourceItemContract
from multi_agent_brief.contracts.schemas.candidate_item import CandidateItemContract
from multi_agent_brief.contracts.schemas.claim_draft import ClaimDraftContract
from multi_agent_brief.contracts.schemas.claim import ClaimContract
from multi_agent_brief.contracts.schemas.audit_report import AuditReportContract
from multi_agent_brief.contracts.schemas.analysis_pack import (
    MarketEventContract,
    AnalysisCardContract,
)
from multi_agent_brief.contracts.migrations.claim_v1_to_v2 import migrate_claim_v1_to_v2


# ── SchemaRegistry ──


class TestSchemaRegistry:
    def test_register_and_get(self):
        assert SchemaRegistry.get("source_item") is SourceItemContract
        assert SchemaRegistry.get("claim") is ClaimContract
        assert SchemaRegistry.get("claim_drafts") is ClaimDraftContract

    def test_get_unknown_returns_none(self):
        assert SchemaRegistry.get("nonexistent") is None

    def test_validate_unknown_schema_raises(self):
        with pytest.raises(KeyError, match="Unknown schema"):
            SchemaRegistry.validate("nonexistent", {})


# ── SourceItemContract ──


class TestSourceItemContract:
    def test_valid_source_passes(self):
        data = {
            "source_id": "S1", "source_name": "Test",
            "source_type": "local_file", "title": "T", "content": "C",
        }
        assert SourceItemContract.is_valid(data)
        assert SourceItemContract.validate(data) == []

    def test_missing_required_field_fails(self):
        data = {"source_id": "S1"}  # missing source_name, source_type, title, content
        violations = SourceItemContract.validate(data)
        error_fields = [v.field for v in violations if v.severity == "error"]
        assert "source_name" in error_fields
        assert "source_type" in error_fields

    def test_unknown_field_warning(self):
        data = {
            "source_id": "S1", "source_name": "Test",
            "source_type": "local_file", "title": "T", "content": "C",
            "custom_field": "value",
        }
        violations = SourceItemContract.validate(data)
        warnings = [v for v in violations if v.severity == "warning"]
        assert any(v.field == "custom_field" for v in warnings)


# ── CandidateItemContract ──


class TestCandidateItemContract:
    def test_valid_candidate_passes(self):
        data = {"item_id": "C1", "title": "T", "summary": "S", "source_id": "S1"}
        assert CandidateItemContract.is_valid(data)

    def test_empty_title_fails(self):
        data = {"item_id": "C1", "title": "", "summary": "S", "source_id": "S1"}
        violations = CandidateItemContract.validate(data)
        assert any(v.field == "title" for v in violations)


# ── ClaimContract ──


class TestClaimContract:
    def test_v1_claim_passes(self):
        data = {
            "claim_id": "X", "statement": "s", "source_id": "S",
            "evidence_text": "e", "claim_type": "fact",
        }
        assert ClaimContract.is_valid(data)

    def test_v2_claim_passes(self):
        data = {
            "claim_id": "X", "statement": "s", "source_id": "S",
            "evidence_text": "e", "claim_type": "fact",
            "schema_version": "v2", "epistemic_type": "observed",
            "evidence_relation": "direct",
        }
        assert ClaimContract.is_valid(data)

    def test_invalid_claim_type_fails(self):
        data = {
            "claim_id": "X", "statement": "s", "source_id": "S",
            "evidence_text": "e", "claim_type": "invalid_type",
        }
        violations = ClaimContract.validate(data)
        assert any(v.field == "claim_type" for v in violations)

    def test_invalid_confidence_fails(self):
        data = {
            "claim_id": "X", "statement": "s", "source_id": "S",
            "evidence_text": "e", "confidence": "certain",
        }
        violations = ClaimContract.validate(data)
        assert any(v.field == "confidence" for v in violations)


# ── ClaimDraftContract ──


class TestClaimDraftContract:
    def test_valid_claim_drafts_pass(self):
        data = {
            "schema_version": "mabw.claim_drafts.v1",
            "drafts": [
                {
                    "statement": "ExampleCo opened a demo facility.",
                    "source_id": "SRC-001",
                    "evidence_text": "Example evidence.",
                    "claim_type": "fact",
                    "confidence": "medium",
                }
            ],
            "metadata": {"created_by": "test"},
        }

        assert ClaimDraftContract.is_valid(data)
        assert ClaimDraftContract.validate(data) == []

    def test_claim_drafts_reject_claim_id(self):
        data = {
            "schema_version": "mabw.claim_drafts.v1",
            "drafts": [
                {
                    "claim_id": "CL-001",
                    "statement": "ExampleCo opened a demo facility.",
                    "source_id": "SRC-001",
                    "evidence_text": "Example evidence.",
                }
            ],
        }

        violations = ClaimDraftContract.validate(data)

        assert any(v.field == "drafts[0].claim_id" for v in violations)
        assert not ClaimDraftContract.is_valid(data)

    def test_claim_drafts_reject_claim_id_in_metadata(self):
        data = {
            "schema_version": "mabw.claim_drafts.v1",
            "drafts": [
                {
                    "statement": "ExampleCo opened a demo facility.",
                    "source_id": "SRC-001",
                    "evidence_text": "Example evidence.",
                    "metadata": {"claim_id": "CL-001"},
                }
            ],
        }

        violations = ClaimDraftContract.validate(data)

        assert any(v.field == "drafts[0].metadata.claim_id" for v in violations)
        assert not ClaimDraftContract.is_valid(data)

    def test_claim_drafts_reject_non_string_required_fields(self):
        data = {
            "schema_version": "mabw.claim_drafts.v1",
            "drafts": [
                {
                    "statement": 123,
                    "source_id": ["SRC-001"],
                    "evidence_text": {"text": "Example evidence."},
                }
            ],
        }

        violations = ClaimDraftContract.validate(data)

        error_fields = {violation.field for violation in violations if violation.severity == "error"}
        assert {
            "drafts[0].statement",
            "drafts[0].source_id",
            "drafts[0].evidence_text",
        } <= error_fields
        assert not ClaimDraftContract.is_valid(data)

    def test_claim_drafts_reject_optional_field_type_mismatches(self):
        data = {
            "schema_version": "mabw.claim_drafts.v1",
            "drafts": [
                {
                    "statement": "ExampleCo opened a demo facility.",
                    "source_id": "SRC-001",
                    "evidence_text": "Example evidence.",
                    "requires_audit": "yes",
                    "used_in_sections": ["summary", 3],
                    "limitations": "none",
                    "metadata": [],
                }
            ],
        }

        violations = ClaimDraftContract.validate(data)

        error_fields = {violation.field for violation in violations if violation.severity == "error"}
        assert {
            "drafts[0].requires_audit",
            "drafts[0].used_in_sections[1]",
            "drafts[0].limitations",
            "drafts[0].metadata",
        } <= error_fields

    def test_claim_drafts_do_not_semantically_dedupe(self):
        draft = {
            "statement": "ExampleCo opened a demo facility.",
            "source_id": "SRC-001",
            "evidence_text": "Example evidence.",
        }
        data = {
            "schema_version": "mabw.claim_drafts.v1",
            "drafts": [dict(draft), dict(draft)],
        }

        assert ClaimDraftContract.validate(data) == []
        assert ClaimDraftContract.is_valid(data)


# ── AuditReportContract ──


class TestAuditReportContract:
    def test_valid_report_passes(self):
        data = {"audit_status": "pass", "audit_score": 100}
        assert AuditReportContract.is_valid(data)

    def test_invalid_status_fails(self):
        data = {"audit_status": "maybe", "audit_score": 50}
        violations = AuditReportContract.validate(data)
        assert any(v.field == "audit_status" for v in violations)

    def test_score_out_of_range_fails(self):
        data = {"audit_status": "pass", "audit_score": 150}
        violations = AuditReportContract.validate(data)
        assert any(v.field == "audit_score" for v in violations)


# ── AnalysisPack Contracts ──


class TestMarketEventContract:
    def test_valid_market_event_passes(self):
        data = {"event_id": "E1", "entity_ids": ["ENT1"], "event_type": "product_launch"}
        assert MarketEventContract.is_valid(data)

    def test_invalid_event_type_fails(self):
        data = {"event_id": "E1", "entity_ids": ["ENT1"], "event_type": "invalid"}
        violations = MarketEventContract.validate(data)
        assert any(v.field == "event_type" for v in violations)


class TestAnalysisCardContract:
    def test_valid_analysis_card_passes(self):
        data = {
            "analysis_id": "A1", "finding_type": "competitive_threat",
            "headline": "H", "observation": "O",
        }
        assert AnalysisCardContract.is_valid(data)

    def test_invalid_finding_type_fails(self):
        data = {
            "analysis_id": "A1", "finding_type": "invalid",
            "headline": "H", "observation": "O",
        }
        violations = AnalysisCardContract.validate(data)
        assert any(v.field == "finding_type" for v in violations)


# ── Claim Migration ──


class TestClaimMigration:
    def test_v1_to_v2_migration_produces_valid_v2(self):
        v1_data = {
            "claim_id": "X", "statement": "s", "source_id": "S",
            "evidence_text": "e", "claim_type": "forecast",
        }
        v2_data = migrate_claim_v1_to_v2(v1_data)
        assert v2_data["schema_version"] == "v2"
        assert v2_data["epistemic_type"] == "hypothesis"
        violations = ClaimContract.validate(v2_data)
        assert not any(v.severity == "error" for v in violations)

    def test_already_v2_no_migration(self):
        v2_data = {
            "claim_id": "X", "statement": "s", "source_id": "S",
            "evidence_text": "e", "claim_type": "fact",
            "schema_version": "v2", "epistemic_type": "observed",
        }
        result = ClaimContract.migrate(v2_data, "v2")
        assert result["epistemic_type"] == "observed"

    def test_unknown_version_passes_through(self):
        data = {"claim_id": "X", "statement": "s", "source_id": "S", "evidence_text": "e"}
        result = ClaimContract.migrate(data, "v99")
        assert result == data


# ── ContractError ──


class TestContractErrors:
    def test_contract_error_contains_violations(self):
        violations = [
            FieldViolation(field="x", error="missing"),
            FieldViolation(field="y", error="invalid", severity="warning"),
        ]
        err = ContractError(violations=violations, schema_id="test", schema_version="v1")
        assert err.error_count == 1
        assert err.warning_count == 1
        assert "2 violation" in str(err)

    def test_field_violation_str_representation(self):
        v = FieldViolation(field="claim_type", error="invalid value", severity="error")
        s = str(v)
        assert "[error]" in s
        assert "claim_type" in s
        assert "invalid value" in s


class TestAuditReportContractConsistency:
    """Verify that all audit agents produce AuditReport data that passes the contract."""

    def test_noop_semantic_passes_contract(self):
        from multi_agent_brief.audit.semantic import NoOpSemanticAuditAgent
        from multi_agent_brief.core.claim_ledger import ClaimLedger
        from multi_agent_brief.core.schemas import Claim

        ledger = ClaimLedger([Claim("X", "s", "S", "e")])
        report = NoOpSemanticAuditAgent().run_audit("draft", ledger)
        violations = AuditReportContract.validate(report.to_dict())
        errors = [v for v in violations if v.severity == "error"]
        assert errors == [], f"NoOp violates AuditReport contract: {errors}"

    def test_deterministic_passes_contract(self):
        from multi_agent_brief.audit.deterministic import DeterministicAuditAgent
        from multi_agent_brief.core.claim_ledger import ClaimLedger
        from multi_agent_brief.core.schemas import Claim

        ledger = ClaimLedger([Claim("X", "s", "S", "e")])
        report = DeterministicAuditAgent().run_audit("- s [src:X]", ledger)
        violations = AuditReportContract.validate(report.to_dict())
        errors = [v for v in violations if v.severity == "error"]
        assert errors == [], f"Deterministic audit violates contract: {errors}"
