"""Strict, advisory-only contracts for reproducible LAJ studies."""

from __future__ import annotations

from typing import Annotated, ClassVar, Literal

from pydantic import Field, StrictBool, StrictInt, model_validator

from multi_agent_brief.contracts.v2 import CleanText, ContractId, Sha256, StrictModel
from multi_agent_brief.semantic_evaluator.contracts import DimensionId, Severity
from multi_agent_brief.semantic_evaluator.serialization import canonical_model_sha256


STUDY_DECLARATION_SCHEMA_ID = "briefloop.semantic_evaluator.study_declaration.v1"
SENSITIVITY_GROUND_TRUTH_SCHEMA_ID = "briefloop.laj_sensitivity_ground_truth.v1"
SENSITIVITY_MANIFEST_SCHEMA_ID = "briefloop.semantic_evaluator.sensitivity_manifest.v1"
RESOLVED_SENSITIVITY_CASE_SCHEMA_ID = (
    "briefloop.semantic_evaluator.resolved_sensitivity_case.v1"
)
PROVIDER_BUDGET_POLICY_SCHEMA_ID = (
    "briefloop.semantic_evaluator.provider_budget_policy.v1"
)
PROVIDER_EXECUTION_AUTHORIZATION_SCHEMA_ID = (
    "briefloop.semantic_evaluator.provider_execution_authorization.v1"
)
BUDGET_PREFLIGHT_SCHEMA_ID = "briefloop.semantic_evaluator.budget_preflight.v1"
STUDY_EXECUTION_EVIDENCE_SCHEMA_ID = (
    "briefloop.semantic_evaluator.study_execution_evidence.v1"
)
SENSITIVITY_COMPARISON_SCHEMA_ID = (
    "briefloop.semantic_evaluator.sensitivity_comparison.v1"
)


def _check_hash(model: StrictModel, field: str) -> None:
    if getattr(model, field) != canonical_model_sha256(model, exclude=(field,)):
        raise ValueError("study contract hash mismatch")


class LajStudyDeclarationV1(StrictModel):
    schema_id: ClassVar[str] = STUDY_DECLARATION_SCHEMA_ID
    schema_version: Literal[STUDY_DECLARATION_SCHEMA_ID]
    study_id: ContractId
    study_kind: Literal["product_utility_check", "sensitivity_calibration"]
    artifact_class: Literal[
        "reader_facing_business_report",
        "technical_postmortem",
        "self_diagnosing_case_study",
        "synthetic_fixture",
        "other",
    ]
    report_sha256: Sha256
    origin_label: CleanText
    public_safe: StrictBool
    synthetic: StrictBool
    self_diagnosing: StrictBool
    reader_facing: StrictBool
    expected_mutation_count: StrictInt = Field(ge=0)
    declaration_sha256: Sha256

    @model_validator(mode="after")
    def validate_hash(self) -> "LajStudyDeclarationV1":
        _check_hash(self, "declaration_sha256")
        return self


class SensitivityMutationV1(StrictModel):
    mutation_id: ContractId
    before_text: CleanText
    after_text: CleanText
    inserted_text: CleanText
    expected_primary_dimension: DimensionId
    expected_secondary_dimension: DimensionId | None = None
    expected_severity: Severity
    public_label: CleanText
    rationale: CleanText


class SensitivitySourceMutationV1(StrictModel):
    mutation_id: ContractId
    label: CleanText
    location: CleanText
    before_text: CleanText
    after_text: CleanText
    inserted_text: CleanText
    expected_primary_dimension: DimensionId
    expected_secondary_dimension: DimensionId | None = None
    expected_severity: Severity
    in_scope_basis: CleanText


class SensitivityReportLocatorV1(StrictModel):
    path: CleanText
    sha256: Sha256


