"""Contract schemas for core data objects."""

from multi_agent_brief.contracts.schemas.analysis_pack import (
    AnalysisCardContract,
    MarketEventContract,
)
from multi_agent_brief.contracts.schemas.atomic_claim_graph import AtomicClaimGraphContract
from multi_agent_brief.contracts.schemas.audit_report import AuditReportContract
from multi_agent_brief.contracts.schemas.candidate_item import CandidateItemContract
from multi_agent_brief.contracts.schemas.claim_draft import ClaimDraftContract
from multi_agent_brief.contracts.schemas.claim import ClaimContract
from multi_agent_brief.contracts.schemas.claim_support_matrix import ClaimSupportMatrixContract
from multi_agent_brief.contracts.schemas.source_item import SourceItemContract
from multi_agent_brief.contracts.schemas.evidence_span_registry import EvidenceSpanRegistryContract
from multi_agent_brief.contracts.schemas.semantic_assessment_report import SemanticAssessmentReportContract

__all__ = [
    "AnalysisCardContract",
    "AtomicClaimGraphContract",
    "AuditReportContract",
    "CandidateItemContract",
    "ClaimDraftContract",
    "ClaimContract",
    "ClaimSupportMatrixContract",
    "EvidenceSpanRegistryContract",
    "MarketEventContract",
    "SemanticAssessmentReportContract",
    "SourceItemContract",
]
