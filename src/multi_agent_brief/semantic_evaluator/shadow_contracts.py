"""Strict v4 contracts for private replayable Semantic Evaluator evidence."""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import Field, StrictBool, StrictInt, StrictStr, model_validator

from multi_agent_brief.contracts.v2 import (
    ContractId,
    IsoDateTime,
    NonNegativeInt,
    PositiveInt,
    Sha256,
    StrictModel,
)
from multi_agent_brief.semantic_evaluator.adapter import (
    PROVIDER_BOUNDARY_FACTS_SCHEMA_ID,
    BoundaryFactState,
    EnvelopeInvalidCode,
    ExternalTextFactV4,
    ExternalTextInvalidCode,
    HttpStatusFactV4,
    HttpStatusInvalidCode,
    KernelAttemptFailureReason,
    ProviderBoundaryFactsV4,
    ProviderShadowReason,
    ProviderTransportKind,
    ResponseEnvelopeFactV4,
    classify_provider_outcome_v4,
    make_provider_boundary_facts_v4,
)
from multi_agent_brief.semantic_evaluator.serialization import canonical_model_sha256


SHADOW_EXECUTION_POLICY_SCHEMA_ID = (
    "briefloop.semantic_evaluator.shadow_execution_policy.v4"
)
SHADOW_EXECUTION_MANIFEST_SCHEMA_ID = (
    "briefloop.semantic_evaluator.shadow_execution_manifest.v4"
)
SHADOW_RUN_REQUEST_SCHEMA_ID = "briefloop.semantic_evaluator.shadow_run_request.v4"
PROVIDER_ATTEMPT_SCHEMA_ID = "briefloop.semantic_evaluator.provider_attempt.v4"
SHADOW_ARCHIVE_MANIFEST_SCHEMA_ID = (
    "briefloop.semantic_evaluator.shadow_archive_manifest.v4"
)
SHADOW_RUN_RECEIPT_SCHEMA_ID = "briefloop.semantic_evaluator.shadow_run_receipt.v4"

SHADOW_SCHEMA_IDS = (
    PROVIDER_BOUNDARY_FACTS_SCHEMA_ID,
    PROVIDER_ATTEMPT_SCHEMA_ID,
    SHADOW_ARCHIVE_MANIFEST_SCHEMA_ID,
    SHADOW_EXECUTION_MANIFEST_SCHEMA_ID,
    SHADOW_EXECUTION_POLICY_SCHEMA_ID,
    SHADOW_RUN_RECEIPT_SCHEMA_ID,
    SHADOW_RUN_REQUEST_SCHEMA_ID,
)

AdapterIdV4 = Literal["openai_responses_v4", "synthetic_fixture_v4"]


def _safe_model_hash(model: StrictModel, *, exclude: tuple[str, ...]) -> str:
    try:
        return canonical_model_sha256(model, exclude=exclude)
    except (TypeError, ValueError, UnicodeError):
        raise ValueError("shadow contract canonicalization failed") from None


class ExternalTextFactRecordV4(StrictModel):
    schema_id: ClassVar[str] = "briefloop.semantic_evaluator.external_text_fact.v4"
    state: BoundaryFactState
    utf8_hex: StrictStr | None
    utf8_sha256: Sha256 | None
    invalid_code: ExternalTextInvalidCode | None

    @model_validator(mode="after")
    def validate_shape(self) -> "ExternalTextFactRecordV4":
        try:
            ExternalTextFactV4(
                self.state, self.utf8_hex, self.utf8_sha256, self.invalid_code
            )
        except TypeError:
            raise ValueError("external text fact shape mismatch") from None
        return self

    def to_runtime(self) -> ExternalTextFactV4:
        return ExternalTextFactV4(
            self.state, self.utf8_hex, self.utf8_sha256, self.invalid_code
        )

    @classmethod
    def from_runtime(cls, fact: ExternalTextFactV4) -> "ExternalTextFactRecordV4":
        return cls(
            state=fact.state,
            utf8_hex=fact.utf8_hex,
            utf8_sha256=fact.utf8_sha256,
            invalid_code=fact.invalid_code,
        )