class SensitivityProviderExclusionV1(StrictModel):
    manifest_path: CleanText
    manifest_is_outside_admission_workspace_root: StrictBool
    admission_workspace_root: CleanText
    archive_root: CleanText
    manifest_path_is_not_a_cli_argument: StrictBool
    manifest_bytes_are_not_an_admission_input: StrictBool
    manifest_bytes_are_not_in_prompt_or_provider_request: StrictBool
    proof_method: CleanText


class SensitivityEvaluationRuleV1(StrictModel):
    human_is_detection_authority: StrictBool
    provider_does_not_receive_this_manifest: StrictBool
    no_finding_is_neutral: StrictBool
    sensitivity_pass: Literal["at_least_3_of_4"]
    sensitivity_partial: Literal["exactly_2_of_4"]
    sensitivity_fail: Literal[
        "0_or_1_of_4_or_invalid_archive_or_false_quality_pass_claim"
    ]


class LajSensitivityGroundTruthSourceV1(StrictModel):
    """Exact Human-authored input; local locators never enter execution records."""

    schema_id: ClassVar[str] = SENSITIVITY_GROUND_TRUTH_SCHEMA_ID
    schema_version: Literal[SENSITIVITY_GROUND_TRUTH_SCHEMA_ID]
    classification: Literal["private_provider_excluded_calibration_evidence"]
    control_report: SensitivityReportLocatorV1
    mutated_report: SensitivityReportLocatorV1
    mutation_count: StrictInt = Field(gt=0)
    mutations: list[SensitivitySourceMutationV1] = Field(min_length=1)
    provider_exclusion: SensitivityProviderExclusionV1
    evaluation_rule: SensitivityEvaluationRuleV1

    @model_validator(mode="after")
    def validate_source_inventory(self) -> "LajSensitivityGroundTruthSourceV1":
        ids = [item.mutation_id for item in self.mutations]
        if self.mutation_count != len(self.mutations) or ids != [
            f"M{index}" for index in range(1, len(ids) + 1)
        ]:
            raise ValueError("sensitivity source inventory mismatch")
        exclusion = self.provider_exclusion
        if not all(
            (
                exclusion.manifest_is_outside_admission_workspace_root,
                exclusion.manifest_path_is_not_a_cli_argument,
                exclusion.manifest_bytes_are_not_an_admission_input,
                exclusion.manifest_bytes_are_not_in_prompt_or_provider_request,
                self.evaluation_rule.human_is_detection_authority,
                self.evaluation_rule.provider_does_not_receive_this_manifest,
                self.evaluation_rule.no_finding_is_neutral,
            )
        ):
            raise ValueError("provider exclusion attestation mismatch")
        return self


class LajSensitivityManifestV1(StrictModel):
    schema_id: ClassVar[str] = SENSITIVITY_MANIFEST_SCHEMA_ID
    schema_version: Literal[SENSITIVITY_MANIFEST_SCHEMA_ID]
    source_manifest_sha256: Sha256
    control_report_sha256: Sha256
    mutated_report_sha256: Sha256
    mutation_count: StrictInt = Field(gt=0)
    mutations: list[SensitivityMutationV1] = Field(min_length=1)
    manifest_sha256: Sha256

    @model_validator(mode="after")
    def validate_inventory(self) -> "LajSensitivityManifestV1":
        ids = [item.mutation_id for item in self.mutations]
        if self.mutation_count != len(self.mutations) or len(ids) != len(set(ids)):
            raise ValueError("sensitivity mutation inventory mismatch")
        if ids != [f"M{index}" for index in range(1, len(ids) + 1)]:
            raise ValueError("sensitivity mutation order mismatch")
        _check_hash(self, "manifest_sha256")
        return self


class ResolvedSensitivityMutationV1(StrictModel):
    mutation_id: ContractId
    mutated_report_sha256: Sha256
    normalized_text_sha256: Sha256
    block_id: ContractId
    start_char: StrictInt = Field(ge=0)
    end_char: StrictInt = Field(gt=0)
    excerpt_sha256: Sha256
    expected_primary_dimension: DimensionId
    expected_secondary_dimension: DimensionId | None = None
    expected_severity: Severity

    @model_validator(mode="after")
    def validate_offsets(self) -> "ResolvedSensitivityMutationV1":
        if self.start_char >= self.end_char:
            raise ValueError("resolved sensitivity offsets invalid")
        return self


