"""Frozen lane and source-eligibility policy for dormant v2 intake."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

from multi_agent_brief.contracts.v2 import (
    AuditProposal,
    CandidateClaimsProposal,
    ClaimDraftsProposal,
    ScreenedCandidatesProposal,
    SourceProposal,
    StrictModel,
)


ProposalKind = Literal["candidate", "screened", "claim_drafts", "audit"]


@dataclass(frozen=True)
class LanePolicy:
    lane: str
    artifact_id: str | None
    proposal_kind: ProposalKind | None
    proposal_model: type[StrictModel]
    owners: tuple[tuple[str, str], ...]
    transaction_type: str


INTAKE_LANES = MappingProxyType(
    {
        "source": LanePolicy(
            lane="source",
            artifact_id=None,
            proposal_kind=None,
            proposal_model=SourceProposal,
            owners=(("source-discovery", "source-provider"),),
            transaction_type="source_evidence_intake",
        ),
        "candidate": LanePolicy(
            lane="candidate",
            artifact_id="candidate_claims",
            proposal_kind="candidate",
            proposal_model=CandidateClaimsProposal,
            owners=(("scout", "scout"),),
            transaction_type="candidate_claims_intake",
        ),
        "screened": LanePolicy(
            lane="screened",
            artifact_id="screened_candidates",
            proposal_kind="screened",
            proposal_model=ScreenedCandidatesProposal,
            owners=(("scout", "scout"), ("screener", "screener")),
            transaction_type="screened_candidates_intake",
        ),
        "claim-drafts": LanePolicy(
            lane="claim-drafts",
            artifact_id="claim_drafts",
            proposal_kind="claim_drafts",
            proposal_model=ClaimDraftsProposal,
            owners=(("claim-ledger", "claim-ledger"),),
            transaction_type="claim_drafts_intake",
        ),
        "audit": LanePolicy(
            lane="audit",
            artifact_id="audit_proposal",
            proposal_kind="audit",
            proposal_model=AuditProposal,
            owners=(("auditor", "auditor"),),
            transaction_type="audit_proposal_intake",
        ),
    }
)


_SOURCE_COMPATIBILITY = MappingProxyType(
    {
        ("uploaded_file", "manual_upload"): (
            frozenset({"uploaded_file"}),
            "forbidden",
            "forbidden",
        ),
        ("manual_evidence", "manual_evidence"): (
            frozenset({"full_content", "partial_extract", "dataset_snapshot"}),
            "forbidden",
            "forbidden",
        ),
        ("provider_response", "provider_search"): (
            frozenset({"search_result", "search_snippet"}),
            "required",
            "required",
        ),
        ("provider_response", "provider_extract"): (
            frozenset({"full_content", "partial_extract", "dataset_snapshot"}),
            "required",
            "required",
        ),
        ("authorized_web_fetch", "authorized_web_fetch"): (
            frozenset({"full_content", "partial_extract"}),
            "forbidden",
            "optional",
        ),
        ("cached_provider_response", "cached_provider_response"): (
            frozenset(
                {
                    "full_content",
                    "partial_extract",
                    "dataset_snapshot",
                    "search_result",
                    "search_snippet",
                }
            ),
            "required",
            "required",
        ),
        ("claim_ledger_derivative", "downstream_derivative"): (
            frozenset({"downstream_derivative"}),
            "forbidden",
            "forbidden",
        ),
        ("claim_draft_derivative", "downstream_derivative"): (
            frozenset({"downstream_derivative"}),
            "forbidden",
            "forbidden",
        ),
        ("brief_derivative", "downstream_derivative"): (
            frozenset({"downstream_derivative"}),
            "forbidden",
            "forbidden",
        ),
        ("audit_derivative", "downstream_derivative"): (
            frozenset({"downstream_derivative"}),
            "forbidden",
            "forbidden",
        ),
        ("model_summary_derivative", "model_generated"): (
            frozenset({"model_synthesis"}),
            "forbidden",
            "forbidden",
        ),
        ("search_snippet_only", "provider_search"): (
            frozenset({"search_snippet"}),
            "required",
            "required",
        ),
        ("unknown", "unknown"): (
            frozenset({"unknown"}),
            "forbidden",
            "forbidden",
        ),
    }
)


class SourcePolicyError(ValueError):
    """The strict source proposal names an impossible acquisition shape."""


def evaluate_source_eligibility(
    proposal: SourceProposal,
    *,
    raw_payload_present: bool,
) -> tuple[bool, str]:
    """Validate the exact compatibility row and return its deterministic verdict."""

    row = _SOURCE_COMPATIBILITY.get(
        (proposal.origin_type, proposal.acquisition_method)
    )
    if row is None:
        raise SourcePolicyError("source_origin_policy_invalid")
    materials, provider_rule, raw_rule = row
    if proposal.material_kind not in materials:
        raise SourcePolicyError("source_origin_policy_invalid")
    provider_present = proposal.provider is not None
    if (provider_rule == "required" and not provider_present) or (
        provider_rule == "forbidden" and provider_present
    ):
        raise SourcePolicyError("source_origin_policy_invalid")
    if (raw_rule == "required" and not raw_payload_present) or (
        raw_rule == "forbidden" and raw_payload_present
    ):
        raise SourcePolicyError("source_origin_policy_invalid")

    if proposal.material_kind in {
        "full_content",
        "partial_extract",
        "dataset_snapshot",
        "uploaded_file",
    } and proposal.origin_type in {
        "uploaded_file",
        "manual_evidence",
        "provider_response",
        "authorized_web_fetch",
        "cached_provider_response",
    }:
        return True, "eligible_durable_source_content"
    if proposal.material_kind == "search_result":
        return False, "ineligible_search_result"
    if proposal.material_kind == "search_snippet":
        return False, "ineligible_search_snippet"
    if proposal.material_kind == "model_synthesis":
        return False, "ineligible_model_synthesis"
    if proposal.material_kind == "downstream_derivative":
        return False, "ineligible_downstream_derivative"
    return False, "ineligible_unknown_origin"


__all__ = [
    "INTAKE_LANES",
    "LanePolicy",
    "ProposalKind",
    "SourcePolicyError",
    "evaluate_source_eligibility",
]