class ResponseEnvelopeFactRecordV4(StrictModel):
    schema_id: ClassVar[str] = "briefloop.semantic_evaluator.response_envelope_fact.v4"
    state: BoundaryFactState
    raw_size_bytes: NonNegativeInt | None
    raw_sha256: Sha256 | None
    invalid_code: EnvelopeInvalidCode | None

    @model_validator(mode="after")
    def validate_shape(self) -> "ResponseEnvelopeFactRecordV4":
        try:
            ResponseEnvelopeFactV4(
                self.state,
                self.raw_size_bytes,
                self.raw_sha256,
                self.invalid_code,
            )
        except TypeError:
            raise ValueError("response envelope fact shape mismatch") from None
        return self

    def to_runtime(self) -> ResponseEnvelopeFactV4:
        return ResponseEnvelopeFactV4(
            self.state,
            self.raw_size_bytes,
            self.raw_sha256,
            self.invalid_code,
        )

    @classmethod
    def from_runtime(
        cls, fact: ResponseEnvelopeFactV4
    ) -> "ResponseEnvelopeFactRecordV4":
        return cls(
            state=fact.state,
            raw_size_bytes=fact.raw_size_bytes,
            raw_sha256=fact.raw_sha256,
            invalid_code=fact.invalid_code,
        )


class HttpStatusFactRecordV4(StrictModel):
    schema_id: ClassVar[str] = "briefloop.semantic_evaluator.http_status_fact.v4"
    state: BoundaryFactState
    value: StrictInt | None = Field(default=None, ge=100, le=599)
    invalid_code: HttpStatusInvalidCode | None

    @model_validator(mode="after")
    def validate_shape(self) -> "HttpStatusFactRecordV4":
        try:
            HttpStatusFactV4(self.state, self.value, self.invalid_code)
        except TypeError:
            raise ValueError("HTTP status fact shape mismatch") from None
        return self

    def to_runtime(self) -> HttpStatusFactV4:
        return HttpStatusFactV4(self.state, self.value, self.invalid_code)

    @classmethod
    def from_runtime(cls, fact: HttpStatusFactV4) -> "HttpStatusFactRecordV4":
        return cls(state=fact.state, value=fact.value, invalid_code=fact.invalid_code)


class ProviderBoundaryFactsRecordV4(StrictModel):
    schema_id: ClassVar[str] = PROVIDER_BOUNDARY_FACTS_SCHEMA_ID
    schema_version: Literal["briefloop.semantic_evaluator.provider_boundary_facts.v4"]
    envelope: ResponseEnvelopeFactRecordV4
    status: ExternalTextFactRecordV4
    response_id: ExternalTextFactRecordV4
    provider_identity: ExternalTextFactRecordV4
    model_identity: ExternalTextFactRecordV4
    output: ExternalTextFactRecordV4
    http_status: HttpStatusFactRecordV4
    transport_kind: ProviderTransportKind
    boundary_facts_sha256: Sha256

    @model_validator(mode="after")
    def validate_hash(self) -> "ProviderBoundaryFactsRecordV4":
        runtime = make_provider_boundary_facts_v4(
            envelope=self.envelope.to_runtime(),
            status=self.status.to_runtime(),
            response_id=self.response_id.to_runtime(),
            provider_identity=self.provider_identity.to_runtime(),
            model_identity=self.model_identity.to_runtime(),
            output=self.output.to_runtime(),
            http_status=self.http_status.to_runtime(),
            transport_kind=self.transport_kind,
        )
        if runtime.boundary_facts_sha256 != self.boundary_facts_sha256:
            raise ValueError("provider boundary facts hash mismatch")
        return self

    def to_runtime(self) -> ProviderBoundaryFactsV4:
        return make_provider_boundary_facts_v4(
            envelope=self.envelope.to_runtime(),
            status=self.status.to_runtime(),
            response_id=self.response_id.to_runtime(),
            provider_identity=self.provider_identity.to_runtime(),
            model_identity=self.model_identity.to_runtime(),
            output=self.output.to_runtime(),
            http_status=self.http_status.to_runtime(),
            transport_kind=self.transport_kind,
        )

    @classmethod
    def from_runtime(
        cls, facts: ProviderBoundaryFactsV4
    ) -> "ProviderBoundaryFactsRecordV4":
        return cls(
            schema_version=facts.schema_version,
            envelope=ResponseEnvelopeFactRecordV4.from_runtime(facts.envelope),
            status=ExternalTextFactRecordV4.from_runtime(facts.status),
            response_id=ExternalTextFactRecordV4.from_runtime(facts.response_id),
            provider_identity=ExternalTextFactRecordV4.from_runtime(
                facts.provider_identity
            ),
            model_identity=ExternalTextFactRecordV4.from_runtime(facts.model_identity),
            output=ExternalTextFactRecordV4.from_runtime(facts.output),
            http_status=HttpStatusFactRecordV4.from_runtime(facts.http_status),
            transport_kind=facts.transport_kind,
            boundary_facts_sha256=facts.boundary_facts_sha256,
        )


