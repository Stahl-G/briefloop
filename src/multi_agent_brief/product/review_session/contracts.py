"""Strict read models and zero-authority command envelopes for Review Session."""

from __future__ import annotations

from typing import Annotated, ClassVar, Literal

from pydantic import Field, StrictBool, StringConstraints, model_validator

from multi_agent_brief.contracts.v2 import (
    CleanText,
    ContractId,
    IsoDateTime,
    NonNegativeInt,
    PositiveInt,
    ReaderReviewAssessmentInput,
    RunDirection,
    Sha256,
    StrictModel,
)

from .serialization import canonical_model_sha256


POST_FINAL_REVIEW_CONTEXT_SCHEMA_ID = "briefloop.post_final_review.context.v1"
POST_FINAL_REVIEW_POLICY_SCHEMA_ID = "briefloop.post_final_review.policy.v1"
QUALITY_PROJECTION_SCHEMA_ID = "briefloop.post_final_review.quality_projection.v1"
SEMANTIC_REVIEW_SCHEMA_ID = "briefloop.post_final_review.semantic_review.v1"
IMPROVEMENT_PROJECTION_SCHEMA_ID = "briefloop.post_final_review.improvement.v1"
POST_FINAL_REVIEW_READ_MODEL_SCHEMA_ID = "briefloop.post_final_review.read_model.v1"
REVIEW_SESSION_DESCRIPTOR_SCHEMA_ID = "briefloop.post_final_review.session.v1"
REVIEW_SESSION_COMMAND_SCHEMA_ID = "briefloop.post_final_review.command.v1"
READER_REVIEW_SELECTION_SCHEMA_ID = (
    "briefloop.post_final_review.reader_review_selection.v1"
)
READER_REVIEW_REFRESH_SCHEMA_ID = "briefloop.post_final_review.refresh.v1"
HUMAN_OBSERVATION_INPUT_SCHEMA_ID = "briefloop.post_final_human_observation_input.v1"
HUMAN_OBSERVATION_SUPERSEDE_INPUT_SCHEMA_ID = (
    "briefloop.post_final_human_observation_supersede_input.v1"
)
HUMAN_GUIDANCE_DRAFT_INPUT_SCHEMA_ID = "briefloop.post_final_guidance_draft_input.v1"
SUCCESSOR_START_INPUT_SCHEMA_ID = "briefloop.post_final_successor_start_input.v1"

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

# Advisory finding DTO mirrored by value from the isolated semantic evaluator.
# The evaluator-side bridge converts findings field-by-field; this kernel never
# imports the evaluator, so the display contract is restated locally.
FindingBlockId = Annotated[str, StringConstraints(pattern=r"^B[0-9]{6}$")]
FindingAssessmentUnitId = Annotated[
    str, StringConstraints(pattern=r"^AU-[0-9a-f]{12}$")
]
FindingProposalId = Annotated[str, StringConstraints(pattern=r"^F-[0-9a-f]{12}$")]

FindingScopeClass = Literal["O1", "O2"]
FindingDimensionId = Literal[
    "cross_section_consistency",
    "scope_definition_stability",
    "reasoning_continuity",
    "uncertainty_calibration",
    "summary_body_alignment",
    "recommendation_constraint_consistency",
    "brief_requirement_coverage",
    "audience_decision_fit",
    "explicit_scope_constraint_compliance",
]
FindingSeverity = Literal["severe", "major", "minor"]
FindingImpactScope = Literal[
    "key_conclusion", "decision", "scope", "recommendation", "supporting_text"
]
FindingConfidenceBasis = Literal[
    "direct_cross_span_conflict",
    "direct_single_span",
    "explicit_requirement_mismatch",
    "artifact_internal_inference",
    "ambiguous_scope",
    "insufficient_context",
]
FindingRecommendedHumanAction = Literal[
    "reconcile_status_language",
    "clarify_scope",
    "repair_reasoning_bridge",
    "recalibrate_uncertainty",
    "align_summary_and_body",
    "review_recommendation_constraints",
    "address_requirement",
    "review_o3_evidence",
    "inspect_manually",
]


class HumanObservationSpan(StrictModel):
    """Exact report span reference accepted by Human observations."""

    schema_version: Literal["briefloop.post_final_human_observation_report_span.v1"]
    report_sha256: Sha256
    block_id: ContractId
    start_char: NonNegativeInt
    end_char: PositiveInt
    excerpt_sha256: Sha256

    @model_validator(mode="after")
    def validate_offsets(self) -> "HumanObservationSpan":
        if self.start_char >= self.end_char:
            raise ValueError("span offsets must be ordered")
        return self


