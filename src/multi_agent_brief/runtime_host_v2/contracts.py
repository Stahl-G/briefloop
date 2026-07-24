"""Strict read-only contracts at the runtime host boundary."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from multi_agent_brief.contracts.v2 import (
    ArtifactRevisionReference,
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


class GateRepairFindingContext(StrictModel):
    """Value-free deterministic finding scope for the repair editor."""

    evaluation_id: ContractId
    finding_id: ContractId
    finding_type: ContractId
    category: ContractId
    recommendation: CleanText


class GateRepairContext(StrictModel):
    """The exact Store-derived scope of one active bounded Gate repair."""

    gate_repair_id: ContractId
    source_stage_id: Literal["auditor", "finalize"]
    source_gate_batch_id: ContractId
    target_artifact: ArtifactRevisionReference
    findings: list[GateRepairFindingContext] = Field(min_length=1)

    @model_validator(mode="after")
    def findings_are_canonical(self) -> "GateRepairContext":
        keys = [(item.evaluation_id, item.finding_id) for item in self.findings]
        if keys != sorted(set(keys)):
            raise ValueError("Gate repair findings must be sorted and unique")
        if self.target_artifact.artifact_id != "audited_brief":
            raise ValueError("Gate repair target must be audited_brief")
        return self


class GateRepairStartRequest(StrictModel):
    """Parameter-free Host request bound only to the current Core action."""

    schema_id = "briefloop.gate_repair_start_request.v2"

    schema_version: Literal["briefloop.gate_repair_start_request.v2"]
    request_id: ContractId
    run_id: ContractId
    action_fingerprint: Sha256
    expected_store_revision: NonNegativeInt


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
    gate_repair_context: GateRepairContext | None = None

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
        if self.gate_repair_context is not None and (
            self.stage_id != "editor" or self.role_id != "editor"
        ):
            raise ValueError("Gate repair context belongs only to the editor")
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


class RuntimeContinuationTrace(StrictModel):
    """Explicit read-only control trace, omitted from friendly CLI output."""

    next_action: CoreRunNextAction
    envelope_path: WorkspacePath | None = None
    transaction_ids: list[ContractId]


class LocalRunActionSummary(StrictModel):
    """Sanitized action facts safe for the local reader presentation."""

    action_kind: Literal[
        "delegate", "deterministic", "human_decision", "blocked", "complete"
    ]
    effect_kind: ContractId
    stage_id: ContractId | None = None
    role_id: ContractId | None = None
    reason_code: ContractId


class LocalRunGateSummary(StrictModel):
    """One deterministic Gate observation from the verified Store history."""

    gate_id: ContractId
    evaluation_id: ContractId
    stage_id: ContractId
    status: Literal["pass", "fail", "warning"]
    blocking: bool


class LocalRunSummary(StrictModel):
    """Deterministic counts and Gate facts from one verified Store snapshot."""

    accepted_source_count: NonNegativeInt
    claim_count: NonNegativeInt
    finalization_count: NonNegativeInt
    gates: list[LocalRunGateSummary]
    receipt_ids: list[ContractId]


class LocalReaderBrief(StrictModel):
    """The exact final reader bytes bound by the Store finalize render."""

    state: Literal["unavailable", "available"]
    artifact_id: Literal["reader_brief"] | None = None
    revision: NonNegativeInt | None = None
    sha256: Sha256 | None = None
    markdown_utf8: bytes | None = None

    @model_validator(mode="after")
    def available_reader_is_complete(self) -> "LocalReaderBrief":
        values = (
            self.artifact_id,
            self.revision,
            self.sha256,
            self.markdown_utf8,
        )
        if self.state == "available":
            if any(value is None for value in values) or self.revision == 0:
                raise ValueError("available reader brief identity is incomplete")
        elif any(value is not None for value in values):
            raise ValueError("unavailable reader brief must not carry bytes")
        return self


class LocalPresentationResult(StrictModel):
    """Replaceable presentation outcome; never runtime or Store authority."""

    status: Literal[
        "not_requested",
        "written",
        "opened",
        "browser_unavailable",
        "projection_unavailable",
    ]
    relative_path: WorkspacePath | None = None
    reason_code: ContractId | None = None

    @model_validator(mode="after")
    def path_matches_status(self) -> "LocalPresentationResult":
        if self.status in {"written", "opened", "browser_unavailable"}:
            if self.relative_path is None:
                raise ValueError("written presentation requires relative path")
        elif self.relative_path is not None:
            raise ValueError("unwritten presentation must not carry a path")
        return self


class LocalRunPresentation(StrictModel):
    """One strict local read model derived from a single verified history."""

    schema_id = "briefloop.local_run_presentation.v2"

    schema_version: Literal["briefloop.local_run_presentation.v2"]
    boundary: Literal[
        "read_only_projection_not_gate_approval_delivery_or_runtime_authority"
    ]
    run_id: ContractId
    store_revision: NonNegativeInt
    runtime: ContractId
    execution_topology: ContractId
    executor_display: CleanText
    execution_topology_display: CleanText
    context_independence: CleanText
    review_mode: CleanText
    role_stages: list[ContractId]
    completion_target: Literal["finalized_local"] | None = None
    view_state: Literal["setup", "running", "needs_attention", "finalized"]
    completed_stages: NonNegativeInt
    total_stages: NonNegativeInt
    current_stage: ContractId | None = None
    current_role: ContractId | None = None
    reason_code: ContractId
    terminal_state: ContractId
    next_action: LocalRunActionSummary
    reader_brief: LocalReaderBrief
    summary: LocalRunSummary
    presentation: LocalPresentationResult


class RuntimeContinuationResult(StrictModel):
    """One bounded, Store-derived authorized continuation observation."""

    schema_id = "briefloop.runtime_continuation_result.v2"

    schema_version: Literal["briefloop.runtime_continuation_result.v2"]
    run_id: ContractId
    store_revision: NonNegativeInt
    status: Literal[
        "progressed",
        "role_work_required",
        "proposal_invalid",
        "needs_human",
        "needs_attention",
        "finalized_local",
    ]
    reason_code: ContractId | None = None
    current_stage: ContractId | None = None
    current_role: ContractId | None = None
    completed_stages: NonNegativeInt
    total_stages: NonNegativeInt
    violations: list[RuntimeProposalViolation]
    trace: RuntimeContinuationTrace
    presentation: LocalPresentationResult | None = None


class RepairContentInput(StrictModel):
    """Non-authoritative bytes locator for one deterministic repair effect."""

    schema_id = "briefloop.runtime_repair_content_input.v2"

    schema_version: Literal["briefloop.runtime_repair_content_input.v2"]
    artifact_id: ContractId
    input_path: ScratchInputPath
    expected_input_sha256: Sha256


__all__ = [
    "FrozenSourceManifestEntry",
    "GateRepairContext",
    "GateRepairFindingContext",
    "GateRepairStartRequest",
    "HumanSourceMaterialRequest",
    "HumanSourcePackMember",
    "HumanSourcePackRequest",
    "LocalPresentationResult",
    "LocalReaderBrief",
    "LocalRunActionSummary",
    "LocalRunGateSummary",
    "LocalRunPresentation",
    "LocalRunSummary",
    "RoleTaskEnvelope",
    "RepairContentInput",
    "RuntimeDiagnoseReport",
    "RuntimeContinuationResult",
    "RuntimeContinuationTrace",
    "RuntimeInvocationResult",
    "RuntimeProposalValidationResult",
    "RuntimeProposalViolation",
]
