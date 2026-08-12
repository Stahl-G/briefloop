"""Thin active host over verified CoreRun services."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, replace
import errno
import json
import os
from pathlib import Path
from pathlib import PurePosixPath
import stat
from threading import Lock
import time
from typing import Iterator, Literal

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
    ExecutionSourceManifest,
    ExecutionSourceManifestMember,
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
    RunTerminationRequest,
    RuntimeAdapterBinding,
    RuntimeSourceRouteBinding,
    RuntimeWebSearchAcquisitionSpecV3,
    RunDirection,
    RunSuccessorStartRequest,
    ScreenedCandidatesProposal,
    SourceCommitRequest,
    SourceAcquisitionAttemptAuthorizeRequest,
    SourcePackCommitRequest,
    SourceProposal,
    StageCompleteRequest,
    StrictModel,
)
from multi_agent_brief.control_store import (
    ControlStoreCommitOutcomeUnknown,
    ControlStoreError,
    SQLiteControlStore,
)
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
from multi_agent_brief.core_run_v2.errors import CoreRunError
from multi_agent_brief.core_run_v2.gates import GateEvaluationService
from multi_agent_brief.core_run_v2.gate_repair import (
    GateRepairService,
    active_gate_repair_context,
)
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
from multi_agent_brief.core_run_v2.publication_platform import capability_profile
from multi_agent_brief.core_run_v2.service import CoreRunService
from multi_agent_brief.core_run_v2.service import workspace_input_fingerprints
from multi_agent_brief.core_run_v2.successor import CoreRunSuccessorService
from multi_agent_brief.core_run_v2.recovery import (
    CoreRunRecoveryService,
    classify_recovery_legality,
)
from multi_agent_brief.core_run_v2.terminal import (
    CoreRunTerminalService,
    classify_terminal_legality,
)
from multi_agent_brief.core_run_v2.tavily_source_binding import (
    expected_tavily_source_pack,
)
from multi_agent_brief.core_run_v2.verifier import CoreRunDomainVerifier
from multi_agent_brief.core.citations import remove_src_marker_spans
from multi_agent_brief.core.env import WorkspaceEnvError, known_env_key_is_set
from multi_agent_brief.intake_v2.errors import IntakeError
from multi_agent_brief.intake_v2.scratch import ScratchReader, parse_json_object
from multi_agent_brief.intake_v2.service import (
    _CoreDiscoveryFailureAttempt,
    IntakeService,
    _CoreDiscoverySourcePack,
    _SourcePackBytes,
    _SourcePackMemberBytes,
)
from multi_agent_brief.sources.search_backends.base import SearchBackendError
from multi_agent_brief.sources.tavily_acquisition import (
    TavilyAcquisitionBundleError,
    TavilyAcquisitionObservation,
    TavilyMultiAcquisitionObservation,
    parse_tavily_acquisition_bundle,
    tavily_observation_matches_spec,
)
from multi_agent_brief.outputs.reader_projection import (
    ReaderProjectionSourceError,
    reader_projection_source_markdown,
)

from .contracts import (
    FrozenGuidanceContext,
    FrozenGuidanceItem,
    FrozenSourceManifestEntry,
    GateRepairStartRequest,
    HumanSourceMaterialRequest,
    HumanSourcePackMember,
    HumanSourcePackRequest,
    RuntimeSourceAcquisitionRecoveryRequest,
    LocalPresentationResult,
    RepairContentInput,
    RoleTaskEnvelope,
    RuntimeDiagnoseReport,
    RuntimeContinuationResult,
    RuntimeInvocationResult,
    RuntimeProposalValidationResult,
)
from .errors import RuntimeHostError
from .initialization import AdapterLoader, initialize_or_open_runtime
from .projections import build_runtime_continuation_result
from .scratch import (
    attest_host_directory,
    materialize_host_bytes,
    materialize_host_request,
    materialize_role_envelope,
    read_role_envelope,
    read_role_outputs,
    verify_optional_host_request,
)
from .submission import (
    HumanSourceStageInput,
    SourceStageBytesInput,
    VerifiedSourceStage,
    discard_source_stage,
    load_source_stage,
    stage_human_source_pack,
    stage_source_pack_bytes,
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


@dataclass(frozen=True)
class _VerifiedSourcePackSubmission:
    """One immutable Host verification result for materialization and Intake."""

    request: SourcePackCommitRequest
    pack: _SourcePackBytes


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
    base = f"Complete only the frozen {role_id} role task in this recorded invocation."
    if output.proposal_model is None:
        return base
    proposal_filename = (
        "source_proposal.json" if output.owner_kind == "source" else output.filenames[0]
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


def _target_relevance_task_instruction(
    role_id: str,
    target_terms: list[str] | tuple[str, ...],
    *,
    gate_repair: bool = False,
) -> str:
    """Bind frozen target framing to the role task without turning it into evidence."""

    serialized_terms = json.dumps(list(target_terms), ensure_ascii=False)
    if role_id == "analyst":
        return (
            " The Analyst first draft must include an executive summary that "
            "preserves every frozen RunDirection target term verbatim: "
            f"target_terms={serialized_terms}. These terms are RunDirection "
            "framing only, not evidence; do not invent supporting facts or "
            "citations solely to satisfy target visibility."
        )
    if role_id == "editor" and gate_repair:
        return (
            " This target-relevance Gate repair requires the executive summary "
            "to preserve every frozen RunDirection target term verbatim: "
            f"target_terms={serialized_terms}. This is RunDirection framing "
            "only, not new evidence; other than preserving these configured "
            "terms, do not add facts, claims, numbers, named entities, dates, "
            "causal claims, or citations."
        )
    return ""


def _strict_proposal_violations(
    output: _RoleOutputSpec,
    outputs: dict[str, bytes],
    *,
    expected_run_id: str,
) -> list[FieldViolation]:
    if output.proposal_model is None:
        return []
    proposal_name = (
        "source_proposal.json" if output.owner_kind == "source" else output.filenames[0]
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
    if output.owner_kind != "source" or not isinstance(proposal, SourceProposal):
        return []
    violations: list[FieldViolation] = []
    content = outputs.get("source_content.bin")
    raw_payload = outputs.get("source_raw.json")
    if content is None or proposal.content_sha256 != sha256_hex(content):
        violations.append(
            FieldViolation(
                field="content_sha256",
                error="must match source_content.bin",
            )
        )
    if raw_payload is None or proposal.raw_payload_sha256 != sha256_hex(raw_payload):
        violations.append(
            FieldViolation(
                field="raw_payload_sha256",
                error="must match source_raw.json",
            )
        )
    return violations


@dataclass(frozen=True)
class InvocationDispatch:
    envelope: RoleTaskEnvelope
    envelope_path: Path


_SOURCE_ACQUISITION_LOCKS_GUARD = Lock()
_SOURCE_ACQUISITION_LOCKS: dict[str, Lock] = {}
_SOURCE_ACQUISITION_LOCK_NAME = ".briefloop-source-acquisition.lock"


def _source_acquisition_thread_lock(workspace: Path) -> Lock:
    key = str(workspace)
    with _SOURCE_ACQUISITION_LOCKS_GUARD:
        lock = _SOURCE_ACQUISITION_LOCKS.get(key)
        if lock is None:
            lock = Lock()
            _SOURCE_ACQUISITION_LOCKS[key] = lock
        return lock


def _validate_source_acquisition_lock_file(path: Path, fd: int) -> None:
    try:
        opened = os.fstat(fd)
        lexical = path.lstat()
    except OSError:
        raise RuntimeHostError("source_acquisition_outcome_unknown") from None
    if (
        not stat.S_ISREG(opened.st_mode)
        or not stat.S_ISREG(lexical.st_mode)
        or opened.st_nlink != 1
        or lexical.st_nlink != 1
        or (opened.st_dev, opened.st_ino) != (lexical.st_dev, lexical.st_ino)
    ):
        raise RuntimeHostError("source_acquisition_outcome_unknown")


def _lock_source_acquisition_file(fd: int) -> None:
    os.lseek(fd, 0, os.SEEK_SET)
    if os.name == "nt":
        import msvcrt

        while True:
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                return
            except OSError as exc:
                if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                    raise
                time.sleep(0.05)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_EX)


def _unlock_source_acquisition_file(fd: int) -> None:
    os.lseek(fd, 0, os.SEEK_SET)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_UN)


@contextmanager
def _exclusive_source_acquisition(workspace: Path) -> Iterator[None]:
    """Serialize one workspace provider lifecycle without becoming authority."""

    thread_lock = _source_acquisition_thread_lock(workspace)
    with thread_lock:
        lock_path = workspace / _SOURCE_ACQUISITION_LOCK_NAME
        flags = os.O_CREAT | os.O_RDWR
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            fd = os.open(lock_path, flags, 0o600)
        except OSError:
            raise RuntimeHostError("source_acquisition_outcome_unknown") from None
        locked = False
        try:
            _validate_source_acquisition_lock_file(lock_path, fd)
            if os.fstat(fd).st_size == 0:
                os.write(fd, b"\0")
                os.fsync(fd)
            try:
                _lock_source_acquisition_file(fd)
            except OSError:
                raise RuntimeHostError("source_acquisition_outcome_unknown") from None
            locked = True
            _validate_source_acquisition_lock_file(lock_path, fd)
            yield
        finally:
            if locked:
                try:
                    _unlock_source_acquisition_file(fd)
                except OSError:
                    pass
            os.close(fd)


@dataclass(frozen=True)
class _VerifiedRoleSubmission:
    envelope: RoleTaskEnvelope
    spec: _RoleOutputSpec
    outputs: dict[str, bytes]
    violations: tuple[FieldViolation, ...]
    acceptance_request: (
        SourceCommitRequest | ArtifactSubmitRequest | OwnedArtifactSubmitRequest | None
    )
    acceptance_lane: str | None
    acceptance_payload: bytes | None


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

    def _observe_post_final_assessment(self) -> None:
        """Best-effort post-commit advisory observer; never affects Core truth."""

        try:
            from multi_agent_brief.product.post_final_assessment import (
                PostFinalAssessmentService,
            )

            PostFinalAssessmentService(self.workspace).observe_finalized_local()
        except Exception:
            # The observer intentionally cannot affect finalization, the
            # continuation result, Store authority, or the next action.
            return

    def continue_authorized(
        self,
        *,
        maximum_progress_attempts: int = 32,
    ) -> RuntimeContinuationResult:
        """Advance only one M2-authorized run through existing typed effects."""

        if maximum_progress_attempts < 1:
            raise RuntimeHostError("runtime_progress_limit_invalid")
        transaction_ids: list[str] = []
        attempts = 0
        while True:
            current = initialize_or_open_runtime(
                self.workspace,
                adapter_loader=self._adapter_loader,
            )
            action = current.action
            execution_authorizations = (
                current.verified.snapshot.run_execution_authorizations
            )
            discovery_authorizations = (
                current.verified.snapshot.run_source_discovery_authorizations
            )
            if (
                len(execution_authorizations) != 1
                and len(discovery_authorizations) != 1
            ):
                return build_runtime_continuation_result(
                    current.verified,
                    action,
                    status="needs_human",
                    reason_code="runtime_continuation_unsupported",
                    transaction_ids=tuple(transaction_ids),
                )
            prepromotion = (
                not execution_authorizations
                and len(discovery_authorizations) == 1
                and not any(
                    item.claims_eligible for item in current.verified.snapshot.sources
                )
            )
            if prepromotion and not self._prepromotion_action_allowed(action):
                return build_runtime_continuation_result(
                    current.verified,
                    action,
                    status="needs_attention",
                    reason_code="control_store_integrity_invalid",
                    transaction_ids=tuple(transaction_ids),
                )
            if action.action_kind == "complete":
                if action.effect_kind == "run_terminated":
                    return build_runtime_continuation_result(
                        current.verified,
                        action,
                        status="terminated",
                        reason_code=action.reason_code,
                        transaction_ids=tuple(transaction_ids),
                    )
                if (
                    action.effect_kind != "finalized_local"
                    or action.reason_code != "local_finalization_complete"
                ):
                    return build_runtime_continuation_result(
                        current.verified,
                        action,
                        status="needs_attention",
                        reason_code="terminal_state_incomplete",
                        transaction_ids=tuple(transaction_ids),
                    )
                self._observe_post_final_assessment()
                from multi_agent_brief.product.brief_html import (
                    maybe_auto_open_brief_pages,
                )

                presentation_payload = maybe_auto_open_brief_pages(self.workspace)
                presentation = LocalPresentationResult.model_validate(
                    presentation_payload or {"status": "not_requested"},
                    strict=True,
                )
                return build_runtime_continuation_result(
                    current.verified,
                    action,
                    status="finalized_local",
                    reason_code=action.reason_code,
                    transaction_ids=tuple(transaction_ids),
                    presentation=presentation,
                )
            if action.action_kind == "human_decision":
                return build_runtime_continuation_result(
                    current.verified,
                    action,
                    status="needs_human",
                    reason_code=action.reason_code,
                    transaction_ids=tuple(transaction_ids),
                )
            if action.action_kind == "blocked":
                return build_runtime_continuation_result(
                    current.verified,
                    action,
                    status="needs_attention",
                    reason_code=action.reason_code,
                    transaction_ids=tuple(transaction_ids),
                )
            if action.action_kind == "delegate":
                try:
                    dispatch = self.start_current_invocation(action)
                except RuntimeHostError as exc:
                    code = str(exc)
                    if code == "runtime_action_stale":
                        continue
                    if code == "commit_outcome_unknown":
                        refreshed = initialize_or_open_runtime(
                            self.workspace,
                            adapter_loader=self._adapter_loader,
                        )
                        return build_runtime_continuation_result(
                            refreshed.verified,
                            refreshed.action,
                            status="needs_attention",
                            reason_code="commit_outcome_unknown",
                            transaction_ids=tuple(transaction_ids),
                        )
                    raise
                refreshed = initialize_or_open_runtime(
                    self.workspace,
                    adapter_loader=self._adapter_loader,
                )
                return build_runtime_continuation_result(
                    refreshed.verified,
                    refreshed.action,
                    status="role_work_required",
                    reason_code="role_work_required",
                    envelope_path=dispatch.envelope_path.relative_to(
                        self.workspace
                    ).as_posix(),
                    transaction_ids=tuple(transaction_ids),
                )
            if action.action_kind != "deterministic":
                return build_runtime_continuation_result(
                    current.verified,
                    action,
                    status="needs_attention",
                    reason_code=action.reason_code,
                    transaction_ids=tuple(transaction_ids),
                )
            if action.effect_kind in {
                "repair_start",
                "repair_complete",
                "recovery_complete",
                "delivery_attempt",
                "delivery_result",
            }:
                return build_runtime_continuation_result(
                    current.verified,
                    action,
                    status="needs_attention",
                    reason_code=action.reason_code,
                    transaction_ids=tuple(transaction_ids),
                )
            if attempts >= maximum_progress_attempts:
                return build_runtime_continuation_result(
                    current.verified,
                    action,
                    status="needs_attention",
                    reason_code="runtime_progress_stalled",
                    transaction_ids=tuple(transaction_ids),
                )
            attempts += 1
            if action.effect_kind == "invocation_accept_or_fail":
                active = [
                    item
                    for item in current.verified.snapshot.invocations
                    if item.status == "active"
                ]
                if len(active) != 1:
                    raise RuntimeHostError("control_store_integrity_invalid")
                envelope = self._expected_invocation_envelope(
                    active[0].invocation_id,
                    current=current,
                )
                validation = self.validate_invocation(
                    envelope.invocation_id,
                    expected_envelope=envelope,
                )
                if validation.status != "valid":
                    status = (
                        "role_work_required"
                        if validation.reason_code == "runtime_proposal_missing"
                        else "proposal_invalid"
                    )
                    return build_runtime_continuation_result(
                        current.verified,
                        action,
                        status=status,
                        reason_code=(
                            "role_work_required"
                            if status == "role_work_required"
                            else "runtime_proposal_invalid"
                        ),
                        envelope_path=(
                            Path(envelope.scratch_directory) / "role_task_envelope.json"
                        ).as_posix(),
                        transaction_ids=tuple(transaction_ids),
                        violations=tuple(
                            item.model_dump(mode="json", exclude_unset=False)
                            for item in validation.violations
                        ),
                    )
                try:
                    result = self.accept_invocation(
                        envelope.invocation_id,
                        expected_envelope=envelope,
                    )
                except RuntimeHostError as exc:
                    code = str(exc)
                    if code == "runtime_action_stale":
                        attempts -= 1
                        continue
                    if code == "commit_outcome_unknown":
                        refreshed = initialize_or_open_runtime(
                            self.workspace,
                            adapter_loader=self._adapter_loader,
                        )
                        return build_runtime_continuation_result(
                            refreshed.verified,
                            refreshed.action,
                            status="needs_attention",
                            reason_code="commit_outcome_unknown",
                            transaction_ids=tuple(transaction_ids),
                        )
                    if code == "checkout_publication_unsupported":
                        refreshed = initialize_or_open_runtime(
                            self.workspace,
                            adapter_loader=self._adapter_loader,
                        )
                        return build_runtime_continuation_result(
                            refreshed.verified,
                            refreshed.action,
                            status="needs_attention",
                            reason_code=code,
                            transaction_ids=tuple(transaction_ids),
                        )
                    raise
                transaction_ids.append(result.transaction_id)
                continue
            try:
                result = self.apply_current(action, presentation_hook=False)
            except RuntimeHostError as exc:
                code = str(exc)
                if code == "runtime_action_stale":
                    attempts -= 1
                    continue
                if code == "commit_outcome_unknown":
                    refreshed = initialize_or_open_runtime(
                        self.workspace,
                        adapter_loader=self._adapter_loader,
                    )
                    if refreshed.action != action:
                        continue
                    try:
                        result = self.apply_current(action, presentation_hook=False)
                    except RuntimeHostError as retry_exc:
                        if str(retry_exc) != "commit_outcome_unknown":
                            raise
                        return build_runtime_continuation_result(
                            refreshed.verified,
                            refreshed.action,
                            status="needs_attention",
                            reason_code="commit_outcome_unknown",
                            transaction_ids=tuple(transaction_ids),
                        )
                elif code in {
                    "checkout_publication_unsupported",
                    "runtime_adapter_binding_mismatch",
                    "control_store_integrity_invalid",
                    "source_provider_secret_unavailable",
                    "source_discovery_authorization_invalid",
                    "source_provider_unavailable",
                    "source_provider_result_invalid",
                    "source_pack_empty",
                    "source_acquisition_outcome_unknown",
                    "workspace_secret_unsafe",
                }:
                    refreshed = initialize_or_open_runtime(
                        self.workspace,
                        adapter_loader=self._adapter_loader,
                    )
                    return build_runtime_continuation_result(
                        refreshed.verified,
                        refreshed.action,
                        status="needs_attention",
                        reason_code=code,
                        transaction_ids=tuple(transaction_ids),
                    )
                else:
                    raise
            receipt = getattr(result, "receipt", None)
            transaction_id = getattr(result, "transaction_id", None)
            if receipt is not None:
                transaction_id = receipt.transaction_id
            if isinstance(transaction_id, str):
                transaction_ids.append(transaction_id)

    @staticmethod
    def _prepromotion_action_allowed(action: CoreRunNextAction) -> bool:
        if action.action_kind == "human_decision":
            return action.stage_id == "source-discovery" and (
                (
                    action.effect_kind == "source_input_required"
                    and action.reason_code == "human_source_material_required"
                    and action.request_schema_id
                    == "briefloop.runtime_human_source_pack_request.v2"
                    and action.source_route_id is None
                    and action.source_provider_id is None
                )
                or (
                    action.effect_kind == "source_acquisition_recovery"
                    and action.reason_code
                    == "source_acquisition_recovery_decision_required"
                    and action.request_schema_id
                    == "briefloop.runtime_source_acquisition_recovery_request.v1"
                    and action.source_route_id is None
                    and action.source_provider_id is None
                )
            )
        if action.action_kind == "deterministic":
            return (
                (action.effect_kind == "doctor_check" and action.stage_id == "doctor")
                or (
                    action.effect_kind == "stage_complete"
                    and action.stage_id in {"doctor", "source-discovery"}
                )
                or (
                    action.effect_kind == "source_acquire"
                    and action.stage_id == "source-discovery"
                    and action.source_route_id == "web-search"
                    and action.source_provider_id == "tavily"
                )
                or (
                    action.effect_kind == "authorized_source_pack_commit"
                    and action.stage_id == "source-discovery"
                    and action.source_route_id is None
                    and action.source_provider_id is None
                )
                or (
                    action.effect_kind == "invocation_accept_or_fail"
                    and action.stage_id == "source-discovery"
                    and action.role_id in {None, "source-planner"}
                )
            )
        return (
            action.action_kind == "delegate"
            and action.effect_kind == "role_proposal"
            and action.stage_id == "source-discovery"
            and action.role_id == "source-planner"
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
        if (
            action.action_kind == "deterministic"
            and action.effect_kind == "source_acquire"
        ):
            raise RuntimeHostError("runtime_action_not_invocable")
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

    def start_successor(
        self,
        *,
        successor_run_id: str,
        run_direction: RunDirection,
        include_approved_guidance: bool,
    ):
        """Start one normal reference successor from the exact finalized head."""

        current = initialize_or_open_runtime(
            self.workspace,
            adapter_loader=self._adapter_loader,
        )
        replay_request = self._existing_successor_request(
            successor_run_id=successor_run_id,
            run_direction=run_direction,
            include_approved_guidance=include_approved_guidance,
        )
        if replay_request is not None:
            return self._apply_successor_request(replay_request)
        action = current.action
        if (
            action.action_kind != "complete"
            or action.effect_kind != "finalized_local"
            or action.reason_code != "local_finalization_complete"
        ):
            raise RuntimeHostError("successor_run_not_ready")
        snapshot = current.verified.snapshot
        head = snapshot.workspace_run_head
        if head is None or head.current_run_id != snapshot.run.run_id:
            raise RuntimeHostError("control_store_integrity_invalid")
        try:
            workspace_sha, sources_sha = workspace_input_fingerprints(self.workspace)
            request_id = derived_id(
                "REQ-HOST-SUCCESSOR",
                snapshot.workspace_id,
                snapshot.run.run_id,
                successor_run_id,
            )
            payload = {
                "schema_version": RunSuccessorStartRequest.schema_id,
                "request_id": request_id,
                "predecessor_run_id": snapshot.run.run_id,
                "successor_run_id": successor_run_id,
                "workspace_id": snapshot.workspace_id,
                "runtime": snapshot.run.runtime,
                "expected_head_run_id": head.current_run_id,
                "expected_store_revision": snapshot.store_revision,
                "expected_workspace_revision": snapshot.store_revision,
                "run_direction": run_direction.model_dump(mode="json"),
                "workspace_config_sha256": workspace_sha,
                "sources_config_sha256": sources_sha,
                "role_topology": current.verified.binding.role_topology,
                "gate_strictness": current.verified.binding.gate_strictness,
                "input_governance_required": (
                    current.verified.binding.input_governance_required
                ),
                "include_approved_guidance": include_approved_guidance,
            }
            payload["request_fingerprint"] = canonical_fingerprint(payload)
            request = RunSuccessorStartRequest.model_validate(payload, strict=True)
        except (CoreRunError, ValidationError, ValueError) as exc:
            raise RuntimeHostError("runtime_successor_request_invalid") from exc
        return self._apply_successor_request(request)

    def _apply_successor_request(self, request: RunSuccessorStartRequest):
        result = CoreRunSuccessorService(self.workspace).start_successor(request)
        if result.status == "commit_outcome_unknown":
            result = CoreRunSuccessorService(self.workspace).start_successor(request)
        if result.status not in {"committed", "replayed"}:
            raise RuntimeHostError(
                result.error_code or "control_store_integrity_invalid"
            )
        return result

    def _existing_successor_request(
        self,
        *,
        successor_run_id: str,
        run_direction: RunDirection,
        include_approved_guidance: bool,
    ) -> RunSuccessorStartRequest | None:
        """Reconstruct one frozen request so the public command can replay exactly."""

        try:
            with SQLiteControlStore.open(self.workspace / "briefloop.db") as store:
                history = store.load_history()
            matches = [
                item
                for item in history.snapshots
                if item.run.run_id == successor_run_id
            ]
            if not matches:
                return None
            if len(matches) != 1:
                raise RuntimeHostError("control_store_integrity_invalid")
            snapshot = matches[0]
            CoreRunDomainVerifier().verify_history(history)
            verified = CoreRunDomainVerifier().verify_loaded_history(
                history,
                successor_run_id,
                require_current_head=False,
            )
            transitions = [
                item
                for item in snapshot.run_head_transitions
                if item.successor_run_id == successor_run_id
                and item.reason_code == "human_started_successor"
                and item.successor_disposition == "reference"
            ]
            if (
                len(transitions) != 1
                or len(snapshot.run_guidance_snapshots) != 1
                or len(snapshot.run_contract_bindings) != 1
            ):
                raise RuntimeHostError("control_store_integrity_invalid")
            transition = transitions[0]
            guidance = snapshot.run_guidance_snapshots[0]
            receipts = [
                item
                for item in snapshot.transactions
                if item.transaction_id == transition.accepted_transaction_id
            ]
            if (
                len(receipts) != 1
                or guidance.accepted_transaction_id
                != transition.accepted_transaction_id
                or guidance.request_fingerprint != transition.request_fingerprint
            ):
                raise RuntimeHostError("control_store_integrity_invalid")
            receipt = receipts[0]
            payload = {
                "schema_version": RunSuccessorStartRequest.schema_id,
                "request_id": transition.accepted_transaction_id,
                "predecessor_run_id": transition.predecessor_run_id,
                "successor_run_id": successor_run_id,
                "workspace_id": snapshot.workspace_id,
                "runtime": snapshot.run.runtime,
                "expected_head_run_id": transition.predecessor_run_id,
                "expected_store_revision": receipt.prior_revision,
                "expected_workspace_revision": (transition.prior_workspace_revision),
                "run_direction": run_direction.model_dump(mode="json"),
                "workspace_config_sha256": verified.binding.workspace_config_sha256,
                "sources_config_sha256": verified.binding.sources_config_sha256,
                "role_topology": verified.binding.role_topology,
                "gate_strictness": verified.binding.gate_strictness,
                "input_governance_required": (
                    verified.binding.input_governance_required
                ),
                "include_approved_guidance": include_approved_guidance,
            }
            payload["request_fingerprint"] = canonical_fingerprint(payload)
            return RunSuccessorStartRequest.model_validate(payload, strict=True)
        except RuntimeHostError:
            raise
        except (ControlStoreError, CoreRunError, ValidationError, ValueError) as exc:
            raise RuntimeHostError("control_store_integrity_invalid") from exc

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
        if (
            action.action_kind == "deterministic"
            and action.effect_kind == "source_acquire"
        ):
            raise RuntimeHostError("runtime_action_not_invocable")
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

    @staticmethod
    def _build_role_envelope(
        verified,
        action: CoreRunNextAction,
        *,
        invocation_id: str,
        committed_revision: int,
        role_id: str,
    ) -> RoleTaskEnvelope:
        if action.stage_id is None:
            raise RuntimeHostError("runtime_envelope_invalid")
        output = _ROLE_OUTPUTS.get(role_id)
        if output is None:
            raise RuntimeHostError("runtime_envelope_invalid")
        topology = core_role_topology_policy(verified.binding.role_topology)
        dispatch_instruction = {
            "main_session": "execute_in_current_session",
            "delegated_specialist": "delegate_exact_role",
            "declared_existing_route": "use_declared_route",
        }[topology.role_executor_route]
        gate_repair_context = (
            active_gate_repair_context(verified.snapshot)
            if role_id == "editor"
            else None
        )
        task_instructions = _role_task_instructions(
            role_id,
            output,
            invocation_id,
        )
        if role_id == "analyst":
            task_instructions = (
                f"{task_instructions}"
                f"{_target_relevance_task_instruction(role_id, verified.binding.run_direction.target_terms)}"
            )
        if gate_repair_context is not None:
            task_instructions = (
                f"{task_instructions} Repair only the exact audited_brief scope "
                "in gate_repair_context; do not change sources, claims, or run direction."
            )
            if any(
                str(item.get("finding_type"))
                in {
                    "target_relevance_gap",
                    "target_priority_claim_missing_from_summary",
                }
                for item in gate_repair_context.get("findings", [])
                if isinstance(item, dict)
            ):
                task_instructions = (
                    f"{task_instructions}"
                    f"{_target_relevance_task_instruction('editor', verified.binding.run_direction.target_terms, gate_repair=True)}"
                )
        frozen_guidance_context = RuntimeHostService._frozen_guidance_context(
            verified,
            role_id=role_id,
        )
        if frozen_guidance_context is not None:
            task_instructions = (
                f"{task_instructions} Use frozen guidance only for audience fit, "
                "structure, style, and expression. Current RunDirection and evidence "
                "govern. Guidance is not a fact, source, Claim Ledger input, Gate rule, "
                "repair command, or delivery authority."
            )
        return RoleTaskEnvelope.model_validate(
            {
                "schema_version": RoleTaskEnvelope.schema_id,
                "run_id": action.run_id,
                "invocation_id": invocation_id,
                "store_revision": committed_revision,
                "action": action.model_dump(mode="json", exclude_unset=False),
                "action_fingerprint": action.action_fingerprint,
                "role_id": role_id,
                "stage_id": action.stage_id,
                "scratch_directory": f"scratch/{invocation_id}",
                "allowed_output_filenames": sorted(output.filenames),
                "proposal_schema_id": output.proposal_schema_id,
                "adapter_binding_fingerprint": (
                    verified.runtime_adapter.binding_fingerprint
                ),
                "source_plan_fingerprint": (
                    verified.source_plan.source_plan_fingerprint
                ),
                "executor_kind": topology.role_executor_route,
                "context_mode": topology.context_mode,
                "review_mode": topology.review_mode,
                "dispatch_instruction": dispatch_instruction,
                "task_instructions": task_instructions,
                "gate_repair_context": gate_repair_context,
                "frozen_guidance_context": frozen_guidance_context,
            },
            strict=True,
        )

    @staticmethod
    def _frozen_guidance_context(verified, *, role_id: str):
        if role_id not in {"analyst", "editor"}:
            return None
        snapshots = verified.snapshot.run_guidance_snapshots
        if not snapshots:
            return None
        if len(snapshots) != 1:
            raise RuntimeHostError("runtime_envelope_invalid")
        snapshot = snapshots[0]
        items = sorted(
            verified.snapshot.run_guidance_snapshot_items,
            key=lambda item: item.position,
        )
        if (
            snapshot.run_id != verified.snapshot.run.run_id
            or snapshot.selected_count != len(items)
            or [item.item_id for item in items] != snapshot.selected_item_ids
        ):
            raise RuntimeHostError("runtime_envelope_invalid")
        if not items:
            return None
        frozen_items = [
            FrozenGuidanceItem.model_validate(
                {
                    "item_id": item.item_id,
                    "position": item.position,
                    "source_run_id": item.source_run_id,
                    "finalized_lineage_fingerprint": (
                        item.finalized_lineage_fingerprint
                    ),
                    "provenance_kind": item.provenance_kind,
                    "assessment_result_id": item.assessment_result_id,
                    "assessment_result_fingerprint": (
                        item.assessment_result_fingerprint
                    ),
                    "finding_id": item.finding_id,
                    "finding_fingerprint": item.finding_fingerprint,
                    "disposition_id": item.disposition_id,
                    "disposition_fingerprint": item.disposition_fingerprint,
                    "observation_id": item.observation_id,
                    "observation_fingerprint": item.observation_fingerprint,
                    "guidance_id": item.guidance_id,
                    "draft_revision": item.draft_revision,
                    "draft_fingerprint": item.draft_fingerprint,
                    "status_revision_id": item.status_revision_id,
                    "status_fingerprint": item.status_fingerprint,
                    "guidance_text": item.guidance_text,
                    "guidance_sha256": item.guidance_sha256,
                    "reuse_scope": item.reuse_scope.model_dump(mode="json"),
                    "item_fingerprint": item.item_fingerprint,
                },
                strict=True,
            )
            for item in items
        ]
        return FrozenGuidanceContext.model_validate(
            {
                "run_id": snapshot.run_id,
                "snapshot_id": snapshot.snapshot_id,
                "snapshot_fingerprint": snapshot.snapshot_fingerprint,
                "items": [
                    item.model_dump(mode="json", exclude_unset=False)
                    for item in frozen_items
                ],
            },
            strict=True,
        )

    def _expected_invocation_envelope(
        self,
        invocation_id: str,
        *,
        current=None,
    ) -> RoleTaskEnvelope:
        if current is None:
            current = initialize_or_open_runtime(
                self.workspace,
                adapter_loader=self._adapter_loader,
            )
        invocation = next(
            (
                item
                for item in current.verified.snapshot.invocations
                if item.invocation_id == invocation_id
            ),
            None,
        )
        starts = [
            item
            for item in current.verified.snapshot.events
            if item.event_type == "role_invocation_started"
            and item.core_run_binding is not None
            and item.core_run_binding.effect_kind == "invocation_start"
            and item.core_run_binding.primary_record_id == invocation_id
        ]
        if invocation is None or len(starts) != 1:
            raise RuntimeHostError("runtime_envelope_invalid")
        start = starts[0]
        try:
            with SQLiteControlStore.open(self.workspace / "briefloop.db") as store:
                history = store.load_history()
                receipt = store.load_transaction_receipt(
                    invocation.run_id,
                    start.transaction_id,
                )
            if (
                receipt is None
                or history.store_revision != current.verified.snapshot.store_revision
                or receipt.committed_revision <= 1
                or receipt.prior_revision != receipt.committed_revision - 1
                or start.event_id not in receipt.event_ids
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
            pre_verified = verifier._verify_snapshot(history, pre_snapshot)
            pre_verified = replace(
                pre_verified,
                exhausted_source_route_keys=verifier._source_route_exhaustion_as_of(
                    history,
                    pre_snapshot,
                ),
            )
            action = classify_core_run_next_action(pre_verified)
        except RuntimeHostError:
            raise
        except Exception as exc:
            raise RuntimeHostError("runtime_envelope_invalid") from exc
        role_id = self._invocation_role_for_action(action)
        if (
            role_id is None
            and action.action_kind == "human_decision"
            and action.effect_kind == "source_input_required"
        ):
            role_id = "source-provider"
        if (
            role_id != invocation.role_id
            or action.stage_id != start.stage_id
            or action.run_id != invocation.run_id
        ):
            raise RuntimeHostError("runtime_envelope_invalid")
        return self._build_role_envelope(
            pre_verified,
            action,
            invocation_id=invocation_id,
            committed_revision=receipt.committed_revision,
            role_id=role_id,
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
            refreshed = initialize_or_open_runtime(
                self.workspace,
                adapter_loader=self._adapter_loader,
            )
            active = [
                item
                for item in refreshed.verified.snapshot.invocations
                if item.status == "active"
            ]
            if active:
                if len(active) != 1:
                    raise RuntimeHostError("control_store_integrity_invalid")
                envelope = self._expected_invocation_envelope(
                    active[0].invocation_id,
                    current=refreshed,
                )
                if envelope.action != action:
                    raise RuntimeHostError("runtime_action_stale")
                result = CoreRunService(self.workspace).start_invocation(request)
            else:
                if refreshed.action != action:
                    raise RuntimeHostError("runtime_action_stale")
                result = CoreRunService(self.workspace).start_invocation(request)
            if result.status == "commit_outcome_unknown":
                raise RuntimeHostError("commit_outcome_unknown")
        if result.status not in {"committed", "replayed"}:
            raise RuntimeHostError(
                result.error_code or "control_store_integrity_invalid"
            )
        if result.primary_record_id is None:
            raise RuntimeHostError("control_store_integrity_invalid")
        invocation_id = result.primary_record_id
        envelope = self._build_role_envelope(
            current.verified,
            action,
            invocation_id=invocation_id,
            committed_revision=result.receipt.committed_revision,
            role_id=role_id,
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
        try:
            verified = self._verify_role_submission(
                envelope,
                spec,
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
        return self._proposal_validation_result(
            envelope,
            spec,
            reason_code=("runtime_proposal_invalid" if verified.violations else None),
            violations=list(verified.violations),
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
                    {"field": item.field, "reason": item.error} for item in violations
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
        verified = self._verify_role_submission(envelope, spec)
        if verified.violations:
            raise RuntimeHostError("runtime_proposal_invalid")
        request = verified.acceptance_request
        request_payload = verified.acceptance_payload
        if request is None or request_payload is None:
            raise RuntimeHostError("runtime_envelope_invalid")
        lane = verified.acceptance_lane
        materialize_host_request(
            self.workspace,
            envelope,
            request_payload,
        )
        result = self._submit_verified_acceptance(verified, request, lane)
        status = result.status
        if status == "commit_outcome_unknown":
            # Resolve the exact acceptance identity through the owning service
            # before refreshing action classification.  If the first commit
            # succeeded, this call returns the receipt replay for that commit.
            result = self._submit_verified_acceptance(verified, request, lane)
            status = result.status
            if status == "commit_outcome_unknown":
                raise RuntimeHostError("commit_outcome_unknown")
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

    def _submit_verified_acceptance(
        self,
        verified: _VerifiedRoleSubmission,
        request: SourceCommitRequest
        | ArtifactSubmitRequest
        | OwnedArtifactSubmitRequest,
        lane: str | None,
    ):
        """Delegate immutable verified bytes to the existing sole owner writer."""

        if verified.spec.owner_kind == "source":
            if not isinstance(request, SourceCommitRequest) or lane is not None:
                raise RuntimeHostError("runtime_envelope_invalid")
            return IntakeService(self.workspace)._submit_source_from_host(
                request,
                proposal_bytes=verified.outputs["source_proposal.json"],
                content_bytes=verified.outputs["source_content.bin"],
                raw_bytes=verified.outputs.get("source_raw.json"),
            )
        if verified.spec.owner_kind == "proposal":
            if not isinstance(request, ArtifactSubmitRequest) or lane is None:
                raise RuntimeHostError("runtime_envelope_invalid")
            return IntakeService(self.workspace)._submit_proposal_from_host(
                lane,
                request,
                verified.outputs[verified.spec.filenames[0]],
            )
        if (
            verified.spec.owner_kind != "owned"
            or not isinstance(request, OwnedArtifactSubmitRequest)
            or lane is not None
        ):
            raise RuntimeHostError("runtime_envelope_invalid")
        return ArtifactAcceptanceService(
            self.workspace
        )._submit_owned_artifact_from_host(
            request,
            verified.outputs[verified.spec.filenames[0]],
        )

    def _validate_envelope(self, current, envelope, spec: _RoleOutputSpec) -> None:
        expected = self._expected_invocation_envelope(
            envelope.invocation_id,
            current=current,
        )
        if _ROLE_OUTPUTS.get(expected.role_id) is not spec or envelope != expected:
            raise RuntimeHostError("runtime_envelope_invalid")

    def _verify_role_submission(
        self,
        envelope: RoleTaskEnvelope,
        spec: _RoleOutputSpec,
    ) -> _VerifiedRoleSubmission:
        current = initialize_or_open_runtime(
            self.workspace,
            adapter_loader=self._adapter_loader,
        )
        self._validate_envelope(current, envelope, spec)
        invocation = next(
            (
                item
                for item in current.verified.snapshot.invocations
                if item.invocation_id == envelope.invocation_id
            ),
            None,
        )
        if invocation is None or invocation.status not in {"active", "completed"}:
            raise RuntimeHostError("runtime_envelope_invalid")
        completed = invocation.status == "completed"
        outputs = read_role_outputs(
            self.workspace,
            envelope,
            host_filenames=(("submit_request.json",) if completed else ()),
            allow_optional_host_request=not completed,
        )
        violations = tuple(
            _strict_proposal_violations(
                spec,
                outputs,
                expected_run_id=envelope.run_id,
            )
        )
        request = None
        lane = None
        request_payload = None
        if not violations:
            request, lane = self._derive_acceptance_request(envelope, spec, outputs)
            request_payload = canonical_json_bytes(
                request.model_dump(mode="json", exclude_unset=False)
            )
            verify_optional_host_request(
                self.workspace,
                envelope,
                request_payload,
            )
        return _VerifiedRoleSubmission(
            envelope=envelope,
            spec=spec,
            outputs=outputs,
            violations=violations,
            acceptance_request=request,
            acceptance_lane=lane,
            acceptance_payload=request_payload,
        )

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
            # Bind replay residue to the exact verified role-output bytes.
            canonical_fingerprint(
                [
                    {
                        "filename": filename,
                        "sha256": sha256_hex(outputs[filename]),
                    }
                    for filename in sorted(outputs)
                ]
            ),
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
                        "expected_artifact_revision": (
                            0 if artifact is None else artifact.current_revision
                        ),
                    },
                    strict=True,
                ),
                spec.proposal_lane,
            )
        if artifact is None:
            raise RuntimeHostError("runtime_envelope_invalid")
        parent: ArtifactRevisionReference | None = None
        if spec.artifact_id == "audited_brief":
            repair_context = active_gate_repair_context(snapshot)
            if repair_context is not None:
                parent = ArtifactRevisionReference.model_validate(
                    repair_context["target_artifact"],
                    strict=True,
                )
            else:
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
        *,
        presentation_hook: bool = True,
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
            if (
                isinstance(
                    human_request,
                    RuntimeSourceAcquisitionRecoveryRequest,
                )
                and action_input is None
                and expected_action.action_kind == "human_decision"
                and expected_action.effect_kind == "source_acquisition_recovery"
            ):
                return self._apply_source_acquisition_recovery(
                    current,
                    expected_action,
                    human_request,
                    replay_only=True,
                )
            if (
                human_request is None
                and action_input is None
                and expected_action.action_kind == "deterministic"
                and expected_action.effect_kind == "source_acquire"
            ):
                return self._apply_source_acquire(
                    current,
                    expected_action,
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
        elif action.effect_kind == "authorized_source_pack_commit":
            result = CoreRunService(self.workspace).apply_authorized_source_pack()
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
            result = self._apply_source_acquire(current, action, replay_only=False)
        elif action.effect_kind == "claim_freeze":
            result = self._apply_claim_freeze(current, action)
        elif action.effect_kind == "audit_promotion":
            result = self._apply_audit_promotion(current, action)
        elif action.effect_kind in {"gate_evaluation", "finalize_gate"}:
            result = self._apply_gate_evaluation(current, action)
        elif action.effect_kind == "stage_complete":
            result = self._apply_stage_complete(current, action)
        elif action.effect_kind == "gate_repair_start":
            request = GateRepairStartRequest.model_validate(
                {
                    "schema_version": GateRepairStartRequest.schema_id,
                    "request_id": derived_id(
                        "REQ-HOST-GATE-REPAIR",
                        action.run_id,
                        action.action_fingerprint,
                    ),
                    "run_id": action.run_id,
                    "action_fingerprint": action.action_fingerprint,
                    "expected_store_revision": action.store_revision,
                },
                strict=True,
            )
            result = GateRepairService(self.workspace).start(
                request_id=request.request_id,
                run_id=request.run_id,
                action_fingerprint=request.action_fingerprint,
                expected_store_revision=request.expected_store_revision,
            )
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
        if (
            presentation_hook
            and result.status == "committed"
            and action.effect_kind
            in {
                "finalize_complete",
                "delivery_result",
            }
        ):
            # Read-only three-page brief HTML auto-open (config-gated, default
            # off, best-effort: the hook never raises into the run).
            from multi_agent_brief.product.brief_html import (
                maybe_auto_open_brief_pages,
            )

            if action.effect_kind == "finalize_complete":
                self._observe_post_final_assessment()
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
        if action.effect_kind in {
            "gate_repair_human_review",
            "audit_human_review",
        } and isinstance(request, RunTerminationRequest):
            result = terminal.record_run_termination(request)
        elif action.effect_kind == "internal_approval" and isinstance(
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
        elif action.effect_kind == "source_acquisition_recovery" and isinstance(
            request,
            RuntimeSourceAcquisitionRecoveryRequest,
        ):
            return self._apply_source_acquisition_recovery(
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

    def _apply_source_acquisition_recovery(
        self,
        current,
        action: CoreRunNextAction,
        request: RuntimeSourceAcquisitionRecoveryRequest,
        *,
        replay_only: bool,
    ):
        if request.expected_action_fingerprint != action.action_fingerprint:
            raise RuntimeHostError("runtime_human_request_invalid")
        if request.decision == "provide_human_source_pack":
            if request.human_source_pack is None:
                raise RuntimeHostError("runtime_human_request_invalid")
            return self._apply_human_source_pack(
                current,
                action,
                request.human_source_pack,
                replay_only=replay_only,
            )
        if request.previous_attempt_authorization_id is None:
            raise RuntimeHostError("runtime_human_request_invalid")
        core_request = SourceAcquisitionAttemptAuthorizeRequest.model_validate(
            {
                "schema_version": (SourceAcquisitionAttemptAuthorizeRequest.schema_id),
                "request_id": request.request_id,
                "run_id": request.run_id,
                "expected_store_revision": request.expected_store_revision,
                "expected_action_fingerprint": (request.expected_action_fingerprint),
                "previous_attempt_authorization_id": (
                    request.previous_attempt_authorization_id
                ),
                "human_confirmation": request.human_confirmation,
                "provider_cost_status": request.provider_cost_status,
            },
            strict=True,
        )
        try:
            result = CoreRunService(
                self.workspace
            ).authorize_source_acquisition_attempt(core_request)
        except CoreRunError as exc:
            raise RuntimeHostError(exc.code) from None
        if result.status not in {"committed", "replayed"}:
            raise RuntimeHostError(
                result.error_code or "source_acquisition_recovery_invalid"
            )
        return result

    def _planned_invocation(
        self,
        current,
        action: CoreRunNextAction,
        *,
        request_id: str,
    ) -> tuple[InvocationStartRequest, str]:
        request = self._invocation_start_request(
            current,
            action,
            role_id="source-provider",
            request_id=request_id,
        )
        fingerprint = canonical_fingerprint(
            request.model_dump(mode="json", exclude_unset=False)
        )
        return request, derived_id("INV", request_id, fingerprint)

    def _invocation_for_id(self, current, invocation_id: str):
        return next(
            (
                item
                for item in current.verified.snapshot.invocations
                if item.invocation_id == invocation_id
            ),
            None,
        )

    def _source_pack_store_replay(
        self,
        current,
        *,
        invocation_id: str,
        commit_request_id: str,
    ) -> RuntimeInvocationResult | None:
        invocation = self._invocation_for_id(current, invocation_id)
        failure_reason = (
            invocation.failure_reason
            if invocation is not None and invocation.status == "failed"
            else None
        )
        try:
            with SQLiteControlStore.open(self.workspace / "briefloop.db") as store:
                receipt = store.load_transaction_receipt(
                    current.verified.snapshot.run.run_id,
                    commit_request_id,
                )
                failure_request_id = derived_id(
                    "REQ-HOST-INVOCATION-FAILURE",
                    invocation_id,
                    failure_reason or "dispatch_unavailable",
                )
                failure_receipt = store.load_transaction_receipt(
                    current.verified.snapshot.run.run_id,
                    failure_request_id,
                )
        except ControlStoreError as exc:
            raise RuntimeHostError("control_store_integrity_invalid") from exc
        if receipt is not None:
            bindings = [
                item.intake_binding
                for item in current.verified.snapshot.events
                if item.event_id in receipt.event_ids
                and item.intake_binding is not None
                and item.intake_binding.request_id == commit_request_id
            ]
            outcomes = {item.outcome for item in bindings}
            if not bindings or len(outcomes) != 1:
                raise RuntimeHostError("control_store_integrity_invalid")
            return self._source_pack_replay_result(
                current,
                invocation_id=invocation_id,
                transaction_id=receipt.transaction_id,
                store_revision=receipt.committed_revision,
                rejected=outcomes == {"rejected"},
            )
        if invocation is None:
            return None
        if invocation.status == "active":
            return None
        if invocation.status == "failed" and failure_receipt is not None:
            return self._source_pack_replay_result(
                current,
                invocation_id=invocation_id,
                transaction_id=failure_receipt.transaction_id,
                store_revision=failure_receipt.committed_revision,
                rejected=True,
            )
        raise RuntimeHostError("submission_replay_conflict")

    @staticmethod
    def _source_pack_replay_result(
        current,
        *,
        invocation_id: str,
        transaction_id: str,
        store_revision: int,
        rejected: bool,
    ) -> RuntimeInvocationResult:
        return RuntimeInvocationResult.model_validate(
            {
                "schema_version": RuntimeInvocationResult.schema_id,
                "run_id": current.verified.snapshot.run.run_id,
                "invocation_id": invocation_id,
                "status": "rejected_recorded" if rejected else "replayed",
                "transaction_id": transaction_id,
                "store_revision": store_revision,
                "next_action": current.action.model_dump(
                    mode="json",
                    exclude_unset=False,
                ),
            },
            strict=True,
        )

    def _record_staged_invocation_failure(
        self,
        current,
        action: CoreRunNextAction,
        *,
        request_id: str,
        invocation_id: str,
        reason_code: str = "dispatch_unavailable",
    ) -> RuntimeInvocationResult:
        invocation = self._invocation_for_id(current, invocation_id)
        if invocation is None:
            request, expected_invocation_id = self._planned_invocation(
                current,
                action,
                request_id=request_id,
            )
            if expected_invocation_id != invocation_id:
                raise RuntimeHostError("control_store_integrity_invalid")
            result = CoreRunService(self.workspace).start_invocation(request)
            if result.status == "commit_outcome_unknown":
                result = CoreRunService(self.workspace).start_invocation(request)
            if (
                result.status not in {"committed", "replayed"}
                or result.receipt is None
                or result.primary_record_id != invocation_id
            ):
                raise RuntimeHostError(
                    result.error_code or "control_store_integrity_invalid"
                )
            envelope = self._build_role_envelope(
                current.verified,
                action,
                invocation_id=invocation_id,
                committed_revision=result.receipt.committed_revision,
                role_id="source-provider",
            )
        else:
            envelope = self._expected_invocation_envelope(invocation_id)
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
        return self._source_pack_runtime_result(invocation_id, result)

    def _materialize_staged_source_pack(
        self,
        dispatch: InvocationDispatch,
        stage: VerifiedSourceStage,
        *,
        commit_request_id: str,
    ) -> _VerifiedSourcePackSubmission:
        invocation_id = dispatch.envelope.invocation_id
        manifest_bytes = stage.manifest_bytes
        if manifest_bytes is not None:
            if stage.manifest_sha256 is None:
                raise RuntimeHostError("runtime_source_staging_invalid")
            self._materialize_tool_input(
                f"scratch/{invocation_id}/source_manifest.json",
                manifest_bytes,
            )
        members: list[dict[str, object]] = []
        member_bytes: list[_SourcePackMemberBytes] = []
        for member in stage.members:
            root = f"scratch/{invocation_id}/sources/{member.member_id}"
            proposal_path = f"{root}/source_proposal.json"
            content_path = f"{root}/source_content.bin"
            raw_path = (
                None if member.raw_payload_bytes is None else f"{root}/source_raw.json"
            )
            proposal_bytes = member.proposal_bytes
            content_bytes = member.content_bytes
            self._materialize_tool_input(
                proposal_path,
                proposal_bytes,
            )
            self._materialize_tool_input(
                content_path,
                content_bytes,
            )
            raw_bytes = member.raw_payload_bytes
            if raw_bytes is not None and raw_path is not None:
                if member.raw_payload_sha256 is None:
                    raise RuntimeHostError("runtime_source_staging_invalid")
                self._materialize_tool_input(
                    raw_path,
                    raw_bytes,
                )
            members.append(
                {
                    "member_id": member.member_id,
                    "proposal_path": proposal_path,
                    "content_path": content_path,
                    "raw_payload_path": raw_path,
                }
            )
            member_bytes.append(
                _SourcePackMemberBytes(
                    proposal_bytes=proposal_bytes,
                    content_bytes=content_bytes,
                    raw_bytes=raw_bytes,
                )
            )
        submit = SourcePackCommitRequest.model_validate(
            {
                "schema_version": SourcePackCommitRequest.schema_id,
                "request_id": commit_request_id,
                "run_id": dispatch.envelope.run_id,
                "invocation_id": invocation_id,
                "members": members,
                "manifest_path": (
                    None
                    if stage.manifest_bytes is None
                    else f"scratch/{invocation_id}/source_manifest.json"
                ),
                "expected_manifest_sha256": stage.manifest_sha256,
                "expected_store_revision": dispatch.envelope.store_revision,
            },
            strict=True,
        )
        self._materialize_tool_input(
            f"scratch/{invocation_id}/submit_request.json",
            canonical_json_bytes(submit.model_dump(mode="json", exclude_unset=False)),
        )
        return _VerifiedSourcePackSubmission(
            request=submit,
            pack=_SourcePackBytes(
                manifest_bytes=manifest_bytes,
                members=tuple(member_bytes),
            ),
        )

    def _submit_staged_source_pack(
        self,
        dispatch: InvocationDispatch,
        stage: VerifiedSourceStage,
        *,
        commit_request_id: str,
        stage_identity: str,
        human_authorized: bool = False,
    ) -> RuntimeInvocationResult:
        try:
            verified = self._materialize_staged_source_pack(
                dispatch,
                stage,
                commit_request_id=commit_request_id,
            )
        except (OSError, RuntimeHostError, ValidationError, ValueError):
            result = self._record_staged_invocation_failure(
                initialize_or_open_runtime(
                    self.workspace,
                    adapter_loader=self._adapter_loader,
                ),
                dispatch.envelope.action,
                request_id=dispatch.envelope.action_fingerprint,
                invocation_id=dispatch.envelope.invocation_id,
            )
            return result
        intake = IntakeService(self.workspace)
        submit = (
            intake._commit_human_source_pack_from_host
            if human_authorized
            else intake._submit_source_pack_from_host
        )
        result = submit(verified.request, verified.pack)
        if result.status == "commit_outcome_unknown":
            result = submit(verified.request, verified.pack)
        runtime_result = self._source_pack_runtime_result(
            dispatch.envelope.invocation_id,
            result,
        )
        discard_source_stage(self.workspace, stage_identity=stage_identity)
        return runtime_result

    def _apply_source_acquire(
        self,
        current,
        action: CoreRunNextAction,
        *,
        replay_only: bool,
    ):
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
        if (
            not current.verified.snapshot.run_execution_authorizations
            and len(current.verified.snapshot.run_source_discovery_authorizations) == 1
        ):
            return self._apply_discovery_source_acquire(
                current,
                action,
                route=route,
                replay_only=replay_only,
            )
        invocation_request_id = derived_id(
            "REQ-HOST-INVOKE",
            action.run_id,
            action.action_fingerprint,
        )
        _, invocation_id = self._planned_invocation(
            current,
            action,
            request_id=invocation_request_id,
        )
        commit_request_id = derived_id(
            "REQ-HOST-SOURCE-PACK",
            action.run_id,
            action.action_fingerprint,
        )
        replay = self._source_pack_store_replay(
            current,
            invocation_id=invocation_id,
            commit_request_id=commit_request_id,
        )
        if replay is not None:
            return replay
        invocation = self._invocation_for_id(current, invocation_id)
        if replay_only and invocation is None:
            raise RuntimeHostError("runtime_action_stale")
        stage_identity = canonical_fingerprint(
            {
                "kind": "deterministic_source_pack",
                "run_id": action.run_id,
                "invocation_id": invocation_id,
            }
        )
        stage_fingerprint = canonical_fingerprint(
            {
                "action": action.model_dump(mode="json", exclude_unset=False),
                "route_fingerprint": route.route_fingerprint,
            }
        )
        try:
            stage = load_source_stage(
                self.workspace,
                stage_identity=stage_identity,
                request_fingerprint=stage_fingerprint,
                expected_manifest_sha256=None,
                expected_stage_kind="provider_outcome",
            )
        except RuntimeHostError as exc:
            if str(exc) == "submission_replay_conflict":
                raise
            if invocation is not None and invocation.status == "active":
                return self._record_staged_invocation_failure(
                    current,
                    action,
                    request_id=invocation_request_id,
                    invocation_id=invocation_id,
                )
            raise
        if invocation is not None and invocation.status == "active" and stage is None:
            return self._record_staged_invocation_failure(
                current,
                action,
                request_id=invocation_request_id,
                invocation_id=invocation_id,
            )
        if stage is None:
            try:
                materials = collect_frozen_sources(
                    self.workspace,
                    run_id=action.run_id,
                    invocation_id=invocation_id,
                    route=route,
                )
                staged_inputs = tuple(
                    SourceStageBytesInput(
                        member_id=f"MEMBER-{position:04d}",
                        proposal_bytes=canonical_json_bytes(
                            material.proposal.model_dump(
                                mode="json",
                                exclude_unset=False,
                            )
                        ),
                        content_bytes=material.content,
                        raw_payload_bytes=material.raw_payload,
                    )
                    for position, material in enumerate(materials, start=1)
                )
                stage = stage_source_pack_bytes(
                    self.workspace,
                    stage_identity=stage_identity,
                    request_fingerprint=stage_fingerprint,
                    members=staged_inputs,
                )
            except RuntimeHostError as exc:
                if str(exc) in {
                    "runtime_source_pack_invalid",
                    "runtime_source_staging_invalid",
                }:
                    raise
                return self._record_staged_invocation_failure(
                    current,
                    action,
                    request_id=invocation_request_id,
                    invocation_id=invocation_id,
                )
            except (
                OSError,
                NotImplementedError,
                RuntimeError,
                SearchBackendError,
                ValidationError,
                ValueError,
            ):
                return self._record_staged_invocation_failure(
                    current,
                    action,
                    request_id=invocation_request_id,
                    invocation_id=invocation_id,
                )
        dispatch = self._start_invocation_for_action(
            current,
            action,
            role_id="source-provider",
            request_id=invocation_request_id,
        )
        if dispatch.envelope.invocation_id != invocation_id:
            raise RuntimeHostError("control_store_integrity_invalid")
        return self._submit_staged_source_pack(
            dispatch,
            stage,
            commit_request_id=commit_request_id,
            stage_identity=stage_identity,
        )

    def _apply_discovery_source_acquire(
        self,
        current,
        action: CoreRunNextAction,
        *,
        route,
        replay_only: bool,
        _acquisition_lock_held: bool = False,
    ) -> RuntimeInvocationResult:
        """Acquire once, then atomically promote the Store-owned discovery run."""

        from .source_routes import collect_frozen_source_pack

        discovery = current.verified.snapshot.run_source_discovery_authorizations[0]
        attempts = (
            current.verified.snapshot.run_source_acquisition_attempt_authorizations
        )
        if not attempts or route.acquisition_spec is None:
            raise RuntimeHostError("source_acquisition_attempt_authorization_invalid")
        attempt = attempts[-1]
        if (
            discovery.run_id != action.run_id
            or discovery.route_id != route.route_id
            or discovery.source_route_fingerprint != route.route_fingerprint
            or discovery.provider_id != route.provider_id
            or discovery.execution_owner != route.execution_owner
            or action.source_route_id != discovery.route_id
            or action.source_provider_id != discovery.provider_id
            or action.source_acquisition_attempt_authorization_id
            != attempt.attempt_authorization_id
            or attempt.discovery_authorization_id != discovery.authorization_id
            or attempt.provider_request_fingerprint
            != route.acquisition_spec.acquisition_spec_fingerprint
        ):
            raise RuntimeHostError("source_discovery_authorization_invalid")
        active_recovery = (
            action.reason_code == "active_discovery_source_acquire_requires_resume"
        )
        recovery_envelope = None
        source_action = action
        if active_recovery:
            active = [
                item
                for item in current.verified.snapshot.invocations
                if item.status == "active"
            ]
            if len(active) == 1:
                recovery_invocation_id = active[0].invocation_id
            elif not active and replay_only:
                failures = [
                    item.intake_binding.source_acquisition_failure
                    for item in current.verified.snapshot.events
                    if item.intake_binding is not None
                    and item.intake_binding.source_acquisition_failure is not None
                    and item.intake_binding.source_acquisition_failure.attempt_authorization_id
                    == attempt.attempt_authorization_id
                ]
                if len(failures) != 1:
                    raise RuntimeHostError("control_store_integrity_invalid")
                recovery_invocation_id = failures[0].invocation_id
            else:
                raise RuntimeHostError("control_store_integrity_invalid")
            recovery_envelope = self._expected_invocation_envelope(
                recovery_invocation_id,
                current=current,
            )
            source_action = recovery_envelope.action
            if (
                source_action.action_kind != "deterministic"
                or source_action.effect_kind != "source_acquire"
                or source_action.reason_code != "deterministic_source_route_required"
                or source_action.stage_id != "source-discovery"
                or source_action.source_route_id != discovery.route_id
                or source_action.source_provider_id != discovery.provider_id
                or source_action.source_acquisition_attempt_authorization_id
                != attempt.attempt_authorization_id
                or recovery_envelope.role_id != "source-provider"
            ):
                raise RuntimeHostError("control_store_integrity_invalid")
        invocation_request_id = derived_id(
            "REQ-HOST-INVOKE",
            source_action.run_id,
            source_action.action_fingerprint,
        )
        _, invocation_id = self._planned_invocation(
            current,
            source_action,
            request_id=invocation_request_id,
        )
        if (
            recovery_envelope is not None
            and recovery_envelope.invocation_id != invocation_id
        ):
            raise RuntimeHostError("control_store_integrity_invalid")
        commit_request_id = derived_id(
            "REQ-HOST-SOURCE-PACK",
            source_action.run_id,
            source_action.action_fingerprint,
        )
        replay = self._source_pack_store_replay(
            current,
            invocation_id=invocation_id,
            commit_request_id=commit_request_id,
        )
        if replay is not None:
            return replay
        if replay_only:
            raise RuntimeHostError("runtime_action_stale")
        stage_identity = canonical_fingerprint(
            {
                "kind": "discovery_source_pack",
                "run_id": source_action.run_id,
                "action_fingerprint": source_action.action_fingerprint,
                "discovery_authorization_id": discovery.authorization_id,
                "attempt_authorization_id": attempt.attempt_authorization_id,
            }
        )
        stage_fingerprint = canonical_fingerprint(
            {
                "action": source_action.model_dump(
                    mode="json",
                    exclude_unset=False,
                ),
                "route_fingerprint": route.route_fingerprint,
                "discovery_request_fingerprint": discovery.request_fingerprint,
                "attempt_authorization_id": attempt.attempt_authorization_id,
            }
        )
        provider_request_fingerprint = (
            route.acquisition_spec.acquisition_spec_fingerprint
        )
        try:
            stage = load_source_stage(
                self.workspace,
                stage_identity=stage_identity,
                request_fingerprint=stage_fingerprint,
                expected_manifest_sha256=None,
                expected_stage_kind="provider_outcome",
                expected_capacity_profile="multi_tavily_v2",
            )
        except RuntimeHostError as exc:
            if str(exc) == "submission_replay_conflict":
                raise
            if active_recovery:
                raise RuntimeHostError("source_acquisition_outcome_unknown") from None
            raise RuntimeHostError("source_provider_result_invalid") from None
        if not _acquisition_lock_held and active_recovery:
            with _exclusive_source_acquisition(self.workspace):
                refreshed = initialize_or_open_runtime(
                    self.workspace,
                    adapter_loader=self._adapter_loader,
                )
                if refreshed.action != action:
                    raise RuntimeHostError("runtime_action_stale")
                return self._apply_discovery_source_acquire(
                    refreshed,
                    action,
                    route=route,
                    replay_only=replay_only,
                    _acquisition_lock_held=True,
                )
        if stage is None and active_recovery:
            if recovery_envelope is None:
                raise RuntimeHostError("control_store_integrity_invalid")
            dispatch = InvocationDispatch(
                envelope=recovery_envelope,
                envelope_path=(
                    self.workspace / "scratch" / invocation_id / "task_envelope.json"
                ),
            )
            return self._record_discovery_acquisition_failure(
                dispatch=dispatch,
                discovery=discovery,
                attempt=attempt,
                route=route,
                provider_request_fingerprint=provider_request_fingerprint,
                commit_request_id=commit_request_id,
                stage_identity=stage_identity,
            )
        try:
            capability_profile(self.workspace)
        except CoreRunError as exc:
            raise RuntimeHostError(exc.code) from None
        if stage is None:
            try:
                secret_available = known_env_key_is_set(
                    discovery.credential_env,
                    self.workspace,
                )
            except WorkspaceEnvError:
                raise RuntimeHostError("workspace_secret_unsafe") from None
            if not secret_available:
                raise RuntimeHostError("source_provider_secret_unavailable")
            if not _acquisition_lock_held:
                with _exclusive_source_acquisition(self.workspace):
                    refreshed = initialize_or_open_runtime(
                        self.workspace,
                        adapter_loader=self._adapter_loader,
                    )
                    if refreshed.action != action:
                        raise RuntimeHostError("runtime_action_stale")
                    return self._apply_discovery_source_acquire(
                        refreshed,
                        action,
                        route=route,
                        replay_only=replay_only,
                        _acquisition_lock_held=True,
                    )
            dispatch = self._start_invocation_for_action(
                current,
                source_action,
                role_id="source-provider",
                request_id=invocation_request_id,
            )
            if dispatch.envelope.invocation_id != invocation_id:
                raise RuntimeHostError("control_store_integrity_invalid")
            retrieved_at = self._source_invocation_started_at(
                source_action.run_id,
                invocation_id,
            )
            try:
                collection = collect_frozen_source_pack(
                    self.workspace,
                    run_id=source_action.run_id,
                    invocation_id=invocation_id,
                    route=route,
                    retrieved_at=retrieved_at,
                )
                materials = collection.materials
                if (
                    collection.provider_response is None
                    or collection.provider_status_code != 200
                ):
                    raise RuntimeHostError("source_provider_result_invalid")
                if collection.material_validation_failed or not materials:
                    stage_members: tuple[SourceStageBytesInput, ...] = ()
                else:
                    observation = self._tavily_observation(collection.provider_response)
                    expected_pack = expected_tavily_source_pack(
                        observation,
                        run_id=source_action.run_id,
                        invocation_id=invocation_id,
                        route_fingerprint=route.route_fingerprint,
                        retrieved_at=retrieved_at,
                    )
                    manifest = expected_pack.manifest
                    proposals = expected_pack.proposals
                    stage_members = tuple(
                        SourceStageBytesInput(
                            member_id=item.proposal.source_id,
                            proposal_bytes=canonical_json_bytes(
                                item.proposal.model_dump(
                                    mode="json",
                                    exclude_unset=False,
                                )
                            ),
                            content_bytes=item.content,
                            raw_payload_bytes=item.raw_payload,
                        )
                        for item in expected_pack.sources
                    )
                stage = stage_source_pack_bytes(
                    self.workspace,
                    stage_identity=stage_identity,
                    request_fingerprint=stage_fingerprint,
                    members=stage_members,
                    provider_response_bytes=collection.provider_response,
                    provider_status_code=collection.provider_status_code,
                    stage_kind="provider_outcome",
                    capacity_profile="multi_tavily_v2",
                )
                (
                    staged_manifest,
                    staged_proposals,
                    staged_contents,
                    staged_raw_payloads,
                    staged_provider_response,
                    staged_result_count,
                    staged_durable_count,
                    staged_validation_rejected,
                ) = self._discovery_source_attempt_from_stage(
                    stage,
                    route,
                    run_id=source_action.run_id,
                    invocation_id=invocation_id,
                    retrieved_at=retrieved_at,
                )
                if (
                    staged_provider_response != collection.provider_response
                    or staged_result_count != collection.result_count
                    or staged_durable_count != collection.durable_content_count
                    or staged_validation_rejected
                    != collection.material_validation_failed
                    or (
                        stage_members
                        and (
                            staged_manifest != manifest
                            or staged_proposals != proposals
                            or staged_contents != expected_pack.contents
                            or staged_raw_payloads != expected_pack.raw_payloads
                        )
                    )
                ):
                    raise RuntimeHostError("submission_replay_conflict")
            except SearchBackendError:
                return self._record_discovery_acquisition_failure(
                    dispatch=dispatch,
                    discovery=discovery,
                    attempt=attempt,
                    route=route,
                    provider_request_fingerprint=provider_request_fingerprint,
                    commit_request_id=commit_request_id,
                    stage_identity=stage_identity,
                )
            except (
                OSError,
                NotImplementedError,
                RuntimeError,
                ValidationError,
                ValueError,
            ):
                return self._record_discovery_acquisition_failure(
                    dispatch=dispatch,
                    discovery=discovery,
                    attempt=attempt,
                    route=route,
                    provider_request_fingerprint=provider_request_fingerprint,
                    commit_request_id=commit_request_id,
                    stage_identity=stage_identity,
                )
        else:
            if recovery_envelope is None:
                raise RuntimeHostError("runtime_source_staging_invalid")
            dispatch = InvocationDispatch(
                envelope=recovery_envelope,
                envelope_path=(
                    self.workspace / "scratch" / invocation_id / "task_envelope.json"
                ),
            )
            retrieved_at = self._source_invocation_started_at(
                source_action.run_id,
                invocation_id,
            )
        try:
            (
                manifest,
                proposals,
                contents,
                raw_payloads,
                provider_response,
                result_count,
                durable_content_count,
                validation_rejected,
            ) = self._discovery_source_attempt_from_stage(
                stage,
                route,
                run_id=source_action.run_id,
                invocation_id=invocation_id,
                retrieved_at=retrieved_at,
            )
        except RuntimeHostError:
            discard_source_stage(self.workspace, stage_identity=stage_identity)
            self._record_staged_invocation_failure(
                current,
                source_action,
                request_id=invocation_request_id,
                invocation_id=invocation_id,
                reason_code="proposal_invalid",
            )
            raise RuntimeHostError("source_provider_result_invalid") from None

        if dispatch.envelope.invocation_id != invocation_id:
            raise RuntimeHostError("control_store_integrity_invalid")
        if manifest is None:
            return self._record_discovery_acquisition_failure(
                dispatch=dispatch,
                discovery=discovery,
                attempt=attempt,
                route=route,
                provider_request_fingerprint=provider_request_fingerprint,
                commit_request_id=commit_request_id,
                stage_identity=stage_identity,
                provider_response=provider_response,
                provider_status_code=stage.provider_status_code,
                result_count=result_count,
                durable_content_count=durable_content_count,
                validation_rejected=validation_rejected,
            )
        manifest_bytes = canonical_json_bytes(
            manifest.model_dump(mode="json", exclude_unset=False)
        )
        intake_input = _CoreDiscoverySourcePack(
            request_id=commit_request_id,
            run_id=source_action.run_id,
            invocation_id=invocation_id,
            attempt_authorization_id=attempt.attempt_authorization_id,
            attempt_ordinal=attempt.attempt_ordinal,
            provider_request_fingerprint=provider_request_fingerprint,
            expected_store_revision=dispatch.envelope.store_revision,
            manifest=manifest,
            source_manifest_sha256=sha256_hex(manifest_bytes),
            proposals=proposals,
            contents=contents,
            raw_payloads=raw_payloads,
            provider_response=provider_response,
        )
        intake = IntakeService(self.workspace)
        try:
            result = intake._commit_discovery_source_pack_from_core(intake_input)
        except ControlStoreCommitOutcomeUnknown:
            try:
                result = intake._commit_discovery_source_pack_from_core(intake_input)
            except ControlStoreCommitOutcomeUnknown as exc:
                raise RuntimeHostError("commit_outcome_unknown") from exc
        except IntakeError as exc:
            if exc.code == "source_discovery_authorization_invalid":
                raise RuntimeHostError(exc.code) from None
            if exc.code not in {
                "source_pack_empty",
                "source_provider_result_invalid",
            }:
                raise RuntimeHostError("source_provider_result_invalid") from None
            return self._record_discovery_acquisition_failure(
                dispatch=dispatch,
                discovery=discovery,
                attempt=attempt,
                route=route,
                provider_request_fingerprint=provider_request_fingerprint,
                commit_request_id=commit_request_id,
                stage_identity=stage_identity,
                provider_response=provider_response,
                provider_status_code=stage.provider_status_code,
                result_count=result_count,
                durable_content_count=durable_content_count,
                validation_rejected=(
                    validation_rejected or exc.code == "source_provider_result_invalid"
                ),
                manifest=(
                    None if exc.code == "source_provider_result_invalid" else manifest
                ),
                source_manifest_sha256=(
                    None
                    if exc.code == "source_provider_result_invalid"
                    else sha256_hex(manifest_bytes)
                ),
                proposals=(
                    () if exc.code == "source_provider_result_invalid" else proposals
                ),
                contents=(
                    () if exc.code == "source_provider_result_invalid" else contents
                ),
                raw_payloads=(
                    () if exc.code == "source_provider_result_invalid" else raw_payloads
                ),
            )
        if result.status == "commit_outcome_unknown":
            result = intake._commit_discovery_source_pack_from_core(intake_input)
        runtime_result = self._source_pack_runtime_result(invocation_id, result)
        discard_source_stage(self.workspace, stage_identity=stage_identity)
        return runtime_result

    @staticmethod
    def _freeze_discovery_source_manifest(materials):
        ordered_materials = tuple(
            sorted(
                materials,
                key=lambda item: item.proposal.source_id,
            )
        )
        manifest, proposals = RuntimeHostService._discovery_manifest_from_proposals(
            tuple(item.proposal for item in ordered_materials)
        )
        return manifest, proposals, ordered_materials

    def _source_invocation_started_at(
        self,
        run_id: str,
        invocation_id: str,
    ) -> str:
        try:
            with SQLiteControlStore.open(self.workspace / "briefloop.db") as store:
                snapshot = store.load_snapshot(run_id)
        except ControlStoreError as exc:
            raise RuntimeHostError("control_store_integrity_invalid") from exc
        matches = [
            item
            for item in snapshot.invocations
            if item.invocation_id == invocation_id
            and item.run_id == run_id
            and item.role_id == "source-provider"
        ]
        if len(matches) != 1:
            raise RuntimeHostError("control_store_integrity_invalid")
        return matches[0].started_at

    @staticmethod
    def _discovery_manifest_from_proposals(proposals):
        members: list[ExecutionSourceManifestMember] = []
        for proposal in proposals:
            members.append(
                ExecutionSourceManifestMember.model_validate(
                    {
                        "source_id": proposal.source_id,
                        "input_path": (f"input/discovered/{proposal.source_id}.txt"),
                        "content_sha256": proposal.content_sha256,
                        "content_media_type": proposal.content_media_type,
                        "origin_type": proposal.origin_type,
                        "acquisition_method": proposal.acquisition_method,
                        "material_kind": proposal.material_kind,
                        "provider": proposal.provider,
                        "locator": proposal.locator.model_dump(mode="json"),
                        "title": proposal.title,
                        "publisher": proposal.publisher,
                        "published_at": proposal.published_at,
                        "retrieved_at": proposal.retrieved_at,
                        "source_category": proposal.source_category,
                        "retrieval_source_type": (proposal.retrieval_source_type),
                        "underlying_evidence_type": (proposal.underlying_evidence_type),
                        "raw_underlying_evidence_type": (
                            proposal.raw_underlying_evidence_type
                        ),
                        "document_kind": proposal.document_kind,
                        "opened_at": proposal.opened_at,
                        "resolved_at": proposal.resolved_at,
                    },
                    strict=True,
                )
            )
        manifest = ExecutionSourceManifest.model_validate(
            {
                "schema_version": ExecutionSourceManifest.schema_id,
                "members": [
                    item.model_dump(mode="json", exclude_unset=False)
                    for item in members
                ],
            },
            strict=True,
        )
        manifest_sha256 = sha256_hex(
            canonical_json_bytes(manifest.model_dump(mode="json", exclude_unset=False))
        )
        by_source_id = {item.source_id: item for item in proposals}
        rebound = tuple(
            SourceProposal.model_validate(
                {
                    **by_source_id[member.source_id].model_dump(
                        mode="json",
                        exclude_unset=False,
                    ),
                    "source_manifest_sha256": manifest_sha256,
                    "manifest_local_file": member.input_path,
                },
                strict=True,
            )
            for member in manifest.members
        )
        return manifest, rebound

    @staticmethod
    def _discovery_source_attempt_from_stage(
        stage: VerifiedSourceStage,
        route: RuntimeSourceRouteBinding,
        *,
        run_id: str,
        invocation_id: str,
        retrieved_at: str,
    ):
        if (
            stage.provider_response_bytes is None
            or stage.provider_response_sha256 is None
            or stage.provider_status_code != 200
        ):
            raise RuntimeHostError("runtime_source_staging_invalid")
        provider_response = stage.provider_response_bytes
        observation = RuntimeHostService._tavily_observation(provider_response)
        spec = route.acquisition_spec
        if (
            route.provider_id != "tavily"
            or not isinstance(spec, RuntimeWebSearchAcquisitionSpecV3)
            or not tavily_observation_matches_spec(observation, spec)
        ):
            raise RuntimeHostError("runtime_source_staging_invalid")
        result_count = observation.result_count
        durable_content_count = observation.durable_content_count
        if not stage.members:
            return (
                None,
                (),
                (),
                (),
                provider_response,
                result_count,
                durable_content_count,
                durable_content_count > 0,
            )
        expected_pack = expected_tavily_source_pack(
            observation,
            run_id=run_id,
            invocation_id=invocation_id,
            route_fingerprint=route.route_fingerprint,
            retrieved_at=retrieved_at,
        )
        proposals: list[SourceProposal] = []
        contents: list[bytes] = []
        raw_payloads: list[bytes] = []
        for member in stage.members:
            proposal = SourceProposal.model_validate_json(
                member.proposal_bytes,
                strict=True,
            )
            if (
                proposal.source_id != member.member_id
                or member.raw_payload_bytes is None
                or member.raw_payload_sha256 is None
            ):
                raise RuntimeHostError("runtime_source_staging_invalid")
            proposals.append(proposal)
            contents.append(member.content_bytes)
            raw_payloads.append(member.raw_payload_bytes)
        if (
            tuple(proposals) != expected_pack.proposals
            or tuple(contents) != expected_pack.contents
            or tuple(raw_payloads) != expected_pack.raw_payloads
            or len(proposals) != durable_content_count
        ):
            raise RuntimeHostError("runtime_source_staging_invalid")
        return (
            expected_pack.manifest,
            expected_pack.proposals,
            expected_pack.contents,
            expected_pack.raw_payloads,
            provider_response,
            result_count,
            durable_content_count,
            False,
        )

    @staticmethod
    def _tavily_response_counts(payload: bytes) -> tuple[int, int]:
        observation = RuntimeHostService._tavily_observation(payload)
        return observation.result_count, observation.durable_content_count

    @staticmethod
    def _tavily_observation(
        payload: bytes,
    ) -> TavilyAcquisitionObservation | TavilyMultiAcquisitionObservation:
        try:
            return parse_tavily_acquisition_bundle(payload)
        except TavilyAcquisitionBundleError as exc:
            raise RuntimeHostError("runtime_source_staging_invalid") from exc

    def _record_discovery_acquisition_failure(
        self,
        *,
        dispatch: InvocationDispatch,
        discovery,
        attempt,
        route,
        provider_request_fingerprint: str,
        commit_request_id: str,
        stage_identity: str,
        provider_response: bytes | None = None,
        provider_status_code: int | None = None,
        result_count: int | None = None,
        durable_content_count: int | None = None,
        validation_rejected: bool = False,
        manifest: ExecutionSourceManifest | None = None,
        source_manifest_sha256: str | None = None,
        proposals: tuple[SourceProposal, ...] = (),
        contents: tuple[bytes, ...] = (),
        raw_payloads: tuple[bytes, ...] = (),
    ) -> RuntimeInvocationResult:
        failure_input = _CoreDiscoveryFailureAttempt(
            request_id=commit_request_id,
            run_id=dispatch.envelope.run_id,
            invocation_id=dispatch.envelope.invocation_id,
            attempt_authorization_id=attempt.attempt_authorization_id,
            attempt_ordinal=attempt.attempt_ordinal,
            expected_store_revision=dispatch.envelope.store_revision,
            discovery_authorization_id=discovery.authorization_id,
            provider_id=discovery.provider_id,
            route_fingerprint=route.route_fingerprint,
            provider_request_fingerprint=provider_request_fingerprint,
            provider_response=provider_response,
            provider_status_code=provider_status_code,
            result_count=result_count,
            durable_content_count=durable_content_count,
            validation_rejected=validation_rejected,
            manifest=manifest,
            source_manifest_sha256=source_manifest_sha256,
            proposals=proposals,
            contents=contents,
            raw_payloads=raw_payloads,
        )
        intake = IntakeService(self.workspace)
        try:
            result = intake._record_discovery_acquisition_failure_from_core(
                failure_input
            )
        except ControlStoreCommitOutcomeUnknown:
            try:
                result = intake._record_discovery_acquisition_failure_from_core(
                    failure_input
                )
            except ControlStoreCommitOutcomeUnknown as exc:
                raise RuntimeHostError("commit_outcome_unknown") from exc
        runtime_result = self._source_pack_runtime_result(
            dispatch.envelope.invocation_id,
            result,
        )
        discard_source_stage(self.workspace, stage_identity=stage_identity)
        return runtime_result

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
        _, invocation_id = self._planned_invocation(
            current,
            action,
            request_id=invocation_request_id,
        )
        commit_request_id = derived_id(
            "REQ-HOST-HUMAN-SOURCE-PACK-COMMIT",
            request.request_id,
            action.action_fingerprint,
            request_fingerprint,
        )
        replay = self._source_pack_store_replay(
            current,
            invocation_id=invocation_id,
            commit_request_id=commit_request_id,
        )
        if replay is not None:
            return replay
        invocation = self._invocation_for_id(current, invocation_id)
        if replay_only and invocation is None:
            raise RuntimeHostError("runtime_action_stale")
        stage_identity = canonical_fingerprint(
            {
                "kind": "human_source_pack",
                "run_id": action.run_id,
                "request_id": request.request_id,
                "action_fingerprint": action.action_fingerprint,
            }
        )
        try:
            stage = load_source_stage(
                self.workspace,
                stage_identity=stage_identity,
                request_fingerprint=request_fingerprint,
                expected_manifest_sha256=request.expected_manifest_sha256,
            )
        except RuntimeHostError as exc:
            if str(exc) == "submission_replay_conflict":
                raise
            if invocation is not None and invocation.status == "active":
                return self._record_staged_invocation_failure(
                    current,
                    action,
                    request_id=invocation_request_id,
                    invocation_id=invocation_id,
                )
            raise RuntimeHostError("runtime_human_request_invalid") from exc
        if invocation is not None and invocation.status == "active" and stage is None:
            return self._record_staged_invocation_failure(
                current,
                action,
                request_id=invocation_request_id,
                invocation_id=invocation_id,
            )
        if stage is None:
            manifest_bytes = self._read_workspace_input_bytes(
                request.manifest_path,
                request.expected_manifest_sha256,
                max_size=4 * 1024 * 1024,
            )
            manifest_entries = _frozen_manifest_entries(
                manifest_bytes,
                request.manifest_schema_version,
            )
            if [item.source_id for item in manifest_entries] != [
                item.member_id for item in request.members
            ]:
                raise RuntimeHostError("runtime_human_request_invalid")
            staged_inputs: list[HumanSourceStageInput] = []
            for member, entry in zip(
                request.members,
                manifest_entries,
                strict=True,
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
                proposal = SourceProposal.model_validate(
                    {
                        "schema_version": SourceProposal.schema_id,
                        "proposal_id": derived_id(
                            "PROP-SOURCE-HUMAN-PACK",
                            invocation_id,
                            entry.source_id,
                        ),
                        "run_id": action.run_id,
                        "source_id": entry.source_id,
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
                staged_inputs.append(
                    HumanSourceStageInput(
                        member_id=member.member_id,
                        input_path=member.input_path,
                        expected_content_sha256=member.expected_input_sha256,
                        proposal_bytes=canonical_json_bytes(
                            proposal.model_dump(mode="json", exclude_unset=False)
                        ),
                    )
                )
            try:
                stage = stage_human_source_pack(
                    self.workspace,
                    stage_identity=stage_identity,
                    request_fingerprint=request_fingerprint,
                    manifest_bytes=manifest_bytes,
                    expected_manifest_sha256=request.expected_manifest_sha256,
                    members=tuple(staged_inputs),
                )
            except RuntimeHostError as exc:
                if str(exc) == "submission_replay_conflict":
                    raise
                raise RuntimeHostError("runtime_human_request_invalid") from exc
        dispatch = self._start_invocation_for_action(
            current,
            action,
            role_id="source-provider",
            request_id=invocation_request_id,
        )
        if dispatch.envelope.invocation_id != invocation_id:
            raise RuntimeHostError("control_store_integrity_invalid")
        return self._submit_staged_source_pack(
            dispatch,
            stage,
            commit_request_id=commit_request_id,
            stage_identity=stage_identity,
            human_authorized=True,
        )

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
