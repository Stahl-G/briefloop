"""Strict contracts for replayable Semantic Evaluator shadow execution.

These records are experiment evidence only.  They are deliberately excluded
from the production contract registry and carry no workflow, gate, finalize,
delivery, Quality Panel, or Claim-Support authority.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Annotated, ClassVar, Literal, Optional

from pydantic import Field, StrictBool, StringConstraints, model_validator

from multi_agent_brief.contracts.v2 import (
    CleanText,
    ContractId,
    IsoDateTime,
    NonNegativeInt,
    PositiveInt,
    Sha256,
    StrictModel,
)
from multi_agent_brief.semantic_evaluator.contracts import RunStatus
from multi_agent_brief.semantic_evaluator.serialization import (
    canonical_model_sha256,
    canonical_sha256,
)


SHADOW_EXECUTION_POLICY_SCHEMA_ID = (
    "briefloop.semantic_evaluator.shadow_execution_policy.v1"
)
SHADOW_EXECUTION_MANIFEST_SCHEMA_ID = (
    "briefloop.semantic_evaluator.shadow_execution_manifest.v1"
)
SHADOW_RUN_REQUEST_SCHEMA_ID = "briefloop.semantic_evaluator.shadow_run_request.v1"
PROVIDER_ATTEMPT_SCHEMA_ID = "briefloop.semantic_evaluator.provider_attempt.v1"
SHADOW_ARCHIVE_MANIFEST_SCHEMA_ID = (
    "briefloop.semantic_evaluator.shadow_archive_manifest.v1"
)
SHADOW_RUN_RECEIPT_SCHEMA_ID = "briefloop.semantic_evaluator.shadow_run_receipt.v1"

SHADOW_SCHEMA_IDS = (
    SHADOW_EXECUTION_POLICY_SCHEMA_ID,
    SHADOW_EXECUTION_MANIFEST_SCHEMA_ID,
    SHADOW_RUN_REQUEST_SCHEMA_ID,
    PROVIDER_ATTEMPT_SCHEMA_ID,
    SHADOW_ARCHIVE_MANIFEST_SCHEMA_ID,
    SHADOW_RUN_RECEIPT_SCHEMA_ID,
)

SHADOW_TIMEOUT_SECONDS = 60

AdapterId = Literal["openai_responses_v1", "synthetic_fixture_v1"]
ProviderAttemptReason = Literal[
    "provider_retryable_failure",
    "provider_failed",
    "provider_identity_mismatch",
]
ValidationStatus = Literal["accepted", "rejected", "incomplete"]
RelativeArchivePath = Annotated[
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


class ShadowExecutionPolicy(StrictModel):
    schema_id: ClassVar[str] = SHADOW_EXECUTION_POLICY_SCHEMA_ID
    schema_version: Literal[SHADOW_EXECUTION_POLICY_SCHEMA_ID]
    adapter_id: AdapterId
    timeout_seconds: Annotated[int, Field(ge=1, le=300)]
    sdk_max_retries: Literal[0]
    raw_retention_days: Literal[30]
    max_attempts_ceiling: Literal[3]
    local_filesystem_only: Literal[True]
    execution_policy_sha256: Sha256

    @model_validator(mode="after")
    def validate_self_hash(self) -> "ShadowExecutionPolicy":
        if self.execution_policy_sha256 != canonical_model_sha256(
            self, exclude=("execution_policy_sha256",)
        ):
            raise ValueError("execution policy hash mismatch")
        return self


class ShadowExecutionManifest(StrictModel):
    schema_id: ClassVar[str] = SHADOW_EXECUTION_MANIFEST_SCHEMA_ID
    schema_version: Literal[SHADOW_EXECUTION_MANIFEST_SCHEMA_ID]
    execution_manifest_id: ContractId
    instrument_sha256: Sha256
    execution_policy_sha256: Sha256
    adapter_id: AdapterId
    adapter_version: ContractId
    adapter_source_sha256: Sha256
    runner_version: ContractId
    runner_source_sha256: Sha256
    archive_version: ContractId
    archive_source_sha256: Sha256
    shadow_schema_sha256s: dict[ContractId, Sha256]
    provider_sdk_name: ContractId
    provider_sdk_version: CleanText
    prompt_sizer_id: ContractId
    prompt_sizer_version: ContractId
    tokenizer_package: ContractId
    tokenizer_version: CleanText
    tokenizer_encoding: ContractId
    python_major_minor: ContractId
    qualification_eligible: StrictBool
    execution_sha256: Sha256

    @model_validator(mode="after")
    def validate_inventory_and_hash(self) -> "ShadowExecutionManifest":
        if list(self.shadow_schema_sha256s) != sorted(SHADOW_SCHEMA_IDS):
            raise ValueError("shadow schema hashes must be complete and sorted")
        if self.execution_sha256 != canonical_model_sha256(
            self, exclude=("execution_sha256",)
        ):
            raise ValueError("execution manifest hash mismatch")
        return self


class ShadowRunRequest(StrictModel):
    schema_id: ClassVar[str] = SHADOW_RUN_REQUEST_SCHEMA_ID
    schema_version: Literal[SHADOW_RUN_REQUEST_SCHEMA_ID]
    trial_id: ContractId
    artifact_id: ContractId
    report_sha256: Sha256
    bounded_context_sha256: Sha256
    input_binding_sha256: Sha256
    instrument_sha256: Sha256
    assessment_plan_sha256: Sha256
    ordered_prompt_request_sha256s: list[Sha256] = Field(
        min_length=9,
        max_length=9,
    )
    execution_sha256: Sha256
    provider_id: ContractId
    model_id: ContractId
    expected_model_version: CleanText
    shadow_request_sha256: Sha256

    @model_validator(mode="after")
    def validate_request_hash(self) -> "ShadowRunRequest":
        if len(set(self.ordered_prompt_request_sha256s)) != len(
            self.ordered_prompt_request_sha256s
        ):
            raise ValueError("prompt request hashes must be unique")
        if self.shadow_request_sha256 != canonical_model_sha256(
            self, exclude=("shadow_request_sha256",)
        ):
            raise ValueError("shadow request hash mismatch")
        return self


class ProviderAttemptRecord(StrictModel):
    schema_id: ClassVar[str] = PROVIDER_ATTEMPT_SCHEMA_ID
    schema_version: Literal[PROVIDER_ATTEMPT_SCHEMA_ID]
    attempt_ref: ContractId
    trial_id: ContractId
    dimension_id: ContractId
    attempt_ordinal: PositiveInt
    prompt_request_sha256: Sha256
    adapter_id: AdapterId
    provider_id: ContractId
    requested_model_id: ContractId
    expected_model_version: CleanText
    status: Literal["completed", "failed"]
    reason_code: Optional[ProviderAttemptReason]
    provider_request_id: Optional[CleanText]
    observed_model_version: Optional[CleanText]
    request_projection_sha256: Sha256
    raw_transport_response_sha256: Optional[Sha256]
    extracted_output_sha256: Optional[Sha256]
    input_tokens: Optional[NonNegativeInt]
    output_tokens: Optional[NonNegativeInt]
    total_tokens: Optional[NonNegativeInt]
    started_at: IsoDateTime
    completed_at: IsoDateTime
    attempt_record_sha256: Sha256

    @model_validator(mode="after")
    def validate_attempt_shape_and_hash(self) -> "ProviderAttemptRecord":
        if self.status == "completed":
            if self.reason_code is not None or self.extracted_output_sha256 is None:
                raise ValueError("completed provider attempt requires output only")
        elif self.reason_code is None or self.extracted_output_sha256 is not None:
            raise ValueError("failed provider attempt requires one reason only")
        if self.total_tokens is not None:
            known = self.input_tokens is not None and self.output_tokens is not None
            if not known or self.total_tokens != self.input_tokens + self.output_tokens:
                raise ValueError("provider usage totals are inconsistent")
        if self.attempt_record_sha256 != canonical_model_sha256(
            self, exclude=("attempt_record_sha256",)
        ):
            raise ValueError("provider attempt record hash mismatch")
        return self


class ArchiveMember(StrictModel):
    path: RelativeArchivePath
    size_bytes: NonNegativeInt
    sha256: Sha256

    @model_validator(mode="after")
    def validate_path(self) -> "ArchiveMember":
        _validate_relative_archive_path(self.path)
        return self


class ShadowArchiveManifest(StrictModel):
    schema_id: ClassVar[str] = SHADOW_ARCHIVE_MANIFEST_SCHEMA_ID
    schema_version: Literal[SHADOW_ARCHIVE_MANIFEST_SCHEMA_ID]
    archive_id: ContractId
    shadow_request_sha256: Sha256
    instrument_sha256: Sha256
    execution_sha256: Sha256
    trial_id: ContractId
    run_status: RunStatus
    validation_status: ValidationStatus
    payload_members: list[ArchiveMember] = Field(min_length=1)
    payload_file_count: PositiveInt
    aggregate_payload_sha256: Sha256
    archive_manifest_sha256: Sha256

    @model_validator(mode="after")
    def validate_inventory_and_hash(self) -> "ShadowArchiveManifest":
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
        if self.archive_manifest_sha256 != canonical_model_sha256(
            self, exclude=("archive_manifest_sha256",)
        ):
            raise ValueError("archive manifest hash mismatch")
        return self


class ShadowRunReceipt(StrictModel):
    schema_id: ClassVar[str] = SHADOW_RUN_RECEIPT_SCHEMA_ID
    schema_version: Literal[SHADOW_RUN_RECEIPT_SCHEMA_ID]
    receipt_id: ContractId
    archive_id: ContractId
    shadow_request_sha256: Sha256
    instrument_sha256: Sha256
    execution_sha256: Sha256
    run_id: ContractId
    trial_id: ContractId
    run_status: RunStatus
    validation_status: ValidationStatus
    archive_status: Literal["complete"]
    archive_manifest_sha256: Sha256
    qualification_eligible: StrictBool
    created_at: IsoDateTime
    receipt_sha256: Sha256

    @model_validator(mode="after")
    def validate_receipt_hash(self) -> "ShadowRunReceipt":
        if self.receipt_sha256 != canonical_model_sha256(
            self, exclude=("receipt_sha256",)
        ):
            raise ValueError("shadow receipt hash mismatch")
        return self


def _self_hashed_example(
    model: type[StrictModel],
    payload: dict[str, object],
    hash_field: str,
) -> dict[str, object]:
    value = model.model_validate({**payload, hash_field: canonical_sha256(payload)})
    return value.model_dump(mode="json", warnings="error")


_ZERO_SHA = "0" * 64
_ONE_SHA = "1" * 64
_EXAMPLE_POLICY = _self_hashed_example(
    ShadowExecutionPolicy,
    {
        "schema_version": SHADOW_EXECUTION_POLICY_SCHEMA_ID,
        "adapter_id": "synthetic_fixture_v1",
        "timeout_seconds": 60,
        "sdk_max_retries": 0,
        "raw_retention_days": 30,
        "max_attempts_ceiling": 3,
        "local_filesystem_only": True,
    },
    "execution_policy_sha256",
)
_EXAMPLE_EXECUTION = _self_hashed_example(
    ShadowExecutionManifest,
    {
        "schema_version": SHADOW_EXECUTION_MANIFEST_SCHEMA_ID,
        "execution_manifest_id": "execution-example-v1",
        "instrument_sha256": _ZERO_SHA,
        "execution_policy_sha256": _EXAMPLE_POLICY["execution_policy_sha256"],
        "adapter_id": "synthetic_fixture_v1",
        "adapter_version": "synthetic-v1",
        "adapter_source_sha256": _ZERO_SHA,
        "runner_version": "runner-v1",
        "runner_source_sha256": _ZERO_SHA,
        "archive_version": "archive-v1",
        "archive_source_sha256": _ZERO_SHA,
        "shadow_schema_sha256s": {
            schema_id: _ZERO_SHA for schema_id in sorted(SHADOW_SCHEMA_IDS)
        },
        "provider_sdk_name": "synthetic",
        "provider_sdk_version": "synthetic-v1",
        "prompt_sizer_id": "synthetic-sizer-v1",
        "prompt_sizer_version": "synthetic-sizer-v1",
        "tokenizer_package": "synthetic",
        "tokenizer_version": "synthetic-v1",
        "tokenizer_encoding": "synthetic-v1",
        "python_major_minor": "python-3.11",
        "qualification_eligible": False,
    },
    "execution_sha256",
)
_EXAMPLE_REQUEST = _self_hashed_example(
    ShadowRunRequest,
    {
        "schema_version": SHADOW_RUN_REQUEST_SCHEMA_ID,
        "trial_id": "trial-example-v1",
        "artifact_id": "artifact-example-v1",
        "report_sha256": _ZERO_SHA,
        "bounded_context_sha256": _ONE_SHA,
        "input_binding_sha256": "2" * 64,
        "instrument_sha256": _ZERO_SHA,
        "assessment_plan_sha256": "3" * 64,
        "ordered_prompt_request_sha256s": [
            canonical_sha256(["prompt", ordinal]) for ordinal in range(9)
        ],
        "execution_sha256": _EXAMPLE_EXECUTION["execution_sha256"],
        "provider_id": "synthetic_fixture",
        "model_id": "synthetic-fixture-v1",
        "expected_model_version": "synthetic-fixture-v1",
    },
    "shadow_request_sha256",
)
_EXAMPLE_ATTEMPT = _self_hashed_example(
    ProviderAttemptRecord,
    {
        "schema_version": PROVIDER_ATTEMPT_SCHEMA_ID,
        "attempt_ref": "attempt-example-v1",
        "trial_id": "trial-example-v1",
        "dimension_id": "cross_section_consistency",
        "attempt_ordinal": 1,
        "prompt_request_sha256": canonical_sha256(["prompt", 0]),
        "adapter_id": "synthetic_fixture_v1",
        "provider_id": "synthetic_fixture",
        "requested_model_id": "synthetic-fixture-v1",
        "expected_model_version": "synthetic-fixture-v1",
        "status": "completed",
        "reason_code": None,
        "provider_request_id": "synthetic-request-v1",
        "observed_model_version": "synthetic-fixture-v1",
        "request_projection_sha256": _ZERO_SHA,
        "raw_transport_response_sha256": _ONE_SHA,
        "extracted_output_sha256": "2" * 64,
        "input_tokens": None,
        "output_tokens": None,
        "total_tokens": None,
        "started_at": "2026-01-01T00:00:00Z",
        "completed_at": "2026-01-01T00:00:00Z",
    },
    "attempt_record_sha256",
)
_EXAMPLE_MEMBER = ArchiveMember(path="request.json", size_bytes=2, sha256=_ZERO_SHA)
_EXAMPLE_MEMBER_PAYLOAD = _EXAMPLE_MEMBER.model_dump(mode="json", warnings="error")
_EXAMPLE_ARCHIVE = _self_hashed_example(
    ShadowArchiveManifest,
    {
        "schema_version": SHADOW_ARCHIVE_MANIFEST_SCHEMA_ID,
        "archive_id": "archive-example-v1",
        "shadow_request_sha256": _EXAMPLE_REQUEST["shadow_request_sha256"],
        "instrument_sha256": _ZERO_SHA,
        "execution_sha256": _EXAMPLE_EXECUTION["execution_sha256"],
        "trial_id": "trial-example-v1",
        "run_status": "completed",
        "validation_status": "accepted",
        "payload_members": [_EXAMPLE_MEMBER_PAYLOAD],
        "payload_file_count": 1,
        "aggregate_payload_sha256": canonical_sha256([_EXAMPLE_MEMBER_PAYLOAD]),
    },
    "archive_manifest_sha256",
)
_EXAMPLE_RECEIPT = _self_hashed_example(
    ShadowRunReceipt,
    {
        "schema_version": SHADOW_RUN_RECEIPT_SCHEMA_ID,
        "receipt_id": "receipt-example-v1",
        "archive_id": "archive-example-v1",
        "shadow_request_sha256": _EXAMPLE_REQUEST["shadow_request_sha256"],
        "instrument_sha256": _ZERO_SHA,
        "execution_sha256": _EXAMPLE_EXECUTION["execution_sha256"],
        "run_id": "run-example-v1",
        "trial_id": "trial-example-v1",
        "run_status": "completed",
        "validation_status": "accepted",
        "archive_status": "complete",
        "archive_manifest_sha256": _EXAMPLE_ARCHIVE["archive_manifest_sha256"],
        "qualification_eligible": False,
        "created_at": "2026-01-01T00:00:00Z",
    },
    "receipt_sha256",
)

for _model, _example in (
    (ShadowExecutionPolicy, _EXAMPLE_POLICY),
    (ShadowExecutionManifest, _EXAMPLE_EXECUTION),
    (ShadowRunRequest, _EXAMPLE_REQUEST),
    (ProviderAttemptRecord, _EXAMPLE_ATTEMPT),
    (ShadowArchiveManifest, _EXAMPLE_ARCHIVE),
    (ShadowRunReceipt, _EXAMPLE_RECEIPT),
):
    _model.minimal_example = _model.full_example = _example


SHADOW_CONTRACT_MODELS: tuple[type[StrictModel], ...] = (
    ShadowExecutionPolicy,
    ShadowExecutionManifest,
    ShadowRunRequest,
    ProviderAttemptRecord,
    ShadowArchiveManifest,
    ShadowRunReceipt,
)


__all__ = [
    "PROVIDER_ATTEMPT_SCHEMA_ID",
    "SHADOW_ARCHIVE_MANIFEST_SCHEMA_ID",
    "SHADOW_CONTRACT_MODELS",
    "SHADOW_EXECUTION_MANIFEST_SCHEMA_ID",
    "SHADOW_EXECUTION_POLICY_SCHEMA_ID",
    "SHADOW_RUN_RECEIPT_SCHEMA_ID",
    "SHADOW_RUN_REQUEST_SCHEMA_ID",
    "SHADOW_SCHEMA_IDS",
    "SHADOW_TIMEOUT_SECONDS",
    "AdapterId",
    "ArchiveMember",
    "ProviderAttemptReason",
    "ProviderAttemptRecord",
    "ShadowArchiveManifest",
    "ShadowExecutionManifest",
    "ShadowExecutionPolicy",
    "ShadowRunReceipt",
    "ShadowRunRequest",
    "ValidationStatus",
]