class ResolvedSensitivityCaseV1(StrictModel):
    schema_id: ClassVar[str] = RESOLVED_SENSITIVITY_CASE_SCHEMA_ID
    schema_version: Literal[RESOLVED_SENSITIVITY_CASE_SCHEMA_ID]
    study_id: ContractId
    study_declaration_sha256: Sha256
    source_manifest_sha256: Sha256
    control_report_sha256: Sha256
    mutated_report_sha256: Sha256
    normalized_text_sha256: Sha256
    resolved_mutation_count: StrictInt = Field(gt=0)
    resolved_mutations: list[ResolvedSensitivityMutationV1] = Field(min_length=1)
    case_sha256: Sha256

    @model_validator(mode="after")
    def validate_case(self) -> "ResolvedSensitivityCaseV1":
        ids = [item.mutation_id for item in self.resolved_mutations]
        if self.resolved_mutation_count != len(ids) or ids != [
            f"M{index}" for index in range(1, len(ids) + 1)
        ]:
            raise ValueError("resolved mutation inventory mismatch")
        if any(
            item.mutated_report_sha256 != self.mutated_report_sha256
            or item.normalized_text_sha256 != self.normalized_text_sha256
            for item in self.resolved_mutations
        ):
            raise ValueError("resolved mutation binding mismatch")
        _check_hash(self, "case_sha256")
        return self


class LajProviderBudgetPolicyV1(StrictModel):
    schema_id: ClassVar[str] = PROVIDER_BUDGET_POLICY_SCHEMA_ID
    schema_version: Literal[PROVIDER_BUDGET_POLICY_SCHEMA_ID]
    max_provider_calls: StrictInt = Field(gt=0)
    max_input_tokens: StrictInt = Field(gt=0)
    policy_sha256: Sha256

    @model_validator(mode="after")
    def validate_hash(self) -> "LajProviderBudgetPolicyV1":
        _check_hash(self, "policy_sha256")
        return self


class LajProviderExecutionAuthorizationV1(StrictModel):
    schema_id: ClassVar[str] = PROVIDER_EXECUTION_AUTHORIZATION_SCHEMA_ID
    schema_version: Literal[PROVIDER_EXECUTION_AUTHORIZATION_SCHEMA_ID]
    study_id: ContractId
    trial_id: ContractId
    report_sha256: Sha256
    bounded_context_sha256: Sha256
    instrument_sha256: Sha256
    assessment_plan_sha256: Sha256
    ordered_prompt_request_sha256s: list[Sha256] = Field(min_length=9, max_length=9)
    budget_policy_sha256: Sha256
    authorization_sha256: Sha256

    @model_validator(mode="after")
    def validate_authorization(self) -> "LajProviderExecutionAuthorizationV1":
        if len(self.ordered_prompt_request_sha256s) != len(
            set(self.ordered_prompt_request_sha256s)
        ):
            raise ValueError("prompt request inventory mismatch")
        _check_hash(self, "authorization_sha256")
        return self


