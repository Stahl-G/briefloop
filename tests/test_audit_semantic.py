"""Boundary tests for the provider-less Semantic Support Auditor prompt contract.

v0.11.12 PR1. These tests lock the BriefLoop-native prompt contract:

- the prompt compares the reader draft against the frozen Claim Ledger;
- it forbids rewrite/repair/prose improvement;
- it forbids external knowledge and inferring missing sources;
- it asks for JSON only, shaped as a semantic_assessment_report;
- the placeholder agent stays not-configured, never a real pass;
- Python never calls an LLM provider.
"""

from __future__ import annotations

import inspect

from multi_agent_brief.audit.semantic import (
    SEMANTIC_SUPPORT_CALIBRATION_METADATA_KEY,
    SEMANTIC_SUPPORT_PROPOSAL_FINDING_TYPE,
    SEMANTIC_SUPPORT_PROPOSAL_LABELS,
    NoOpSemanticAuditAgent,
    SemanticAuditPromptBuilder,
    findings_from_semantic_proposal_rows,
    semantic_support_proposal_finding,
)
from multi_agent_brief.contracts.schemas.semantic_assessment_report import (
    SEMANTIC_ASSESSMENT_REPORT_SCHEMA_VERSION,
)
from multi_agent_brief.contracts.schemas.claim_support_matrix import VALID_SUPPORT_LABELS
from multi_agent_brief.core.claim_ledger import ClaimLedger
from multi_agent_brief.core.schemas import Claim


def _ledger() -> ClaimLedger:
    return ClaimLedger(
        [
            Claim("CL-src", "Revenue rose 4% in Q2.", "SRC-1", "Q2 revenue was up 4% YoY."),
        ]
    )


def _prompt() -> str:
    return SemanticAuditPromptBuilder().build_prompt("- Revenue rose 4% [src:SRC-1]", _ledger())


class TestSemanticAuditPromptContract:
    def test_prompt_references_frozen_claim_ledger(self):
        prompt = _prompt().lower()
        assert "claim ledger" in prompt
        assert "frozen" in prompt

    def test_prompt_forbids_external_knowledge(self):
        prompt = _prompt().lower()
        assert "external knowledge" in prompt
        # No permission to bring outside knowledge in.
        assert "do not use external knowledge" in prompt
        assert "do not infer missing sources" in prompt

    def test_prompt_forbids_rewrite_and_repair(self):
        prompt = _prompt().lower()
        assert "do not rewrite" in prompt
        assert "do not repair" in prompt
        assert "do not improve" in prompt

    def test_prompt_requires_json_only(self):
        prompt = _prompt().lower()
        assert "json only" in prompt

    def test_prompt_preserves_uncertainty_and_scope(self):
        prompt = _prompt().lower()
        for term in ("uncertainty", "limitation", "scope", "date", "source strength"):
            assert term in prompt, f"prompt must ask to preserve {term!r}"

    def test_prompt_binds_findings_to_claim_atom_or_span(self):
        prompt = _prompt().lower()
        assert "claim" in prompt and "atom" in prompt and "span" in prompt
        # Explicit unmatched-text fallback when no artifact id matches.
        assert "unmatched" in prompt

    def test_prompt_declares_response_shape_contract(self):
        prompt = _prompt()
        assert SEMANTIC_ASSESSMENT_REPORT_SCHEMA_VERSION in prompt
        # Required row fields the runtime auditor must produce.
        for field in ("row_id", "claim_id", "atom_id", "proposed_support_label", "rationale"):
            assert field in prompt, f"response contract must mention {field!r}"

    def test_prompt_lists_support_and_proposal_vocabulary(self):
        prompt = _prompt()
        # Support labels come straight from the CSM contract (single source of truth).
        for label in VALID_SUPPORT_LABELS:
            assert label in prompt, f"prompt must expose support label {label!r}"
        # Calibration labels come from the module constant.
        for label in SEMANTIC_SUPPORT_PROPOSAL_LABELS:
            assert label in prompt

    def test_prompt_declares_proposal_only_boundary(self):
        prompt = _prompt().lower()
        assert "proposal" in prompt
        # Must not claim authority to gate, deliver, or release.
        assert "not a gate" in prompt or "not release authority" in prompt


