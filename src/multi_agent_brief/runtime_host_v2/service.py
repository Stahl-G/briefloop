"""Thin active host over verified CoreRun services."""

from __future__ import annotations

from dataclasses import dataclass, replace
import os
from pathlib import Path
from pathlib import PurePosixPath
import stat
from typing import Literal

from pydantic import ValidationError

from multi_agent_brief.contracts.errors import (
    FieldViolation,
    pydantic_error_violations,
)
from multi_agent_brief.contracts.v2 import (
    ArtifactRevisionReference,
    ArtifactSubmitRequest,
    AuditPromotionRequest,
    AuditProposal,
    CandidateClaimsProposal,
    ClaimFreezeRequest,
    ClaimDraftsProposal,
    CoreRunNextAction,
    GateCheckRequest,
    DeliveryAuthorizationRequest,
    DeliveryAttemptRequest,
    DeliveryResultObservation,
    DeliveryResultRequest,
    FinalizeCompleteRequest,
    FinalizeRenderRequest,
    ArtifactSupersedeRequest,
    RecoveryCompleteRequest,
    RepairCompleteRequest,
    RepairStartRequest,
    IntegrityCheckRequest,
    InternalApprovalRequest,
    InvocationFailureRequest,
    InvocationStartRequest,
    OwnedArtifactSubmitRequest,
    RuntimeAdapterBinding,
    ScreenedCandidatesProposal,
    SourceCommitRequest,
    SourcePackCommitRequest,
    SourceProposal,
    StageCompleteRequest,
    StrictModel,
)
from multi_agent_brief.control_store import ControlStoreError, SQLiteControlStore
from multi_agent_brief.control_store.serialization import (
    canonical_fingerprint,
    canonical_json_bytes,
    sha256_hex,
)
from multi_agent_brief.core_run_v2.artifacts import (
    ArtifactAcceptanceService,
    _input_classification_bytes,
)
from multi_agent_brief.core_run_v2.claims import ClaimFreezeService
from multi_agent_brief.core_run_v2.gates import GateEvaluationService
from multi_agent_brief.core_run_v2.lineage import (
    classify_current_audit_promotion,
    classify_current_lineage,
)
from multi_agent_brief.core_run_v2.next_action import classify_core_run_next_action
from multi_agent_brief.core_run_v2.policy import (
    ARTIFACT_POLICIES,
    core_role_topology_policy,
    derived_id,
)
from multi_agent_brief.core_run_v2.service import CoreRunService
from multi_agent_brief.core_run_v2.recovery import (
    CoreRunRecoveryService,
    classify_recovery_legality,
)
from multi_agent_brief.core_run_v2.terminal import (
    CoreRunTerminalService,
    classify_terminal_legality,
)
from multi_agent_brief.core_run_v2.verifier import CoreRunDomainVerifier
from multi_agent_brief.core.citations import remove_src_marker_spans
from multi_agent_brief.intake_v2.errors import IntakeError
from multi_agent_brief.intake_v2.scratch import ScratchReader, parse_json_object
from multi_agent_brief.intake_v2.service import IntakeService
from multi_agent_brief.sources.search_backends.base import SearchBackendError
from multi_agent_brief.outputs.reader_projection import (
    ReaderProjectionSourceError,
    reader_projection_source_markdown,
)

from .contracts import (
    FrozenSourceManifestEntry,
    HumanSourceMaterialRequest,
    HumanSourcePackMember,
    HumanSourcePackRequest,
    RepairContentInput,
    RoleTaskEnvelope,
    RuntimeDiagnoseReport,
    RuntimeInvocationResult,
    RuntimeProposalValidationResult,
)
from .errors import RuntimeHostError
from .initialization import AdapterLoader, initialize_or_open_runtime
from .scratch import (
    attest_host_directory,
    materialize_host_bytes,
    materialize_host_request,
    materialize_role_envelope,
    read_role_envelope,
    read_role_outputs,
)


@dataclass(frozen=True)
class _RoleOutputSpec:
    filenames: tuple[str, ...]
    proposal_schema_id: str
    owner_kind: Literal["source", "proposal", "owned"]
    artifact_id: str | None = None
    proposal_lane: str | None = None
    proposal_model: type[StrictModel] | None = None
    producer_tool_id: str | None = None


_ROLE_OUTPUTS: dict[str, _RoleOutputSpec] = {
    "source-planner": _RoleOutputSpec(
        filenames=("source_candidates.yaml",),
        proposal_schema_id="briefloop.owned_artifact_submit_request.v2",
        owner_kind="owned",
        artifact_id="source_candidates",
    ),
    "source-provider": _RoleOutputSpec(
        filenames=("source_content.bin", "source_proposal.json", "source_raw.json"),
        proposal_schema_id=SourceProposal.schema_id,
        owner_kind="source",
        proposal_model=SourceProposal,
    ),
    "scout": _RoleOutputSpec(
        filenames=("candidate_claims.json",),
        proposal_schema_id=CandidateClaimsProposal.schema_id,
        owner_kind="proposal",
        artifact_id="candidate_claims",
        proposal_lane="candidate",
        proposal_model=CandidateClaimsProposal,
    ),
    "screener": _RoleOutputSpec(
        filenames=("screened_candidates.json",),
        proposal_schema_id=ScreenedCandidatesProposal.schema_id,
        owner_kind="proposal",
        artifact_id="screened_candidates",
        proposal_lane="screened",
        proposal_model=ScreenedCandidatesProposal,
    ),
    "claim-ledger": _RoleOutputSpec(
        filenames=("claim_drafts.json",),
        proposal_schema_id=ClaimDraftsProposal.schema_id,
        owner_kind="proposal",
        artifact_id="claim_drafts",
        proposal_lane="claim-drafts",
        proposal_model=ClaimDraftsProposal,
    ),
    "analyst": _RoleOutputSpec(
        filenames=("analyst_draft.md",),
        proposal_schema_id="briefloop.owned_artifact_submit_request.v2",
        owner_kind="owned",
        artifact_id="analyst_draft_snapshot",
        producer_tool_id="analyst-snapshot-v2",
    ),
    "editor": _RoleOutputSpec(
        filenames=("audited_brief.md",),
        proposal_schema_id="briefloop.owned_artifact_submit_request.v2",
        owner_kind="owned",
        artifact_id="audited_brief",
    ),
    "auditor": _RoleOutputSpec(
        filenames=("audit_proposal.json",),
        proposal_schema_id=AuditProposal.schema_id,
        owner_kind="proposal",
        artifact_id="audit_proposal",
        proposal_lane="audit",
        proposal_model=AuditProposal,
    ),
}


def _role_task_instructions(
    role_id: str,
    output: _RoleOutputSpec,
    invocation_id: str,
) -> str:
    base = (
        f"Complete only the frozen {role_id} role task in this recorded "
        "invocation."
    )
    if output.proposal_model is None:
        return base
    proposal_filename = (
        "source_proposal.json"
        if output.owner_kind == "source"
        else output.filenames[0]
    )
    return (
        f"{base} Before writing {proposal_filename}, run `briefloop contract "
        f"show {output.proposal_schema_id} --example full` and follow that "
        "exact wrapper and field contract. After writing all allowed outputs, "
        "run `briefloop runtime invocation-validate --workspace . --envelope "
        f"scratch/{invocation_id}/role_task_envelope.json`. Return only after "
        "status is valid; never guess aliases, wrapper names, or invocation "
        "bindings."
    )


def _strict_proposal_violations(
    output: _RoleOutputSpec,
    outputs: dict[str, bytes],
    *,
    expected_run_id: str,
) -> list[FieldViolation]:
    if output.proposal_model is None:
        return []
    proposal_name = (
        "source_proposal.json"
        if output.owner_kind == "source"
        else output.filenames[0]
    )
    try:
        parsed = parse_json_object(outputs[proposal_name])
        proposal = output.proposal_model.model_validate(parsed, strict=True)
    except IntakeError:
        return [FieldViolation(field="$", error="proposal payload is unreadable")]
    except ValidationError as exc:
        return pydantic_error_violations(exc)
    except (KeyError, TypeError, ValueError):
        return [FieldViolation(field="$", error="proposal payload is invalid")]
    if getattr(proposal, "run_id", None) != expected_run_id:
        return [
            FieldViolation(
                field="run_id",
                error="must match the current invocation run",
            )
        ]
    return []


@dataclass(frozen=True)
class InvocationDispatch:
    envelope: RoleTaskEnvelope
    envelope_path: Path