class HumanObservationInput(StrictModel):
    """Strict loopback/CLI envelope for an independent Human observation.

    The finalized report lineage is always resolved by the Store service.  A
    selected assessment may be supplied by the page, but it is optional so a
    Human can record an observation when Reader Review was not run or did not
    produce a qualified result.  References are identifiers only; the
    deterministic service validates each one against the frozen report.
    """

    schema_version: Literal[HUMAN_OBSERVATION_INPUT_SCHEMA_ID]
    human_actor_id: ContractId
    human_request_id: ContractId
    observation_text: Annotated[str, StringConstraints(min_length=1, max_length=12000)]
    assessment_result_id: ContractId | None = None
    assessment_result_fingerprint: Sha256 | None = None
    reader_view_sha256: Sha256 | None = None
    requirement_id: ContractId | None = None
    claim_id: ContractId | None = None
    report_span: HumanObservationSpan | None = None
    scope_class: FindingScopeClass | None = None
    dimension_id: ContractId | None = None

    @model_validator(mode="after")
    def validate_observation_input(self) -> "HumanObservationInput":
        if not self.observation_text.strip():
            raise ValueError("observation_text must not be blank")
        selected = (
            self.assessment_result_id,
            self.assessment_result_fingerprint,
            self.reader_view_sha256,
        )
        if any(value is not None for value in selected) and not all(
            value is not None for value in selected
        ):
            raise ValueError("selected assessment binding must be complete")
        if (self.scope_class is None) != (self.dimension_id is None):
            raise ValueError("scope_class and dimension_id must be paired")
        return self


class HumanObservationSupersedeInput(StrictModel):
    """Strict append-only supersede command for one Human observation.

    Superseding is a new observation revision, not a status update.  It uses
    the same text/reference fields as append and adds the exact predecessor
    binding required by the Store transaction.
    """

    schema_version: Literal[HUMAN_OBSERVATION_SUPERSEDE_INPUT_SCHEMA_ID]
    human_actor_id: ContractId
    human_request_id: ContractId
    observation_text: Annotated[str, StringConstraints(min_length=1, max_length=12000)]
    assessment_result_id: ContractId | None = None
    assessment_result_fingerprint: Sha256 | None = None
    reader_view_sha256: Sha256 | None = None
    requirement_id: ContractId | None = None
    claim_id: ContractId | None = None
    report_span: HumanObservationSpan | None = None
    scope_class: FindingScopeClass | None = None
    dimension_id: ContractId | None = None
    previous_observation_id: ContractId
    previous_observation_fingerprint: Sha256

    @model_validator(mode="after")
    def validate_supersede_input(self) -> "HumanObservationSupersedeInput":
        if not self.observation_text.strip():
            raise ValueError("observation_text must not be blank")
        selected = (
            self.assessment_result_id,
            self.assessment_result_fingerprint,
            self.reader_view_sha256,
        )
        if any(value is not None for value in selected) and not all(
            value is not None for value in selected
        ):
            raise ValueError("selected assessment binding must be complete")
        if (self.scope_class is None) != (self.dimension_id is None):
            raise ValueError("scope_class and dimension_id must be paired")
        return self


class HumanGuidanceDraftInput(StrictModel):
    """Transport DTO for the tagged guidance provenance union.

    The Store remains the authority for eligibility and persistence.  The
    session transport nevertheless rejects ambiguous provenance before a
    request reaches the service: accepted model findings require the existing
    finding/disposition pair, while a Human observation requires only its
    exact observation id.
    """

    schema_version: Literal[HUMAN_GUIDANCE_DRAFT_INPUT_SCHEMA_ID]
    human_actor_id: ContractId
    human_request_id: ContractId
    provenance_kind: Literal["accepted_model_finding", "human_observation"]
    guidance_text: Annotated[str, StringConstraints(min_length=1, max_length=12000)]
    assessment_result_id: ContractId | None = None
    assessment_result_fingerprint: Sha256 | None = None
    finding_id: ContractId | None = None
    finding_fingerprint: Sha256 | None = None
    disposition_id: ContractId | None = None
    disposition_fingerprint: Sha256 | None = None
    observation_id: ContractId | None = None
    observation_fingerprint: Sha256 | None = None

    @model_validator(mode="after")
    def validate_provenance(self) -> "HumanGuidanceDraftInput":
        if not self.guidance_text.strip():
            raise ValueError("guidance_text must not be blank")
        if self.provenance_kind == "accepted_model_finding":
            if not all(
                item is not None
                for item in (
                    self.assessment_result_id,
                    self.assessment_result_fingerprint,
                    self.finding_id,
                    self.finding_fingerprint,
                    self.disposition_id,
                    self.disposition_fingerprint,
                )
            ):
                raise ValueError(
                    "accepted finding provenance requires result/finding/disposition"
                )
            if self.observation_id is not None:
                raise ValueError(
                    "accepted finding provenance cannot include observation"
                )
            if self.observation_fingerprint is not None:
                raise ValueError(
                    "accepted finding provenance cannot include observation fingerprint"
                )
            if self.finding_fingerprint is None or self.disposition_fingerprint is None:
                raise ValueError(
                    "accepted finding provenance requires finding/disposition fingerprints"
                )
        else:
            if self.observation_id is None or self.observation_fingerprint is None:
                raise ValueError(
                    "Human observation provenance requires observation id/fingerprint"
                )
            if self.finding_id is not None or self.disposition_id is not None:
                raise ValueError("Human observation provenance cannot include finding")
            if (
                self.finding_fingerprint is not None
                or self.disposition_fingerprint is not None
            ):
                raise ValueError(
                    "Human observation provenance cannot include finding fingerprints"
                )
            result_fields = (
                self.assessment_result_id,
                self.assessment_result_fingerprint,
            )
            if any(value is not None for value in result_fields) and not all(
                value is not None for value in result_fields
            ):
                raise ValueError("Human observation result binding must be complete")
        return self


