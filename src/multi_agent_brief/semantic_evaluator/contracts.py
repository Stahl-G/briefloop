"""Strict contracts for the isolated Semantic Evaluator research instrument.

These models describe shadow-only research records.  They are intentionally
kept out of the production v2 contract registry and carry no workflow, gate,
finalize, delivery, or claim-support authority.
"""

from __future__ import annotations

import hashlib
from pathlib import PurePath
from typing import Annotated, ClassVar, Literal, Optional, Union

from pydantic import (
    Field,
    JsonValue,
    StrictBool,
    StringConstraints,
    field_validator,
    model_validator,
)

from multi_agent_brief.contracts.v2 import (
    CleanText,
    ContractId,
    NonNegativeInt,
    PositiveInt,
    Sha256,
    StrictModel,
)


READER_ARTIFACT_SCHEMA_ID = "briefloop.semantic_evaluator.reader_artifact.v1"
BOUNDED_CONTEXT_SCHEMA_ID = "briefloop.semantic_evaluator.bounded_context.v1"
PROFILE_SCHEMA_ID = "briefloop.semantic_evaluator.profile.v1"
INSTRUMENT_CONFIG_SCHEMA_ID = "briefloop.semantic_evaluator.instrument_config.v1"
ADMISSION_REQUEST_SCHEMA_ID = "briefloop.semantic_evaluator.admission_request.v1"
INSTRUMENT_MANIFEST_SCHEMA_ID = "briefloop.semantic_evaluator.instrument_manifest.v1"
INPUT_BINDING_SCHEMA_ID = "briefloop.semantic_evaluator.input_binding.v1"
ASSESSMENT_PLAN_SCHEMA_ID = "briefloop.semantic_evaluator.assessment_plan.v1"
DIMENSION_RESPONSE_SCHEMA_ID = "briefloop.semantic_evaluator.dimension_response.v1"
RUN_SCHEMA_ID = "briefloop.semantic_evaluator.run.v1"
VALIDATION_REPORT_SCHEMA_ID = "briefloop.semantic_evaluator.validation_report.v1"
EVENT_SCHEMA_ID = "briefloop.semantic_evaluator.event.v1"
LAJ_COMPOSITION_WITNESS_SCHEMA_ID = (
    "briefloop.semantic_evaluator.laj_composition_witness.v1"
)
BASELINE_SCHEMA_ID = "briefloop.semantic_evaluator.baseline.v1"
COMPOSITION_SCHEMA_ID = "briefloop.semantic_evaluator.composition.v1"
PRESENTATION_SCHEMA_ID = "briefloop.semantic_evaluator.presentation.v1"

_INSTRUMENT_SCHEMA_IDS = (
    READER_ARTIFACT_SCHEMA_ID,
    BOUNDED_CONTEXT_SCHEMA_ID,
    PROFILE_SCHEMA_ID,
    INSTRUMENT_CONFIG_SCHEMA_ID,
    ADMISSION_REQUEST_SCHEMA_ID,
    INSTRUMENT_MANIFEST_SCHEMA_ID,
    INPUT_BINDING_SCHEMA_ID,
    ASSESSMENT_PLAN_SCHEMA_ID,
    DIMENSION_RESPONSE_SCHEMA_ID,
    RUN_SCHEMA_ID,
    VALIDATION_REPORT_SCHEMA_ID,
    EVENT_SCHEMA_ID,
    LAJ_COMPOSITION_WITNESS_SCHEMA_ID,
    BASELINE_SCHEMA_ID,
    COMPOSITION_SCHEMA_ID,
    PRESENTATION_SCHEMA_ID,
)


BlockId = Annotated[str, StringConstraints(pattern=r"^B[0-9]{6}$")]
AssessmentUnitId = Annotated[str, StringConstraints(pattern=r"^AU-[0-9a-f]{12}$")]
FindingId = Annotated[str, StringConstraints(pattern=r"^F-[0-9a-f]{12}$")]
HandoffId = Annotated[str, StringConstraints(pattern=r"^H-[0-9a-f]{12}$")]
CharOffset = Annotated[int, Field(ge=0)]
HexBytes = Annotated[str, StringConstraints(pattern=r"^(?:[0-9a-f]{2})*$")]
AbsolutePathText = Annotated[str, StringConstraints(min_length=1)]
JsonObject = dict[str, JsonValue]

