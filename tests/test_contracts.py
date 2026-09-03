"""Tests for Contracts Package — schema registry, validation, and migration."""

from __future__ import annotations

from multi_agent_brief.contracts.base import SchemaRegistry
from multi_agent_brief.contracts.schemas.atomic_claim_graph import AtomicClaimGraphContract
from multi_agent_brief.contracts.schemas.claim_draft import ClaimDraftContract
from multi_agent_brief.contracts.schemas.claim import ClaimContract
from multi_agent_brief.contracts.schemas.claim_support_matrix import ClaimSupportMatrixContract
from multi_agent_brief.contracts.schemas.evidence_span_registry import EvidenceSpanRegistryContract
from multi_agent_brief.contracts.schemas.semantic_assessment_report import SemanticAssessmentReportContract


# ── SchemaRegistry ──


class TestSchemaRegistry:
    def test_register_and_get(self):
        assert SchemaRegistry.get("claim") is ClaimContract
        assert SchemaRegistry.get("claim_drafts") is ClaimDraftContract
        assert SchemaRegistry.get("claim_support_matrix") is ClaimSupportMatrixContract
        assert SchemaRegistry.get("atomic_claim_graph") is AtomicClaimGraphContract
        assert SchemaRegistry.get("evidence_span_registry") is EvidenceSpanRegistryContract
        assert SchemaRegistry.get("semantic_assessment_report") is SemanticAssessmentReportContract


class TestClaimContract:
    def test_v2_claim_passes(self):
        data = {
            "claim_id": "X", "statement": "s", "source_id": "S",
            "evidence_text": "e", "claim_type": "fact",
            "schema_version": "v2", "epistemic_type": "observed",
            "evidence_relation": "direct",
        }
        assert ClaimContract.is_valid(data)

    def test_malformed_source_url_returns_violation(self):
        data = {
            "claim_id": "X",
            "statement": "s",
            "source_id": "S",
            "evidence_text": "e",
            "source_url": "https://[::1",
        }

        violations = ClaimContract.validate(data)

        assert any(v.field == "source_url" for v in violations)
