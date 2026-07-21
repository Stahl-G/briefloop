"""Strict read-only contracts at the runtime host boundary."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from multi_agent_brief.contracts.v2 import (
    ContractId,
    CleanText,
    CoreRunNextAction,
    HttpUrlString,
    IsoDate,
    IsoDateTime,
    MimeType,
    NonNegativeInt,
    Sha256,
    ScratchInputPath,
    StrictModel,
    WorkspacePath,
)


class HumanSourceMaterialRequest(StrictModel):
    """One explicit human-provided source consumed through normal intake."""

    schema_id = "briefloop.runtime_human_source_material_request.v2"

    schema_version: Literal["briefloop.runtime_human_source_material_request.v2"]
    request_id: ContractId
    run_id: ContractId
    expected_store_revision: NonNegativeInt
    input_path: WorkspacePath
    expected_input_sha256: Sha256
    title: CleanText
    publisher: CleanText | None = None
    published_at: IsoDate | None = None
    retrieved_at: IsoDateTime
    content_media_type: MimeType

    @model_validator(mode="after")
    def input_is_explicit_workspace_material(self) -> "HumanSourceMaterialRequest":
        if not self.input_path.startswith("input/"):
            raise ValueError("human source material must be under input")
        return self


class HumanSourcePackMember(StrictModel):
    """One explicit workspace file in a human-frozen source pack."""

    member_id: ContractId
    input_path: WorkspacePath
    manifest_local_file: WorkspacePath
    expected_input_sha256: Sha256
    title: CleanText
    publisher: CleanText | None = None
    published_at: IsoDate | None = None
    url: HttpUrlString
    document_kind: Literal["status_incident"] | None = None
    opened_at: IsoDateTime | None = None
    resolved_at: IsoDateTime | None = None
    retrieved_at: IsoDateTime
    content_media_type: MimeType

    @model_validator(mode="after")
    def input_is_explicit_workspace_material(self) -> "HumanSourcePackMember":
        if not self.input_path.startswith("input/"):
            raise ValueError("human source material must be under input")
        if self.document_kind == "status_incident":
            if self.opened_at is None or self.published_at is not None:
                raise ValueError("status incident requires opened_at instead of published_at")
        elif self.opened_at is not None or self.resolved_at is not None:
            raise ValueError("incident timestamps require status_incident")
        return self


class FrozenSourceManifestEntry(StrictModel):
    """The exact metadata projection that may become source authority."""

    source_id: ContractId
    title: CleanText
    publisher: CleanText
    published_at: IsoDate | None = None
    document_kind: Literal["status_incident"] | None = None
    opened_at: IsoDateTime | None = None
    resolved_at: IsoDateTime | None = None
    url: HttpUrlString
    local_file: WorkspacePath
    sha256: Sha256

    @model_validator(mode="after")
    def temporal_shape_is_explicit(self) -> "FrozenSourceManifestEntry":
        if self.document_kind == "status_incident":
            if self.opened_at is None or self.published_at is not None:
                raise ValueError("status incident requires opened_at instead of published_at")
        elif self.published_at is None or self.opened_at is not None or self.resolved_at is not None:
            raise ValueError("ordinary source requires published_at")
        return self


class HumanSourcePackRequest(StrictModel):
    """One complete ordered pack committed by the host as a single effect."""

    schema_id = "briefloop.runtime_human_source_pack_request.v2"

    schema_version: Literal["briefloop.runtime_human_source_pack_request.v2"]
    request_id: ContractId
    run_id: ContractId
    expected_store_revision: NonNegativeInt
    manifest_path: WorkspacePath
    manifest_schema_version: ContractId
    expected_manifest_sha256: Sha256
    members: list[HumanSourcePackMember] = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def members_are_sorted_and_unique(self) -> "HumanSourcePackRequest":
        if not self.manifest_path.startswith("input/"):
            raise ValueError("source pack manifest must be under input")
        member_ids = [item.member_id for item in self.members]
        input_paths = [item.input_path for item in self.members]
        if member_ids != sorted(set(member_ids)):
            raise ValueError("human source pack members must be sorted and unique")
        if len(input_paths) != len(set(input_paths)):
            raise ValueError("human source pack input paths must be unique")
        return self


class RoleTaskEnvelope(StrictModel):
    schema_id = "briefloop.role_task_envelope.v2"

    schema_version: Literal["briefloop.role_task_envelope.v2"]
    run_id: ContractId
    invocation_id: ContractId
    store_revision: NonNegativeInt
    action: CoreRunNextAction
    action_fingerprint: Sha256
    role_id: ContractId
    stage_id: ContractId
    scratch_directory: WorkspacePath
    allowed_output_filenames: list[ContractId] = Field(min_length=1)
    proposal_schema_id: ContractId
    adapter_binding_fingerprint: Sha256
    source_plan_fingerprint: Sha256
    executor_kind: Literal[
        "main_session", "delegated_specialist", "declared_existing_route"
    ]
    context_mode: Literal[
        "shared_session",
        "independent_stage_context",
        "delegated_context",
        "declared_existing_context",
    ]
    review_mode: Literal[
        "stage_separated_self_review",
        "independent_stage_context",
        "delegated_review",
        "declared_existing_route",
    ]
    dispatch_instruction: Literal[
        "execute_in_current_session", "delegate_exact_role", "use_declared_route"
    ]
    task_instructions: CleanText

    @model_validator(mode="after")
    def exact_action_binding(self) -> "RoleTaskEnvelope":
        if self.action.run_id != self.run_id:
            raise ValueError("envelope run does not match action")
        if self.action.action_fingerprint != self.action_fingerprint:
            raise ValueError("envelope action fingerprint mismatch")
        if self.action.stage_id != self.stage_id or self.action.role_id not in {
            self.role_id,
            None,
        }:
            raise ValueError("envelope owner does not match action")
        if self.allowed_output_filenames != sorted(set(self.allowed_output_filenames)):
            raise ValueError("allowed output filenames must be sorted and unique")
        return self


class RuntimeDiagnoseReport(StrictModel):
    schema_id = "briefloop.runtime_diagnose_report.v2"

    schema_version: Literal["briefloop.runtime_diagnose_report.v2"]
    run_id: ContractId
    store_revision: NonNegativeInt
    store_valid: bool
    adapter_binding_valid: bool
    projection_drift: bool | None = None
    next_action: CoreRunNextAction


class RuntimeInvocationResult(StrictModel):
    schema_id = "briefloop.runtime_invocation_result.v2"

    schema_version: Literal["briefloop.runtime_invocation_result.v2"]
    run_id: ContractId
    invocation_id: ContractId
    status: Literal["committed", "replayed", "rejected_recorded"]
    transaction_id: ContractId
    store_revision: NonNegativeInt
    next_action: CoreRunNextAction


class RuntimeProposalViolation(StrictModel):
    """One value-free proposal preflight failure."""

    field: CleanText
    reason: CleanText


class RuntimeProposalValidationResult(StrictModel):
    """Read-only validation of the current invocation scratch proposal."""

    schema_id = "briefloop.runtime_proposal_validation_result.v2"

    schema_version: Literal["briefloop.runtime_proposal_validation_result.v2"]
    run_id: ContractId
    invocation_id: ContractId
    proposal_schema_id: ContractId
    status: Literal["valid", "invalid"]
    reason_code: ContractId | None = None
    checked_filenames: list[ContractId]
    violations: list[RuntimeProposalViolation]


class RepairContentInput(StrictModel):
    """Non-authoritative bytes locator for one deterministic repair effect."""

    schema_id = "briefloop.runtime_repair_content_input.v2"

    schema_version: Literal["briefloop.runtime_repair_content_input.v2"]
    artifact_id: ContractId
    input_path: ScratchInputPath
    expected_input_sha256: Sha256


__all__ = [
    "FrozenSourceManifestEntry",
    "HumanSourceMaterialRequest",
    "HumanSourcePackMember",
    "HumanSourcePackRequest",
    "RoleTaskEnvelope",
    "RepairContentInput",
    "RuntimeDiagnoseReport",
    "RuntimeInvocationResult",
    "RuntimeProposalValidationResult",
    "RuntimeProposalViolation",
]