class RuntimeHostService:
    def __init__(self, workspace: Path, *, adapter_loader: AdapterLoader) -> None:
        self.workspace = workspace.resolve(strict=True)
        self._adapter_loader = adapter_loader

    def next_action(self) -> CoreRunNextAction:
        return initialize_or_open_runtime(
            self.workspace,
            adapter_loader=self._adapter_loader,
        ).action

    def diagnose(self) -> RuntimeDiagnoseReport:
        current = initialize_or_open_runtime(
            self.workspace,
            adapter_loader=self._adapter_loader,
        )
        return RuntimeDiagnoseReport.model_validate(
            {
                "schema_version": RuntimeDiagnoseReport.schema_id,
                "run_id": current.verified.snapshot.run.run_id,
                "store_revision": current.verified.snapshot.store_revision,
                "store_valid": True,
                "adapter_binding_valid": True,
                "projection_drift": None,
                "next_action": current.action.model_dump(
                    mode="json", exclude_unset=False
                ),
            },
            strict=True,
        )

    def start_current_invocation(
        self,
        expected_action: CoreRunNextAction | None = None,
    ) -> InvocationDispatch:
        current = initialize_or_open_runtime(
            self.workspace,
            adapter_loader=self._adapter_loader,
        )
        recovered = self._recover_active_invocation(current, expected_action)
        if recovered is not None:
            return recovered
        action = current.action
        if expected_action is not None and expected_action != action:
            raise RuntimeHostError("runtime_action_stale")
        role_id = self._invocation_role_for_action(action)
        if role_id is None or action.stage_id is None:
            raise RuntimeHostError("runtime_action_not_invocable")
        return self._start_invocation_for_action(
            current,
            action,
            role_id=role_id,
            request_id=derived_id(
                "REQ-HOST-INVOKE",
                action.run_id,
                action.action_fingerprint,
            ),
        )

    @staticmethod
    def _invocation_role_for_action(action: CoreRunNextAction) -> str | None:
        if action.action_kind == "delegate":
            return action.role_id
        if (
            action.action_kind == "deterministic"
            and action.effect_kind == "source_acquire"
        ):
            return "source-provider"
        return None

    def _recover_active_invocation(
        self,
        current,
        expected_action: CoreRunNextAction | None,
    ) -> InvocationDispatch | None:
        active = [
            item
            for item in current.verified.snapshot.invocations
            if item.status == "active"
        ]
        if not active:
            return None
        if len(active) != 1:
            raise RuntimeHostError("runtime_envelope_invalid")
        invocation = active[0]
        starts = [
            item
            for item in current.verified.snapshot.events
            if item.event_type == "role_invocation_started"
            and item.core_run_binding is not None
            and item.core_run_binding.effect_kind == "invocation_start"
            and item.core_run_binding.primary_record_id == invocation.invocation_id
        ]
        if len(starts) != 1 or starts[0].stage_id is None:
            raise RuntimeHostError("runtime_envelope_invalid")
        start = starts[0]
        binding = start.core_run_binding
        if binding is None:
            raise RuntimeHostError("runtime_envelope_invalid")
        try:
            with SQLiteControlStore.open(self.workspace / "briefloop.db") as store:
                history = store.load_history()
                receipt = store.load_transaction_receipt(
                    invocation.run_id,
                    start.transaction_id,
                )
            if (
                receipt is None
                or receipt.committed_revision <= 1
                or receipt.prior_revision != receipt.committed_revision - 1
            ):
                raise RuntimeHostError("runtime_envelope_invalid")
            verifier = CoreRunDomainVerifier()
            verifier.verify_history(
                history,
                through_revision=receipt.committed_revision,
            )
            pre_snapshot = history.snapshot_at_revision(
                invocation.run_id,
                receipt.prior_revision,
            )
        except RuntimeHostError:
            raise
        except Exception as exc:
            raise RuntimeHostError("runtime_envelope_invalid") from exc
        historical = replace(current.verified, snapshot=pre_snapshot)
        action = classify_core_run_next_action(historical)
        role_id = self._invocation_role_for_action(action)
        if role_id is None or action.stage_id is None:
            raise RuntimeHostError("runtime_envelope_invalid")
        request_id = derived_id(
            "REQ-HOST-INVOKE",
            action.run_id,
            action.action_fingerprint,
        )
        request = self._invocation_start_request(
            current,
            action,
            role_id=role_id,
            request_id=request_id,
        )
        fingerprint = canonical_fingerprint(
            request.model_dump(mode="json", exclude_unset=False)
        )
        if (
            (expected_action is not None and expected_action != action)
            or action.stage_id != start.stage_id
            or invocation.role_id != role_id
            or invocation.runtime != request.runtime
            or start.transaction_id != request_id
            or binding.request_id != request_id
            or binding.request_fingerprint != fingerprint
            or derived_id("INV", request_id, fingerprint) != invocation.invocation_id
        ):
            raise RuntimeHostError("runtime_envelope_invalid")
        return self._start_invocation_for_action(
            current,
            action,
            role_id=role_id,
            request_id=request_id,
        )

    @staticmethod
    def _invocation_start_request(
        current,
        action: CoreRunNextAction,
        *,
        role_id: str | None,
        request_id: str,
    ) -> InvocationStartRequest:
        if role_id is None or action.stage_id is None:
            raise RuntimeHostError("runtime_action_not_invocable")
        return InvocationStartRequest.model_validate(
            {
                "schema_version": InvocationStartRequest.schema_id,
                "request_id": request_id,
                "run_id": action.run_id,
                "stage_id": action.stage_id,
                "role_id": role_id,
                "runtime": current.verified.snapshot.run.runtime,
                "expected_store_revision": action.store_revision,
            },
            strict=True,
        )

    def _start_invocation_for_action(
        self,
        current,
        action: CoreRunNextAction,
        *,
        role_id: str,
        request_id: str,
    ) -> InvocationDispatch:
        if action.stage_id is None:
            raise RuntimeHostError("runtime_action_not_invocable")
        request = self._invocation_start_request(
            current,
            action,
            role_id=role_id,
            request_id=request_id,
        )
        result = CoreRunService(self.workspace).start_invocation(request)
        if result.status == "commit_outcome_unknown":
            result = CoreRunService(self.workspace).start_invocation(request)
        if result.status not in {"committed", "replayed"}:
            raise RuntimeHostError(
                result.error_code or "control_store_integrity_invalid"
            )
        if result.primary_record_id is None:
            raise RuntimeHostError("control_store_integrity_invalid")
        invocation_id = result.primary_record_id
        output = _ROLE_OUTPUTS[role_id]
        topology = core_role_topology_policy(current.verified.binding.role_topology)
        dispatch_instruction = {
            "main_session": "execute_in_current_session",
            "delegated_specialist": "delegate_exact_role",
            "declared_existing_route": "use_declared_route",
        }[topology.role_executor_route]
        envelope = RoleTaskEnvelope.model_validate(
            {
                "schema_version": RoleTaskEnvelope.schema_id,
                "run_id": action.run_id,
                "invocation_id": invocation_id,
                "store_revision": result.receipt.committed_revision,
                "action": action.model_dump(mode="json", exclude_unset=False),
                "action_fingerprint": action.action_fingerprint,
                "role_id": role_id,
                "stage_id": action.stage_id,
                "scratch_directory": f"scratch/{invocation_id}",
                "allowed_output_filenames": sorted(output.filenames),
                "proposal_schema_id": output.proposal_schema_id,
                "adapter_binding_fingerprint": (
                    current.verified.runtime_adapter.binding_fingerprint
                ),
                "source_plan_fingerprint": (
                    current.verified.source_plan.source_plan_fingerprint
                ),
                "executor_kind": topology.role_executor_route,
                "context_mode": topology.context_mode,
                "review_mode": topology.review_mode,
                "dispatch_instruction": dispatch_instruction,
                "task_instructions": _role_task_instructions(
                    role_id,
                    output,
                    invocation_id,
                ),
            },
            strict=True,
        )
        try:
            envelope_path = materialize_role_envelope(self.workspace, envelope)
        except RuntimeHostError:
            failed = self._record_invocation_failure(
                envelope,
                reason_code="envelope_materialization_failed",
                expected_store_revision=result.receipt.committed_revision,
            )
            if failed.status not in {"rejected_recorded", "replayed"}:
                raise RuntimeHostError("control_store_integrity_invalid")
            raise RuntimeHostError("runtime_envelope_materialization_failed")
        return InvocationDispatch(envelope=envelope, envelope_path=envelope_path)

    def fail_invocation(
        self,
        invocation_id: str,
        *,
        reason_code: str,
        expected_envelope: RoleTaskEnvelope | None = None,
    ) -> RuntimeInvocationResult:
        envelope = read_role_envelope(self.workspace, invocation_id)
        if expected_envelope is not None and expected_envelope != envelope:
            raise RuntimeHostError("runtime_envelope_invalid")
        current = initialize_or_open_runtime(
            self.workspace,
            adapter_loader=self._adapter_loader,
        )
        spec = _ROLE_OUTPUTS.get(envelope.role_id)
        if spec is None:
            raise RuntimeHostError("runtime_envelope_invalid")
        self._validate_envelope(current, envelope, spec)
        result = self._record_invocation_failure(
            envelope,
            reason_code=reason_code,
            expected_store_revision=envelope.store_revision,
        )
        if result.status == "commit_outcome_unknown":
            result = self._record_invocation_failure(
                envelope,
                reason_code=reason_code,
                expected_store_revision=envelope.store_revision,
            )
        if result.status not in {"rejected_recorded", "replayed"}:
            raise RuntimeHostError(
                result.error_code or "control_store_integrity_invalid"
            )
        receipt = result.receipt
        if receipt is None:
            raise RuntimeHostError("control_store_integrity_invalid")
        return RuntimeInvocationResult.model_validate(
            {
                "schema_version": RuntimeInvocationResult.schema_id,
                "run_id": envelope.run_id,
                "invocation_id": invocation_id,
                "status": result.status,
                "transaction_id": receipt.transaction_id,
                "store_revision": receipt.committed_revision,
                "next_action": self.next_action().model_dump(
                    mode="json", exclude_unset=False
                ),
            },
            strict=True,
        )

    def _record_invocation_failure(
        self,
        envelope: RoleTaskEnvelope,
        *,
        reason_code: str,
        expected_store_revision: int,
    ):
        try:
            request = InvocationFailureRequest.model_validate(
                {
                    "schema_version": InvocationFailureRequest.schema_id,
                    "request_id": derived_id(
                        "REQ-HOST-INVOCATION-FAILURE",
                        envelope.invocation_id,
                        reason_code,
                    ),
                    "run_id": envelope.run_id,
                    "invocation_id": envelope.invocation_id,
                    "reason_code": reason_code,
                    "expected_store_revision": expected_store_revision,
                },
                strict=True,
            )
        except ValidationError as exc:
            raise RuntimeHostError("runtime_failure_reason_invalid") from exc
        return IntakeService(self.workspace).fail_invocation(request)

    def validate_invocation(
        self,
        invocation_id: str,
        *,
        expected_envelope: RoleTaskEnvelope | None = None,
    ) -> RuntimeProposalValidationResult:
        """Validate exact invocation outputs without writing Store state."""

        envelope = read_role_envelope(self.workspace, invocation_id)
        if expected_envelope is not None and expected_envelope != envelope:
            raise RuntimeHostError("runtime_envelope_invalid")
        spec = _ROLE_OUTPUTS.get(envelope.role_id)
        if spec is None:
            raise RuntimeHostError("runtime_envelope_invalid")
        current = initialize_or_open_runtime(
            self.workspace,
            adapter_loader=self._adapter_loader,
        )
        self._validate_envelope(current, envelope, spec)
        invocation = next(
            (
                item
                for item in current.verified.snapshot.invocations
                if item.invocation_id == invocation_id
            ),
            None,
        )
        if invocation is None or invocation.status not in {"active", "completed"}:
            raise RuntimeHostError("runtime_envelope_invalid")
        host_files = (
            ("submit_request.json",) if invocation.status == "completed" else ()
        )
        try:
            outputs = read_role_outputs(
                self.workspace,
                envelope,
                host_filenames=host_files,
            )
        except RuntimeHostError as exc:
            code = str(exc)
            if code not in {"runtime_proposal_missing", "runtime_scratch_invalid"}:
                raise
            return self._proposal_validation_result(
                envelope,
                spec,
                reason_code=code,
                violations=[],
            )
        violations = _strict_proposal_violations(
            spec,
            outputs,
            expected_run_id=envelope.run_id,
        )
        return self._proposal_validation_result(
            envelope,
            spec,
            reason_code=("runtime_proposal_invalid" if violations else None),
            violations=violations,
        )

    @staticmethod
    def _proposal_validation_result(
        envelope: RoleTaskEnvelope,
        spec: _RoleOutputSpec,
        *,
        reason_code: str | None,
        violations: list[FieldViolation],
    ) -> RuntimeProposalValidationResult:
        return RuntimeProposalValidationResult.model_validate(
            {
                "schema_version": RuntimeProposalValidationResult.schema_id,
                "run_id": envelope.run_id,
                "invocation_id": envelope.invocation_id,
                "proposal_schema_id": envelope.proposal_schema_id,
                "status": "invalid" if reason_code is not None else "valid",
                "reason_code": reason_code,
                "checked_filenames": sorted(spec.filenames),
                "violations": [
                    {"field": item.field, "reason": item.error}
                    for item in violations
                ],
            },
            strict=True,
        )

    def accept_invocation(
        self,
        invocation_id: str,
        *,
        expected_envelope: RoleTaskEnvelope | None = None,
    ) -> RuntimeInvocationResult:
        envelope = read_role_envelope(self.workspace, invocation_id)
        if expected_envelope is not None and expected_envelope != envelope:
            raise RuntimeHostError("runtime_envelope_invalid")
        spec = _ROLE_OUTPUTS.get(envelope.role_id)
        if spec is None:
            raise RuntimeHostError("runtime_envelope_invalid")
        current = initialize_or_open_runtime(
            self.workspace,
            adapter_loader=self._adapter_loader,
        )
        self._validate_envelope(current, envelope, spec)
        invocation = next(
            (
                item
                for item in current.verified.snapshot.invocations
                if item.invocation_id == invocation_id
            ),
            None,
        )
        if invocation is None or invocation.status not in {"active", "completed"}:
            raise RuntimeHostError("runtime_envelope_invalid")
        host_files = (
            ("submit_request.json",) if invocation.status == "completed" else ()
        )
        outputs = read_role_outputs(
            self.workspace,
            envelope,
            host_filenames=host_files,
        )
        request, lane = self._derive_acceptance_request(envelope, spec, outputs)
        request_path = materialize_host_request(
            self.workspace,
            envelope,
            canonical_json_bytes(request.model_dump(mode="json", exclude_unset=False)),
        )
        relative_request = request_path.relative_to(self.workspace).as_posix()
        if spec.owner_kind == "source":
            result = IntakeService(self.workspace).submit_source(relative_request)
        elif spec.owner_kind == "proposal":
            if lane is None:
                raise RuntimeHostError("runtime_envelope_invalid")
            result = IntakeService(self.workspace).submit_proposal(
                lane,
                relative_request,
            )
        else:
            result = ArtifactAcceptanceService(self.workspace).submit_owned_artifact(
                request
            )
        status = result.status
        if status == "commit_outcome_unknown":
            if spec.owner_kind == "source":
                result = IntakeService(self.workspace).submit_source(relative_request)
            elif spec.owner_kind == "proposal":
                result = IntakeService(self.workspace).submit_proposal(
                    lane or "",
                    relative_request,
                )
            else:
                result = ArtifactAcceptanceService(
                    self.workspace
                ).submit_owned_artifact(request)
            status = result.status
        if status not in {"committed", "replayed", "rejected_recorded"}:
            raise RuntimeHostError(
                getattr(result, "error_code", None) or "control_store_integrity_invalid"
            )
        receipt = result.receipt
        if receipt is None:
            raise RuntimeHostError("control_store_integrity_invalid")
        next_action = self.next_action()
        return RuntimeInvocationResult.model_validate(
            {
                "schema_version": RuntimeInvocationResult.schema_id,
                "run_id": envelope.run_id,
                "invocation_id": invocation_id,
                "status": status,
                "transaction_id": receipt.transaction_id,
                "store_revision": receipt.committed_revision,
                "next_action": next_action.model_dump(mode="json", exclude_unset=False),
            },
            strict=True,
        )

    def _validate_envelope(self, current, envelope, spec: _RoleOutputSpec) -> None:
        topology = core_role_topology_policy(current.verified.binding.role_topology)
        dispatch_instruction = {
            "main_session": "execute_in_current_session",
            "delegated_specialist": "delegate_exact_role",
            "declared_existing_route": "use_declared_route",
        }[topology.role_executor_route]
        if (
            envelope.run_id != current.verified.snapshot.run.run_id
            or envelope.adapter_binding_fingerprint
            != current.verified.runtime_adapter.binding_fingerprint
            or envelope.source_plan_fingerprint
            != current.verified.source_plan.source_plan_fingerprint
            or envelope.allowed_output_filenames != sorted(spec.filenames)
            or envelope.proposal_schema_id != spec.proposal_schema_id
            or envelope.scratch_directory != f"scratch/{envelope.invocation_id}"
            or envelope.store_revision != envelope.action.store_revision + 1
            or envelope.executor_kind != topology.role_executor_route
            or envelope.context_mode != topology.context_mode
            or envelope.review_mode != topology.review_mode
            or envelope.dispatch_instruction != dispatch_instruction
        ):
            raise RuntimeHostError("runtime_envelope_invalid")
        invocation = next(
            (
                item
                for item in current.verified.snapshot.invocations
                if item.invocation_id == envelope.invocation_id
            ),
            None,
        )
        if (
            invocation is None
            or invocation.run_id != envelope.run_id
            or invocation.role_id != envelope.role_id
        ):
            raise RuntimeHostError("runtime_envelope_invalid")
        start_events = [
            item
            for item in current.verified.snapshot.events
            if item.core_run_binding is not None
            and item.core_run_binding.effect_kind == "invocation_start"
            and item.core_run_binding.primary_record_id == envelope.invocation_id
        ]
        if len(start_events) != 1:
            raise RuntimeHostError("runtime_envelope_invalid")
        start = start_events[0]
        if start.stage_id != envelope.stage_id:
            raise RuntimeHostError("runtime_envelope_invalid")
        with SQLiteControlStore.open(self.workspace / "briefloop.db") as store:
            receipt = store.load_transaction_receipt(
                envelope.run_id,
                start.transaction_id,
            )
        if receipt is None or receipt.committed_revision != envelope.store_revision:
            raise RuntimeHostError("runtime_envelope_invalid")

    def _derive_acceptance_request(
        self,
        envelope: RoleTaskEnvelope,
        spec: _RoleOutputSpec,
        outputs: dict[str, bytes],
    ) -> tuple[
        SourceCommitRequest | ArtifactSubmitRequest | OwnedArtifactSubmitRequest,
        str | None,
    ]:
        with SQLiteControlStore.open(self.workspace / "briefloop.db") as store:
            history = store.load_history()
        try:
            snapshot = history.snapshot_at_revision(
                envelope.run_id,
                envelope.store_revision,
            )
        except Exception as exc:
            raise RuntimeHostError("runtime_envelope_invalid") from exc
        request_id = derived_id(
            "REQ-HOST-ACCEPT",
            envelope.invocation_id,
            envelope.action_fingerprint,
        )
        scratch = f"scratch/{envelope.invocation_id}"
        if spec.proposal_model is not None:
            if _strict_proposal_violations(
                spec,
                outputs,
                expected_run_id=envelope.run_id,
            ):
                raise RuntimeHostError("runtime_proposal_invalid")
        if spec.owner_kind == "source":
            return (
                SourceCommitRequest.model_validate(
                    {
                        "schema_version": SourceCommitRequest.schema_id,
                        "request_id": request_id,
                        "run_id": envelope.run_id,
                        "invocation_id": envelope.invocation_id,
                        "proposal_path": f"{scratch}/source_proposal.json",
                        "content_path": f"{scratch}/source_content.bin",
                        "raw_payload_path": f"{scratch}/source_raw.json",
                        "expected_store_revision": envelope.store_revision,
                    },
                    strict=True,
                ),
                None,
            )
        if spec.artifact_id is None:
            raise RuntimeHostError("runtime_envelope_invalid")
        artifact = next(
            (
                item
                for item in snapshot.artifacts
                if item.artifact_id == spec.artifact_id
            ),
            None,
        )
        if artifact is None:
            raise RuntimeHostError("runtime_envelope_invalid")
        if spec.owner_kind == "proposal":
            return (
                ArtifactSubmitRequest.model_validate(
                    {
                        "schema_version": ArtifactSubmitRequest.schema_id,
                        "request_id": request_id,
                        "run_id": envelope.run_id,
                        "artifact_id": spec.artifact_id,
                        "invocation_id": envelope.invocation_id,
                        "input_path": f"{scratch}/{spec.filenames[0]}",
                        "expected_store_revision": envelope.store_revision,
                        "expected_artifact_revision": artifact.current_revision,
                    },
                    strict=True,
                ),
                spec.proposal_lane,
            )
        parent: ArtifactRevisionReference | None = None
        if spec.artifact_id == "audited_brief":
            analyst = next(
                (
                    item
                    for item in snapshot.artifacts
                    if item.artifact_id == "analyst_draft_snapshot"
                ),
                None,
            )
            if analyst is None or analyst.current_revision < 1:
                raise RuntimeHostError("runtime_proposal_invalid")
            parent = ArtifactRevisionReference.model_validate(
                {
                    "artifact_id": analyst.artifact_id,
                    "revision": analyst.current_revision,
                },
                strict=True,
            )
        return (
            OwnedArtifactSubmitRequest.model_validate(
                {
                    "schema_version": OwnedArtifactSubmitRequest.schema_id,
                    "request_id": request_id,
                    "run_id": envelope.run_id,
                    "artifact_id": spec.artifact_id,
                    "invocation_id": envelope.invocation_id,
                    "producer_tool_id": spec.producer_tool_id,
                    "input_path": f"{scratch}/{spec.filenames[0]}",
                    "expected_store_revision": envelope.store_revision,
                    "expected_artifact_revision": artifact.current_revision,
                    "expected_parent_artifact": (
                        None
                        if parent is None
                        else parent.model_dump(mode="json", exclude_unset=False)
                    ),
                },
                strict=True,
            ),
            None,
        )

    def apply_current(
        self,
        expected_action: CoreRunNextAction | None = None,
        human_request: StrictModel | None = None,
        action_input: StrictModel | None = None,
    ):
        current = initialize_or_open_runtime(
            self.workspace,
            adapter_loader=self._adapter_loader,
        )
        action = current.action
        if expected_action is not None and expected_action != action:
            if (
                expected_action.effect_kind == "artifact_supersede"
                and isinstance(action_input, RepairContentInput)
                and human_request is None
            ):
                return self._replay_artifact_supersede(
                    current,
                    expected_action,
                    action_input,
                )
            if (
                isinstance(human_request, HumanSourcePackRequest)
                and action_input is None
                and expected_action.action_kind == "human_decision"
                and expected_action.effect_kind == "source_input_required"
            ):
                return self._apply_human_source_pack(
                    current,
                    expected_action,
                    human_request,
                    replay_only=True,
                )
            raise RuntimeHostError("runtime_action_stale")
        if action.action_kind == "human_decision":
            if action_input is not None:
                raise RuntimeHostError("runtime_action_input_invalid")
            return self._apply_human_decision(current, action, human_request)
        if action.action_kind != "deterministic":
            raise RuntimeHostError("runtime_action_not_deterministic")
        if human_request is not None:
            raise RuntimeHostError("runtime_human_request_invalid")
        if action.effect_kind == "artifact_supersede":
            if not isinstance(action_input, RepairContentInput):
                raise RuntimeHostError("runtime_action_input_required")
            result = self._apply_artifact_supersede(current, action, action_input)
        elif action_input is not None:
            raise RuntimeHostError("runtime_action_input_invalid")
        elif action.effect_kind == "invocation_accept_or_fail":
            active = [
                item
                for item in current.verified.snapshot.invocations
                if item.status == "active"
            ]
            if len(active) != 1:
                raise RuntimeHostError("control_store_integrity_invalid")
            result = self.accept_invocation(active[0].invocation_id)
        elif action.effect_kind == "doctor_check":
            request = IntegrityCheckRequest.model_validate(
                {
                    "schema_version": IntegrityCheckRequest.schema_id,
                    "request_id": derived_id(
                        "REQ-HOST-DOCTOR",
                        action.run_id,
                        action.action_fingerprint,
                    ),
                    "run_id": action.run_id,
                    "expected_store_revision": action.store_revision,
                },
                strict=True,
            )
            result = CoreRunService(self.workspace).doctor_check(request)
        elif action.effect_kind == "owned_artifact_acceptance":
            result = self._apply_input_governance(current, action)
        elif action.effect_kind == "source_acquire":
            result = self._apply_source_acquire(current, action)
        elif action.effect_kind == "claim_freeze":
            result = self._apply_claim_freeze(current, action)
        elif action.effect_kind == "audit_promotion":
            result = self._apply_audit_promotion(current, action)
        elif action.effect_kind in {"gate_evaluation", "finalize_gate"}:
            result = self._apply_gate_evaluation(current, action)
        elif action.effect_kind == "stage_complete":
            result = self._apply_stage_complete(current, action)
        elif action.effect_kind == "repair_start":
            result = self._apply_repair_start(current, action)
        elif action.effect_kind == "repair_complete":
            result = self._apply_repair_complete(current, action)
        elif action.effect_kind == "recovery_complete":
            result = self._apply_recovery_complete(current, action)
        elif action.effect_kind == "finalize_render":
            result = self._apply_finalize_render(current, action)
        elif action.effect_kind == "finalize_complete":
            result = self._apply_finalize_complete(current, action)
        elif action.effect_kind == "delivery_attempt":
            result = self._apply_delivery_attempt(current, action)
        elif action.effect_kind == "delivery_result":
            result = self._apply_delivery_result(current, action)
        else:
            raise RuntimeHostError("runtime_action_not_implemented")
        if isinstance(result, RuntimeInvocationResult):
            if result.status not in {"committed", "replayed", "rejected_recorded"}:
                raise RuntimeHostError("control_store_integrity_invalid")
            return result
        if result.status not in {"committed", "replayed"}:
            raise RuntimeHostError(
                result.error_code or "control_store_integrity_invalid"
            )
        if result.status == "committed" and action.effect_kind in {
            "finalize_complete",
            "delivery_result",
        }:
            # Read-only three-page brief HTML auto-open (config-gated, default
            # off, best-effort: the hook never raises into the run).
            from multi_agent_brief.product.brief_html import (
                maybe_auto_open_brief_pages,
            )

            maybe_auto_open_brief_pages(self.workspace)
        return result

    def _apply_human_decision(
        self,
        current,
        action: CoreRunNextAction,
        request: StrictModel | None,
    ):
        if request is None or request.schema_id != action.request_schema_id:
            raise RuntimeHostError("runtime_human_request_required")
        request_run_id = getattr(request, "run_id", None)
        expected_revision = getattr(request, "expected_store_revision", None)
        if (
            request_run_id != action.run_id
            or expected_revision != action.store_revision
        ):
            raise RuntimeHostError("runtime_human_request_invalid")
        terminal = CoreRunTerminalService(self.workspace)
        if action.effect_kind == "internal_approval" and isinstance(
            request, InternalApprovalRequest
        ):
            result = terminal.record_internal_approval(request)
        elif action.effect_kind in {
            "delivery_authorization",
            "delivery_reconciliation",
            "delivery_retry_authorization",
        } and isinstance(request, DeliveryAuthorizationRequest):
            result = terminal.authorize_delivery(request)
        elif action.effect_kind == "source_input_required" and isinstance(
            request, HumanSourcePackRequest
        ):
            return self._apply_human_source_pack(
                current,
                action,
                request,
                replay_only=False,
            )
        else:
            raise RuntimeHostError("runtime_human_request_invalid")
        if result.status not in {"committed", "replayed"}:
            raise RuntimeHostError(
                result.error_code or "control_store_integrity_invalid"
            )
        return result

    def _apply_source_acquire(self, current, action: CoreRunNextAction):
        from .source_routes import collect_frozen_sources

        route = next(
            (
                item
                for item in current.verified.source_plan.routes
                if item.route_id == action.source_route_id
                and item.provider_id == action.source_provider_id
            ),
            None,
        )
        if route is None or route.execution_owner != "deterministic":
            raise RuntimeHostError("runtime_source_plan_invalid")
        dispatch = self.start_current_invocation(action)
        invocation_id = dispatch.envelope.invocation_id
        try:
            materials = collect_frozen_sources(
                self.workspace,
                run_id=action.run_id,
                invocation_id=invocation_id,
                route=route,
            )
            members: list[dict[str, object]] = []
            for position, material in enumerate(materials, start=1):
                member_id = f"MEMBER-{position:04d}"
                root = f"scratch/{invocation_id}/sources/{member_id}"
                proposal_path = f"{root}/source_proposal.json"
                content_path = f"{root}/source_content.bin"
                raw_path = f"{root}/source_raw.json"
                self._materialize_tool_input(content_path, material.content)
                self._materialize_tool_input(raw_path, material.raw_payload)
                self._materialize_tool_input(
                    proposal_path,
                    canonical_json_bytes(
                        material.proposal.model_dump(
                            mode="json",
                            exclude_unset=False,
                        )
                    ),
                )
                members.append(
                    {
                        "member_id": member_id,
                        "proposal_path": proposal_path,
                        "content_path": content_path,
                        "raw_payload_path": raw_path,
                    }
                )
            submit = SourcePackCommitRequest.model_validate(
                {
                    "schema_version": SourcePackCommitRequest.schema_id,
                    "request_id": derived_id(
                        "REQ-HOST-SOURCE-PACK",
                        action.run_id,
                        action.action_fingerprint,
                    ),
                    "run_id": action.run_id,
                    "invocation_id": invocation_id,
                    "members": members,
                    "expected_store_revision": dispatch.envelope.store_revision,
                },
                strict=True,
            )
            submit_path = self._materialize_tool_input(
                f"scratch/{invocation_id}/submit_request.json",
                canonical_json_bytes(
                    submit.model_dump(mode="json", exclude_unset=False)
                ),
            )
        except (
            OSError,
            NotImplementedError,
            RuntimeError,
            RuntimeHostError,
            SearchBackendError,
            ValidationError,
            ValueError,
        ):
            return self.fail_invocation(
                invocation_id,
                reason_code="dispatch_unavailable",
            )
        relative = submit_path.relative_to(self.workspace).as_posix()
        intake = IntakeService(self.workspace)
        result = intake.submit_source_pack(relative)
        if result.status == "commit_outcome_unknown":
            result = intake.submit_source_pack(relative)
        return self._source_pack_runtime_result(invocation_id, result)

    def _apply_human_source_pack(
        self,
        current,
        action: CoreRunNextAction,
        request: HumanSourcePackRequest,
        *,
        replay_only: bool,
    ):
        request_fingerprint = canonical_fingerprint(
            request.model_dump(mode="json", exclude_unset=False)
        )
        invocation_request_id = derived_id(
            "REQ-HOST-HUMAN-SOURCE-PACK-INVOKE",
            request.request_id,
            action.action_fingerprint,
        )
        invocation_request = InvocationStartRequest.model_validate(
            {
                "schema_version": InvocationStartRequest.schema_id,
                "request_id": invocation_request_id,
                "run_id": action.run_id,
                "stage_id": "source-discovery",
                "role_id": "source-provider",
                "runtime": current.verified.snapshot.run.runtime,
                "expected_store_revision": action.store_revision,
            },
            strict=True,
        )
        invocation_id = derived_id(
            "INV",
            invocation_request_id,
            canonical_fingerprint(
                invocation_request.model_dump(mode="json", exclude_unset=False)
            ),
        )
        submit_relative = f"scratch/{invocation_id}/submit_request.json"
        submit_path = self.workspace / submit_relative
        commit_request_id = derived_id(
            "REQ-HOST-HUMAN-SOURCE-PACK-COMMIT",
            request.request_id,
            action.action_fingerprint,
            request_fingerprint,
        )
        contents: list[bytes] | None = None
        manifest_bytes: bytes | None = None
        manifest_entries: list[FrozenSourceManifestEntry] | None = None
        if submit_path.exists():
            try:
                stored = SourcePackCommitRequest.model_validate(
                    parse_json_object(
                        ScratchReader(self.workspace).read_request(submit_relative)
                    ),
                    strict=True,
                )
            except (IntakeError, ValidationError, ValueError) as exc:
                raise RuntimeHostError("runtime_human_request_invalid") from exc
            if stored.request_id != commit_request_id:
                raise RuntimeHostError("submission_replay_conflict")
        else:
            if replay_only:
                raise RuntimeHostError("runtime_action_stale")
            contents = [self._read_human_source_bytes(item) for item in request.members]
            manifest_bytes = self._read_workspace_input_bytes(
                request.manifest_path,
                request.expected_manifest_sha256,
                max_size=4 * 1024 * 1024,
            )
            manifest_entries = _frozen_manifest_entries(
                manifest_bytes, request.manifest_schema_version
            )
            if [item.source_id for item in manifest_entries] != [
                item.member_id for item in request.members
            ]:
                raise RuntimeHostError("runtime_human_request_invalid")
            for member, entry in zip(
                request.members, manifest_entries, strict=True
            ):
                if (
                    member.expected_input_sha256 != entry.sha256
                    or member.manifest_local_file != entry.local_file
                    or member.title != entry.title
                    or member.publisher != entry.publisher
                    or member.published_at != entry.published_at
                    or member.url != entry.url
                    or member.document_kind != entry.document_kind
                    or member.opened_at != entry.opened_at
                    or member.resolved_at != entry.resolved_at
                ):
                    raise RuntimeHostError("runtime_human_request_invalid")
            if sum(len(item) for item in contents) > 256 * 1024 * 1024:
                raise RuntimeHostError("runtime_human_request_invalid")
        dispatch = self._start_invocation_for_action(
            current,
            action,
            role_id="source-provider",
            request_id=invocation_request_id,
        )
        if dispatch.envelope.invocation_id != invocation_id:
            raise RuntimeHostError("control_store_integrity_invalid")
        if submit_path.exists():
            intake = IntakeService(self.workspace)
            result = intake.submit_source_pack(submit_relative)
            if result.status == "commit_outcome_unknown":
                result = intake.submit_source_pack(submit_relative)
            return self._source_pack_runtime_result(invocation_id, result)
        if (
            contents is None
            or manifest_bytes is None
            or manifest_entries is None
        ):  # pragma: no cover - guarded above
            raise RuntimeHostError("runtime_action_stale")
        commit_members: list[dict[str, object]] = []
        try:
            manifest_relative = f"scratch/{invocation_id}/source_manifest.json"
            self._materialize_tool_input(manifest_relative, manifest_bytes)
            for member, entry, content in zip(
                request.members, manifest_entries, contents, strict=True
            ):
                source_id = entry.source_id
                proposal = SourceProposal.model_validate(
                    {
                        "schema_version": SourceProposal.schema_id,
                        "proposal_id": derived_id(
                            "PROP-SOURCE-HUMAN-PACK",
                            invocation_id,
                            source_id,
                        ),
                        "run_id": action.run_id,
                        "source_id": source_id,
                        "origin_type": "uploaded_file",
                        "acquisition_method": "manual_upload",
                        "material_kind": "uploaded_file",
                        "provider": None,
                        "locator": {"kind": "web", "url": entry.url},
                        "title": entry.title,
                        "publisher": entry.publisher,
                        "published_at": entry.published_at,
                        "retrieved_at": member.retrieved_at,
                        "source_category": "other",
                        "retrieval_source_type": "local_file",
                        "underlying_evidence_type": "unknown",
                        "raw_underlying_evidence_type": entry.document_kind,
                        "content_sha256": member.expected_input_sha256,
                        "content_media_type": member.content_media_type,
                        "raw_payload_sha256": None,
                        "raw_payload_media_type": None,
                        "source_manifest_sha256": request.expected_manifest_sha256,
                        "manifest_local_file": entry.local_file,
                        "document_kind": entry.document_kind,
                        "opened_at": entry.opened_at,
                        "resolved_at": entry.resolved_at,
                    },
                    strict=True,
                )
                root = f"scratch/{invocation_id}/sources/{member.member_id}"
                proposal_relative = f"{root}/source_proposal.json"
                content_relative = f"{root}/source_content.bin"
                self._materialize_tool_input(content_relative, content)
                self._materialize_tool_input(
                    proposal_relative,
                    canonical_json_bytes(
                        proposal.model_dump(mode="json", exclude_unset=False)
                    ),
                )
                commit_members.append(
                    {
                        "member_id": member.member_id,
                        "proposal_path": proposal_relative,
                        "content_path": content_relative,
                        "raw_payload_path": None,
                    }
                )
            submit = SourcePackCommitRequest.model_validate(
                {
                    "schema_version": SourcePackCommitRequest.schema_id,
                    "request_id": commit_request_id,
                    "run_id": action.run_id,
                    "invocation_id": invocation_id,
                    "members": commit_members,
                    "manifest_path": manifest_relative,
                    "expected_manifest_sha256": request.expected_manifest_sha256,
                    "expected_store_revision": dispatch.envelope.store_revision,
                },
                strict=True,
            )
            self._materialize_tool_input(
                submit_relative,
                canonical_json_bytes(
                    submit.model_dump(mode="json", exclude_unset=False)
                ),
            )
        except (OSError, RuntimeHostError, ValidationError, ValueError):
            self.fail_invocation(
                invocation_id,
                reason_code="proposal_invalid",
                expected_envelope=dispatch.envelope,
            )
            raise RuntimeHostError("runtime_human_request_invalid")
        intake = IntakeService(self.workspace)
        result = intake.submit_source_pack(submit_relative)
        if result.status == "commit_outcome_unknown":
            result = intake.submit_source_pack(submit_relative)
        return self._source_pack_runtime_result(invocation_id, result)

    def _source_pack_runtime_result(self, invocation_id: str, result):
        if result.status not in {"committed", "replayed", "rejected_recorded"}:
            raise RuntimeHostError(
                result.error_code or "control_store_integrity_invalid"
            )
        receipt = result.receipt
        if receipt is None:
            raise RuntimeHostError("control_store_integrity_invalid")
        return RuntimeInvocationResult.model_validate(
            {
                "schema_version": RuntimeInvocationResult.schema_id,
                "run_id": receipt.run_id,
                "invocation_id": invocation_id,
                "status": result.status,
                "transaction_id": receipt.transaction_id,
                "store_revision": receipt.committed_revision,
                "next_action": self.next_action().model_dump(
                    mode="json", exclude_unset=False
                ),
            },
            strict=True,
        )

    def _apply_human_source_material(
        self,
        current,
        action: CoreRunNextAction,
        request: HumanSourceMaterialRequest,
        *,
        replay_only: bool,
    ):
        human_request_fingerprint = canonical_fingerprint(
            request.model_dump(mode="json", exclude_unset=False)
        )
        invocation_request_id = derived_id(
            "REQ-HOST-HUMAN-SOURCE-INVOKE",
            request.request_id,
            action.action_fingerprint,
        )
        invocation_request = InvocationStartRequest.model_validate(
            {
                "schema_version": InvocationStartRequest.schema_id,
                "request_id": invocation_request_id,
                "run_id": action.run_id,
                "stage_id": "source-discovery",
                "role_id": "source-provider",
                "runtime": current.verified.snapshot.run.runtime,
                "expected_store_revision": action.store_revision,
            },
            strict=True,
        )
        invocation_fingerprint = canonical_fingerprint(
            invocation_request.model_dump(mode="json", exclude_unset=False)
        )
        invocation_id = derived_id(
            "INV",
            invocation_request_id,
            invocation_fingerprint,
        )
        submit_relative = f"scratch/{invocation_id}/submit_request.json"
        submit_path = self.workspace / submit_relative
        commit_request_id = derived_id(
            "REQ-HOST-HUMAN-SOURCE-COMMIT",
            request.request_id,
            action.action_fingerprint,
            human_request_fingerprint,
        )
        content: bytes | None = None
        if submit_path.exists():
            try:
                stored_submit = SourceCommitRequest.model_validate(
                    parse_json_object(
                        ScratchReader(self.workspace).read_request(submit_relative)
                    ),
                    strict=True,
                )
            except (IntakeError, ValidationError, ValueError) as exc:
                raise RuntimeHostError("runtime_human_request_invalid") from exc
            if stored_submit.request_id != commit_request_id:
                raise RuntimeHostError("submission_replay_conflict")
        else:
            if replay_only:
                raise RuntimeHostError("runtime_action_stale")
            content = self._read_human_source_bytes(request)
        dispatch = self._start_invocation_for_action(
            current,
            action,
            role_id="source-provider",
            request_id=invocation_request_id,
        )
        if dispatch.envelope.invocation_id != invocation_id:
            raise RuntimeHostError("control_store_integrity_invalid")
        if submit_path.exists():
            return IntakeService(self.workspace).submit_source(submit_relative)
        if content is None:  # pragma: no cover - guarded by replay branch above
            raise RuntimeHostError("runtime_action_stale")
        source_id = derived_id(
            "SRC-HUMAN",
            action.run_id,
            request.request_id,
            request.expected_input_sha256,
        )
        proposal = SourceProposal.model_validate(
            {
                "schema_version": SourceProposal.schema_id,
                "proposal_id": derived_id(
                    "PROP-SOURCE-HUMAN",
                    invocation_id,
                    source_id,
                ),
                "run_id": action.run_id,
                "source_id": source_id,
                "origin_type": "uploaded_file",
                "acquisition_method": "manual_upload",
                "material_kind": "uploaded_file",
                "provider": None,
                "locator": {"kind": "file", "path": request.input_path},
                "title": request.title,
                "publisher": request.publisher,
                "published_at": request.published_at,
                "retrieved_at": request.retrieved_at,
                "source_category": "other",
                "retrieval_source_type": "local_file",
                "underlying_evidence_type": "unknown",
                "raw_underlying_evidence_type": None,
                "content_sha256": request.expected_input_sha256,
                "content_media_type": request.content_media_type,
                "raw_payload_sha256": None,
                "raw_payload_media_type": None,
            },
            strict=True,
        )
        content_relative = f"scratch/{invocation_id}/source_content.bin"
        proposal_relative = f"scratch/{invocation_id}/source_proposal.json"
        try:
            self._materialize_tool_input(content_relative, content)
            self._materialize_tool_input(
                proposal_relative,
                canonical_json_bytes(
                    proposal.model_dump(mode="json", exclude_unset=False)
                ),
            )
            submit = SourceCommitRequest.model_validate(
                {
                    "schema_version": SourceCommitRequest.schema_id,
                    "request_id": commit_request_id,
                    "run_id": action.run_id,
                    "invocation_id": invocation_id,
                    "proposal_path": proposal_relative,
                    "content_path": content_relative,
                    "raw_payload_path": None,
                    "expected_store_revision": dispatch.envelope.store_revision,
                },
                strict=True,
            )
            self._materialize_tool_input(
                submit_relative,
                canonical_json_bytes(
                    submit.model_dump(mode="json", exclude_unset=False)
                ),
            )
        except (OSError, RuntimeHostError, ValidationError, ValueError):
            self.fail_invocation(
                invocation_id,
                reason_code="proposal_invalid",
                expected_envelope=dispatch.envelope,
            )
            raise RuntimeHostError("runtime_human_request_invalid")
        return IntakeService(self.workspace).submit_source(submit_relative)

    def _read_human_source_bytes(
        self,
        request: HumanSourceMaterialRequest | HumanSourcePackMember,
    ) -> bytes:
        return self._read_workspace_input_bytes(
            request.input_path,
            request.expected_input_sha256,
            max_size=16 * 1024 * 1024,
        )

    def _read_workspace_input_bytes(
        self,
        input_path: str,
        expected_sha256: str,
        *,
        max_size: int,
    ) -> bytes:
        candidate = self.workspace / input_path
        try:
            current = self.workspace
            for part in Path(input_path).parts:
                current = current / part
                metadata = current.lstat()
                if current.is_symlink():
                    raise RuntimeHostError("runtime_human_request_invalid")
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_nlink != 1
                or metadata.st_size > max_size
            ):
                raise RuntimeHostError("runtime_human_request_invalid")
            descriptor = os.open(
                candidate,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            )
            try:
                opened = os.fstat(descriptor)
                if (
                    (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino)
                    or opened.st_nlink != 1
                    or opened.st_size > max_size
                ):
                    raise RuntimeHostError("runtime_human_request_invalid")
                payload = os.read(descriptor, max_size + 1)
            finally:
                os.close(descriptor)
        except RuntimeHostError:
            raise
        except OSError as exc:
            raise RuntimeHostError("runtime_human_request_invalid") from exc
        if not payload or sha256_hex(payload) != expected_sha256:
            raise RuntimeHostError("runtime_human_request_invalid")
        return payload

    def _apply_input_governance(self, current, action: CoreRunNextAction):
        snapshot = current.verified.snapshot
        artifact = self._artifact(snapshot, "input_classification")
        request_id = derived_id(
            "REQ-HOST-INPUT-GOVERNANCE",
            action.run_id,
            action.action_fingerprint,
        )
        tool_id = derived_id("TOOL-INPUT-GOVERNANCE", request_id)
        relative = f"scratch/{tool_id}/input_classification.json"
        payload = _input_classification_bytes(self.workspace)
        self._materialize_tool_input(relative, payload)
        request = OwnedArtifactSubmitRequest.model_validate(
            {
                "schema_version": OwnedArtifactSubmitRequest.schema_id,
                "request_id": request_id,
                "run_id": action.run_id,
                "artifact_id": "input_classification",
                "invocation_id": None,
                "producer_tool_id": "input-governance-v2",
                "input_path": relative,
                "expected_store_revision": action.store_revision,
                "expected_artifact_revision": artifact.current_revision,
                "expected_parent_artifact": None,
            },
            strict=True,
        )
        return ArtifactAcceptanceService(self.workspace).submit_owned_artifact(request)

    def _apply_claim_freeze(self, current, action: CoreRunNextAction):
        snapshot = current.verified.snapshot
        drafts = classify_current_lineage(snapshot).current_proposal("claim_drafts")
        ledger = self._artifact(snapshot, "claim_ledger")
        request = ClaimFreezeRequest.model_validate(
            {
                "schema_version": ClaimFreezeRequest.schema_id,
                "request_id": derived_id(
                    "REQ-HOST-CLAIM-FREEZE",
                    action.run_id,
                    action.action_fingerprint,
                ),
                "run_id": action.run_id,
                "claim_drafts_proposal_id": drafts.proposal_id,
                "expected_claim_drafts_artifact": {
                    "artifact_id": drafts.artifact_id,
                    "revision": drafts.artifact_revision,
                },
                "expected_store_revision": action.store_revision,
                "expected_ledger_revision": ledger.current_revision,
            },
            strict=True,
        )
        return ClaimFreezeService(self.workspace).freeze(request)

    def _apply_audit_promotion(self, current, action: CoreRunNextAction):
        snapshot = current.verified.snapshot
        proposal = classify_current_lineage(snapshot).current_proposal("audit")
        audit_report = self._artifact(snapshot, "audit_report")
        request = AuditPromotionRequest.model_validate(
            {
                "schema_version": AuditPromotionRequest.schema_id,
                "request_id": derived_id(
                    "REQ-HOST-AUDIT-PROMOTION",
                    action.run_id,
                    action.action_fingerprint,
                ),
                "run_id": action.run_id,
                "audit_proposal_id": proposal.proposal_id,
                "expected_target_artifact": {
                    "artifact_id": proposal.target_artifact_id,
                    "revision": proposal.target_artifact_revision,
                },
                "expected_audit_report_revision": audit_report.current_revision,
                "expected_store_revision": action.store_revision,
            },
            strict=True,
        )
        return ArtifactAcceptanceService(self.workspace).promote_audit_proposal(request)

    def _apply_gate_evaluation(self, current, action: CoreRunNextAction):
        snapshot = current.verified.snapshot
        stage_id = action.stage_id
        if stage_id not in {"auditor", "finalize"}:
            raise RuntimeHostError("runtime_action_not_implemented")
        artifacts = {item.artifact_id: item for item in snapshot.artifacts}

        def reference(artifact_id: str) -> dict[str, object]:
            artifact = artifacts.get(artifact_id)
            if artifact is None or artifact.current_revision < 1:
                raise RuntimeHostError("control_store_integrity_invalid")
            return {
                "artifact_id": artifact.artifact_id,
                "revision": artifact.current_revision,
            }

        if stage_id == "auditor":
            references = [
                reference("claim_ledger"),
                reference("audited_brief"),
            ]
            analyst = artifacts.get("analyst_draft_snapshot")
            if analyst is not None and analyst.current_revision > 0:
                references.append(reference("analyst_draft_snapshot"))
            references.extend(
                [reference("screened_candidates"), reference("candidate_claims")]
            )
        else:
            if len(snapshot.finalize_renders) != 1:
                raise RuntimeHostError("control_store_integrity_invalid")
            render = snapshot.finalize_renders[0]
            references = [
                reference("candidate_claims"),
                reference("screened_candidates"),
                *[
                    {
                        "artifact_id": item.artifact_id,
                        "revision": item.revision,
                    }
                    for item in render.reader_artifacts
                ],
                {
                    "artifact_id": render.audit_report.artifact_id,
                    "revision": render.audit_report.revision,
                },
                reference("claim_ledger"),
            ]
        report = next(
            (
                item
                for item in snapshot.artifacts
                if item.artifact_id == f"{stage_id}_quality_gate_report"
            ),
            None,
        )
        request = GateCheckRequest.model_validate(
            {
                "schema_version": GateCheckRequest.schema_id,
                "request_id": derived_id(
                    "REQ-HOST-GATE",
                    action.run_id,
                    action.action_fingerprint,
                ),
                "run_id": action.run_id,
                "stage_id": stage_id,
                "expected_store_revision": action.store_revision,
                "expected_report_artifact_revision": (
                    0 if report is None else report.current_revision
                ),
                "expected_input_artifacts": references,
            },
            strict=True,
        )
        return GateEvaluationService(self.workspace).evaluate(request)

    def _apply_stage_complete(self, current, action: CoreRunNextAction):
        if action.stage_id is None:
            raise RuntimeHostError("runtime_action_not_implemented")
        service = CoreRunService(self.workspace)
        with SQLiteControlStore.open(self.workspace / "briefloop.db") as store:
            verified = service._verifier.verify(store, action.run_id)
            if verified.snapshot.store_revision != action.store_revision:
                raise RuntimeHostError("runtime_action_stale")
            bindings, gate_ids, _invocation, _tool = service._completion_bindings(
                store,
                verified,
                action.stage_id,
            )
        stage = next(
            (
                item
                for item in current.verified.snapshot.stage_states
                if item.stage_id == action.stage_id
            ),
            None,
        )
        if stage is None:
            raise RuntimeHostError("runtime_action_not_implemented")
        request = StageCompleteRequest.model_validate(
            {
                "schema_version": StageCompleteRequest.schema_id,
                "request_id": derived_id(
                    "REQ-HOST-STAGE-COMPLETE",
                    action.run_id,
                    action.action_fingerprint,
                ),
                "run_id": action.run_id,
                "stage_id": action.stage_id,
                "reason": "verified current Stage effect is complete",
                "expected_stage_revision": stage.revision,
                "expected_store_revision": action.store_revision,
                "expected_artifact_revisions": [
                    {
                        "artifact_id": revision.artifact_id,
                        "revision": revision.revision,
                    }
                    for revision, _usage in bindings
                ],
                "expected_gate_evaluation_ids": list(gate_ids),
            },
            strict=True,
        )
        return service.complete_stage(request)

    def _apply_finalize_render(self, current, action: CoreRunNextAction):
        snapshot = current.verified.snapshot
        with SQLiteControlStore.open(self.workspace / "briefloop.db") as store:
            promotion = classify_current_audit_promotion(
                snapshot,
                store.read_artifact_revision_bytes,
            )
            if promotion is None or not promotion.is_current_lineage:
                raise RuntimeHostError("control_store_integrity_invalid")
            try:
                audited_bytes = store.read_artifact_revision_bytes(
                    action.run_id,
                    promotion.brief_revision.artifact_id,
                    promotion.brief_revision.revision,
                )
            except Exception as exc:
                raise RuntimeHostError("control_store_integrity_invalid") from exc
        try:
            audited = audited_bytes.decode("utf-8")
            reader = remove_src_marker_spans(
                reader_projection_source_markdown(audited)
            ).strip()
        except (UnicodeDecodeError, ReaderProjectionSourceError) as exc:
            raise RuntimeHostError("runtime_deterministic_input_invalid") from exc
        if not reader:
            raise RuntimeHostError("runtime_deterministic_input_invalid")
        reader_bytes = (reader + "\n").encode("utf-8")
        request_id = derived_id(
            "REQ-HOST-FINALIZE-RENDER",
            action.run_id,
            action.action_fingerprint,
        )
        relative = f"scratch/{request_id}/reader_brief.md"
        self._materialize_tool_input(relative, reader_bytes)
        reader_artifact = next(
            (item for item in snapshot.artifacts if item.artifact_id == "reader_brief"),
            None,
        )
        request = FinalizeRenderRequest.model_validate(
            {
                "schema_version": FinalizeRenderRequest.schema_id,
                "request_id": request_id,
                "run_id": action.run_id,
                "audit_proposal_id": promotion.proposal_record.proposal_id,
                "expected_audited_brief": {
                    "artifact_id": promotion.brief_revision.artifact_id,
                    "revision": promotion.brief_revision.revision,
                },
                "expected_audit_report": {
                    "artifact_id": promotion.report_revision.artifact_id,
                    "revision": promotion.report_revision.revision,
                },
                "reader_scratch_inputs": {"reader_brief": relative},
                "expected_reader_sha256": {"reader_brief": sha256_hex(reader_bytes)},
                "expected_reader_revisions": {
                    "reader_brief": (
                        0
                        if reader_artifact is None
                        else reader_artifact.current_revision
                    )
                },
                "expected_store_revision": action.store_revision,
            },
            strict=True,
        )
        return CoreRunTerminalService(self.workspace).accept_finalize_render(request)

    def _apply_finalize_complete(self, current, action: CoreRunNextAction):
        snapshot = current.verified.snapshot
        if len(snapshot.finalize_renders) != 1:
            raise RuntimeHostError("control_store_integrity_invalid")
        render = snapshot.finalize_renders[0]
        stage = next(
            (item for item in snapshot.stage_states if item.stage_id == "finalize"),
            None,
        )
        report = self._artifact(snapshot, "finalize_quality_gate_report")
        evaluations = sorted(
            (
                item
                for item in snapshot.gate_evaluations
                if item.stage_id == "finalize"
                and item.report_artifact.artifact_id == report.artifact_id
                and item.report_artifact.revision == report.current_revision
            ),
            key=lambda item: item.gate_id,
        )
        if stage is None or not evaluations:
            raise RuntimeHostError("control_store_integrity_invalid")
        recovery = classify_recovery_legality(snapshot)
        recovery_id = (
            recovery.recovery_id if recovery.state == "recovered_current" else None
        )
        request = FinalizeCompleteRequest.model_validate(
            {
                "schema_version": FinalizeCompleteRequest.schema_id,
                "request_id": derived_id(
                    "REQ-HOST-FINALIZE-COMPLETE",
                    action.run_id,
                    action.action_fingerprint,
                ),
                "run_id": action.run_id,
                "render_id": render.render_id,
                "expected_finalize_stage_revision": stage.revision,
                "gate_evaluation_ids": [
                    item.evaluation_id
                    for item in sorted(
                        evaluations,
                        key=lambda item: item.evaluation_id,
                    )
                ],
                "recovery_id": recovery_id,
                "expected_store_revision": action.store_revision,
            },
            strict=True,
        )
        return CoreRunTerminalService(self.workspace).complete_finalize(request)

    def _apply_delivery_attempt(self, current, action: CoreRunNextAction):
        snapshot = current.verified.snapshot
        terminal = classify_terminal_legality(snapshot)
        authorization = next(
            (
                item
                for item in snapshot.delivery_authorizations
                if item.authorization_id == terminal.current_authorization_id
            ),
            None,
        )
        if authorization is None or terminal.package_id is None:
            raise RuntimeHostError("control_store_integrity_invalid")
        connector_operation_id = derived_id(
            "DELIVERY-HOST-OPERATION",
            authorization.authorization_id,
            action.action_fingerprint,
        )
        connector_fingerprint = canonical_fingerprint(
            {
                "run_id": action.run_id,
                "package_id": terminal.package_id,
                "authorization_id": authorization.authorization_id,
                "target": authorization.target,
                "channel": authorization.channel,
                "recipient_fingerprint": authorization.recipient_fingerprint,
                "connector_operation_id": connector_operation_id,
            }
        )
        request = DeliveryAttemptRequest.model_validate(
            {
                "schema_version": DeliveryAttemptRequest.schema_id,
                "request_id": derived_id(
                    "REQ-HOST-DELIVERY-ATTEMPT",
                    action.run_id,
                    action.action_fingerprint,
                ),
                "run_id": action.run_id,
                "package_id": terminal.package_id,
                "authorization_id": authorization.authorization_id,
                "connector_operation_id": connector_operation_id,
                "connector_request_fingerprint": connector_fingerprint,
                "expected_store_revision": action.store_revision,
            },
            strict=True,
        )
        return CoreRunTerminalService(self.workspace).record_delivery_attempt(request)

    def _apply_delivery_result(self, current, action: CoreRunNextAction):
        snapshot = current.verified.snapshot
        terminal = classify_terminal_legality(snapshot)
        attempt = next(
            (
                item
                for item in snapshot.delivery_attempts
                if item.attempt_id == terminal.attempt_id_for_current_authorization
            ),
            None,
        )
        if attempt is None or attempt.target != "local":
            raise RuntimeHostError("runtime_delivery_connector_required")
        bundle_manifest = self._materialize_local_delivery_bundle(
            snapshot,
            run_id=action.run_id,
            package_id=attempt.package_id,
        )
        observation = DeliveryResultObservation.model_validate(
            {
                "schema_version": DeliveryResultObservation.schema_id,
                "attempt_id": attempt.attempt_id,
                "adapter_id": current.verified.runtime_adapter.adapter_id,
                "adapter_version": current.verified.runtime_adapter.adapter_version,
                "connector_operation_id": attempt.connector_operation_id,
                "status": "bundle_prepared",
                "evidence_sha256": canonical_fingerprint(
                    {
                        "run_id": action.run_id,
                        "package_id": attempt.package_id,
                        "attempt_id": attempt.attempt_id,
                        "bundle": bundle_manifest,
                    }
                ),
                "diagnostic_code": "bundle_prepared",
                "connector_request_fingerprint": (
                    attempt.connector_request_fingerprint
                ),
            },
            strict=True,
        )
        payload = canonical_json_bytes(
            observation.model_dump(mode="json", exclude_unset=False)
        )
        request_id = derived_id(
            "REQ-HOST-DELIVERY-RESULT",
            action.run_id,
            action.action_fingerprint,
        )
        relative = f"scratch/{request_id}/delivery_result.json"
        self._materialize_tool_input(relative, payload)
        request = DeliveryResultRequest.model_validate(
            {
                "schema_version": DeliveryResultRequest.schema_id,
                "request_id": request_id,
                "run_id": action.run_id,
                "attempt_id": attempt.attempt_id,
                "prior_result_id": terminal.current_result_id,
                "observation_input_path": relative,
                "expected_observation_sha256": sha256_hex(payload),
                "reconciliation_authorization_id": None,
                "expected_store_revision": action.store_revision,
            },
            strict=True,
        )
        return CoreRunTerminalService(self.workspace).record_delivery_result(request)

    def _materialize_local_delivery_bundle(
        self,
        snapshot,
        *,
        run_id: str,
        package_id: str,
    ) -> list[dict[str, object]]:
        bindings = sorted(
            (
                item
                for item in snapshot.package_artifact_bindings
                if item.package_id == package_id and item.usage == "reader"
            ),
            key=lambda item: item.position,
        )
        if not bindings:
            raise RuntimeHostError("control_store_integrity_invalid")
        revisions = {
            (item.artifact_id, item.revision): item
            for item in snapshot.artifact_revisions
        }
        payloads: list[tuple[str, bytes, object]] = []
        names: set[str] = set()
        with SQLiteControlStore.open(self.workspace / "briefloop.db") as store:
            for binding in bindings:
                revision = revisions.get(
                    (binding.artifact_id, binding.artifact_revision)
                )
                if (
                    revision is None
                    or revision.sha256 != binding.artifact_sha256
                    or revision.run_id != run_id
                ):
                    raise RuntimeHostError("control_store_integrity_invalid")
                name = PurePosixPath(revision.path).name
                if not name or name in names or name in {".", ".."}:
                    raise RuntimeHostError("control_store_integrity_invalid")
                names.add(name)
                try:
                    payload = store.read_artifact_revision_bytes(
                        run_id,
                        binding.artifact_id,
                        binding.artifact_revision,
                    )
                except (ControlStoreError, OSError) as exc:
                    raise RuntimeHostError("control_store_integrity_invalid") from exc
                if sha256_hex(payload) != binding.artifact_sha256:
                    raise RuntimeHostError("control_store_integrity_invalid")
                payloads.append((name, payload, binding))
        try:
            manifest: list[dict[str, object]] = []
            for name, payload, binding in payloads:
                materialize_host_bytes(
                    self.workspace,
                    f"output/delivery/{name}",
                    payload,
                    error_code="runtime_delivery_materialization_failed",
                )
                manifest.append(
                    {
                        "artifact_id": binding.artifact_id,
                        "revision": binding.artifact_revision,
                        "path": f"output/delivery/{name}",
                        "sha256": binding.artifact_sha256,
                    }
                )
            attest_host_directory(
                self.workspace,
                "output/delivery",
                expected_members=names,
                error_code="runtime_delivery_materialization_failed",
            )
        except RuntimeHostError:
            raise
        except OSError as exc:
            raise RuntimeHostError("runtime_delivery_materialization_failed") from exc
        return manifest

    def _apply_artifact_supersede(
        self,
        current,
        action: CoreRunNextAction,
        repair_input: RepairContentInput,
    ):
        snapshot = current.verified.snapshot
        legality = classify_recovery_legality(snapshot)
        if legality.state != "active_repair" or legality.repair_id is None:
            raise RuntimeHostError("runtime_action_input_invalid")
        superseded = {
            item.prior_artifact.artifact_id
            for item in snapshot.artifact_supersessions
            if item.repair_id == legality.repair_id
        }
        remaining = set(legality.permitted_artifact_ids) - superseded
        if repair_input.artifact_id not in remaining:
            raise RuntimeHostError("runtime_action_input_invalid")
        artifact = self._artifact(snapshot, repair_input.artifact_id)
        if artifact.current_revision < 1:
            raise RuntimeHostError("control_store_integrity_invalid")
        request = ArtifactSupersedeRequest.model_validate(
            {
                "schema_version": ArtifactSupersedeRequest.schema_id,
                "request_id": derived_id(
                    "REQ-HOST-ARTIFACT-SUPERSEDE",
                    action.run_id,
                    action.action_fingerprint,
                    repair_input.artifact_id,
                ),
                "run_id": action.run_id,
                "repair_id": legality.repair_id,
                "prior_artifact": {
                    "artifact_id": artifact.artifact_id,
                    "revision": artifact.current_revision,
                },
                "input_path": repair_input.input_path,
                "expected_input_sha256": repair_input.expected_input_sha256,
                "expected_current_revision": artifact.current_revision,
                "mode": "repair",
                "reason_code": "frozen_artifact_repaired",
                "expected_store_revision": action.store_revision,
            },
            strict=True,
        )
        return CoreRunRecoveryService(self.workspace).supersede_artifact(request)

    def _replay_artifact_supersede(
        self,
        current,
        action: CoreRunNextAction,
        repair_input: RepairContentInput,
    ):
        """Resolve one committed supersession without reading scratch again."""

        request_id = derived_id(
            "REQ-HOST-ARTIFACT-SUPERSEDE",
            action.run_id,
            action.action_fingerprint,
            repair_input.artifact_id,
        )
        with SQLiteControlStore.open(self.workspace / "briefloop.db") as store:
            receipt = store.load_transaction_receipt(action.run_id, request_id)
        if receipt is None:
            raise RuntimeHostError("runtime_action_stale")
        relations = [
            item
            for item in current.verified.snapshot.artifact_supersessions
            if item.accepted_transaction_id == request_id
        ]
        if len(relations) != 1:
            raise RuntimeHostError("control_store_integrity_invalid")
        relation = relations[0]
        if (
            relation.run_id != action.run_id
            or relation.prior_artifact.artifact_id != repair_input.artifact_id
            or receipt.committed_revision != action.store_revision + 1
        ):
            raise RuntimeHostError("control_store_integrity_invalid")
        request = ArtifactSupersedeRequest.model_validate(
            {
                "schema_version": ArtifactSupersedeRequest.schema_id,
                "request_id": request_id,
                "run_id": action.run_id,
                "repair_id": relation.repair_id,
                "prior_artifact": relation.prior_artifact.model_dump(
                    mode="json",
                    exclude_unset=False,
                ),
                "input_path": repair_input.input_path,
                "expected_input_sha256": repair_input.expected_input_sha256,
                "expected_current_revision": relation.prior_artifact.revision,
                "mode": relation.mode,
                "reason_code": relation.reason_code,
                "expected_store_revision": action.store_revision,
            },
            strict=True,
        )
        result = CoreRunRecoveryService(self.workspace).supersede_artifact(request)
        if result.status != "replayed":
            raise RuntimeHostError(
                result.error_code or "control_store_integrity_invalid"
            )
        return result

    def _apply_repair_start(self, current, action: CoreRunNextAction):
        snapshot = current.verified.snapshot
        legality = classify_recovery_legality(snapshot)
        if (
            legality.state != "blocked"
            or legality.latest_contamination_revision is None
        ):
            raise RuntimeHostError("control_store_integrity_invalid")
        contamination = next(
            (
                item
                for item in snapshot.run_integrity_records
                if item.integrity_revision == legality.latest_contamination_revision
                and item.status == "contaminated"
            ),
            None,
        )
        if contamination is None or contamination.affected_artifact_id is None:
            raise RuntimeHostError("control_store_integrity_invalid")
        owner_stage_id = self._artifact_owner_stage(
            snapshot,
            contamination.affected_artifact_id,
            contamination.affected_artifact_revision,
        )
        request = RepairStartRequest.model_validate(
            {
                "schema_version": RepairStartRequest.schema_id,
                "request_id": derived_id(
                    "REQ-HOST-REPAIR-START",
                    action.run_id,
                    action.action_fingerprint,
                ),
                "run_id": action.run_id,
                "contamination_revision": contamination.integrity_revision,
                "owner_stage_id": owner_stage_id,
                "permitted_artifact_ids": [contamination.affected_artifact_id],
                "reason_code": contamination.reason_code,
                "expected_store_revision": action.store_revision,
            },
            strict=True,
        )
        return CoreRunRecoveryService(self.workspace).start_repair(request)

    def _apply_repair_complete(self, current, action: CoreRunNextAction):
        snapshot = current.verified.snapshot
        legality = classify_recovery_legality(snapshot)
        if legality.state != "active_repair" or legality.repair_id is None:
            raise RuntimeHostError("control_store_integrity_invalid")
        supersessions = sorted(
            (
                item
                for item in snapshot.artifact_supersessions
                if item.repair_id == legality.repair_id
            ),
            key=lambda item: item.supersession_id,
        )
        owner_stages = sorted(
            {
                submission.owner_stage_id
                for relation in supersessions
                for submission in snapshot.owned_artifact_submissions
                if submission.artifact_id == relation.successor_artifact.artifact_id
                and submission.artifact_revision == relation.successor_artifact.revision
            }
        )
        stages = {item.stage_id: item for item in snapshot.stage_states}
        if not supersessions or any(stage not in stages for stage in owner_stages):
            raise RuntimeHostError("control_store_integrity_invalid")
        request = RepairCompleteRequest.model_validate(
            {
                "schema_version": RepairCompleteRequest.schema_id,
                "request_id": derived_id(
                    "REQ-HOST-REPAIR-COMPLETE",
                    action.run_id,
                    action.action_fingerprint,
                ),
                "run_id": action.run_id,
                "repair_id": legality.repair_id,
                "supersession_ids": [item.supersession_id for item in supersessions],
                "expected_stage_revisions": {
                    stage_id: stages[stage_id].revision for stage_id in owner_stages
                },
                "expected_store_revision": action.store_revision,
            },
            strict=True,
        )
        return CoreRunRecoveryService(self.workspace).complete_repair(request)

    def _apply_recovery_complete(self, current, action: CoreRunNextAction):
        legality = classify_recovery_legality(current.verified.snapshot)
        if (
            legality.state != "rerun_required"
            or legality.repair_completion_id is None
            or legality.latest_contamination_revision is None
            or not legality.required_rerun_transition_ids
        ):
            raise RuntimeHostError("control_store_integrity_invalid")
        request = RecoveryCompleteRequest.model_validate(
            {
                "schema_version": RecoveryCompleteRequest.schema_id,
                "request_id": derived_id(
                    "REQ-HOST-RECOVERY-COMPLETE",
                    action.run_id,
                    action.action_fingerprint,
                ),
                "run_id": action.run_id,
                "repair_completion_id": legality.repair_completion_id,
                "contamination_revision": legality.latest_contamination_revision,
                "rerun_transition_ids": list(legality.required_rerun_transition_ids),
                "gate_evaluation_ids": list(legality.required_gate_evaluation_ids),
                "expected_store_revision": action.store_revision,
            },
            strict=True,
        )
        return CoreRunRecoveryService(self.workspace).complete_recovery(request)

    @staticmethod
    def _artifact_owner_stage(
        snapshot,
        artifact_id: str,
        revision: int | None,
    ) -> str:
        policy = ARTIFACT_POLICIES.get(artifact_id)
        if policy is not None:
            return policy.owner_stage_id
        proposal = next(
            (
                item
                for item in snapshot.accepted_proposals
                if item.artifact_id == artifact_id
                and item.artifact_revision == revision
            ),
            None,
        )
        stages = {
            "candidate": "scout",
            "screened": "screener",
            "claim_drafts": "claim-ledger",
            "audit": "auditor",
        }
        if proposal is not None and proposal.proposal_kind in stages:
            return stages[proposal.proposal_kind]
        submission = next(
            (
                item
                for item in snapshot.owned_artifact_submissions
                if item.artifact_id == artifact_id
                and item.artifact_revision == revision
            ),
            None,
        )
        if submission is not None:
            return submission.owner_stage_id
        raise RuntimeHostError("control_store_integrity_invalid")

    @staticmethod
    def _artifact(snapshot, artifact_id: str):
        artifact = next(
            (item for item in snapshot.artifacts if item.artifact_id == artifact_id),
            None,
        )
        if artifact is None:
            raise RuntimeHostError("control_store_integrity_invalid")
        return artifact

    def _materialize_tool_input(self, relative: str, payload: bytes) -> Path:
        return materialize_host_bytes(
            self.workspace,
            relative,
            payload,
            error_code="runtime_deterministic_input_invalid",
        )


def _frozen_manifest_entries(
    payload: bytes,
    expected_schema_version: str,
) -> list[FrozenSourceManifestEntry]:
    try:
        manifest = parse_json_object(payload)
        if manifest.get("schema_version") != expected_schema_version:
            raise ValueError
        raw = manifest.get("sources")
        if not isinstance(raw, list) or not 1 <= len(raw) <= 256:
            raise ValueError
        entries = [
            FrozenSourceManifestEntry.model_validate(item, strict=True) for item in raw
        ]
        source_ids = [item.source_id for item in entries]
        if source_ids != sorted(set(source_ids)):
            raise ValueError
        return entries
    except (IntakeError, ValidationError, TypeError, ValueError) as exc:
        raise RuntimeHostError("runtime_human_request_invalid") from exc


__all__ = ["InvocationDispatch", "RuntimeHostService"]
