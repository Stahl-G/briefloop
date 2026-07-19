"""Thin active host over verified CoreRun services."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

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
    IntegrityCheckRequest,
    InternalApprovalRequest,
    InvocationFailureRequest,
    InvocationStartRequest,
    OwnedArtifactSubmitRequest,
    RuntimeAdapterBinding,
    ScreenedCandidatesProposal,
    SourceCommitRequest,
    SourceProposal,
    StageCompleteRequest,
    StrictModel,
)
from multi_agent_brief.control_store import SQLiteControlStore
from multi_agent_brief.control_store.serialization import canonical_json_bytes
from multi_agent_brief.core_run_v2.artifacts import (
    ArtifactAcceptanceService,
    _input_classification_bytes,
)
from multi_agent_brief.core_run_v2.claims import ClaimFreezeService
from multi_agent_brief.core_run_v2.gates import GateEvaluationService
from multi_agent_brief.core_run_v2.lineage import classify_current_lineage
from multi_agent_brief.core_run_v2.policy import (
    core_role_topology_policy,
    derived_id,
)
from multi_agent_brief.core_run_v2.service import CoreRunService
from multi_agent_brief.core_run_v2.terminal import CoreRunTerminalService
from multi_agent_brief.intake_v2.errors import IntakeError
from multi_agent_brief.intake_v2.scratch import parse_json_object
from multi_agent_brief.intake_v2.service import IntakeService
from multi_agent_brief.sources.search_backends.base import SearchBackendError

from .contracts import (
    RoleTaskEnvelope,
    RuntimeDiagnoseReport,
    RuntimeInvocationResult,
)
from .errors import RuntimeHostError
from .initialization import AdapterLoader, initialize_or_open_runtime
from .scratch import (
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
        action = current.action
        if expected_action is not None and expected_action != action:
            raise RuntimeHostError("runtime_action_stale")
        if action.action_kind == "delegate":
            role_id = action.role_id
        elif (
            action.action_kind == "deterministic"
            and action.effect_kind == "source_acquire"
        ):
            role_id = "source-provider"
        else:
            raise RuntimeHostError("runtime_action_not_invocable")
        if role_id is None or action.stage_id is None:
            raise RuntimeHostError("runtime_action_not_invocable")
        request = InvocationStartRequest.model_validate(
            {
                "schema_version": InvocationStartRequest.schema_id,
                "request_id": derived_id(
                    "REQ-HOST-INVOKE",
                    action.run_id,
                    action.action_fingerprint,
                ),
                "run_id": action.run_id,
                "stage_id": action.stage_id,
                "role_id": role_id,
                "runtime": current.verified.snapshot.run.runtime,
                "expected_store_revision": action.store_revision,
            },
            strict=True,
        )
        result = CoreRunService(self.workspace).start_invocation(request)
        if result.status not in {"committed", "replayed"}:
            raise RuntimeHostError(
                result.error_code or "control_store_integrity_invalid"
            )
        if result.primary_record_id is None:
            raise RuntimeHostError("control_store_integrity_invalid")
        invocation_id = result.primary_record_id
        output = _ROLE_OUTPUTS[role_id]
        topology = core_role_topology_policy(
            current.verified.binding.role_topology
        )
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
                "task_instructions": (
                    f"Complete only the frozen {role_id} role task in this "
                    "recorded invocation."
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
        topology = core_role_topology_policy(
            current.verified.binding.role_topology
        )
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
            proposal_name = (
                "source_proposal.json"
                if spec.owner_kind == "source"
                else spec.filenames[0]
            )
            try:
                parsed = parse_json_object(outputs[proposal_name])
                proposal = spec.proposal_model.model_validate(parsed, strict=True)
            except (IntakeError, ValidationError, ValueError) as exc:
                raise RuntimeHostError("runtime_proposal_invalid") from exc
            if getattr(proposal, "run_id", None) != envelope.run_id:
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
    ):
        current = initialize_or_open_runtime(
            self.workspace,
            adapter_loader=self._adapter_loader,
        )
        action = current.action
        if expected_action is not None and expected_action != action:
            raise RuntimeHostError("runtime_action_stale")
        if action.action_kind == "human_decision":
            return self._apply_human_decision(action, human_request)
        if action.action_kind != "deterministic":
            raise RuntimeHostError("runtime_action_not_deterministic")
        if human_request is not None:
            raise RuntimeHostError("runtime_human_request_invalid")
        if action.effect_kind == "doctor_check":
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
        elif action.effect_kind == "gate_evaluation":
            result = self._apply_gate_evaluation(current, action)
        elif action.effect_kind == "stage_complete":
            result = self._apply_stage_complete(current, action)
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
        return result

    def _apply_human_decision(
        self,
        action: CoreRunNextAction,
        request: StrictModel | None,
    ):
        if request is None or request.schema_id != action.request_schema_id:
            raise RuntimeHostError("runtime_human_request_required")
        request_run_id = getattr(request, "run_id", None)
        expected_revision = getattr(request, "expected_store_revision", None)
        if request_run_id != action.run_id or expected_revision != action.store_revision:
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
        else:
            raise RuntimeHostError("runtime_human_request_invalid")
        if result.status not in {"committed", "replayed"}:
            raise RuntimeHostError(
                result.error_code or "control_store_integrity_invalid"
            )
        return result

    def _apply_source_acquire(self, current, action: CoreRunNextAction):
        from .source_routes import collect_frozen_source

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
            material = collect_frozen_source(
                self.workspace,
                run_id=action.run_id,
                invocation_id=invocation_id,
                route=route,
            )
            scratch = f"scratch/{invocation_id}"
            self._materialize_tool_input(
                f"{scratch}/source_content.bin",
                material.content,
            )
            self._materialize_tool_input(
                f"{scratch}/source_raw.json",
                material.raw_payload,
            )
            self._materialize_tool_input(
                f"{scratch}/source_proposal.json",
                canonical_json_bytes(
                    material.proposal.model_dump(mode="json", exclude_unset=False)
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
        return self.accept_invocation(invocation_id)

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
        allowed = (
            {
                "candidate_claims",
                "screened_candidates",
                "claim_ledger",
                "analyst_draft_snapshot",
                "audited_brief",
            }
            if stage_id == "auditor"
            else {"audit_report", "reader_brief", "reader_brief_docx"}
        )
        references = sorted(
            (
                {"artifact_id": item.artifact_id, "revision": item.current_revision}
                for item in snapshot.artifacts
                if item.artifact_id in allowed and item.current_revision > 0
            ),
            key=lambda item: str(item["artifact_id"]),
        )
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
        path = self.workspace / relative
        try:
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            descriptor = path.open("xb")
        except FileExistsError:
            if path.is_symlink() or path.read_bytes() != payload:
                raise RuntimeHostError("runtime_deterministic_input_invalid")
            return path
        except OSError as exc:
            raise RuntimeHostError("runtime_deterministic_input_invalid") from exc
        with descriptor:
            descriptor.write(payload)
            descriptor.flush()
        return path


__all__ = ["InvocationDispatch", "RuntimeHostService"]
