"""Strict read-only contracts at the runtime host boundary."""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import Field, model_validator

from multi_agent_brief.contracts.v2 import (
    ArtifactRevisionReference,
    ContractId,
    CleanText,
    CoreRunNextAction,
    GuidanceReuseScopeV1,
    GateId,
    HttpUrlString,
    IsoDate,
    IsoDateTime,
    MimeType,
    NonNegativeInt,
    PositiveInt,
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
                raise ValueError(
                    "status incident requires opened_at instead of published_at"
                )
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
                raise ValueError(
                    "status incident requires opened_at instead of published_at"
                )
        elif (
            self.published_at is None
            or self.opened_at is not None
            or self.resolved_at is not None
        ):
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


class RuntimeSourceAcquisitionRecoveryRequest(StrictModel):
    """One explicit Human choice after a terminal acquisition attempt."""

    schema_id = "briefloop.runtime_source_acquisition_recovery_request.v1"

    schema_version: Literal["briefloop.runtime_source_acquisition_recovery_request.v1"]
    request_id: ContractId
    run_id: ContractId
    expected_store_revision: NonNegativeInt
    expected_action_fingerprint: Sha256
    decision: Literal[
        "authorize_next_tavily_attempt",
        "provide_human_source_pack",
    ]
    previous_attempt_authorization_id: ContractId | None = None
    human_confirmation: Literal[True] | None = None
    provider_cost_status: Literal["not_reported_acknowledged"] | None = None
    human_source_pack: HumanSourcePackRequest | None = None

    @model_validator(mode="after")
    def decision_shape_is_exact(self) -> "RuntimeSourceAcquisitionRecoveryRequest":
        if self.decision == "authorize_next_tavily_attempt":
            if (
                self.previous_attempt_authorization_id is None
                or self.human_confirmation is not True
                or self.provider_cost_status != "not_reported_acknowledged"
                or self.human_source_pack is not None
            ):
                raise ValueError("next Tavily attempt request is incomplete")
        elif (
            self.previous_attempt_authorization_id is not None
            or self.human_confirmation is not None
            or self.provider_cost_status is not None
            or self.human_source_pack is None
        ):
            raise ValueError("human source pack recovery request is invalid")
        if self.human_source_pack is not None and (
            self.human_source_pack.run_id != self.run_id
            or self.human_source_pack.expected_store_revision
            != self.expected_store_revision
        ):
            raise ValueError("human source pack recovery identity mismatch")
        return self


class FrozenGuidanceItem(StrictModel):
    """One Human-authored guidance item copied into a successor snapshot."""

    item_id: ContractId
    position: NonNegativeInt
    source_run_id: ContractId
    finalized_lineage_fingerprint: Sha256
    assessment_result_id: ContractId
    assessment_result_fingerprint: Sha256
    finding_id: ContractId
    finding_fingerprint: Sha256
    disposition_id: ContractId
    disposition_fingerprint: Sha256
    guidance_id: ContractId
    draft_revision: PositiveInt
    draft_fingerprint: Sha256
    status_revision_id: ContractId
    status_fingerprint: Sha256
    guidance_text: CleanText
    guidance_sha256: Sha256
    reuse_scope: GuidanceReuseScopeV1
    item_fingerprint: Sha256

    @model_validator(mode="after")
    def copied_text_is_exact(self) -> "FrozenGuidanceItem":
        if (
            self.guidance_sha256
            != hashlib.sha256(self.guidance_text.encode("utf-8")).hexdigest()
        ):
            raise ValueError("frozen guidance text hash mismatch")
        return self


class FrozenGuidanceContext(StrictModel):
    """The complete immutable approved-guidance context for one role."""

    run_id: ContractId
    snapshot_id: ContractId
    snapshot_fingerprint: Sha256
    items: list[FrozenGuidanceItem] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def items_are_canonical_and_bounded(self) -> "FrozenGuidanceContext":
        if [item.position for item in self.items] != list(range(len(self.items))):
            raise ValueError("frozen guidance positions are not canonical")
        identities = [item.item_fingerprint for item in self.items]
        if len(identities) != len(set(identities)):
            raise ValueError("duplicate frozen guidance identity")
        if sum(len(item.guidance_text.encode("utf-8")) for item in self.items) > 65_536:
            raise ValueError("frozen guidance context exceeds byte limit")
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
    frozen_guidance_context: FrozenGuidanceContext | None = None

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
        if self.frozen_guidance_context is not None and (
            self.role_id not in {"analyst", "editor"}
            or self.stage_id not in {"analyst", "editor"}
            or self.frozen_guidance_context.run_id != self.run_id
        ):
            raise ValueError("frozen guidance belongs only to analyst and editor")
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


class FinalizedLocalGateBinding(StrictModel):
    """One receipt-bound finalize Gate observation for the local terminal."""

    schema_id = "briefloop.finalized_local_gate_binding.v2"

    schema_version: Literal["briefloop.finalized_local_gate_binding.v2"]
    evaluation_id: ContractId
    gate_batch_id: ContractId
    gate_id: GateId
    stage_id: Literal["finalize"]
    accepted_transaction_id: ContractId


class FinalizedLocalReportBinding(StrictModel):
    """Exact immutable reader revision selected by the finalization render."""

    schema_id = "briefloop.finalized_local_report_binding.v2"

    schema_version: Literal["briefloop.finalized_local_report_binding.v2"]
    render_id: ContractId
    render_receipt_id: ContractId
    artifact_id: Literal["reader_brief"]
    artifact_revision: PositiveInt
    relative_path: WorkspacePath
    sha256: Sha256
    size_bytes: NonNegativeInt
    markdown_utf8: bytes

    @model_validator(mode="after")
    def immutable_reader_bytes_match_identity(self) -> "FinalizedLocalReportBinding":
        try:
            self.markdown_utf8.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise ValueError("reader brief must be strict UTF-8") from exc
        if len(self.markdown_utf8) != self.size_bytes:
            raise ValueError("reader brief size does not match immutable bytes")
        if hashlib.sha256(self.markdown_utf8).hexdigest() != self.sha256:
            raise ValueError("reader brief SHA-256 does not match immutable bytes")
        return self


class FinalizedLocalReviewFacts(StrictModel):
    """Strict, read-only facts needed to review one finalized-local run."""

    schema_id = "briefloop.finalized_local_review_facts.v2"

    schema_version: Literal["briefloop.finalized_local_review_facts.v2"]
    boundary: Literal[
        "read_only_projection_not_runtime_gate_approval_delivery_or_provider_authority"
    ]
    workspace_id: ContractId
    run_id: ContractId
    store_revision: NonNegativeInt
    terminal_state: Literal["finalized_local"]
    terminal_action_fingerprint: Sha256
    finalization_id: ContractId
    finalization_receipt_id: ContractId
    finalize_gate_batch_id: ContractId
    gate_bindings: list[FinalizedLocalGateBinding] = Field(min_length=1)
    report: FinalizedLocalReportBinding
    facts_fingerprint: Sha256

    @staticmethod
    def _fingerprint_payload(payload: dict[str, object]) -> str:
        """Return the canonical JSON fingerprint without the self field."""

        canonical = dict(payload)
        canonical.pop("facts_fingerprint", None)
        try:
            encoded = json.dumps(
                canonical,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ValueError("finalized-local facts are not canonical JSON") from exc
        return hashlib.sha256(encoded).hexdigest()

    @classmethod
    def fingerprint_for(cls, payload: dict[str, object]) -> str:
        """Calculate the public, canonical facts fingerprint for a payload."""

        return cls._fingerprint_payload(payload)

    @model_validator(mode="after")
    def facts_are_canonical_and_bound(self) -> "FinalizedLocalReviewFacts":
        bindings = [(item.gate_id, item.evaluation_id) for item in self.gate_bindings]
        evaluation_ids = [item.evaluation_id for item in self.gate_bindings]
        if (
            bindings != sorted(bindings)
            or len(evaluation_ids) != len(set(evaluation_ids))
            or any(
                item.gate_batch_id != self.finalize_gate_batch_id
                for item in self.gate_bindings
            )
        ):
            raise ValueError("finalize Gate bindings must be sorted and exact")
        expected = self._fingerprint_payload(
            self.model_dump(mode="json", exclude={"facts_fingerprint"})
        )
        if self.facts_fingerprint != expected:
            raise ValueError("finalized-local facts fingerprint mismatch")
        return self


class FinalizedLocalReviewProjection(StrictModel):
    """One strict Core-derived finalized-local facts projection."""

    schema_id = "briefloop.finalized_local_review_projection.v2"

    schema_version: Literal["briefloop.finalized_local_review_projection.v2"]
    facts: FinalizedLocalReviewFacts
    local_run: LocalRunPresentation

    @model_validator(mode="after")
    def local_presentation_matches_exact_finalized_facts(
        self,
    ) -> "FinalizedLocalReviewProjection":
        local = self.local_run
        report = self.facts.report
        if (
            local.run_id != self.facts.run_id
            or local.store_revision != self.facts.store_revision
            or local.completion_target != "finalized_local"
            or local.view_state != "finalized"
            or local.terminal_state != "finalized_local"
            or local.next_action.action_kind != "complete"
            or local.next_action.effect_kind != "finalized_local"
            or local.next_action.reason_code != "local_finalization_complete"
            or local.reader_brief.state != "available"
            or local.reader_brief.artifact_id != report.artifact_id
            or local.reader_brief.revision != report.artifact_revision
            or local.reader_brief.sha256 != report.sha256
            or local.reader_brief.markdown_utf8 != report.markdown_utf8
        ):
            raise ValueError("local presentation does not match finalized-local facts")
        return self


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
    "FrozenGuidanceContext",
    "FrozenGuidanceItem",
    "FrozenSourceManifestEntry",
    "FinalizedLocalGateBinding",
    "FinalizedLocalReportBinding",
    "FinalizedLocalReviewFacts",
    "FinalizedLocalReviewProjection",
    "GateRepairContext",
    "GateRepairFindingContext",
    "GateRepairStartRequest",
    "HumanSourceMaterialRequest",
    "HumanSourcePackMember",
    "HumanSourcePackRequest",
    "RuntimeSourceAcquisitionRecoveryRequest",
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