Language = Literal["zh-CN"]
DataClass = Literal["public", "synthetic"]
ScopeClass = Literal["O1", "O2"]
RequirementType = Literal[
    "must_answer",
    "must_include",
    "must_not_claim",
    "audience_need",
    "decision_use",
    "scope_included",
    "scope_excluded",
]
DimensionId = Literal[
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
Disposition = Literal[
    "finding_emitted",
    "no_finding",
    "abstain_insufficient_context",
    "abstain_unable_to_assess",
    "abstain_conflicting_context",
    "rubric_not_applicable",
]
RunStatus = Literal[
    "completed",
    "incomplete",
    "policy_blocked",
    "provider_failed",
    "parser_failed",
    "validation_failed",
    "security_failed",
    "archive_failed",
]
Severity = Literal["severe", "major", "minor"]
ImpactScope = Literal[
    "key_conclusion", "decision", "scope", "recommendation", "supporting_text"
]
ConfidenceBasis = Literal[
    "direct_cross_span_conflict",
    "direct_single_span",
    "explicit_requirement_mismatch",
    "artifact_internal_inference",
    "ambiguous_scope",
    "insufficient_context",
]
RecommendedHumanAction = Literal[
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


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _require_unique(values: list[str], field_name: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{field_name} must be unique")


class ReaderBlock(StrictModel):
    block_id: BlockId
    ordinal: NonNegativeInt
    section_path: list[CleanText]
    role: Literal["heading", "paragraph", "list", "table", "code"]
    text: str = Field(min_length=1)
    text_sha256: Sha256
    start_char: CharOffset
    end_char: PositiveInt

    @model_validator(mode="after")
    def validate_local_binding(self) -> "ReaderBlock":
        if not self.text.strip():
            raise ValueError("block text must not be blank")
        if self.start_char >= self.end_char:
            raise ValueError("block offsets must be ordered")
        if self.end_char - self.start_char != len(self.text):
            raise ValueError("block offsets must match text length")
        if self.text_sha256 != _sha256_text(self.text):
            raise ValueError("block text hash mismatch")
        return self


class ReaderArtifact(StrictModel):
    schema_id: ClassVar[str] = READER_ARTIFACT_SCHEMA_ID
    schema_version: Literal[READER_ARTIFACT_SCHEMA_ID]
    artifact_id: ContractId
    report_sha256: Sha256
    language: Language
    format: Literal["normalized_markdown"]
    normalized_text_sha256: Sha256
    blocks: list[ReaderBlock] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_block_inventory(self) -> "ReaderArtifact":
        block_ids = [block.block_id for block in self.blocks]
        _require_unique(block_ids, "block ids")
        expected_ordinals = list(range(len(self.blocks)))
        if [block.ordinal for block in self.blocks] != expected_ordinals:
            raise ValueError("block ordinals must be contiguous from zero")
        if block_ids != [f"B{index:06d}" for index in range(1, len(self.blocks) + 1)]:
            raise ValueError("block ids must be contiguous in source order")
        previous_end = -1
        for block in self.blocks:
            if block.start_char < previous_end:
                raise ValueError("blocks must be ordered and non-overlapping")
            previous_end = block.end_char
        return self


class BoundedRequirement(StrictModel):
    requirement_id: ContractId
    type: RequirementType
    text: CleanText
    source_locator: CleanText


class BoundedContext(StrictModel):
    schema_id: ClassVar[str] = BOUNDED_CONTEXT_SCHEMA_ID
    schema_version: Literal[BOUNDED_CONTEXT_SCHEMA_ID]
    context_id: ContractId
    context_sha256: Sha256
    language: Language
    data_class: DataClass
    requirements: list[BoundedRequirement]

    @model_validator(mode="after")
    def validate_requirement_inventory(self) -> "BoundedContext":
        _require_unique(
            [item.requirement_id for item in self.requirements], "requirement ids"
        )
        return self


class SeverityRubric(StrictModel):
    severe: CleanText
    major: CleanText
    minor: CleanText


class SubAspectProfile(StrictModel):
    sub_aspect_id: ContractId
    definition: CleanText
    positive_criterion: CleanText
    exclusions: list[CleanText] = Field(min_length=1)
    severity_rubric: SeverityRubric
    abstention_conditions: list[CleanText] = Field(min_length=1)
    o3_handoff_conditions: list[CleanText] = Field(min_length=1)
    eligible_requirement_types: list[RequirementType]

    @field_validator("eligible_requirement_types")
    @classmethod
    def validate_requirement_types(
        cls, value: list[RequirementType]
    ) -> list[RequirementType]:
        _require_unique(list(value), "eligible requirement types")
        return value


class DimensionProfile(StrictModel):
    dimension_id: DimensionId
    scope_class: ScopeClass
    definition: CleanText
    positive_criterion: CleanText
    exclusions: list[CleanText] = Field(min_length=1)
    severity_rubric: SeverityRubric
    abstention_conditions: list[CleanText] = Field(min_length=1)
    o3_handoff_conditions: list[CleanText] = Field(min_length=1)
    eligible_requirement_types: list[RequirementType]
    sub_aspects: list[SubAspectProfile] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_dimension_scope(self) -> "DimensionProfile":
        _require_unique(
            [item.sub_aspect_id for item in self.sub_aspects], "sub-aspect ids"
        )
        if self.scope_class == "O1" and self.eligible_requirement_types:
            raise ValueError("O1 dimensions cannot bind requirements")
        if self.scope_class == "O2" and not self.eligible_requirement_types:
            raise ValueError("O2 dimensions require eligible requirement types")
        allowed = set(self.eligible_requirement_types)
        covered: set[str] = set()
        for item in self.sub_aspects:
            item_types = set(item.eligible_requirement_types)
            if not item_types.issubset(allowed):
                raise ValueError(
                    "sub-aspect requirement type is not dimension-eligible"
                )
            if self.scope_class == "O1" and item_types:
                raise ValueError("O1 sub-aspects cannot bind requirements")
            if self.scope_class == "O2" and not item_types:
                raise ValueError("O2 sub-aspects require eligible requirement types")
            covered.update(item_types)
        if covered != allowed:
            raise ValueError("sub-aspects must cover dimension requirement types")
        return self


class EvaluatorProfile(StrictModel):
    schema_id: ClassVar[str] = PROFILE_SCHEMA_ID
    schema_version: Literal[PROFILE_SCHEMA_ID]
    profile_id: Literal["research_design_report_zh_v1"]
    report_type: Literal["research_design_report"]
    language: Language
    allowed_scope_classes: list[ScopeClass]
    dimensions: list[DimensionProfile] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_profile_inventory_shape(self) -> "EvaluatorProfile":
        if self.allowed_scope_classes != ["O1", "O2"]:
            raise ValueError("profile scope classes must be ordered O1 then O2")
        _require_unique(
            [item.dimension_id for item in self.dimensions], "dimension ids"
        )
        return self


class DecodingConfig(StrictModel):
    temperature: Annotated[float, Field(ge=0.0, le=1.0)]
    top_p: Annotated[float, Field(ge=0.0, le=1.0)]
    max_output_tokens: PositiveInt
    seed: Optional[int]


class RetryPolicy(StrictModel):
    max_attempts: PositiveInt
    retryable_reason_codes: list[ContractId]
    backoff_schedule_ms: list[NonNegativeInt]

    @model_validator(mode="after")
    def validate_retry_policy(self) -> "RetryPolicy":
        if self.retryable_reason_codes != sorted(set(self.retryable_reason_codes)):
            raise ValueError("retry reason codes must be sorted and unique")
        if len(self.backoff_schedule_ms) != self.max_attempts - 1:
            raise ValueError("retry backoff count must equal max attempts minus one")
        return self


class PromptSizerConfig(StrictModel):
    sizer_id: ContractId
    sizer_version: ContractId
    max_context_tokens: PositiveInt
    reserved_output_tokens: PositiveInt

    @model_validator(mode="after")
    def validate_context_budget(self) -> "PromptSizerConfig":
        if self.reserved_output_tokens >= self.max_context_tokens:
            raise ValueError("reserved output must be smaller than context limit")
        return self


class TransportPolicy(StrictModel):
    provider_transport_only: Literal[True]
    model_tools: Literal[False]
    browser: Literal[False]
    cross_run_memory: Literal[False]
    provider_file_search: Literal[False]


class InstrumentConfig(StrictModel):
    schema_id: ClassVar[str] = INSTRUMENT_CONFIG_SCHEMA_ID
    schema_version: Literal[INSTRUMENT_CONFIG_SCHEMA_ID]
    instrument_config_id: ContractId
    provider_id: ContractId
    model_id: ContractId
    model_version: CleanText
    language: Language
    decoding: DecodingConfig
    retry_policy: RetryPolicy
    prompt_sizer: PromptSizerConfig
    transport_policy: TransportPolicy


class AdmissionRequest(StrictModel):
    schema_id: ClassVar[str] = ADMISSION_REQUEST_SCHEMA_ID
    schema_version: Literal[ADMISSION_REQUEST_SCHEMA_ID]
    artifact_id: ContractId
    trial_id: ContractId
    report_bytes_hex: HexBytes
    declared_report_sha256: Sha256
    bounded_context: BoundedContext
    declared_bounded_context_sha256: Sha256
    instrument_config: InstrumentConfig
    public_data_attestation: StrictBool
    private_or_confidential_material: StrictBool
    archive_root: Optional[AbsolutePathText]
    workspace_root: Optional[AbsolutePathText]

    @field_validator("archive_root", "workspace_root")
    @classmethod
    def validate_absolute_path(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and not PurePath(value).is_absolute():
            raise ValueError("path must be absolute")
        return value


class AdmittedReportEvidence(StrictModel):
    artifact_id: ContractId
    report_bytes_hex: HexBytes
    report_sha256: Sha256
    normalized_text_sha256: Sha256
    evidence_sha256: Sha256


class DimensionAttemptEvidence(StrictModel):
    attempt_ref: ContractId
    dimension_id: DimensionId
    attempt_ordinal: PositiveInt
    prompt_request_sha256: Sha256
    status: Literal["completed", "failed"]
    reason_code: Optional[ContractId]
    raw_response_bytes_hex: Optional[HexBytes]
    raw_response_sha256: Optional[Sha256]
    forbidden_canary_values: list[CleanText]
    evidence_sha256: Sha256

    @model_validator(mode="after")
    def validate_attempt_evidence_shape(self) -> "DimensionAttemptEvidence":
        if self.forbidden_canary_values != sorted(set(self.forbidden_canary_values)):
            raise ValueError("canary values must be sorted and unique")
        if self.status == "completed":
            if (
                self.reason_code is not None
                or self.raw_response_bytes_hex is None
                or self.raw_response_sha256 is None
            ):
                raise ValueError("completed evidence requires raw response only")
        elif (
            self.reason_code is None
            or self.raw_response_bytes_hex is not None
            or self.raw_response_sha256 is not None
        ):
            raise ValueError("failed evidence requires one reason only")
        return self


class ImplementationComponent(StrictModel):
    component_id: Literal[
        "parser", "validator", "normalizer", "unit_planner", "prompt_assembler"
    ]
    implementation_version: ContractId
    source_sha256: Sha256


class InstrumentManifest(StrictModel):
    schema_id: ClassVar[str] = INSTRUMENT_MANIFEST_SCHEMA_ID
    schema_version: Literal[INSTRUMENT_MANIFEST_SCHEMA_ID]
    manifest_id: ContractId
    frozen_design_sha256: Sha256
    freeze_manifest_sha256: Sha256
    profile_sha256: Sha256
    system_prompt_sha256: Sha256
    dimension_prompt_sha256: Sha256
    schema_sha256s: dict[ContractId, Sha256]
    implementation_components: list[ImplementationComponent]
    retry_policy_sha256: Sha256
    decoding_sha256: Sha256
    instrument_config_sha256: Sha256
    provider_id: ContractId
    model_id: ContractId
    model_version: CleanText
    prompt_sizer_id: ContractId
    prompt_sizer_version: ContractId
    language: Language
    max_context_tokens: PositiveInt
    reserved_output_tokens: PositiveInt
    transport_policy: TransportPolicy
    instrument_sha256: Sha256

    @model_validator(mode="after")
    def validate_component_inventory(self) -> "InstrumentManifest":
        expected = [
            "parser",
            "validator",
            "normalizer",
            "unit_planner",
            "prompt_assembler",
        ]
        if [item.component_id for item in self.implementation_components] != expected:
            raise ValueError("implementation component inventory is not canonical")
        expected_schema_ids = sorted(_INSTRUMENT_SCHEMA_IDS)
        if list(self.schema_sha256s) != expected_schema_ids:
            raise ValueError("schema hashes must use sorted schema ids")
        return self


class InputBinding(StrictModel):
    schema_id: ClassVar[str] = INPUT_BINDING_SCHEMA_ID
    schema_version: Literal[INPUT_BINDING_SCHEMA_ID]
    binding_id: ContractId
    trial_id: ContractId
    report_sha256: Sha256
    normalized_text_sha256: Sha256
    bounded_context_sha256: Sha256
    profile_sha256: Sha256
    instrument_config_sha256: Sha256
    language: Language
    data_class: DataClass
    public_data_attestation: StrictBool
    private_or_confidential_material: StrictBool
    input_binding_sha256: Sha256


class AssessmentUnit(StrictModel):
    assessment_unit_id: AssessmentUnitId
    trial_id: ContractId
    report_sha256: Sha256
    dimension_id: DimensionId
    sub_aspect_id: ContractId
    scope_class: ScopeClass
    eligible_requirement_types: list[RequirementType]

    @model_validator(mode="after")
    def validate_unit_scope(self) -> "AssessmentUnit":
        if self.scope_class == "O1" and self.eligible_requirement_types:
            raise ValueError("O1 unit cannot bind requirements")
        if self.scope_class == "O2" and not self.eligible_requirement_types:
            raise ValueError("O2 unit requires eligible requirement types")
        return self


class AssessmentPlan(StrictModel):
    schema_id: ClassVar[str] = ASSESSMENT_PLAN_SCHEMA_ID
    schema_version: Literal[ASSESSMENT_PLAN_SCHEMA_ID]
    plan_id: ContractId
    trial_id: ContractId
    report_sha256: Sha256
    profile_sha256: Sha256
    units: list[AssessmentUnit] = Field(min_length=1)
    assessment_plan_sha256: Sha256

    @model_validator(mode="after")
    def validate_unit_bindings(self) -> "AssessmentPlan":
        _require_unique(
            [item.assessment_unit_id for item in self.units], "assessment unit ids"
        )
        pairs = [(item.dimension_id, item.sub_aspect_id) for item in self.units]
        if len(pairs) != len(set(pairs)):
            raise ValueError("dimension and sub-aspect pairs must be unique")
        for unit in self.units:
            if (
                unit.trial_id != self.trial_id
                or unit.report_sha256 != self.report_sha256
            ):
                raise ValueError("assessment unit binding mismatch")
        return self


class SpanLocator(StrictModel):
    report_sha256: Sha256
    block_id: BlockId
    start_char: CharOffset
    end_char: PositiveInt
    excerpt_sha256: Sha256

    @model_validator(mode="after")
    def validate_offsets(self) -> "SpanLocator":
        if self.start_char >= self.end_char:
            raise ValueError("span offsets must be ordered")
        return self


class FindingDraft(StrictModel):
    assessment_unit_id: AssessmentUnitId
    scope_class: ScopeClass
    dimension_id: DimensionId
    severity: Severity
    impact_scope: ImpactScope
    report_spans: list[SpanLocator] = Field(min_length=1)
    context_requirement_ids: list[ContractId]
    observation: CleanText
    rationale: CleanText
    severity_basis: CleanText
    confidence_basis: ConfidenceBasis
    external_premise_disclosure: Literal["none", "suspected", "required"]
    recommended_human_action: RecommendedHumanAction
    suggested_rewrite: None

    @model_validator(mode="after")
    def validate_scope_binding(self) -> "FindingDraft":
        _require_unique(self.context_requirement_ids, "context requirement ids")
        if self.scope_class == "O1":
            if self.context_requirement_ids:
                raise ValueError("O1 findings cannot bind requirements")
            if self.external_premise_disclosure == "required":
                raise ValueError("O1 evidence-dependent assessments require handoff")
        elif not self.context_requirement_ids:
            raise ValueError("O2 findings require context requirement ids")
        return self


class FindingProposal(FindingDraft):
    finding_id: FindingId
    status: Literal["proposal"]


class O3HandoffDraft(StrictModel):
    assessment_unit_id: AssessmentUnitId
    type: Literal["evidence_dependent_assessment"]
    report_spans: list[SpanLocator] = Field(min_length=1)
    context_requirement_ids: list[ContractId]
    reason: CleanText


class O3Handoff(O3HandoffDraft):
    handoff_id: HandoffId


class FindingEmittedResult(StrictModel):
    assessment_unit_id: AssessmentUnitId
    disposition: Literal["finding_emitted"]
    findings: list[FindingDraft] = Field(min_length=1)


class NoFindingResult(StrictModel):
    assessment_unit_id: AssessmentUnitId
    disposition: Literal["no_finding"]


class AbstainInsufficientContextResult(StrictModel):
    assessment_unit_id: AssessmentUnitId
    disposition: Literal["abstain_insufficient_context"]
    reason_code: Literal["insufficient_context"]
    handoffs: list[O3HandoffDraft]


class AbstainUnableToAssessResult(StrictModel):
    assessment_unit_id: AssessmentUnitId
    disposition: Literal["abstain_unable_to_assess"]
    reason_code: Literal["unable_to_assess", "evidence_dependent_assessment"]
    handoffs: list[O3HandoffDraft]


class AbstainConflictingContextResult(StrictModel):
    assessment_unit_id: AssessmentUnitId
    disposition: Literal["abstain_conflicting_context"]
    reason_code: Literal["conflicting_context"]
    handoffs: list[O3HandoffDraft]


class RubricNotApplicableResult(StrictModel):
    assessment_unit_id: AssessmentUnitId
    disposition: Literal["rubric_not_applicable"]


UnitResult = Annotated[
    Union[
        FindingEmittedResult,
        NoFindingResult,
        AbstainInsufficientContextResult,
        AbstainUnableToAssessResult,
        AbstainConflictingContextResult,
        RubricNotApplicableResult,
    ],
    Field(discriminator="disposition"),
]


class DimensionResponse(StrictModel):
    schema_id: ClassVar[str] = DIMENSION_RESPONSE_SCHEMA_ID
    schema_version: Literal[DIMENSION_RESPONSE_SCHEMA_ID]
    trial_id: ContractId
    dimension_id: DimensionId
    unit_results: list[UnitResult] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_response_inventory(self) -> "DimensionResponse":
        unit_ids = [item.assessment_unit_id for item in self.unit_results]
        _require_unique(unit_ids, "unit result ids")
        for result in self.unit_results:
            if isinstance(result, FindingEmittedResult):
                for finding in result.findings:
                    if finding.assessment_unit_id != result.assessment_unit_id:
                        raise ValueError("finding owner mismatch")
            elif isinstance(
                result,
                (
                    AbstainInsufficientContextResult,
                    AbstainUnableToAssessResult,
                    AbstainConflictingContextResult,
                ),
            ):
                for handoff in result.handoffs:
                    if handoff.assessment_unit_id != result.assessment_unit_id:
                        raise ValueError("handoff owner mismatch")
        return self


class AssessmentUnitOutcome(StrictModel):
    assessment_unit_id: AssessmentUnitId
    dimension_id: DimensionId
    sub_aspect_id: ContractId
    disposition: Disposition
    finding_ids: list[FindingId]
    handoff_ids: list[HandoffId]
    attempt_ref: ContractId


class AttemptRef(StrictModel):
    attempt_ref: ContractId
    dimension_id: DimensionId
    attempt_ordinal: PositiveInt
    prompt_request_sha256: Sha256
    status: Literal["completed", "failed"]
    reason_code: Optional[ContractId]

    @model_validator(mode="after")
    def validate_status_reason(self) -> "AttemptRef":
        if self.status == "completed" and self.reason_code is not None:
            raise ValueError("completed attempts cannot carry a failure reason")
        if self.status == "failed" and self.reason_code is None:
            raise ValueError("failed attempts require a reason code")
        return self


class SemanticAssessmentRun(StrictModel):
    schema_id: ClassVar[str] = RUN_SCHEMA_ID
    schema_version: Literal[RUN_SCHEMA_ID]
    run_id: ContractId
    trial_id: ContractId
    report_sha256: Sha256
    bounded_context_sha256: Sha256
    profile_sha256: Sha256
    instrument_sha256: Sha256
    assessment_plan_sha256: Sha256
    run_status: RunStatus
    assessment_units: list[AssessmentUnitOutcome]
    findings: list[FindingProposal]
    handoffs: list[O3Handoff]
    attempt_refs: list[AttemptRef]
    event_stream_sha256: Sha256

    @model_validator(mode="after")
    def validate_run_references(self) -> "SemanticAssessmentRun":
        _require_unique(
            [item.assessment_unit_id for item in self.assessment_units],
            "run assessment unit ids",
        )
        _require_unique([item.finding_id for item in self.findings], "run finding ids")
        _require_unique([item.handoff_id for item in self.handoffs], "run handoff ids")
        _require_unique(
            [item.attempt_ref for item in self.attempt_refs], "attempt refs"
        )
        return self


class ValidationReport(StrictModel):
    schema_id: ClassVar[str] = VALIDATION_REPORT_SCHEMA_ID
    schema_version: Literal[VALIDATION_REPORT_SCHEMA_ID]
    run_id: ContractId
    trial_id: ContractId
    validation_status: Literal["accepted", "rejected", "incomplete"]
    reason_codes: list[ContractId]
    accepted_finding_ids: list[FindingId]
    rejected_finding_ids: list[FindingId]
    planned_unit_count: NonNegativeInt
    disposed_unit_count: NonNegativeInt
    finding_count: NonNegativeInt
    abstention_count: NonNegativeInt
    handoff_count: NonNegativeInt
    raw_attempt_refs: list[ContractId]

    @model_validator(mode="after")
    def validate_report_lists(self) -> "ValidationReport":
        if self.reason_codes != sorted(set(self.reason_codes)):
            raise ValueError("reason codes must be sorted and unique")
        for values, name in (
            (self.accepted_finding_ids, "accepted finding ids"),
            (self.rejected_finding_ids, "rejected finding ids"),
            (self.raw_attempt_refs, "raw attempt refs"),
        ):
            _require_unique(list(values), name)
        if set(self.accepted_finding_ids) & set(self.rejected_finding_ids):
            raise ValueError("finding acceptance sets must be disjoint")
        if self.disposed_unit_count > self.planned_unit_count:
            raise ValueError("disposed units cannot exceed planned units")
        return self


class AdmissionDecidedPayload(StrictModel):
    event_type: Literal["admission_decided"]
    admitted: bool
    reason_codes: list[ContractId]


class AssessmentPlanCreatedPayload(StrictModel):
    event_type: Literal["assessment_plan_created"]
    assessment_plan_sha256: Sha256
    planned_unit_count: NonNegativeInt


class AttemptStartedPayload(StrictModel):
    event_type: Literal["attempt_started"]
    dimension_id: DimensionId
    attempt_ref: ContractId
    attempt_ordinal: PositiveInt
    prompt_request_sha256: Sha256


class AttemptCompletedPayload(StrictModel):
    event_type: Literal["attempt_completed"]
    dimension_id: DimensionId
    attempt_ref: ContractId
    attempt_ordinal: PositiveInt
    prompt_request_sha256: Sha256


class AttemptFailedPayload(StrictModel):
    event_type: Literal["attempt_failed"]
    dimension_id: DimensionId
    attempt_ref: ContractId
    attempt_ordinal: PositiveInt
    prompt_request_sha256: Sha256
    reason_code: ContractId


class DimensionParsedPayload(StrictModel):
    event_type: Literal["dimension_parsed"]
    dimension_id: DimensionId
    disposed_unit_count: NonNegativeInt


class UnitDispositionRecordedPayload(StrictModel):
    event_type: Literal["unit_disposition_recorded"]
    assessment_unit_id: AssessmentUnitId
    disposition: Disposition
    finding_ids: list[FindingId]
    handoff_ids: list[HandoffId]


class FindingAcceptedPayload(StrictModel):
    event_type: Literal["finding_accepted"]
    finding_id: FindingId
    assessment_unit_id: AssessmentUnitId


class FindingRejectedPayload(StrictModel):
    event_type: Literal["finding_rejected"]
    finding_id: FindingId
    assessment_unit_id: AssessmentUnitId
    reason_codes: list[ContractId]


class O3HandoffRecordedPayload(StrictModel):
    event_type: Literal["o3_handoff_recorded"]
    handoff_id: HandoffId
    assessment_unit_id: AssessmentUnitId


class SecurityFailureRecordedPayload(StrictModel):
    event_type: Literal["security_failure_recorded"]
    reason_code: ContractId


class RunCompletedPayload(StrictModel):
    event_type: Literal["run_completed"]
    disposed_unit_count: NonNegativeInt
    finding_count: NonNegativeInt
    abstention_count: NonNegativeInt
    handoff_count: NonNegativeInt


class RunIncompletePayload(StrictModel):
    event_type: Literal["run_incomplete"]
    run_status: RunStatus
    reason_codes: list[ContractId]


class PresentationComposedPayload(StrictModel):
    event_type: Literal["presentation_composed"]
    composition_sha256: Sha256
    presentation_sha256: Sha256


EventPayload = Annotated[
    Union[
        AdmissionDecidedPayload,
        AssessmentPlanCreatedPayload,
        AttemptStartedPayload,
        AttemptCompletedPayload,
        AttemptFailedPayload,
        DimensionParsedPayload,
        UnitDispositionRecordedPayload,
        FindingAcceptedPayload,
        FindingRejectedPayload,
        O3HandoffRecordedPayload,
        SecurityFailureRecordedPayload,
        RunCompletedPayload,
        RunIncompletePayload,
        PresentationComposedPayload,
    ],
    Field(discriminator="event_type"),
]

EventType = Literal[
    "admission_decided",
    "assessment_plan_created",
    "attempt_started",
    "attempt_completed",
    "attempt_failed",
    "dimension_parsed",
    "unit_disposition_recorded",
    "finding_accepted",
    "finding_rejected",
    "o3_handoff_recorded",
    "security_failure_recorded",
    "run_completed",
    "run_incomplete",
    "presentation_composed",
]


class SemanticEvaluatorEvent(StrictModel):
    schema_id: ClassVar[str] = EVENT_SCHEMA_ID
    schema_version: Literal[EVENT_SCHEMA_ID]
    event_id: ContractId
    sequence: PositiveInt
    run_id: ContractId
    trial_id: ContractId
    event_type: EventType
    payload: EventPayload

    @model_validator(mode="after")
    def validate_event_discriminator(self) -> "SemanticEvaluatorEvent":
        if self.event_type != self.payload.event_type:
            raise ValueError("event type and payload type must match")
        return self


class LajCompositionWitness(StrictModel):
    schema_id: ClassVar[str] = LAJ_COMPOSITION_WITNESS_SCHEMA_ID
    schema_version: Literal[LAJ_COMPOSITION_WITNESS_SCHEMA_ID]
    input_binding: InputBinding
    report_evidence: AdmittedReportEvidence
    reader_artifact: ReaderArtifact
    bounded_context: BoundedContext
    instrument_config: InstrumentConfig
    instrument_manifest: InstrumentManifest
    assessment_plan: AssessmentPlan
    dimension_attempt_evidence: list[DimensionAttemptEvidence] = Field(min_length=1)
    run: SemanticAssessmentRun
    validation_report: ValidationReport
    events: list[SemanticEvaluatorEvent] = Field(min_length=1)
    witness_sha256: Sha256


class ChecklistItem(StrictModel):
    item_id: ContractId
    ordinal: NonNegativeInt
    category: Literal["profile_dimension", "bounded_requirement"]
    dimension_id: Optional[DimensionId]
    requirement_id: Optional[ContractId]
    requirement_type: Optional[RequirementType]
    text: CleanText

    @model_validator(mode="after")
    def validate_item_owner(self) -> "ChecklistItem":
        if self.category == "profile_dimension":
            if self.dimension_id is None or self.requirement_id is not None:
                raise ValueError("dimension checklist item binding is invalid")
        elif self.requirement_id is None or self.requirement_type is None:
            raise ValueError("requirement checklist item binding is invalid")
        return self


class LintItem(StrictModel):
    item_id: ContractId
    ordinal: NonNegativeInt
    rule_id: Literal[
        "unresolved_placeholder",
        "empty_atx_heading",
        "duplicate_atx_heading",
        "unclosed_fenced_code",
        "malformed_markdown_link_destination",
    ]
    message: CleanText
    report_spans: list[SpanLocator]


class BaselinePayload(StrictModel):
    schema_id: ClassVar[str] = BASELINE_SCHEMA_ID
    schema_version: Literal[BASELINE_SCHEMA_ID]
    baseline_id: ContractId
    report_sha256: Sha256
    bounded_context_sha256: Sha256
    profile_sha256: Sha256
    checklist_id: Literal["structured_checklist_zh_v1"]
    lint_id: Literal["deterministic_lint_v1"]
    checklist_items: list[ChecklistItem]
    lint_items: list[LintItem]
    baseline_sha256: Sha256


class DuplicateAnnotation(StrictModel):
    baseline_item_id: ContractId
    finding_id: FindingId
    label: Literal["duplicate", "corroborating"]


class CompositionRecord(StrictModel):
    schema_id: ClassVar[str] = COMPOSITION_SCHEMA_ID
    schema_version: Literal[COMPOSITION_SCHEMA_ID]
    condition: Literal["matched_non_LLM", "actual_LAJ"]
    baseline_schema_id: Literal[BASELINE_SCHEMA_ID]
    baseline_sha256: Sha256
    baseline_payload: BaselinePayload
    laj_witness_sha256: Optional[Sha256]
    laj_run_sha256: Optional[Sha256]
    laj_run_status: Optional[RunStatus]
    laj_validation_status: Optional[Literal["accepted", "rejected", "incomplete"]]
    laj_reason_codes: list[ContractId]
    laj_advice_items: list[FindingProposal]
    duplicate_annotations: list[DuplicateAnnotation]
    composition_sha256: Sha256

    @model_validator(mode="after")
    def validate_additive_condition(self) -> "CompositionRecord":
        if self.baseline_payload.baseline_sha256 != self.baseline_sha256:
            raise ValueError("embedded baseline hash mismatch")
        if self.condition == "matched_non_LLM":
            if (
                self.laj_witness_sha256 is not None
                or self.laj_run_sha256 is not None
                or self.laj_run_status is not None
                or self.laj_validation_status is not None
                or self.laj_reason_codes
                or self.laj_advice_items
                or self.duplicate_annotations
            ):
                raise ValueError("matched baseline cannot include LAJ data")
        elif any(
            item is None
            for item in (
                self.laj_witness_sha256,
                self.laj_run_sha256,
                self.laj_run_status,
                self.laj_validation_status,
            )
        ):
            raise ValueError("actual LAJ composition requires witness status")
        if self.condition == "actual_LAJ":
            legal_pairs = {
                ("completed", "accepted"),
                ("incomplete", "incomplete"),
                ("provider_failed", "incomplete"),
                ("parser_failed", "rejected"),
                ("validation_failed", "rejected"),
                ("security_failed", "rejected"),
            }
            if (self.laj_run_status, self.laj_validation_status) not in legal_pairs:
                raise ValueError("actual LAJ status pair is invalid")
            if (
                self.laj_run_status != "completed"
                or self.laj_validation_status != "accepted"
            ) and self.laj_advice_items:
                raise ValueError("failed LAJ composition cannot display advice")
        if self.laj_reason_codes != sorted(set(self.laj_reason_codes)):
            raise ValueError("LAJ reason codes must be sorted and unique")
        if not self.laj_advice_items and self.duplicate_annotations:
            raise ValueError("annotations require displayable LAJ advice")
        return self


class PresentationRecord(StrictModel):
    schema_id: ClassVar[str] = PRESENTATION_SCHEMA_ID
    schema_version: Literal[PRESENTATION_SCHEMA_ID]
    presentation_id: ContractId
    condition: Literal["matched_non_LLM", "actual_LAJ"]
    composition_sha256: Sha256
    baseline_sha256: Sha256
    baseline_items: list[ChecklistItem]
    baseline_lint_items: list[LintItem]
    additional_semantic_findings: list[FindingProposal]
    laj_witness_sha256: Optional[Sha256]
    laj_run_status: Optional[RunStatus]
    laj_validation_status: Optional[Literal["accepted", "rejected", "incomplete"]]
    failure_reason_codes: list[ContractId]
    assessed_unit_count: NonNegativeInt
    finding_count: NonNegativeInt
    withheld_finding_count: NonNegativeInt
    abstention_count: NonNegativeInt
    failure_count: NonNegativeInt
    advisory_only: Literal[True]
    disclaimer: CleanText
    presentation_sha256: Sha256

    @model_validator(mode="after")
    def validate_failure_reasons(self) -> "PresentationRecord":
        if self.failure_reason_codes != sorted(set(self.failure_reason_codes)):
            raise ValueError("failure reason codes must be sorted and unique")
        if self.finding_count != len(self.additional_semantic_findings):
            raise ValueError("presentation finding count mismatch")
        if self.condition == "matched_non_LLM":
            if any(
                item is not None
                for item in (
                    self.laj_witness_sha256,
                    self.laj_run_status,
                    self.laj_validation_status,
                )
            ) or any(
                (
                    self.failure_reason_codes,
                    self.additional_semantic_findings,
                    self.withheld_finding_count,
                )
            ):
                raise ValueError("matched presentation cannot include LAJ data")
        elif any(
            item is None
            for item in (
                self.laj_witness_sha256,
                self.laj_run_status,
                self.laj_validation_status,
            )
        ):
            raise ValueError("actual LAJ presentation requires witness status")
        elif (
            self.laj_run_status != "completed"
            or self.laj_validation_status != "accepted"
        ) and self.additional_semantic_findings:
            raise ValueError("failed LAJ presentation cannot display findings")
        return self


_ZERO_SHA = "0" * 64
_ONE_SHA = "1" * 64
_EXAMPLE_BLOCK = {
    "block_id": "B000001",
    "ordinal": 0,
    "section_path": ["摘要"],
    "role": "paragraph",
    "text": "合成示例。",
    "text_sha256": _sha256_text("合成示例。"),
    "start_char": 0,
    "end_char": 5,
}
_EXAMPLE_REQUIREMENT = {
    "requirement_id": "REQ-001",
    "type": "must_answer",
    "text": "回答冻结状态。",
    "source_locator": "brief:B000001",
}
_EXAMPLE_SEVERITY = {
    "severe": "可能直接改变关键决策。",
    "major": "可能显著误导报告使用。",
    "minor": "局部问题且不改变主要决定。",
}
_EXAMPLE_SUB_ASPECT = {
    "sub_aspect_id": "status_consistency",
    "definition": "检查状态表述是否一致。",
    "positive_criterion": "相同状态在各处保持一致。",
    "exclusions": ["不判断外部事实。"],
    "severity_rubric": _EXAMPLE_SEVERITY,
    "abstention_conditions": ["报告内无法确定比较范围。"],
    "o3_handoff_conditions": ["需要打开外部来源。"],
    "eligible_requirement_types": [],
}
_EXAMPLE_DIMENSION = {
    "dimension_id": "cross_section_consistency",
    "scope_class": "O1",
    "definition": "检查跨章节内部一致性。",
    "positive_criterion": "相同实体和范围的陈述可以同时成立。",
    "exclusions": ["不判断外部事实。"],
    "severity_rubric": _EXAMPLE_SEVERITY,
    "abstention_conditions": ["报告内无法消除歧义。"],
    "o3_handoff_conditions": ["问题依赖外部证据。"],
    "eligible_requirement_types": [],
    "sub_aspects": [_EXAMPLE_SUB_ASPECT],
}
_EXAMPLE_TRANSPORT = {
    "provider_transport_only": True,
    "model_tools": False,
    "browser": False,
    "cross_run_memory": False,
    "provider_file_search": False,
}
_EXAMPLE_CONFIG = {
    "schema_version": INSTRUMENT_CONFIG_SCHEMA_ID,
    "instrument_config_id": "instrument-test-v1",
    "provider_id": "fake-provider",
    "model_id": "fake-model",
    "model_version": "unavailable",
    "language": "zh-CN",
    "decoding": {
        "temperature": 0.0,
        "top_p": 1.0,
        "max_output_tokens": 256,
        "seed": None,
    },
    "retry_policy": {
        "max_attempts": 1,
        "retryable_reason_codes": [],
        "backoff_schedule_ms": [],
    },
    "prompt_sizer": {
        "sizer_id": "fake-sizer",
        "sizer_version": "v1",
        "max_context_tokens": 4096,
        "reserved_output_tokens": 256,
    },
    "transport_policy": _EXAMPLE_TRANSPORT,
}
_EXAMPLE_UNIT = {
    "assessment_unit_id": "AU-000000000001",
    "trial_id": "trial-001",
    "report_sha256": _ZERO_SHA,
    "dimension_id": "cross_section_consistency",
    "sub_aspect_id": "status_consistency",
    "scope_class": "O1",
    "eligible_requirement_types": [],
}
_EXAMPLE_SPAN = {
    "report_sha256": _ZERO_SHA,
    "block_id": "B000001",
    "start_char": 0,
    "end_char": 1,
    "excerpt_sha256": _ONE_SHA,
}
_EXAMPLE_FINDING = {
    "finding_id": "F-000000000001",
    "assessment_unit_id": "AU-000000000001",
    "status": "proposal",
    "scope_class": "O1",
    "dimension_id": "cross_section_consistency",
    "severity": "major",
    "impact_scope": "key_conclusion",
    "report_spans": [_EXAMPLE_SPAN],
    "context_requirement_ids": [],
    "observation": "两个状态表述不一致。",
    "rationale": "两个片段描述相同阶段。",
    "severity_basis": "可能影响阶段判断。",
    "confidence_basis": "direct_cross_span_conflict",
    "external_premise_disclosure": "none",
    "recommended_human_action": "reconcile_status_language",
    "suggested_rewrite": None,
}
_EXAMPLE_FINDING_DRAFT = {
    key: value
    for key, value in _EXAMPLE_FINDING.items()
    if key not in {"finding_id", "status"}
}

ReaderArtifact.minimal_example = ReaderArtifact.full_example = {
    "schema_version": READER_ARTIFACT_SCHEMA_ID,
    "artifact_id": "reader-001",
    "report_sha256": _ZERO_SHA,
    "language": "zh-CN",
    "format": "normalized_markdown",
    "normalized_text_sha256": _ONE_SHA,
    "blocks": [_EXAMPLE_BLOCK],
}
BoundedContext.minimal_example = BoundedContext.full_example = {
    "schema_version": BOUNDED_CONTEXT_SCHEMA_ID,
    "context_id": "context-001",
    "context_sha256": _ZERO_SHA,
    "language": "zh-CN",
    "data_class": "synthetic",
    "requirements": [_EXAMPLE_REQUIREMENT],
}
EvaluatorProfile.minimal_example = EvaluatorProfile.full_example = {
    "schema_version": PROFILE_SCHEMA_ID,
    "profile_id": "research_design_report_zh_v1",
    "report_type": "research_design_report",
    "language": "zh-CN",
    "allowed_scope_classes": ["O1", "O2"],
    "dimensions": [_EXAMPLE_DIMENSION],
}
InstrumentConfig.minimal_example = InstrumentConfig.full_example = _EXAMPLE_CONFIG
AdmissionRequest.minimal_example = AdmissionRequest.full_example = {
    "schema_version": ADMISSION_REQUEST_SCHEMA_ID,
    "artifact_id": "reader-001",
    "trial_id": "trial-001",
    "report_bytes_hex": "23",
    "declared_report_sha256": _sha256_text("#"),
    "bounded_context": BoundedContext.minimal_example,
    "declared_bounded_context_sha256": _ZERO_SHA,
    "instrument_config": _EXAMPLE_CONFIG,
    "public_data_attestation": True,
    "private_or_confidential_material": False,
    "archive_root": None,
    "workspace_root": None,
}
InstrumentManifest.minimal_example = InstrumentManifest.full_example = {
    "schema_version": INSTRUMENT_MANIFEST_SCHEMA_ID,
    "manifest_id": "manifest-001",
    "frozen_design_sha256": _ZERO_SHA,
    "freeze_manifest_sha256": _ONE_SHA,
    "profile_sha256": _ZERO_SHA,
    "system_prompt_sha256": _ZERO_SHA,
    "dimension_prompt_sha256": _ZERO_SHA,
    "schema_sha256s": {
        schema_id: _ZERO_SHA for schema_id in sorted(_INSTRUMENT_SCHEMA_IDS)
    },
    "implementation_components": [
        {
            "component_id": name,
            "implementation_version": "v1",
            "source_sha256": _ZERO_SHA,
        }
        for name in (
            "parser",
            "validator",
            "normalizer",
            "unit_planner",
            "prompt_assembler",
        )
    ],
    "retry_policy_sha256": _ZERO_SHA,
    "decoding_sha256": _ZERO_SHA,
    "instrument_config_sha256": _ZERO_SHA,
    "provider_id": "fake-provider",
    "model_id": "fake-model",
    "model_version": "unavailable",
    "prompt_sizer_id": "fake-sizer",
    "prompt_sizer_version": "v1",
    "language": "zh-CN",
    "max_context_tokens": 4096,
    "reserved_output_tokens": 256,
    "transport_policy": _EXAMPLE_TRANSPORT,
    "instrument_sha256": _ZERO_SHA,
}
InputBinding.minimal_example = InputBinding.full_example = {
    "schema_version": INPUT_BINDING_SCHEMA_ID,
    "binding_id": "binding-001",
    "trial_id": "trial-001",
    "report_sha256": _ZERO_SHA,
    "normalized_text_sha256": _ONE_SHA,
    "bounded_context_sha256": _ZERO_SHA,
    "profile_sha256": _ZERO_SHA,
    "instrument_config_sha256": _ZERO_SHA,
    "language": "zh-CN",
    "data_class": "synthetic",
    "public_data_attestation": True,
    "private_or_confidential_material": False,
    "input_binding_sha256": _ZERO_SHA,
}
AssessmentPlan.minimal_example = AssessmentPlan.full_example = {
    "schema_version": ASSESSMENT_PLAN_SCHEMA_ID,
    "plan_id": "plan-001",
    "trial_id": "trial-001",
    "report_sha256": _ZERO_SHA,
    "profile_sha256": _ZERO_SHA,
    "units": [_EXAMPLE_UNIT],
    "assessment_plan_sha256": _ZERO_SHA,
}
DimensionResponse.minimal_example = DimensionResponse.full_example = {
    "schema_version": DIMENSION_RESPONSE_SCHEMA_ID,
    "trial_id": "trial-001",
    "dimension_id": "cross_section_consistency",
    "unit_results": [
        {
            "assessment_unit_id": "AU-000000000001",
            "disposition": "finding_emitted",
            "findings": [_EXAMPLE_FINDING_DRAFT],
        }
    ],
}
SemanticAssessmentRun.minimal_example = SemanticAssessmentRun.full_example = {
    "schema_version": RUN_SCHEMA_ID,
    "run_id": "run-001",
    "trial_id": "trial-001",
    "report_sha256": _ZERO_SHA,
    "bounded_context_sha256": _ZERO_SHA,
    "profile_sha256": _ZERO_SHA,
    "instrument_sha256": _ZERO_SHA,
    "assessment_plan_sha256": _ZERO_SHA,
    "run_status": "completed",
    "assessment_units": [],
    "findings": [],
    "handoffs": [],
    "attempt_refs": [],
    "event_stream_sha256": _ZERO_SHA,
}
ValidationReport.minimal_example = ValidationReport.full_example = {
    "schema_version": VALIDATION_REPORT_SCHEMA_ID,
    "run_id": "run-001",
    "trial_id": "trial-001",
    "validation_status": "accepted",
    "reason_codes": [],
    "accepted_finding_ids": [],
    "rejected_finding_ids": [],
    "planned_unit_count": 1,
    "disposed_unit_count": 1,
    "finding_count": 0,
    "abstention_count": 0,
    "handoff_count": 0,
    "raw_attempt_refs": ["attempt-001"],
}
SemanticEvaluatorEvent.minimal_example = SemanticEvaluatorEvent.full_example = {
    "schema_version": EVENT_SCHEMA_ID,
    "event_id": "event-001",
    "sequence": 1,
    "run_id": "run-001",
    "trial_id": "trial-001",
    "event_type": "admission_decided",
    "payload": {
        "event_type": "admission_decided",
        "admitted": True,
        "reason_codes": [],
    },
}
LajCompositionWitness.minimal_example = LajCompositionWitness.full_example = {
    "schema_version": LAJ_COMPOSITION_WITNESS_SCHEMA_ID,
    "input_binding": InputBinding.minimal_example,
    "report_evidence": {
        "artifact_id": "reader-001",
        "report_bytes_hex": "23",
        "report_sha256": _sha256_text("#"),
        "normalized_text_sha256": _sha256_text("#"),
        "evidence_sha256": _ZERO_SHA,
    },
    "reader_artifact": ReaderArtifact.minimal_example,
    "bounded_context": BoundedContext.minimal_example,
    "instrument_config": _EXAMPLE_CONFIG,
    "instrument_manifest": InstrumentManifest.minimal_example,
    "assessment_plan": AssessmentPlan.minimal_example,
    "dimension_attempt_evidence": [
        {
            "attempt_ref": "attempt-001",
            "dimension_id": "cross_section_consistency",
            "attempt_ordinal": 1,
            "prompt_request_sha256": _ZERO_SHA,
            "status": "completed",
            "reason_code": None,
            "raw_response_bytes_hex": "7b7d",
            "raw_response_sha256": _sha256_text("{}"),
            "forbidden_canary_values": [],
            "evidence_sha256": _ZERO_SHA,
        }
    ],
    "run": SemanticAssessmentRun.minimal_example,
    "validation_report": ValidationReport.minimal_example,
    "events": [SemanticEvaluatorEvent.minimal_example],
    "witness_sha256": _ZERO_SHA,
}
BaselinePayload.minimal_example = BaselinePayload.full_example = {
    "schema_version": BASELINE_SCHEMA_ID,
    "baseline_id": "baseline-001",
    "report_sha256": _ZERO_SHA,
    "bounded_context_sha256": _ZERO_SHA,
    "profile_sha256": _ZERO_SHA,
    "checklist_id": "structured_checklist_zh_v1",
    "lint_id": "deterministic_lint_v1",
    "checklist_items": [],
    "lint_items": [],
    "baseline_sha256": _ZERO_SHA,
}
CompositionRecord.minimal_example = CompositionRecord.full_example = {
    "schema_version": COMPOSITION_SCHEMA_ID,
    "condition": "matched_non_LLM",
    "baseline_schema_id": BASELINE_SCHEMA_ID,
    "baseline_sha256": _ZERO_SHA,
    "baseline_payload": BaselinePayload.minimal_example,
    "laj_witness_sha256": None,
    "laj_run_sha256": None,
    "laj_run_status": None,
    "laj_validation_status": None,
    "laj_reason_codes": [],
    "laj_advice_items": [],
    "duplicate_annotations": [],
    "composition_sha256": _ZERO_SHA,
}
PresentationRecord.minimal_example = PresentationRecord.full_example = {
    "schema_version": PRESENTATION_SCHEMA_ID,
    "presentation_id": "presentation-001",
    "condition": "matched_non_LLM",
    "composition_sha256": _ZERO_SHA,
    "baseline_sha256": _ZERO_SHA,
    "baseline_items": [],
    "baseline_lint_items": [],
    "additional_semantic_findings": [],
    "laj_witness_sha256": None,
    "laj_run_status": None,
    "laj_validation_status": None,
    "failure_reason_codes": [],
    "assessed_unit_count": 0,
    "finding_count": 0,
    "withheld_finding_count": 0,
    "abstention_count": 0,
    "failure_count": 0,
    "advisory_only": True,
    "disclaimer": "本记录仅供研究复核，不代表报告正确、完整或可交付。",
    "presentation_sha256": _ZERO_SHA,
}


SEMANTIC_EVALUATOR_CONTRACT_MODELS: tuple[type[StrictModel], ...] = (
    ReaderArtifact,
    BoundedContext,
    EvaluatorProfile,
    InstrumentConfig,
    AdmissionRequest,
    InstrumentManifest,
    InputBinding,
    AssessmentPlan,
    DimensionResponse,
    SemanticAssessmentRun,
    ValidationReport,
    SemanticEvaluatorEvent,
    LajCompositionWitness,
    BaselinePayload,
    CompositionRecord,
    PresentationRecord,
)

SEMANTIC_EVALUATOR_CONTRACT_IDS: tuple[str, ...] = tuple(
    model.schema_id for model in SEMANTIC_EVALUATOR_CONTRACT_MODELS
)


__all__ = [
    "ADMISSION_REQUEST_SCHEMA_ID",
    "ASSESSMENT_PLAN_SCHEMA_ID",
    "BASELINE_SCHEMA_ID",
    "BOUNDED_CONTEXT_SCHEMA_ID",
    "COMPOSITION_SCHEMA_ID",
    "DIMENSION_RESPONSE_SCHEMA_ID",
    "EVENT_SCHEMA_ID",
    "INPUT_BINDING_SCHEMA_ID",
    "INSTRUMENT_CONFIG_SCHEMA_ID",
    "INSTRUMENT_MANIFEST_SCHEMA_ID",
    "LAJ_COMPOSITION_WITNESS_SCHEMA_ID",
    "PRESENTATION_SCHEMA_ID",
    "PROFILE_SCHEMA_ID",
    "READER_ARTIFACT_SCHEMA_ID",
    "RUN_SCHEMA_ID",
    "SEMANTIC_EVALUATOR_CONTRACT_IDS",
    "SEMANTIC_EVALUATOR_CONTRACT_MODELS",
    "VALIDATION_REPORT_SCHEMA_ID",
    "AbstainConflictingContextResult",
    "AbstainInsufficientContextResult",
    "AbstainUnableToAssessResult",
    "AdmissionRequest",
    "AdmittedReportEvidence",
    "AssessmentPlan",
    "AssessmentUnit",
    "AssessmentUnitId",
    "AssessmentUnitOutcome",
    "AttemptRef",
    "BaselinePayload",
    "BlockId",
    "BoundedContext",
    "BoundedRequirement",
    "CharOffset",
    "ChecklistItem",
    "CompositionRecord",
    "ConfidenceBasis",
    "DataClass",
    "DecodingConfig",
    "DimensionId",
    "DimensionAttemptEvidence",
    "DimensionProfile",
    "DimensionResponse",
    "Disposition",
    "DuplicateAnnotation",
    "EvaluatorProfile",
    "FindingEmittedResult",
    "FindingDraft",
    "FindingId",
    "FindingProposal",
    "HandoffId",
    "ImplementationComponent",
    "InputBinding",
    "InstrumentConfig",
    "InstrumentManifest",
    "JsonObject",
    "Language",
    "LintItem",
    "LajCompositionWitness",
    "NoFindingResult",
    "O3Handoff",
    "O3HandoffDraft",
    "PresentationRecord",
    "PromptSizerConfig",
    "ReaderArtifact",
    "ReaderBlock",
    "RequirementType",
    "RetryPolicy",
    "RubricNotApplicableResult",
    "ScopeClass",
    "SemanticAssessmentRun",
    "SemanticEvaluatorEvent",
    "Severity",
    "SeverityRubric",
    "SpanLocator",
    "SubAspectProfile",
    "TransportPolicy",
    "UnitResult",
    "ValidationReport",
]
