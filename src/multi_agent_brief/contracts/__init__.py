"""Contracts package — schema definitions, validators, and migrations."""

from multi_agent_brief.contracts.base import Contract, SchemaRegistry
from multi_agent_brief.contracts.errors import ContractError, FieldViolation
from multi_agent_brief.contracts.schemas import (
    AuditReportContract,
    CandidateItemContract,
    ClaimContract,
    SourceItemContract,
)
from multi_agent_brief.contracts.migrations import migrate_claim_v1_to_v2

__all__ = [
    "Contract",
    "SchemaRegistry",
    "ContractError",
    "FieldViolation",
    "AuditReportContract",
    "CandidateItemContract",
    "ClaimContract",
    "SourceItemContract",
    "migrate_claim_v1_to_v2",
]