class ReviewFindingSpan(StrictModel):
    report_sha256: Sha256
    block_id: FindingBlockId
    start_char: NonNegativeInt
    end_char: PositiveInt
    excerpt_sha256: Sha256

    @model_validator(mode="after")
    def validate_offsets(self) -> "ReviewFindingSpan":
        if self.start_char >= self.end_char:
            raise ValueError("span offsets must be ordered")
        return self


class ReviewFinding(StrictModel):
    assessment_unit_id: FindingAssessmentUnitId
    scope_class: FindingScopeClass
    dimension_id: FindingDimensionId
    severity: FindingSeverity
    impact_scope: FindingImpactScope
    report_spans: list[ReviewFindingSpan] = Field(min_length=1)
    context_requirement_ids: list[ContractId]
    observation: CleanText
    rationale: CleanText
    severity_basis: CleanText
    confidence_basis: FindingConfidenceBasis
    external_premise_disclosure: Literal["none", "suspected", "required"]
    recommended_human_action: FindingRecommendedHumanAction
    suggested_rewrite: None
    finding_id: FindingProposalId
    status: Literal["proposal"]

    @model_validator(mode="after")
    def validate_scope_binding(self) -> "ReviewFinding":
        if len(self.context_requirement_ids) != len(set(self.context_requirement_ids)):
            raise ValueError("context requirement ids must be unique")
        if self.scope_class == "O1":
            if self.context_requirement_ids:
                raise ValueError("O1 findings cannot bind requirements")
            if self.external_premise_disclosure == "required":
                raise ValueError("O1 evidence-dependent assessments require handoff")
        elif not self.context_requirement_ids:
            raise ValueError("O2 findings require context requirement ids")
        return self


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
    findings: list[ReviewFinding]
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
        if (
            self.context.qp_projection_fingerprint
            != self.quality.projection_fingerprint
        ):
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


class ReaderReviewResultSelection(StrictModel):
    """Ephemeral display selection; Store qualification remains projection-owned."""

    schema_version: Literal[READER_REVIEW_SELECTION_SCHEMA_ID]
    assessment_result_id: ContractId
    assessment_result_fingerprint: Sha256


class ReaderReviewRefresh(StrictModel):
    """Zero-authority request to rebuild the canonical read-only projection."""

    schema_version: Literal[READER_REVIEW_REFRESH_SCHEMA_ID]


class SuccessorStartInput(StrictModel):
    """Human-facing successor choice for the secured Review Session.

    The page carries the complete frozen RunDirection as a readable payload so
    the runtime can re-validate it against SQLite before the Core successor
    writer is invoked.  No Store fingerprints or hidden authority fields are
    accepted here; the runtime derives those from the verified predecessor.
    """

    schema_version: Literal[SUCCESSOR_START_INPUT_SCHEMA_ID]
    successor_run_id: ContractId
    run_direction: RunDirection
    include_approved_guidance: StrictBool


class ReviewSessionCommand(StrictModel):
    """Ephemeral transport envelope; nested domain DTOs remain Store-service owned."""

    schema_version: Literal["briefloop.post_final_review.command.v1"]
    action: Literal[
        "run_reader_review",
        "select_result",
        "refresh",
        "start_successor",
        "append_observation",
        "supersede_observation",
        "accept",
        "reject",
        "defer",
        "draft",
        "approve",
        "deactivate",
        "revert",
        "supersede",
        "status",
    ]
    payload: dict[str, object]

    @model_validator(mode="after")
    def validate_payload_contract(self) -> "ReviewSessionCommand":
        if self.action == "run_reader_review":
            ReaderReviewAssessmentInput.model_validate(self.payload, strict=True)
        elif self.action == "select_result":
            ReaderReviewResultSelection.model_validate(self.payload, strict=True)
        elif self.action == "refresh":
            ReaderReviewRefresh.model_validate(self.payload, strict=True)
        elif self.action == "start_successor":
            SuccessorStartInput.model_validate(self.payload, strict=True)
        elif self.action == "append_observation":
            HumanObservationInput.model_validate(self.payload, strict=True)
        elif self.action == "supersede_observation":
            HumanObservationSupersedeInput.model_validate(self.payload, strict=True)
        elif self.action == "draft":
            HumanGuidanceDraftInput.model_validate(self.payload, strict=True)
        return self