class LajBudgetPreflightV1(StrictModel):
    schema_id: ClassVar[str] = BUDGET_PREFLIGHT_SCHEMA_ID
    schema_version: Literal[BUDGET_PREFLIGHT_SCHEMA_ID]
    authorization_sha256: Sha256
    input_binding_sha256: Sha256
    instrument_sha256: Sha256
    assessment_plan_sha256: Sha256
    ordered_prompt_request_sha256s: list[Sha256] = Field(min_length=9, max_length=9)
    prompt_sizer_id: ContractId
    prompt_sizer_version: ContractId
    tokenizer_package: CleanText
    tokenizer_version: CleanText
    tokenizer_encoding: CleanText
    count_semantics: Literal[
        "exact_tokenizer",
        "conservative_utf8_byte_upper_bound",
        "synthetic_test_counter",
    ]
    per_prompt_input_counts: list[Annotated[StrictInt, Field(gt=0)]] = Field(
        min_length=9, max_length=9
    )
    max_attempts: StrictInt = Field(gt=0)
    planned_provider_calls: StrictInt = Field(gt=0)
    planned_input_token_upper_bound: StrictInt = Field(gt=0)
    max_provider_calls: StrictInt = Field(gt=0)
    max_input_tokens: StrictInt = Field(gt=0)
    decision: Literal["allowed", "blocked"]
    reason_codes: list[
        Literal[
            "budget_provider_call_limit_exceeded",
            "budget_input_token_limit_exceeded",
        ]
    ]
    preflight_sha256: Sha256

    @model_validator(mode="after")
    def validate_preflight(self) -> "LajBudgetPreflightV1":
        prompt_count = len(self.ordered_prompt_request_sha256s)
        if len(self.per_prompt_input_counts) != prompt_count:
            raise ValueError("budget count inventory mismatch")
        if self.planned_provider_calls != prompt_count * self.max_attempts:
            raise ValueError("provider call bound mismatch")
        if (
            self.planned_input_token_upper_bound
            != sum(self.per_prompt_input_counts) * self.max_attempts
        ):
            raise ValueError("input token bound mismatch")
        expected: list[str] = []
        if self.planned_provider_calls > self.max_provider_calls:
            expected.append("budget_provider_call_limit_exceeded")
        elif self.planned_input_token_upper_bound > self.max_input_tokens:
            expected.append("budget_input_token_limit_exceeded")
        if self.reason_codes != expected or self.decision != (
            "blocked" if expected else "allowed"
        ):
            raise ValueError("budget decision mismatch")
        _check_hash(self, "preflight_sha256")
        return self


class LajStudyExecutionEvidenceV1(StrictModel):
    schema_id: ClassVar[str] = STUDY_EXECUTION_EVIDENCE_SCHEMA_ID
    schema_version: Literal[STUDY_EXECUTION_EVIDENCE_SCHEMA_ID]
    study_id: ContractId
    trial_id: ContractId
    budget_policy: LajProviderBudgetPolicyV1
    authorization: LajProviderExecutionAuthorizationV1
    preflight: LajBudgetPreflightV1
    authorization_sha256: Sha256
    preflight_sha256: Sha256
    shadow_request_sha256: Sha256
    receipt_sha256: Sha256
    archive_manifest_sha256: Sha256
    execution_sha256: Sha256
    report_sha256: Sha256
    provider_usage: Literal["reported", "not_reported"]
    input_tokens: Annotated[StrictInt, Field(ge=0)] | None
    output_tokens: Annotated[StrictInt, Field(ge=0)] | None
    total_tokens: Annotated[StrictInt, Field(ge=0)] | None
    study_source_sha256: Sha256
    study_schema_sha256s: dict[ContractId, Sha256]
    runner_source_sha256: Sha256
    evidence_sha256: Sha256

    @model_validator(mode="after")
    def validate_evidence(self) -> "LajStudyExecutionEvidenceV1":
        if (
            self.authorization.authorization_sha256 != self.authorization_sha256
            or self.authorization.budget_policy_sha256
            != self.budget_policy.policy_sha256
            or self.preflight.preflight_sha256 != self.preflight_sha256
            or self.preflight.authorization_sha256 != self.authorization_sha256
            or self.preflight.decision != "allowed"
            or self.preflight.reason_codes
            or self.authorization.study_id != self.study_id
            or self.authorization.trial_id != self.trial_id
            or self.authorization.report_sha256 != self.report_sha256
            or self.authorization.instrument_sha256 != self.preflight.instrument_sha256
            or self.authorization.assessment_plan_sha256
            != self.preflight.assessment_plan_sha256
            or self.authorization.ordered_prompt_request_sha256s
            != self.preflight.ordered_prompt_request_sha256s
            or self.preflight.max_provider_calls
            != self.budget_policy.max_provider_calls
            or self.preflight.max_input_tokens != self.budget_policy.max_input_tokens
        ):
            raise ValueError("study execution evidence binding mismatch")
        values = (self.input_tokens, self.output_tokens, self.total_tokens)
        if self.provider_usage == "not_reported" and any(v is not None for v in values):
            raise ValueError("unreported usage cannot contain values")
        if self.provider_usage == "reported" and any(v is None for v in values):
            raise ValueError("reported usage requires all values")
        if (
            self.provider_usage == "reported"
            and self.total_tokens != self.input_tokens + self.output_tokens
        ):
            raise ValueError("reported usage total mismatch")
        _check_hash(self, "evidence_sha256")
        return self


