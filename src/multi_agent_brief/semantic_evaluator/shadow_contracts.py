"""Strict v4 contracts for private replayable Semantic Evaluator evidence."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Annotated, ClassVar, Literal

from pydantic import (
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    StringConstraints,
    model_validator,
)

from multi_agent_brief.contracts.v2 import (
    CleanText,
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
from multi_agent_brief.semantic_evaluator.contracts import RunStatus
from multi_agent_brief.semantic_evaluator.serialization import (
    canonical_model_sha256,
    canonical_sha256,
)


SHADOW_EXECUTION_POLICY_SCHEMA_ID = (
    "briefloop.semantic_evaluator.shadow_execution_policy.v5"
)
SHADOW_EXECUTION_MANIFEST_SCHEMA_ID = (
    "briefloop.semantic_evaluator.shadow_execution_manifest.v5"
)
SHADOW_RUN_REQUEST_SCHEMA_ID = "briefloop.semantic_evaluator.shadow_run_request.v5"
PROVIDER_ATTEMPT_SCHEMA_ID = "briefloop.semantic_evaluator.provider_attempt.v5"
SHADOW_ARCHIVE_MANIFEST_SCHEMA_ID = (
    "briefloop.semantic_evaluator.shadow_archive_manifest.v5"
)
SHADOW_RUN_RECEIPT_SCHEMA_ID = "briefloop.semantic_evaluator.shadow_run_receipt.v5"

SHADOW_SCHEMA_IDS = (
    PROVIDER_BOUNDARY_FACTS_SCHEMA_ID,
    PROVIDER_ATTEMPT_SCHEMA_ID,
    SHADOW_ARCHIVE_MANIFEST_SCHEMA_ID,
    SHADOW_EXECUTION_MANIFEST_SCHEMA_ID,
    SHADOW_EXECUTION_POLICY_SCHEMA_ID,
    SHADOW_RUN_RECEIPT_SCHEMA_ID,
    SHADOW_RUN_REQUEST_SCHEMA_ID,
)

AdapterIdV5 = Literal[
    "local_proxy_responses_v1",
    "openai_responses_v4",
    "synthetic_fixture_v4",
]
ExecutionOriginV5 = Literal["direct_openai", "local_cliproxy", "synthetic_fixture"]
QualificationClassV5 = Literal[
    "direct_openai",
    "local_proxy_experimental",
    "synthetic_only",
]
SHADOW_TIMEOUT_SECONDS = 60
ValidationStatusV5 = Literal["accepted", "rejected", "incomplete"]
RelativeArchivePathV4 = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=300,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._/-]*$",
    ),
]


def _validate_relative_archive_path(value: str) -> None:
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
        or str(path) != value
        or "//" in value
        or "\\" in value
    ):
        raise ValueError("archive member path is not canonical")


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


class ProviderAttemptRecordV5(StrictModel):
    schema_id: ClassVar[str] = PROVIDER_ATTEMPT_SCHEMA_ID
    schema_version: Literal["briefloop.semantic_evaluator.provider_attempt.v5"]
    attempt_ref: ContractId
    trial_id: ContractId
    dimension_id: ContractId
    attempt_ordinal: PositiveInt
    prompt_request_sha256: Sha256
    adapter_id: AdapterIdV5
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
    def validate_classifier_and_hash(self) -> "ProviderAttemptRecordV5":
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


class ShadowExecutionPolicyV5(StrictModel):
    schema_id: ClassVar[str] = SHADOW_EXECUTION_POLICY_SCHEMA_ID
    schema_version: Literal["briefloop.semantic_evaluator.shadow_execution_policy.v5"]
    adapter_id: AdapterIdV5
    timeout_seconds: Literal[60]
    sdk_max_retries: Literal[0]
    raw_retention_days: Literal[30]
    max_attempts_ceiling: Literal[3]
    local_filesystem_only: Literal[True]
    execution_policy_sha256: Sha256

    @model_validator(mode="after")
    def validate_self_hash(self) -> "ShadowExecutionPolicyV5":
        if self.execution_policy_sha256 != _safe_model_hash(
            self, exclude=("execution_policy_sha256",)
        ):
            raise ValueError("execution policy hash mismatch")
        return self


class ShadowExecutionManifestV5(StrictModel):
    schema_id: ClassVar[str] = SHADOW_EXECUTION_MANIFEST_SCHEMA_ID
    schema_version: Literal["briefloop.semantic_evaluator.shadow_execution_manifest.v5"]
    execution_manifest_id: ContractId
    instrument_sha256: Sha256
    execution_policy_sha256: Sha256
    adapter_id: AdapterIdV5
    adapter_version: ContractId
    adapter_source_sha256: Sha256
    runner_version: ContractId
    runner_source_sha256: Sha256
    archive_version: ContractId
    archive_source_sha256: Sha256
    shadow_schema_sha256s: dict[ContractId, Sha256]
    provider_sdk_name: ContractId
    provider_sdk_version: CleanText
    execution_origin: ExecutionOriginV5
    qualification_class: QualificationClassV5
    provider_endpoint_sha256: Sha256
    prompt_sizer_id: ContractId
    prompt_sizer_version: ContractId
    tokenizer_package: ContractId
    tokenizer_version: CleanText
    tokenizer_encoding: ContractId
    python_major_minor: ContractId
    qualification_eligible: StrictBool
    execution_sha256: Sha256

    @model_validator(mode="after")
    def validate_inventory_and_hash(self) -> "ShadowExecutionManifestV5":
        if list(self.shadow_schema_sha256s) != sorted(SHADOW_SCHEMA_IDS):
            raise ValueError("shadow schema hashes must be complete and sorted")
        expected = {
            "local_proxy_responses_v1": (
                "local_cliproxy",
                "local_proxy_experimental",
                False,
            ),
            "openai_responses_v4": ("direct_openai", "direct_openai", True),
            "synthetic_fixture_v4": (
                "synthetic_fixture",
                "synthetic_only",
                False,
            ),
        }[self.adapter_id]
        if (
            self.execution_origin,
            self.qualification_class,
            self.qualification_eligible,
        ) != expected:
            raise ValueError("execution qualification projection mismatch")
        if self.execution_sha256 != _safe_model_hash(
            self, exclude=("execution_sha256",)
        ):
            raise ValueError("execution manifest hash mismatch")
        return self


class ShadowRunRequestV5(StrictModel):
    schema_id: ClassVar[str] = SHADOW_RUN_REQUEST_SCHEMA_ID
    schema_version: Literal["briefloop.semantic_evaluator.shadow_run_request.v5"]
    trial_id: ContractId
    artifact_id: ContractId
    report_sha256: Sha256
    bounded_context_sha256: Sha256
    input_binding_sha256: Sha256
    instrument_sha256: Sha256
    assessment_plan_sha256: Sha256
    ordered_prompt_request_sha256s: list[Sha256] = Field(min_length=9, max_length=9)
    execution_sha256: Sha256
    provider_id: ContractId
    model_id: ContractId
    expected_model_version_utf8_hex: StrictStr
    shadow_request_sha256: Sha256

    @model_validator(mode="after")
    def validate_request_hash(self) -> "ShadowRunRequestV5":
        if len(set(self.ordered_prompt_request_sha256s)) != len(
            self.ordered_prompt_request_sha256s
        ):
            raise ValueError("prompt request hashes must be unique")
        try:
            expected = bytes.fromhex(self.expected_model_version_utf8_hex)
            expected.decode("utf-8", errors="strict")
        except (ValueError, UnicodeDecodeError):
            raise ValueError("expected model identity encoding mismatch") from None
        if not expected:
            raise ValueError("expected model identity encoding mismatch")
        if self.shadow_request_sha256 != _safe_model_hash(
            self, exclude=("shadow_request_sha256",)
        ):
            raise ValueError("shadow request hash mismatch")
        return self


class ArchiveMemberV5(StrictModel):
    path: RelativeArchivePathV4
    size_bytes: NonNegativeInt
    sha256: Sha256

    @model_validator(mode="after")
    def validate_path(self) -> "ArchiveMemberV5":
        _validate_relative_archive_path(self.path)
        return self


class ShadowArchiveManifestV5(StrictModel):
    schema_id: ClassVar[str] = SHADOW_ARCHIVE_MANIFEST_SCHEMA_ID
    schema_version: Literal["briefloop.semantic_evaluator.shadow_archive_manifest.v5"]
    archive_id: ContractId
    shadow_request_sha256: Sha256
    instrument_sha256: Sha256
    execution_sha256: Sha256
    trial_id: ContractId
    run_status: RunStatus
    validation_status: ValidationStatusV5
    payload_members: list[ArchiveMemberV5] = Field(min_length=1)
    payload_file_count: PositiveInt
    aggregate_payload_sha256: Sha256
    archive_manifest_sha256: Sha256

    @model_validator(mode="after")
    def validate_inventory_and_hash(self) -> "ShadowArchiveManifestV5":
        paths = [item.path for item in self.payload_members]
        if paths != sorted(paths) or len(paths) != len(set(paths)):
            raise ValueError("archive payload inventory must be sorted and unique")
        if self.payload_file_count != len(self.payload_members):
            raise ValueError("archive payload count mismatch")
        expected_aggregate = canonical_sha256(
            [
                item.model_dump(mode="json", warnings="error")
                for item in self.payload_members
            ]
        )
        if self.aggregate_payload_sha256 != expected_aggregate:
            raise ValueError("archive aggregate hash mismatch")
        if self.archive_manifest_sha256 != _safe_model_hash(
            self, exclude=("archive_manifest_sha256",)
        ):
            raise ValueError("archive manifest hash mismatch")
        return self


class ShadowRunReceiptV5(StrictModel):
    schema_id: ClassVar[str] = SHADOW_RUN_RECEIPT_SCHEMA_ID
    schema_version: Literal["briefloop.semantic_evaluator.shadow_run_receipt.v5"]
    receipt_id: ContractId
    archive_id: ContractId
    shadow_request_sha256: Sha256
    instrument_sha256: Sha256
    execution_sha256: Sha256
    run_id: ContractId
    trial_id: ContractId
    run_status: RunStatus
    validation_status: ValidationStatusV5
    archive_status: Literal["complete"]
    archive_manifest_sha256: Sha256
    execution_origin: ExecutionOriginV5
    qualification_class: QualificationClassV5
    qualification_eligible: StrictBool
    created_at: IsoDateTime
    receipt_sha256: Sha256

    @model_validator(mode="after")
    def validate_receipt_hash(self) -> "ShadowRunReceiptV5":
        if self.receipt_sha256 != _safe_model_hash(self, exclude=("receipt_sha256",)):
            raise ValueError("shadow receipt hash mismatch")
        return self


SHADOW_CONTRACT_MODELS_V5: tuple[type[StrictModel], ...] = (
    ProviderBoundaryFactsRecordV4,
    ShadowExecutionPolicyV5,
    ShadowExecutionManifestV5,
    ShadowRunRequestV5,
    ProviderAttemptRecordV5,
    ShadowArchiveManifestV5,
    ShadowRunReceiptV5,
)

# The runner/archive use these package-local names; there is no v4 reader.
ProviderAttemptRecord = ProviderAttemptRecordV5
ShadowExecutionManifest = ShadowExecutionManifestV5
ShadowExecutionPolicy = ShadowExecutionPolicyV5
ShadowRunRequest = ShadowRunRequestV5
ArchiveMember = ArchiveMemberV5
ShadowArchiveManifest = ShadowArchiveManifestV5
ShadowRunReceipt = ShadowRunReceiptV5
SHADOW_CONTRACT_MODELS = SHADOW_CONTRACT_MODELS_V5


__all__ = [
    "AdapterIdV5",
    "ArchiveMemberV5",
    "ExecutionOriginV5",
    "ExternalTextFactRecordV4",
    "HttpStatusFactRecordV4",
    "PROVIDER_ATTEMPT_SCHEMA_ID",
    "ProviderAttemptRecordV5",
    "ProviderAttemptRecord",
    "ProviderBoundaryFactsRecordV4",
    "ResponseEnvelopeFactRecordV4",
    "SHADOW_ARCHIVE_MANIFEST_SCHEMA_ID",
    "SHADOW_EXECUTION_MANIFEST_SCHEMA_ID",
    "SHADOW_EXECUTION_POLICY_SCHEMA_ID",
    "SHADOW_RUN_RECEIPT_SCHEMA_ID",
    "SHADOW_RUN_REQUEST_SCHEMA_ID",
    "SHADOW_SCHEMA_IDS",
    "SHADOW_CONTRACT_MODELS_V5",
    "SHADOW_CONTRACT_MODELS",
    "SHADOW_TIMEOUT_SECONDS",
    "ShadowArchiveManifestV5",
    "ShadowExecutionManifestV5",
    "ShadowExecutionPolicyV5",
    "ShadowRunReceiptV5",
    "ShadowRunRequestV5",
    "QualificationClassV5",
    "ValidationStatusV5",
]