class TestNoOpSemanticAuditAgentStaysUnconfigured:
    def test_status_is_not_configured_not_a_real_pass(self):
        report = NoOpSemanticAuditAgent().run_audit("draft", _ledger())
        assert report.metadata.get("semantic_status") == "not_configured"
        assert report.findings == []


def _projected_row(**overrides):
    """A projected proposal row, matching project_semantic_assessment_proposals output."""
    row = {
        "proposal_id": "SAR-0001",
        "source_row_id": "SAR-0001",
        "claim_id": "CL-0001",
        "atom_id": "AC-0001-01",
        "evidence_span_id": "ESP-001-01",
        "candidate_evidence_span_ids": [],
        "relation_status": "single_span",
        "proposed_support_label": "partial_support",
        "proposed_support_reason": "Span supports activity but not the acceleration wording.",
        "confidence": 0.72,
        "uncertainty": "medium",
        "disagreement": "none",
        "requires_human_adjudication": False,
        "assessor_id": "ASR-001",
        "assessor_label": "Reviewer A",
        "assessment_method": "llm_assisted_human",
        "accepted_support_truth": False,
        "writes_claim_support_matrix": False,
        "metadata": {SEMANTIC_SUPPORT_CALIBRATION_METADATA_KEY: "overstated_claim"},
    }
    row.update(overrides)
    return row


class TestSemanticSupportProposalFindingAdapter:
    def test_valid_row_converts_to_advisory_finding(self):
        finding = semantic_support_proposal_finding(_projected_row())
        assert finding.finding_type == SEMANTIC_SUPPORT_PROPOSAL_FINDING_TYPE
        assert finding.finding_id == "SAR-0001"
        assert finding.related_claim_id == "CL-0001"
        # Proposal findings are advisory: never a blocking severity or blocking level.
        assert finding.severity == "low"
        assert not finding.blocking_level.endswith("_blocking")
        assert "proposal" in finding.recommendation.lower()

    def test_llm_only_row_does_not_become_blocking(self):
        finding = semantic_support_proposal_finding(
            _projected_row(
                assessment_method="llm_only",
                requires_human_adjudication=True,
                uncertainty="high",
                proposed_support_label="unsupported",
            )
        )
        assert finding.severity == "low"
        assert not finding.blocking_level.endswith("_blocking")
        # Human adjudication requirement must be surfaced, not silently dropped.
        assert "adjudication" in finding.recommendation.lower()

    def test_finding_preserves_proposal_metadata(self):
        finding = semantic_support_proposal_finding(
            _projected_row(assessment_method="llm_only", confidence=0.4)
        )
        blob = f"{finding.description}\n{finding.evidence}".lower()
        assert "llm_only" in blob
        assert "overstated_claim" in blob  # calibration label from metadata
        assert "partial_support" in blob  # proposed support label
        assert "0.4" in blob  # confidence preserved

    def test_rows_adapter_maps_all_valid_rows(self):
        findings = findings_from_semantic_proposal_rows(
            [_projected_row(proposal_id="SAR-0001"), _projected_row(proposal_id="SAR-0002")]
        )
        assert [f.finding_id for f in findings] == ["SAR-0001", "SAR-0002"]
        assert all(f.finding_type == SEMANTIC_SUPPORT_PROPOSAL_FINDING_TYPE for f in findings)

    def test_rows_adapter_skips_non_mapping_rows(self):
        findings = findings_from_semantic_proposal_rows([_projected_row(), "junk", None])
        assert len(findings) == 1


class TestPromptBuilderProviderLess:
    def test_no_provider_calls_in_module_source(self):
        import multi_agent_brief.audit.semantic as semantic_module

        source = inspect.getsource(semantic_module)
        for banned in (
            "provider.call",
            "openai",
            "anthropic",
            "requests.post",
            "httpx",
            "urllib.request",
        ):
            assert banned not in source, f"provider-less rule violated: {banned!r}"