class SensitivityCandidateLinkV1(StrictModel):
    finding_id: ContractId
    dimension_id: DimensionId
    block_id: ContractId
    overlap_start_char: StrictInt = Field(ge=0)
    overlap_end_char: StrictInt = Field(gt=0)

    @model_validator(mode="after")
    def validate_overlap(self) -> "SensitivityCandidateLinkV1":
        if self.overlap_start_char >= self.overlap_end_char:
            raise ValueError("candidate overlap invalid")
        return self


class SensitivityComparisonRowV1(StrictModel):
    mutation_id: ContractId
    candidate_links: list[SensitivityCandidateLinkV1]
    human_adjudication: Literal["unreviewed"]


class LajSensitivityComparisonV1(StrictModel):
    schema_id: ClassVar[str] = SENSITIVITY_COMPARISON_SCHEMA_ID
    schema_version: Literal[SENSITIVITY_COMPARISON_SCHEMA_ID]
    study_id: ContractId
    case_sha256: Sha256
    execution_evidence_sha256: Sha256 | None
    archive_manifest_sha256: Sha256 | None
    receipt_sha256: Sha256 | None
    report_sha256: Sha256
    state: Literal[
        "ready_for_human_adjudication",
        "not_run",
        "budget_blocked",
        "invalid",
        "ineligible",
    ]
    rows: list[SensitivityComparisonRowV1]
    reason_codes: list[
        Literal[
            "utility_target_ineligible",
            "budget_provider_call_limit_exceeded",
            "budget_input_token_limit_exceeded",
            "study_execution_evidence_incomplete",
            "study_execution_binding_mismatch",
            "sensitivity_comparison_invalid",
        ]
    ]
    comparison_sha256: Sha256

    @model_validator(mode="after")
    def validate_comparison(self) -> "LajSensitivityComparisonV1":
        mutation_ids = [row.mutation_id for row in self.rows]
        if len(mutation_ids) != len(set(mutation_ids)):
            raise ValueError("comparison mutation inventory mismatch")
        if self.state == "ready_for_human_adjudication" and self.reason_codes:
            raise ValueError("ready comparison cannot carry failure reasons")
        _check_hash(self, "comparison_sha256")
        return self


STUDY_CONTRACT_MODELS: tuple[type[StrictModel], ...] = (
    LajStudyDeclarationV1,
    LajSensitivityGroundTruthSourceV1,
    LajSensitivityManifestV1,
    ResolvedSensitivityCaseV1,
    LajProviderBudgetPolicyV1,
    LajProviderExecutionAuthorizationV1,
    LajBudgetPreflightV1,
    LajStudyExecutionEvidenceV1,
    LajSensitivityComparisonV1,
)


__all__ = [
    name
    for name in globals()
    if name.startswith("Laj")
    or name.startswith("Resolved")
    or name.startswith("Sensitivity")
    or name == "STUDY_CONTRACT_MODELS"
]