class ProviderAttemptRecordV4(StrictModel):
    schema_id: ClassVar[str] = PROVIDER_ATTEMPT_SCHEMA_ID
    schema_version: Literal["briefloop.semantic_evaluator.provider_attempt.v4"]
    attempt_ref: ContractId
    trial_id: ContractId
    dimension_id: ContractId
    attempt_ordinal: PositiveInt
    prompt_request_sha256: Sha256
    adapter_id: AdapterIdV4
    provider_id: ContractId
    requested_model_id: ContractId
    expected_model_version_utf8_hex: StrictStr
    facts: ProviderBoundaryFactsRecordV4
    attempt_status: Literal["completed", "failed"]
    shadow_reason: ProviderShadowReason | None
    kernel_reason: KernelAttemptFailureReason | None
    retry_eligible: StrictBool
    output_eligible: StrictBool
    request_projection_sha256: Sha256
    raw_transport_response_sha256: Sha256 | None
    extracted_output_sha256: Sha256 | None
    input_tokens: NonNegativeInt | None
    output_tokens: NonNegativeInt | None
    total_tokens: NonNegativeInt | None
    started_at: IsoDateTime
    completed_at: IsoDateTime
    attempt_record_sha256: Sha256

    @model_validator(mode="after")
    def validate_classifier_and_hash(self) -> "ProviderAttemptRecordV4":
        try:
            expected = bytes.fromhex(self.expected_model_version_utf8_hex)
            expected.decode("utf-8", errors="strict")
        except (ValueError, UnicodeDecodeError):
            raise ValueError("expected model identity encoding mismatch") from None
        if not expected:
            raise ValueError("expected model identity encoding mismatch")
        outcome = classify_provider_outcome_v4(
            self.facts.to_runtime(), expected_model_version_utf8=expected
        )
        if (
            self.attempt_status != outcome.attempt_status
            or self.shadow_reason != outcome.shadow_reason
            or self.kernel_reason != outcome.kernel_reason
            or self.retry_eligible != outcome.retry_eligible
            or self.output_eligible != outcome.output_eligible
        ):
            raise ValueError("provider outcome projection mismatch")
        if self.output_eligible != (self.extracted_output_sha256 is not None):
            raise ValueError("provider output inventory mismatch")
        if self.facts.envelope.state == "absent":
            if self.raw_transport_response_sha256 is not None:
                raise ValueError("provider envelope inventory mismatch")
        elif self.raw_transport_response_sha256 != self.facts.envelope.raw_sha256:
            raise ValueError("provider envelope hash mismatch")
        if self.total_tokens is not None and (
            self.input_tokens is None
            or self.output_tokens is None
            or self.total_tokens != self.input_tokens + self.output_tokens
        ):
            raise ValueError("provider usage mismatch")
        if self.attempt_record_sha256 != _safe_model_hash(
            self, exclude=("attempt_record_sha256",)
        ):
            raise ValueError("provider attempt hash mismatch")
        return self


class ShadowExecutionPolicyV4(StrictModel):
    schema_id: ClassVar[str] = SHADOW_EXECUTION_POLICY_SCHEMA_ID
    schema_version: Literal["briefloop.semantic_evaluator.shadow_execution_policy.v4"]
    adapter_id: AdapterIdV4
    timeout_seconds: Literal[60]
    sdk_max_retries: Literal[0]
    raw_retention_days: Literal[30]
    max_attempts_ceiling: Literal[3]
    local_filesystem_only: Literal[True]
    execution_policy_sha256: Sha256

    @model_validator(mode="after")
    def validate_self_hash(self) -> "ShadowExecutionPolicyV4":
        if self.execution_policy_sha256 != _safe_model_hash(
            self, exclude=("execution_policy_sha256",)
        ):
            raise ValueError("execution policy hash mismatch")
        return self


__all__ = [
    "AdapterIdV4",
    "ExternalTextFactRecordV4",
    "HttpStatusFactRecordV4",
    "PROVIDER_ATTEMPT_SCHEMA_ID",
    "ProviderAttemptRecordV4",
    "ProviderBoundaryFactsRecordV4",
    "ResponseEnvelopeFactRecordV4",
    "SHADOW_ARCHIVE_MANIFEST_SCHEMA_ID",
    "SHADOW_EXECUTION_MANIFEST_SCHEMA_ID",
    "SHADOW_EXECUTION_POLICY_SCHEMA_ID",
    "SHADOW_RUN_RECEIPT_SCHEMA_ID",
    "SHADOW_RUN_REQUEST_SCHEMA_ID",
    "SHADOW_SCHEMA_IDS",
    "ShadowExecutionPolicyV4",
]
