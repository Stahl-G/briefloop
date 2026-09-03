"""Boundary tests for the provider-less Semantic Support Auditor prompt contract.

These tests lock the BriefLoop-native prompt contract: the prompt forbids
external knowledge and inferring missing sources, and Python never calls an
LLM provider.
"""

from __future__ import annotations

from multi_agent_brief.audit.semantic import (
    SEMANTIC_SUPPORT_CALIBRATION_METADATA_KEY,
    SemanticAuditPromptBuilder,
    semantic_support_proposal_finding,
)
from multi_agent_brief.core.claim_ledger import ClaimLedger
from multi_agent_brief.core.schemas import Claim


def _ledger() -> ClaimLedger:
    return ClaimLedger(
        [
            Claim("CL-0001", "Revenue rose 4% in Q2.", "SRC-001", "Q2 revenue was up 4% YoY."),
        ]
    )


def _graph() -> dict:
    return {
        "schema_version": "mabw.atomic_claim_graph.v1",
        "claims": [
            {
                "claim_id": "CL-0001",
                "statement": "Revenue rose 4% in Q2.",
                "atoms": [
                    {
                        "atom_id": "AC-0001-01",
                        "text": "Q2 revenue was up 4% year over year.",
                        "claim_role": "numeric_fact",
                        "materiality": "high",
                    }
                ],
            }
        ],
    }


def _registry() -> dict:
    return {
        "schema_version": "mabw.evidence_span_registry.v1",
        "sources": [
            {
                "source_id": "SRC-001",
                "spans": [
                    {
                        "span_id": "ESP-001-01",
                        "raw_excerpt": "Q2 revenue was up 4% YoY.",
                        "span_role": "numeric_observation",
                    }
                ],
            }
        ],
    }


def _prompt() -> str:
    return SemanticAuditPromptBuilder().build_prompt(
        "- Revenue rose 4% [src:SRC-001]",
        _ledger(),
        atomic_claim_graph=_graph(),
        evidence_span_registry=_registry(),
    )


class TestSemanticAuditPromptContract:
    def test_prompt_forbids_external_knowledge(self):
        prompt = _prompt().lower()
        assert "external knowledge" in prompt
        # No permission to bring outside knowledge in.
        assert "do not use external knowledge" in prompt
        assert "do not infer missing sources" in prompt


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
