"""Contracts package — schema definitions, validators, and migrations."""

from multi_agent_brief.contracts.base import Contract, SchemaRegistry
from multi_agent_brief.contracts.errors import ContractError, FieldViolation
from multi_agent_brief.contracts.registry import (
    ArtifactContract,
    ContractRegistry,
    StageSpec,
)
from multi_agent_brief.contracts.schemas import (
    AuditReportContract,
    AtomicClaimGraphContract,
    CandidateItemContract,
    ClaimDraftContract,
    ClaimContract,
    SourceItemContract,
)
from multi_agent_brief.contracts.migrations import migrate_claim_v1_to_v2

__all__ = [
    "Contract",
    "SchemaRegistry",
    "ContractError",
    "FieldViolation",
    "ArtifactContract",
    "ContractRegistry",
    "StageSpec",
    "AuditReportContract",
    "AtomicClaimGraphContract",
    "CandidateItemContract",
    "ClaimDraftContract",
    "ClaimContract",
    "SourceItemContract",
    "migrate_claim_v1_to_v2",
]
