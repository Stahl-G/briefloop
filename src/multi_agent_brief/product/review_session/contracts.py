"""Strict read-only contracts for the post-final Human Review Session."""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import Field, StrictBool, model_validator

from multi_agent_brief.contracts.v2 import (
    CleanText,
    ContractId,
    IsoDateTime,
    NonNegativeInt,
    PositiveInt,
    Sha256,
    StrictModel,
)
from multi_agent_brief.semantic_evaluator.contracts import FindingProposal
from multi_agent_brief.semantic_evaluator.serialization import canonical_model_sha256


POST_FINAL_REVIEW_CONTEXT_SCHEMA_ID = "briefloop.post_final_review.context.v1"
POST_FINAL_REVIEW_POLICY_SCHEMA_ID = "briefloop.post_final_review.policy.v1"
QUALITY_PROJECTION_SCHEMA_ID = "briefloop.post_final_review.quality_projection.v1"
SEMANTIC_REVIEW_SCHEMA_ID = "briefloop.post_final_review.semantic_review.v1"
IMPROVEMENT_PROJECTION_SCHEMA_ID = "briefloop.post_final_review.improvement.v1"
POST_FINAL_REVIEW_READ_MODEL_SCHEMA_ID = "briefloop.post_final_review.read_model.v1"
REVIEW_SESSION_DESCRIPTOR_SCHEMA_ID = "briefloop.post_final_review.session.v1"

QualityStatus = Literal["pass", "warning", "block", "incomplete"]
SemanticReviewStatus = Literal[
    "not_requested",
    "pending",
    "available",
    "abstained",
    "unsupported",
    "budget_blocked",
    "unavailable",
    "invalid",
    "stale",
]


def _check_fingerprint(model: StrictModel, field: str) -> None:
    if getattr(model, field) != canonical_model_sha256(model, exclude=(field,)):
        raise ValueError("post-final review fingerprint mismatch")


class PostFinalReviewPolicyBinding(StrictModel):
    """Immutable typed policy; persistence and activation belong to Store services."""

    schema_id: ClassVar[str] = POST_FINAL_REVIEW_POLICY_SCHEMA_ID
    schema_version: Literal[POST_FINAL_REVIEW_POLICY_SCHEMA_ID]
    workspace_id: ContractId
    run_id: ContractId
    revision: PositiveInt
    enabled: StrictBool
    trigger: Literal["manual", "post_final"]
    auto_open: StrictBool
    profile_id: ContractId
    provider_id: ContractId
    model_id: ContractId
    model_version: CleanText
    data_policy: Literal["public_safe_only", "explicit_private_egress"]
    max_provider_calls: NonNegativeInt
    max_input_tokens: NonNegativeInt
    max_output_tokens: NonNegativeInt
    max_wall_seconds: NonNegativeInt
    instrument_sha256: Sha256
    policy_fingerprint: Sha256
    created_by: ContractId
    created_at: IsoDateTime
    accepted_transaction_id: ContractId

    @model_validator(mode="after")
    def validate_policy_fingerprint(self) -> "PostFinalReviewPolicyBinding":
        _check_fingerprint(self, "policy_fingerprint")
        return self


class PostFinalReviewContext(StrictModel):
    """Identity projected from exactly one re-verified core run."""

    schema_id: ClassVar[str] = POST_FINAL_REVIEW_CONTEXT_SCHEMA_ID
    schema_version: Literal[POST_FINAL_REVIEW_CONTEXT_SCHEMA_ID]
    workspace_id: ContractId
    run_id: ContractId
    store_revision: NonNegativeInt
    finalization_id: ContractId
    finalization_receipt_id: ContractId
    package_id: ContractId
    package_receipt_id: ContractId
    report_artifact_id: ContractId
    report_artifact_revision: PositiveInt
    report_sha256: Sha256
    review_policy_fingerprint: Sha256
    qp_projection_fingerprint: Sha256
    context_fingerprint: Sha256

    @model_validator(mode="after")
    def validate_fingerprint(self) -> "PostFinalReviewContext":
        _check_fingerprint(self, "context_fingerprint")
        return self


class QualityMetric(StrictModel):
    metric_id: ContractId
    label: CleanText
    value: NonNegativeInt
    status: QualityStatus


class QualityItem(StrictModel):
    item_id: ContractId
    label: CleanText
    status: QualityStatus
    detail: CleanText


class QualitySection(StrictModel):
    section_id: ContractId
    title: CleanText
    items: list[QualityItem]


class QualityProjection(StrictModel):
    """Opaque typed QP facts; this module never recomputes core truth."""

    schema_id: ClassVar[str] = QUALITY_PROJECTION_SCHEMA_ID
    schema_version: Literal[QUALITY_PROJECTION_SCHEMA_ID]
    authority_label: Literal["deterministic_projection"]
    runtime_effect: Literal["none"]
    overall_status: QualityStatus
    source_fingerprint: Sha256
    metrics: list[QualityMetric]
    sections: list[QualitySection]
    projection_fingerprint: Sha256

    @model_validator(mode="after")
    def validate_projection(self) -> "QualityProjection":
        if self.source_fingerprint != self.projection_fingerprint:
            raise ValueError("quality projection source fingerprint mismatch")
        return self


class SemanticReviewBinding(StrictModel):
    report_sha256: Sha256
    assessment_key: Sha256
    instrument_sha256: Sha256
    archive_manifest_sha256: Sha256
    receipt_id: ContractId
    model_id: ContractId
    model_version: CleanText


class SemanticReviewProjection(StrictModel):
    schema_id: ClassVar[str] = SEMANTIC_REVIEW_SCHEMA_ID
    schema_version: Literal[SEMANTIC_REVIEW_SCHEMA_ID]
    status: SemanticReviewStatus
    advisory_only: Literal[True]
    authority_effect: Literal["none"]
    binding: SemanticReviewBinding | None
    reason_codes: list[ContractId]
    findings: list[FindingProposal]
    abstention_count: NonNegativeInt
    assessed_unit_count: NonNegativeInt

    @model_validator(mode="after")
    def validate_advisory_boundary(self) -> "SemanticReviewProjection":
        if self.reason_codes != sorted(set(self.reason_codes)):
            raise ValueError("semantic reason codes must be sorted and unique")
        if self.status != "available" and self.findings:
            raise ValueError("non-available semantic state cannot display findings")
        if self.findings and self.binding is None:
            raise ValueError("semantic findings require exact evidence binding")
        if self.binding is not None and any(
            span.report_sha256 != self.binding.report_sha256
            for finding in self.findings
            for span in finding.report_spans
        ):
            raise ValueError("semantic finding report binding mismatch")
        return self


class ImprovementProjection(StrictModel):
    """PF-REVIEW-1 placeholder; it carries no command or persistence surface."""

    schema_id: ClassVar[str] = IMPROVEMENT_PROJECTION_SCHEMA_ID
    schema_version: Literal[IMPROVEMENT_PROJECTION_SCHEMA_ID]
    available: Literal[False]
    authority_effect: Literal["none"]
    reason_code: Literal["pf_review_2_not_shipped"]


class PostFinalReviewReadModel(StrictModel):
    schema_id: ClassVar[str] = POST_FINAL_REVIEW_READ_MODEL_SCHEMA_ID
    schema_version: Literal[POST_FINAL_REVIEW_READ_MODEL_SCHEMA_ID]
    generated_at: IsoDateTime
    context: PostFinalReviewContext
    quality: QualityProjection
    semantic_review: SemanticReviewProjection
    improvement: ImprovementProjection
    read_model_fingerprint: Sha256

    @model_validator(mode="after")
    def validate_read_model(self) -> "PostFinalReviewReadModel":
        if self.context.qp_projection_fingerprint != self.quality.projection_fingerprint:
            raise ValueError("quality projection binding mismatch")
        binding = self.semantic_review.binding
        if binding is not None and binding.report_sha256 != self.context.report_sha256:
            raise ValueError("semantic review context binding mismatch")
        _check_fingerprint(self, "read_model_fingerprint")
        return self


class ReviewSessionDescriptor(StrictModel):
    schema_id: ClassVar[str] = REVIEW_SESSION_DESCRIPTOR_SCHEMA_ID
    schema_version: Literal[REVIEW_SESSION_DESCRIPTOR_SCHEMA_ID]
    session_id: ContractId
    run_id: ContractId
    loopback_host: Literal["127.0.0.1"]
    port: NonNegativeInt = Field(le=65535)
    token_hash: Sha256
    created_at: IsoDateTime
    expires_at: IsoDateTime
    ephemeral: Literal[True]
    runtime_authority: Literal[False]


class ReviewSessionStatus(StrictModel):
    schema_id: ClassVar[str] = "briefloop.post_final_review.session_status.v1"
    schema_version: Literal["briefloop.post_final_review.session_status.v1"]
    active: StrictBool
    reason_code: ContractId
