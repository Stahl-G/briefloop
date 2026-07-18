"""Pure recovery legality for dormant fresh-v2 historical verification."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import os
from pathlib import Path
from typing import Callable

from multi_agent_brief.contracts.v2 import (
    ArtifactRecord,
    ArtifactRevertRequest,
    ArtifactRevision,
    ArtifactSupersedeRequest,
    ArtifactSupersessionRecord,
    ArtifactRevisionReference,
    OwnedArtifactSubmissionRecord,
    RecoveryCompletionRecord,
    RecoveryCompleteRequest,
    RepairCompletionRecord,
    RepairCompleteRequest,
    RepairCycleRecord,
    RepairStartRequest,
    RunHeadTransitionRecord,
    RunIdentity,
    RunIntegrityRecord,
    RunResetRequest,
    CoreRunEventBinding,
    EventEnvelope,
    StageState,
    StageTransitionRecord,
    WorkspaceRunHead,
)
from multi_agent_brief.control_store import ControlStoreError, SQLiteControlStore
from multi_agent_brief.control_store.serialization import (
    canonical_fingerprint,
    canonical_json_bytes,
    sha256_hex,
)
from multi_agent_brief.control_store.sqlite_store import ControlStoreSnapshot
from multi_agent_brief.intake_v2.errors import IntakeError
from multi_agent_brief.intake_v2.scratch import ScratchReader

from .errors import CoreRunError


@dataclass(frozen=True)
class ReopenedArtifactEpoch:
    """Immutable identities derived for one exact post-repair rerun epoch."""

    repair_completion_id: str
    supersession_id: str
    reopen_transition_id: str


class CoreEffect(str, Enum):
    INITIALIZE = "initialize"
    INVOCATION_START = "invocation_start"
    SOURCE_INTAKE = "source_intake"
    PROPOSAL_INTAKE = "proposal_intake"
    INTAKE_REJECTION = "intake_rejection"
    OWNED_ARTIFACT_ACCEPT = "owned_artifact_accept"
    AUDIT_PROPOSAL_PROMOTE = "audit_proposal_promote"
    CLAIM_FREEZE = "claim_freeze"
    GATE_EVALUATE = "gate_evaluate"
    STAGE_COMPLETE = "stage_complete"
    INTEGRITY_CONTAMINATION = "integrity_contamination"
    REPAIR_START = "repair_start"
    ARTIFACT_SUPERSEDE = "artifact_supersede"
    ARTIFACT_REVERT = "artifact_revert"
    REPAIR_COMPLETE = "repair_complete"
    RECOVERY_COMPLETE = "recovery_complete"
    RUN_RESET = "run_reset"
    FINALIZE_RENDER = "finalize_render"
    FINALIZE_GATE = "finalize_gate"
    FINALIZE_COMPLETE = "finalize_complete"
    INTERNAL_APPROVAL = "internal_approval"
    DELIVERY_AUTHORIZE = "delivery_authorize"
    DELIVERY_ATTEMPT = "delivery_attempt"
    DELIVERY_RESULT = "delivery_result"


@dataclass(frozen=True)
class CoreEffectSubject:
    contamination_revision: int | None = None
    stage_id: str | None = None
    artifact_id: str | None = None
    repair_id: str | None = None
    repair_completion_id: str | None = None


@dataclass(frozen=True)
class EffectAuthorization:
    decision: str
    recovery_state: str
    contamination_revision: int | None = None
    repair_id: str | None = None
    repair_completion_id: str | None = None
    recovery_id: str | None = None
    reopened_epoch: ReopenedArtifactEpoch | None = None
    reason_code: str = "recovery_effect_allowed"

    def require_allowed(self) -> "EffectAuthorization":
        if self.decision != "allow":
            raise CoreRunError(self.reason_code)
        return self


@dataclass(frozen=True)
class RecoveryLegality:
    """Pure recovery legality derived from one verified Store snapshot."""

    state: str
    latest_contamination_revision: int | None = None
    repair_id: str | None = None
    permitted_artifact_ids: tuple[str, ...] = ()
    repair_completion_id: str | None = None
    recovery_id: str | None = None
    required_rerun_transition_ids: tuple[str, ...] = ()
    required_gate_evaluation_ids: tuple[str, ...] = ()
    disposition: str | None = None

    @property
    def ordinary_consumption_eligible(self) -> bool:
        return self.state in {"not_required", "recovered_current"}


def classify_recovery_legality(snapshot: ControlStoreSnapshot) -> RecoveryLegality:
    """Replay exact per-contamination repair and recovery closure without I/O."""

    tx_revision = {
        item.transaction_id: item.committed_revision for item in snapshot.transactions
    }
    repairs_by_epoch: dict[int, list[RepairCycleRecord]] = {}
    repairs_by_id: dict[str, list[RepairCycleRecord]] = {}
    for repair in snapshot.repair_cycles:
        repairs_by_epoch.setdefault(repair.contamination_revision, []).append(repair)
        repairs_by_id.setdefault(repair.repair_id, []).append(repair)
    for supersession in snapshot.artifact_supersessions:
        repairs = repairs_by_id.get(supersession.repair_id, [])
        artifact_id = supersession.prior_artifact.artifact_id
        if (
            len(repairs) != 1
            or artifact_id != supersession.successor_artifact.artifact_id
            or artifact_id not in repairs[0].permitted_artifact_ids
        ):
            return RecoveryLegality("invalid")

    contaminations = sorted(
        (
            item
            for item in snapshot.run_integrity_records
            if item.status == "contaminated"
        ),
        key=lambda item: (
            item.integrity_revision,
            tx_revision.get(item.accepted_transaction_id, -1),
        ),
    )
    if not contaminations:
        if (
            snapshot.repair_cycles
            or snapshot.repair_completions
            or snapshot.recovery_completions
        ):
            return RecoveryLegality("invalid")
        return RecoveryLegality("not_required")
    if len({item.integrity_revision for item in contaminations}) != len(contaminations):
        return RecoveryLegality("invalid")

    completions_by_repair: dict[str, list[RepairCompletionRecord]] = {}
    for completion in snapshot.repair_completions:
        completions_by_repair.setdefault(completion.repair_id, []).append(completion)
    recoveries_by_completion: dict[str, list[RecoveryCompletionRecord]] = {}
    for recovery in snapshot.recovery_completions:
        recoveries_by_completion.setdefault(recovery.repair_completion_id, []).append(
            recovery
        )

    epochs: list[RecoveryLegality] = []
    contamination_ids = {item.integrity_revision for item in contaminations}
    if set(repairs_by_epoch) - contamination_ids:
        return RecoveryLegality("invalid")
    for contamination in contaminations:
        repairs = repairs_by_epoch.get(contamination.integrity_revision, [])
        if len(repairs) > 1:
            return RecoveryLegality("invalid")
        if not repairs:
            epochs.append(
                RecoveryLegality(
                    "blocked",
                    latest_contamination_revision=contamination.integrity_revision,
                )
            )
            continue
        repair = repairs[0]
        completions = completions_by_repair.get(repair.repair_id, [])
        if len(completions) > 1:
            return RecoveryLegality("invalid")
        if not completions:
            epochs.append(
                RecoveryLegality(
                    "active_repair",
                    latest_contamination_revision=contamination.integrity_revision,
                    repair_id=repair.repair_id,
                    permitted_artifact_ids=tuple(repair.permitted_artifact_ids),
                )
            )
            continue
        completion = completions[0]
        if completion.contamination_revision != contamination.integrity_revision or set(
            completion.supersession_ids
        ) != {
            item.supersession_id
            for item in snapshot.artifact_supersessions
            if item.repair_id == repair.repair_id
        }:
            return RecoveryLegality("invalid")
        recoveries = recoveries_by_completion.get(completion.repair_completion_id, [])
        if len(recoveries) > 1:
            return RecoveryLegality("invalid")
        cutoff = (
            None
            if not recoveries
            else tx_revision.get(recoveries[0].accepted_transaction_id)
        )
        required = _required_recovery_relations(
            snapshot,
            completion,
            tx_revision,
            cutoff_revision=cutoff,
        )
        if required is None:
            return RecoveryLegality("invalid")
        rerun_ids, gate_ids = required
        if not recoveries:
            epochs.append(
                RecoveryLegality(
                    "rerun_required",
                    latest_contamination_revision=contamination.integrity_revision,
                    repair_id=repair.repair_id,
                    permitted_artifact_ids=tuple(repair.permitted_artifact_ids),
                    repair_completion_id=completion.repair_completion_id,
                    required_rerun_transition_ids=rerun_ids,
                    required_gate_evaluation_ids=gate_ids,
                )
            )
            continue
        recovery = recoveries[0]
        if (
            recovery.contamination_revision != contamination.integrity_revision
            or tuple(sorted(recovery.supersession_ids))
            != tuple(sorted(completion.supersession_ids))
            or tuple(sorted(recovery.rerun_transition_ids)) != rerun_ids
            or tuple(sorted(recovery.gate_evaluation_ids)) != gate_ids
            or recovery.disposition != "recovered_non_reference"
        ):
            return RecoveryLegality("invalid")
        epochs.append(
            RecoveryLegality(
                "recovered_current",
                latest_contamination_revision=contamination.integrity_revision,
                repair_id=repair.repair_id,
                permitted_artifact_ids=tuple(repair.permitted_artifact_ids),
                repair_completion_id=completion.repair_completion_id,
                recovery_id=recovery.recovery_id,
                required_rerun_transition_ids=rerun_ids,
                required_gate_evaluation_ids=gate_ids,
                disposition="recovered_non_reference",
            )
        )

    known_repairs = {item.repair_id for item in snapshot.repair_cycles}
    known_completions = {
        item.repair_completion_id for item in snapshot.repair_completions
    }
    if (
        set(completions_by_repair) - known_repairs
        or set(recoveries_by_completion) - known_completions
    ):
        return RecoveryLegality("invalid")
    for index, epoch in enumerate(epochs):
        if epoch.state == "recovered_current":
            continue
        if any(
            repairs_by_epoch.get(item.integrity_revision)
            for item in contaminations[index + 1 :]
        ):
            return RecoveryLegality("invalid")
        return epoch
    return epochs[-1]


def _required_recovery_relations(
    snapshot: ControlStoreSnapshot,
    completion: RepairCompletionRecord,
    tx_revision: dict[str, int],
    *,
    cutoff_revision: int | None = None,
) -> tuple[tuple[str, ...], tuple[str, ...]] | None:
    completion_revision = tx_revision.get(completion.accepted_transaction_id)
    if completion_revision is None:
        return None
    reopened = [
        item
        for item in snapshot.stage_transitions
        if item.transition_id in completion.reopened_transition_ids
    ]
    if (
        len(reopened) != len(completion.reopened_transition_ids)
        or any(
            item.transition_kind != "repair_reopen"
            or item.accepted_transaction_id != completion.accepted_transaction_id
            for item in reopened
        )
        or len({item.stage_id for item in reopened}) != len(reopened)
    ):
        return None
    owner_stages = {
        submission.owner_stage_id
        for supersession in snapshot.artifact_supersessions
        if supersession.supersession_id in completion.supersession_ids
        for submission in snapshot.owned_artifact_submissions
        if submission.artifact_id == supersession.successor_artifact.artifact_id
        and submission.artifact_revision == supersession.successor_artifact.revision
    }
    required_reopens = [
        item
        for item in reopened
        if item.stage_id in owner_stages or item.prior_status in {"complete", "skipped"}
    ]
    selected = []
    for reopen in required_reopens:
        later = [
            item
            for item in snapshot.stage_transitions
            if item.stage_id == reopen.stage_id
            and item.transition_kind != "repair_reopen"
            and tx_revision.get(item.accepted_transaction_id, -1) > completion_revision
            and (
                cutoff_revision is None
                or tx_revision.get(item.accepted_transaction_id, -1) < cutoff_revision
            )
        ]
        if not later:
            return ((), ())
        current = max(later, key=lambda item: tx_revision[item.accepted_transaction_id])
        if current.result_status not in {"complete", "skipped"}:
            return None
        selected.append(current)
    transition_ids = tuple(sorted(item.transition_id for item in selected))
    selected_by_id = set(transition_ids)
    gate_ids = tuple(
        sorted(
            item.evaluation_id
            for item in snapshot.stage_gate_bindings
            if item.transition_id in selected_by_id
            and (
                cutoff_revision is None
                or tx_revision.get(item.accepted_transaction_id, -1) < cutoff_revision
            )
        )
    )
    evaluations = {item.evaluation_id: item for item in snapshot.gate_evaluations}
    if any(
        gate_id not in evaluations or evaluations[gate_id].blocking
        for gate_id in gate_ids
    ):
        return None
    return transition_ids, gate_ids


def recovery_stage_rerun_permitted(
    snapshot: ControlStoreSnapshot,
    stage_id: str,
) -> bool:
    """Return the narrow pending-recovery permission for one reopened stage."""

    legality = classify_recovery_legality(snapshot)
    if legality.state != "rerun_required" or legality.repair_completion_id is None:
        return False
    completion = next(
        item
        for item in snapshot.repair_completions
        if item.repair_completion_id == legality.repair_completion_id
    )
    reopened = [
        item
        for item in snapshot.stage_transitions
        if item.transition_id in completion.reopened_transition_ids
        and item.stage_id == stage_id
    ]
    return len(reopened) == 1


def classify_effect_authorization(
    snapshot: ControlStoreSnapshot,
    effect: CoreEffect,
    subject: CoreEffectSubject = CoreEffectSubject(),
) -> EffectAuthorization:
    """Authorize one proposed effect from the immutable state before it."""

    legality = classify_recovery_legality(snapshot)
    common = {
        "recovery_state": legality.state,
        "contamination_revision": legality.latest_contamination_revision,
        "repair_id": legality.repair_id,
        "repair_completion_id": legality.repair_completion_id,
        "recovery_id": legality.recovery_id,
    }
    # Reset is the explicit escape hatch. Integrity contamination is the
    # fail-closed observation effect and must remain recordable even when all
    # ordinary consumption is blocked. Initialization is authorized only by
    # the verifier's revision-zero genesis rule, never by an existing prefix.
    if effect in {
        CoreEffect.RUN_RESET,
        CoreEffect.INTEGRITY_CONTAMINATION,
        CoreEffect.INTAKE_REJECTION,
    }:
        return EffectAuthorization("allow", **common)
    if effect is CoreEffect.INITIALIZE:
        return EffectAuthorization(
            "deny",
            reason_code="recovery_state_invalid",
            **common,
        )
    if legality.state == "invalid":
        return EffectAuthorization(
            "invalid",
            reason_code="recovery_state_invalid",
            **common,
        )
    recovery_only = {
        CoreEffect.REPAIR_START,
        CoreEffect.ARTIFACT_SUPERSEDE,
        CoreEffect.ARTIFACT_REVERT,
        CoreEffect.REPAIR_COMPLETE,
        CoreEffect.RECOVERY_COMPLETE,
    }
    if legality.state in {"not_required", "recovered_current"}:
        if effect in recovery_only:
            return EffectAuthorization(
                "deny",
                reason_code="recovery_state_invalid",
                **common,
            )
        return EffectAuthorization("allow", **common)
    if legality.state == "blocked":
        allowed = (
            effect is CoreEffect.REPAIR_START
            and subject.contamination_revision == legality.latest_contamination_revision
        )
        return EffectAuthorization(
            "allow" if allowed else "deny",
            reason_code=(
                "recovery_effect_allowed" if allowed else "recovery_state_invalid"
            ),
            **common,
        )
    if legality.state == "active_repair":
        allowed = subject.repair_id == legality.repair_id and (
            effect is CoreEffect.REPAIR_COMPLETE
            or (
                effect
                in {
                    CoreEffect.ARTIFACT_SUPERSEDE,
                    CoreEffect.ARTIFACT_REVERT,
                }
                and subject.artifact_id is not None
                and subject.artifact_id in legality.permitted_artifact_ids
            )
        )
        return EffectAuthorization(
            "allow" if allowed else "deny",
            reason_code=(
                "recovery_effect_allowed" if allowed else "recovery_state_invalid"
            ),
            **common,
        )
    if legality.state != "rerun_required" or legality.repair_completion_id is None:
        return EffectAuthorization(
            "invalid",
            reason_code="recovery_state_invalid",
            **common,
        )
    if effect is CoreEffect.RECOVERY_COMPLETE:
        allowed = subject.repair_completion_id == legality.repair_completion_id
        return EffectAuthorization(
            "allow" if allowed else "deny",
            reason_code=(
                "recovery_effect_allowed" if allowed else "recovery_state_invalid"
            ),
            **common,
        )
    completion = next(
        (
            item
            for item in snapshot.repair_completions
            if item.repair_completion_id == legality.repair_completion_id
        ),
        None,
    )
    if completion is None:
        return EffectAuthorization(
            "invalid",
            reason_code="recovery_state_invalid",
            **common,
        )
    reopens = [
        item
        for item in snapshot.stage_transitions
        if item.transition_id in completion.reopened_transition_ids
        and item.transition_kind == "repair_reopen"
        and item.stage_id == subject.stage_id
    ]
    allowed = (
        effect
        in {
            CoreEffect.INVOCATION_START,
            CoreEffect.SOURCE_INTAKE,
            CoreEffect.PROPOSAL_INTAKE,
            CoreEffect.OWNED_ARTIFACT_ACCEPT,
            CoreEffect.AUDIT_PROPOSAL_PROMOTE,
            CoreEffect.GATE_EVALUATE,
            CoreEffect.STAGE_COMPLETE,
            CoreEffect.FINALIZE_GATE,
        }
        and len(reopens) == 1
    )
    supersessions = [
        item
        for item in snapshot.artifact_supersessions
        if item.supersession_id in completion.supersession_ids
    ]
    epoch = (
        ReopenedArtifactEpoch(
            completion.repair_completion_id,
            supersessions[0].supersession_id,
            reopens[0].transition_id,
        )
        if allowed and supersessions
        else None
    )
    return EffectAuthorization(
        "allow" if allowed else "deny",
        reopened_epoch=epoch,
        reason_code=(
            "recovery_effect_allowed" if allowed else "recovery_state_invalid"
        ),
        **common,
    )


def require_reopened_artifact_epoch(
    snapshot: ControlStoreSnapshot,
    *,
    artifact_id: str,
    stage_id: str,
    invocation_id: str,
) -> ReopenedArtifactEpoch:
    """Classify the sole legal protected-current artifact rerun epoch."""

    artifacts = [item for item in snapshot.artifacts if item.artifact_id == artifact_id]
    if len(artifacts) != 1 or artifacts[0].current_revision <= 0:
        raise CoreRunError("artifact_revision_conflict")
    current_revision = artifacts[0].current_revision
    supersessions = [
        item
        for item in snapshot.artifact_supersessions
        if item.successor_artifact.artifact_id == artifact_id
        and item.successor_artifact.revision == current_revision
    ]
    if len(supersessions) != 1:
        raise CoreRunError("artifact_revision_conflict")
    supersession = supersessions[0]
    completions = [
        item
        for item in snapshot.repair_completions
        if item.repair_id == supersession.repair_id
        and supersession.supersession_id in item.supersession_ids
    ]
    if len(completions) != 1:
        raise CoreRunError("artifact_revision_conflict")
    completion = completions[0]
    reopens = [
        item
        for item in snapshot.stage_transitions
        if item.transition_id in completion.reopened_transition_ids
        and item.transition_kind == "repair_reopen"
        and item.stage_id == stage_id
    ]
    if len(reopens) != 1:
        raise CoreRunError("artifact_revision_conflict")
    reopen = reopens[0]
    transactions = {
        item.transaction_id: item.committed_revision for item in snapshot.transactions
    }
    stage_transitions = [
        item for item in snapshot.stage_transitions if item.stage_id == stage_id
    ]
    if (
        not stage_transitions
        or max(
            stage_transitions,
            key=lambda item: transactions.get(item.accepted_transaction_id, -1),
        ).transition_id
        != reopen.transition_id
    ):
        raise CoreRunError("artifact_revision_conflict")
    states = [item for item in snapshot.stage_states if item.stage_id == stage_id]
    if (
        len(states) != 1
        or states[0].status != "ready"
        or states[0].revision != reopen.result_revision
    ):
        raise CoreRunError("artifact_revision_conflict")
    completed_repairs = {item.repair_id for item in snapshot.repair_completions}
    if (
        any(item.repair_id not in completed_repairs for item in snapshot.repair_cycles)
        or any(
            item.repair_completion_id == completion.repair_completion_id
            for item in snapshot.recovery_completions
        )
        or snapshot.finalizations
        or snapshot.package_ready_records
    ):
        raise CoreRunError("artifact_revision_conflict")
    invocation = next(
        (item for item in snapshot.invocations if item.invocation_id == invocation_id),
        None,
    )
    invocation_starts = {
        item.core_run_binding.primary_record_id: item
        for item in snapshot.events
        if item.core_run_binding is not None
        and item.core_run_binding.effect_kind == "invocation_start"
    }
    start_event = invocation_starts.get(invocation_id)
    active_for_stage = [
        item
        for item in snapshot.invocations
        if item.status == "active"
        and invocation_starts.get(item.invocation_id) is not None
        and invocation_starts[item.invocation_id].stage_id == stage_id
    ]
    completion_revision = transactions.get(completion.accepted_transaction_id)
    start_revision = (
        None if start_event is None else transactions.get(start_event.transaction_id)
    )
    if (
        invocation is None
        or invocation.status != "active"
        or start_event is None
        or start_event.stage_id != stage_id
        or len(active_for_stage) != 1
        or active_for_stage[0].invocation_id != invocation_id
        or completion_revision is None
        or start_revision is None
        or start_revision <= completion_revision
    ):
        raise CoreRunError("artifact_revision_conflict")
    return ReopenedArtifactEpoch(
        repair_completion_id=completion.repair_completion_id,
        supersession_id=supersession.supersession_id,
        reopen_transition_id=reopen.transition_id,
    )


_Clock = Callable[[], datetime]


class CoreRunRecoveryService:
    """Typed deterministic recovery transactions over one verified snapshot."""

    def __init__(
        self,
        workspace: str | os.PathLike[str],
        *,
        clock: _Clock | None = None,
    ) -> None:
        try:
            self.workspace = Path(workspace).expanduser().resolve(strict=True)
            if not self.workspace.is_dir():
                raise ValueError
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            raise CoreRunError("core_run_request_invalid") from exc
        try:
            self._reader = ScratchReader(self.workspace)
        except IntakeError as exc:
            raise CoreRunError("core_run_request_invalid") from exc
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def start_repair(self, request: RepairStartRequest):
        from .errors import core_run_failure_result

        try:
            return self._start_repair(request)
        except (CoreRunError, ControlStoreError) as exc:
            return core_run_failure_result(exc)

    def supersede_artifact(self, request: ArtifactSupersedeRequest):
        from .errors import core_run_failure_result

        try:
            return self._supersede_artifact(request)
        except (CoreRunError, ControlStoreError) as exc:
            return core_run_failure_result(exc)

    def revert_artifact(self, request: ArtifactRevertRequest):
        from .errors import core_run_failure_result

        try:
            return self._revert_artifact(request)
        except (CoreRunError, ControlStoreError) as exc:
            return core_run_failure_result(exc)

    def complete_repair(self, request: RepairCompleteRequest):
        from .errors import core_run_failure_result

        try:
            return self._complete_repair(request)
        except (CoreRunError, ControlStoreError) as exc:
            return core_run_failure_result(exc)

    def complete_recovery(self, request: RecoveryCompleteRequest):
        from .errors import core_run_failure_result

        try:
            return self._complete_recovery(request)
        except (CoreRunError, ControlStoreError) as exc:
            return core_run_failure_result(exc)

    def reset_run(self, request: RunResetRequest):
        from .errors import core_run_failure_result

        try:
            return self._reset_run(request)
        except (CoreRunError, ControlStoreError) as exc:
            return core_run_failure_result(exc)

    def _start_repair(self, request: RepairStartRequest):
        from .checkout import prepare_checkout_effect, stage_checkout_effect
        from .errors import CoreRunResult
        from .policy import derived_id, transaction_type_for
        from .verifier import CoreRunDomainVerifier, resolve_core_replay

        fingerprint = canonical_fingerprint(
            request.model_dump(mode="json", exclude_unset=False)
        )
        with SQLiteControlStore.open(
            self.workspace / "briefloop.db",
            clock=self._clock,
        ) as store:
            replay = resolve_core_replay(
                store,
                run_id=request.run_id,
                request_id=request.request_id,
                request_fingerprint=fingerprint,
            )
            if replay is not None:
                return replay
            verifier = CoreRunDomainVerifier()
            verified = verifier.verify(store, request.run_id)
            if verified.snapshot.store_revision != request.expected_store_revision:
                raise CoreRunError("store_revision_conflict")
            legality = classify_recovery_legality(verified.snapshot)
            if (
                legality.state != "blocked"
                or legality.latest_contamination_revision
                != request.contamination_revision
            ):
                raise CoreRunError("repair_scope_invalid")
            contamination = next(
                (
                    item
                    for item in verified.snapshot.run_integrity_records
                    if item.integrity_revision == request.contamination_revision
                    and item.status == "contaminated"
                ),
                None,
            )
            if (
                contamination is None
                or contamination.affected_artifact_id
                not in request.permitted_artifact_ids
                or request.permitted_artifact_ids
                != sorted(set(request.permitted_artifact_ids))
            ):
                raise CoreRunError("repair_scope_invalid")
            classify_effect_authorization(
                verified.snapshot,
                CoreEffect.REPAIR_START,
                CoreEffectSubject(
                    contamination_revision=request.contamination_revision,
                    stage_id=request.owner_stage_id,
                ),
            ).require_allowed()
            now = self._now()
            repair_id = derived_id("REPAIR", request.request_id, fingerprint)
            event_id = derived_id("EVT-REPAIR", request.request_id, fingerprint)
            record = RepairCycleRecord.model_validate(
                {
                    "schema_version": RepairCycleRecord.schema_id,
                    "repair_id": repair_id,
                    "run_id": request.run_id,
                    "contamination_revision": request.contamination_revision,
                    "owner_stage_id": request.owner_stage_id,
                    "permitted_artifact_ids": request.permitted_artifact_ids,
                    "reason_code": request.reason_code,
                    "started_at": now,
                    "start_event_id": event_id,
                    "accepted_transaction_id": request.request_id,
                    "request_fingerprint": fingerprint,
                },
                strict=True,
            )
            event = EventEnvelope.model_validate(
                {
                    "schema_version": EventEnvelope.schema_id,
                    "event_id": event_id,
                    "run_id": request.run_id,
                    "event_type": "repair_started",
                    "created_at": now,
                    "actor": "system",
                    "transaction_id": request.request_id,
                    "stage_id": request.owner_stage_id,
                    "decision": "continue",
                    "reason": "repair scope accepted",
                    "metadata": {},
                    "core_run_binding": CoreRunEventBinding(
                        request_id=request.request_id,
                        request_fingerprint=fingerprint,
                        effect_kind="repair_start",
                        primary_record_id=repair_id,
                        outcome="committed",
                    ),
                },
                strict=True,
            )
            checkout = prepare_checkout_effect(
                workspace=self.workspace,
                snapshot=verified.snapshot,
                transaction_id=request.request_id,
                created_at=self._clock(),
            )
            unit = store.begin(
                request.run_id,
                request.request_id,
                transaction_type_for("repair_start"),
                request.expected_store_revision,
            )
            unit.put_repair_cycle(record)
            unit.append_event(event)
            stage_checkout_effect(unit, checkout)
            receipt = unit.commit(
                _postcommit_observer=lambda _receipt: verifier.verify(
                    store,
                    request.run_id,
                )
            )
            return CoreRunResult(
                status="committed",
                receipt=receipt,
                primary_record_id=repair_id,
            )

    def _supersede_artifact(self, request: ArtifactSupersedeRequest):
        fingerprint = canonical_fingerprint(
            request.model_dump(mode="json", exclude_unset=False)
        )
        with SQLiteControlStore.open(
            self.workspace / "briefloop.db", clock=self._clock
        ) as store:
            replay = self._replay(store, request.run_id, request.request_id, fingerprint)
            if replay is not None:
                return replay
            try:
                content = self._reader.read(request.input_path)
            except IntakeError as exc:
                raise CoreRunError("repair_scope_invalid") from exc
            if sha256_hex(content) != request.expected_input_sha256:
                raise CoreRunError("repair_scope_invalid")
            return self._write_supersession(
                store=store,
                request=request,
                fingerprint=fingerprint,
                content=content,
                source=None,
            )

    def _revert_artifact(self, request: ArtifactRevertRequest):
        fingerprint = canonical_fingerprint(
            request.model_dump(mode="json", exclude_unset=False)
        )
        with SQLiteControlStore.open(
            self.workspace / "briefloop.db", clock=self._clock
        ) as store:
            replay = self._replay(store, request.run_id, request.request_id, fingerprint)
            if replay is not None:
                return replay
            verified = self._verified_current(
                store, request.run_id, request.expected_store_revision
            )
            source = self._exact_revision(
                verified.snapshot, request.historical_source
            )
            try:
                content = store.read_artifact_revision_bytes(
                    request.run_id, source.artifact_id, source.revision
                )
            except ControlStoreError as exc:
                raise CoreRunError("repair_history_invalid") from exc
            return self._write_supersession(
                store=store,
                request=request,
                fingerprint=fingerprint,
                content=content,
                source=source,
                verified=verified,
            )

    def _write_supersession(
        self,
        *,
        store,
        request,
        fingerprint: str,
        content: bytes,
        source: ArtifactRevision | None,
        verified=None,
    ):
        from .checkout import (
            prepare_checkout_effect,
            publish_checkout_effect,
            stage_checkout_effect,
        )
        from .errors import CoreRunResult
        from .policy import derived_id, transaction_type_for
        from .verifier import CoreRunDomainVerifier

        verified = verified or self._verified_current(
            store, request.run_id, request.expected_store_revision
        )
        prior_ref = (
            request.prior_artifact
            if isinstance(request, ArtifactSupersedeRequest)
            else request.current_artifact
        )
        if (
            prior_ref.revision != request.expected_current_revision
            or source is not None
            and source.revision >= prior_ref.revision
        ):
            raise CoreRunError("repair_history_invalid")
        prior = self._exact_revision(verified.snapshot, prior_ref)
        artifact = next(
            (
                item
                for item in verified.snapshot.artifacts
                if item.artifact_id == prior.artifact_id
            ),
            None,
        )
        if artifact is None or artifact.current_revision != prior.revision:
            raise CoreRunError("repair_history_invalid")
        authorization = classify_effect_authorization(
            verified.snapshot,
            CoreEffect.ARTIFACT_REVERT if source is not None else CoreEffect.ARTIFACT_SUPERSEDE,
            CoreEffectSubject(repair_id=request.repair_id, artifact_id=artifact.artifact_id),
        ).require_allowed()
        if authorization.repair_id != request.repair_id:
            raise CoreRunError("repair_scope_invalid")
        repair = next(
            (item for item in verified.snapshot.repair_cycles if item.repair_id == request.repair_id),
            None,
        )
        if repair is None or artifact.artifact_id not in repair.permitted_artifact_ids:
            raise CoreRunError("repair_scope_invalid")
        now = self._now()
        digest = sha256_hex(content)
        successor_number = prior.revision + 1
        supersession_id = derived_id("SUPERSESSION", request.request_id, fingerprint)
        event_id = derived_id("EVT-SUPERSESSION", request.request_id, fingerprint)
        owned_event_id = derived_id("EVT-REPAIR-ARTIFACT", request.request_id, fingerprint)
        submission_id = derived_id("SUBMISSION-REPAIR", request.request_id, digest)
        updated = ArtifactRecord.model_validate(
            {**artifact.model_dump(mode="json", exclude_unset=False), "current_revision": successor_number, "status": "valid"},
            strict=True,
        )
        successor = ArtifactRevision.model_validate(
            {
                "schema_version": ArtifactRevision.schema_id,
                "run_id": request.run_id,
                "artifact_id": artifact.artifact_id,
                "revision": successor_number,
                "path": artifact.path,
                "sha256": digest,
                "size_bytes": len(content),
                "frozen": True,
                "producer_kind": "control_tool",
                "producer_id": "python_tool",
                "created_at": now,
            },
            strict=True,
        )
        submission = OwnedArtifactSubmissionRecord.model_validate(
            {
                "schema_version": OwnedArtifactSubmissionRecord.schema_id,
                "submission_id": submission_id,
                "run_id": request.run_id,
                "artifact_id": artifact.artifact_id,
                "artifact_revision": successor_number,
                "artifact_sha256": digest,
                "owner_stage_id": repair.owner_stage_id,
                "owner_role_id": "python_tool",
                "run_contract_fingerprint": verified.binding.contract_fingerprint,
                "invocation_id": None,
                "producer_tool_id": "repair-control-v2",
                "parent_artifact": prior_ref,
                "source_proposal_id": None,
                "canonical_workspace_path": artifact.path,
                "request_fingerprint": fingerprint,
                "accepted_event_id": owned_event_id,
                "accepted_transaction_id": request.request_id,
                "created_at": now,
            },
            strict=True,
        )
        relation = ArtifactSupersessionRecord.model_validate(
            {
                "schema_version": ArtifactSupersessionRecord.schema_id,
                "supersession_id": supersession_id,
                "run_id": request.run_id,
                "repair_id": request.repair_id,
                "mode": request.mode,
                "prior_artifact": prior_ref,
                "successor_artifact": {"artifact_id": artifact.artifact_id, "revision": successor_number},
                "reason_code": request.reason_code,
                "created_at": now,
                "accepted_event_id": event_id,
                "accepted_transaction_id": request.request_id,
                "request_fingerprint": fingerprint,
            },
            strict=True,
        )
        checkout = prepare_checkout_effect(
            workspace=self.workspace,
            snapshot=verified.snapshot,
            transaction_id=request.request_id,
            created_at=self._clock(),
            additional_revisions=(successor,),
        )
        unit = store.begin(
            request.run_id,
            request.request_id,
            transaction_type_for("artifact_supersession"),
            request.expected_store_revision,
        )
        unit.put_artifact(updated)
        unit.put_artifact_revision(successor, content)
        unit.put_owned_artifact_submission(submission)
        unit.put_artifact_supersession(relation)
        unit.append_event(self._event(owned_event_id, request, fingerprint, "owned_artifact_accepted", submission_id, repair.owner_stage_id, artifact.artifact_id, bind=False))
        unit.append_event(self._event(event_id, request, fingerprint, "repair_stage_superseded", supersession_id, repair.owner_stage_id, artifact.artifact_id))
        stage_checkout_effect(unit, checkout)
        verifier = CoreRunDomainVerifier()
        receipt = unit.commit(
            _postcommit_observer=lambda _receipt: verifier.verify(store, request.run_id)
        )
        published, _warnings = publish_checkout_effect(
            workspace=self.workspace, store=store, prepared=checkout
        )
        if not published:
            return CoreRunResult(status="commit_outcome_unknown", error_code="commit_outcome_unknown")
        return CoreRunResult(status="committed", receipt=receipt, primary_record_id=supersession_id)

    def _complete_repair(self, request: RepairCompleteRequest):
        from .checkout import prepare_checkout_effect, stage_checkout_effect
        from .errors import CoreRunResult
        from .policy import derived_id, transaction_type_for
        from .verifier import CoreRunDomainVerifier

        fingerprint = canonical_fingerprint(
            request.model_dump(mode="json", exclude_unset=False)
        )
        with SQLiteControlStore.open(
            self.workspace / "briefloop.db", clock=self._clock
        ) as store:
            replay = self._replay(store, request.run_id, request.request_id, fingerprint)
            if replay is not None:
                return replay
            verified = self._verified_current(
                store, request.run_id, request.expected_store_revision
            )
            legality = classify_recovery_legality(verified.snapshot)
            classify_effect_authorization(
                verified.snapshot,
                CoreEffect.REPAIR_COMPLETE,
                CoreEffectSubject(repair_id=request.repair_id),
            ).require_allowed()
            supersessions = sorted(
                (
                    item
                    for item in verified.snapshot.artifact_supersessions
                    if item.repair_id == request.repair_id
                ),
                key=lambda item: item.supersession_id,
            )
            if (
                legality.repair_id != request.repair_id
                or not supersessions
                or request.supersession_ids != sorted(set(request.supersession_ids))
                or request.supersession_ids
                != [item.supersession_id for item in supersessions]
            ):
                raise CoreRunError("repair_history_invalid")
            owner_stages = sorted(
                {
                    submission.owner_stage_id
                    for relation in supersessions
                    for submission in verified.snapshot.owned_artifact_submissions
                    if submission.artifact_id == relation.successor_artifact.artifact_id
                    and submission.artifact_revision == relation.successor_artifact.revision
                }
            )
            if not owner_stages or sorted(request.expected_stage_revisions) != owner_stages:
                raise CoreRunError("repair_history_invalid")
            stages = {
                item.stage_id: item
                for item in verified.snapshot.stage_states
                if item.stage_id in owner_stages
            }
            if any(
                stage_id not in stages
                or stages[stage_id].revision != request.expected_stage_revisions[stage_id]
                for stage_id in owner_stages
            ):
                raise CoreRunError("repair_history_invalid")
            now = self._now()
            completion_id = derived_id("REPAIR-COMPLETION", request.request_id, fingerprint)
            completion_event_id = derived_id("EVT-REPAIR-COMPLETE", request.request_id, fingerprint)
            transitions: list[StageTransitionRecord] = []
            for stage_id in owner_stages:
                prior = stages[stage_id]
                transition_id = derived_id(
                    "TRANSITION-REPAIR-REOPEN", request.request_id, stage_id
                )
                event_id = derived_id("EVT-REPAIR-REOPEN", request.request_id, stage_id)
                transitions.append(
                    StageTransitionRecord.model_validate(
                        {
                            "schema_version": StageTransitionRecord.schema_id,
                            "transition_id": transition_id,
                            "run_id": request.run_id,
                            "stage_id": stage_id,
                            "transition_kind": "repair_reopen",
                            "requested_decision": None,
                            "prior_status": prior.status,
                            "prior_revision": prior.revision,
                            "result_status": "ready",
                            "result_revision": prior.revision + 1,
                            "reason": "repair reopens artifact owner stage",
                            "run_contract_fingerprint": verified.binding.contract_fingerprint,
                            "actor": "system",
                            "producer_invocation_id": None,
                            "producer_tool_id": None,
                            "producer_result_status": None,
                            "producer_result_fingerprint": None,
                            "producer_implementation": None,
                            "producer_version": None,
                            "topology": None,
                            "satisfaction_source_kind": None,
                            "satisfied_by_id": None,
                            "created_at": now,
                            "transition_event_id": event_id,
                            "accepted_transaction_id": request.request_id,
                            "request_fingerprint": fingerprint,
                        },
                        strict=True,
                    )
                )
            completion = RepairCompletionRecord.model_validate(
                {
                    "schema_version": RepairCompletionRecord.schema_id,
                    "repair_completion_id": completion_id,
                    "run_id": request.run_id,
                    "repair_id": request.repair_id,
                    "contamination_revision": legality.latest_contamination_revision,
                    "supersession_ids": request.supersession_ids,
                    "reopened_transition_ids": [item.transition_id for item in transitions],
                    "completed_at": now,
                    "completion_event_id": completion_event_id,
                    "accepted_transaction_id": request.request_id,
                    "request_fingerprint": fingerprint,
                },
                strict=True,
            )
            checkout = prepare_checkout_effect(
                workspace=self.workspace,
                snapshot=verified.snapshot,
                transaction_id=request.request_id,
                created_at=self._clock(),
            )
            unit = store.begin(
                request.run_id,
                request.request_id,
                transaction_type_for("repair_complete"),
                request.expected_store_revision,
            )
            for transition in transitions:
                unit.put_stage_state(
                    StageState.model_validate(
                        {
                            "schema_version": StageState.schema_id,
                            "run_id": request.run_id,
                            "stage_id": transition.stage_id,
                            "status": transition.result_status,
                            "revision": transition.result_revision,
                            "updated_at": now,
                        },
                        strict=True,
                    )
                )
                unit.append_stage_transition(transition)
                unit.append_event(
                    self._event(
                        transition.transition_event_id,
                        request,
                        fingerprint,
                        "stage_status_changed",
                        transition.transition_id,
                        transition.stage_id,
                        bind=False,
                    )
                )
            unit.put_repair_completion(completion)
            unit.append_event(
                self._event(
                    completion_event_id,
                    request,
                    fingerprint,
                    "repair_completed",
                    completion_id,
                    owner_stages[0],
                )
            )
            stage_checkout_effect(unit, checkout)
            verifier = CoreRunDomainVerifier()
            receipt = unit.commit(
                _postcommit_observer=lambda _receipt: verifier.verify(store, request.run_id)
            )
            return CoreRunResult(status="committed", receipt=receipt, primary_record_id=completion_id)

    def _complete_recovery(self, request: RecoveryCompleteRequest):
        from .checkout import prepare_checkout_effect, stage_checkout_effect
        from .errors import CoreRunResult
        from .policy import derived_id, transaction_type_for
        from .verifier import CoreRunDomainVerifier

        fingerprint = canonical_fingerprint(
            request.model_dump(mode="json", exclude_unset=False)
        )
        with SQLiteControlStore.open(
            self.workspace / "briefloop.db", clock=self._clock
        ) as store:
            replay = self._replay(store, request.run_id, request.request_id, fingerprint)
            if replay is not None:
                return replay
            verified = self._verified_current(store, request.run_id, request.expected_store_revision)
            legality = classify_recovery_legality(verified.snapshot)
            classify_effect_authorization(
                verified.snapshot,
                CoreEffect.RECOVERY_COMPLETE,
                CoreEffectSubject(repair_completion_id=request.repair_completion_id),
            ).require_allowed()
            if (
                legality.state != "rerun_required"
                or legality.repair_completion_id != request.repair_completion_id
                or legality.latest_contamination_revision != request.contamination_revision
                or request.rerun_transition_ids != sorted(set(request.rerun_transition_ids))
                or request.gate_evaluation_ids != sorted(set(request.gate_evaluation_ids))
                or request.rerun_transition_ids != list(legality.required_rerun_transition_ids)
                or request.gate_evaluation_ids != list(legality.required_gate_evaluation_ids)
            ):
                raise CoreRunError("repair_history_invalid")
            completion = next(
                item
                for item in verified.snapshot.repair_completions
                if item.repair_completion_id == request.repair_completion_id
            )
            now = self._now()
            recovery_id = derived_id("RECOVERY", request.request_id, fingerprint)
            event_id = derived_id("EVT-RECOVERY", request.request_id, fingerprint)
            recovery = RecoveryCompletionRecord.model_validate(
                {
                    "schema_version": RecoveryCompletionRecord.schema_id,
                    "recovery_id": recovery_id,
                    "run_id": request.run_id,
                    "repair_completion_id": request.repair_completion_id,
                    "contamination_revision": request.contamination_revision,
                    "supersession_ids": completion.supersession_ids,
                    "rerun_transition_ids": request.rerun_transition_ids,
                    "gate_evaluation_ids": request.gate_evaluation_ids,
                    "disposition": "recovered_non_reference",
                    "completed_at": now,
                    "completion_event_id": event_id,
                    "accepted_transaction_id": request.request_id,
                    "request_fingerprint": fingerprint,
                },
                strict=True,
            )
            checkout = prepare_checkout_effect(
                workspace=self.workspace,
                snapshot=verified.snapshot,
                transaction_id=request.request_id,
                created_at=self._clock(),
            )
            unit = store.begin(
                request.run_id,
                request.request_id,
                transaction_type_for("recovery_complete"),
                request.expected_store_revision,
            )
            unit.put_recovery_completion(recovery)
            unit.append_event(
                self._event(event_id, request, fingerprint, "decision_recorded", recovery_id)
            )
            stage_checkout_effect(unit, checkout)
            verifier = CoreRunDomainVerifier()
            receipt = unit.commit(
                _postcommit_observer=lambda _receipt: verifier.verify(store, request.run_id)
            )
            return CoreRunResult(status="committed", receipt=receipt, primary_record_id=recovery_id)

    def _reset_run(self, request: RunResetRequest):
        from .checkout import (
            prepare_cross_run_checkout_effect,
            publish_checkout_effect,
            stage_checkout_effect,
        )
        from .errors import CoreRunResult
        from .policy import (
            CORE_ARTIFACT_IDS,
            INTERNAL_CONTRACT_ARTIFACT_IDS,
            blob_workspace_path,
            derived_id,
            run_contract_fingerprint,
            transaction_type_for,
        )
        from .service import (
            _artifact_pair,
            _derive_runtime_source_plan,
            workspace_input_fingerprints,
        )
        from .verifier import CoreRunDomainVerifier

        fingerprint = canonical_fingerprint(
            request.model_dump(mode="json", exclude_unset=False)
        )
        with SQLiteControlStore.open(
            self.workspace / "briefloop.db", clock=self._clock
        ) as store:
            replay = self._replay(
                store,
                request.successor_run_id,
                request.request_id,
                fingerprint,
            )
            if replay is not None:
                return replay
            (
                workspace_config_sha256,
                sources_config_sha256,
                sources_content,
            ) = workspace_input_fingerprints(
                self.workspace,
                include_sources_content=True,
            )
            if (
                workspace_config_sha256 != request.workspace_config_sha256
                or sources_config_sha256 != request.sources_config_sha256
            ):
                raise CoreRunError("core_run_contract_mismatch")
            verified = self._verified_current(
                store,
                request.predecessor_run_id,
                request.expected_store_revision,
            )
            snapshot = verified.snapshot
            head = snapshot.workspace_run_head
            if (
                request.predecessor_run_id == request.successor_run_id
                or request.workspace_id != snapshot.workspace_id
                or request.runtime != snapshot.run.runtime
                or head is None
                or head.current_run_id != request.expected_head_run_id
                or head.current_run_id != request.predecessor_run_id
                or request.expected_workspace_revision != snapshot.store_revision
            ):
                raise CoreRunError("repair_history_invalid")
            classify_effect_authorization(snapshot, CoreEffect.RUN_RESET).require_allowed()
            now = self._now()
            adapter_payload = verified.runtime_adapter.model_dump(
                mode="json", exclude_unset=False
            )
            adapter_payload.update(run_id=request.successor_run_id)
            adapter_payload.pop("binding_fingerprint", None)
            adapter_payload["binding_fingerprint"] = canonical_fingerprint(adapter_payload)
            adapter_bytes = canonical_json_bytes(adapter_payload)
            source_plan = _derive_runtime_source_plan(
                sources_content,
                run_id=request.successor_run_id,
                sources_config_sha256=sources_config_sha256,
            )
            source_payload = source_plan.model_dump(
                mode="json", exclude_unset=False
            )
            source_bytes = canonical_json_bytes(source_payload)
            frozen_payloads = (
                store.read_artifact_revision_bytes(
                    request.predecessor_run_id,
                    verified.binding.stage_specs_artifact.artifact_id,
                    verified.binding.stage_specs_artifact.revision,
                ),
                store.read_artifact_revision_bytes(
                    request.predecessor_run_id,
                    verified.binding.artifact_contracts_artifact.artifact_id,
                    verified.binding.artifact_contracts_artifact.revision,
                ),
                store.read_artifact_revision_bytes(
                    request.predecessor_run_id,
                    verified.binding.policy_pack_artifact.artifact_id,
                    verified.binding.policy_pack_artifact.revision,
                ),
                adapter_bytes,
                source_bytes,
            )
            contract_artifacts = [
                _artifact_pair(
                    run_id=request.successor_run_id,
                    artifact_id=artifact_id,
                    revision=1,
                    path=blob_workspace_path(sha256_hex(content)),
                    artifact_format="json",
                    content=content,
                    producer_kind="control_tool",
                    producer_id="core-v2-initializer",
                    created_at=now,
                    required=True,
                )
                + (content,)
                for artifact_id, content in zip(
                    INTERNAL_CONTRACT_ARTIFACT_IDS,
                    frozen_payloads,
                )
            ]
            contract_values = verified.binding.model_dump(
                mode="json", exclude_unset=False
            )
            contract_values.update(
                run_id=request.successor_run_id,
                run_direction=request.run_direction.model_dump(mode="json"),
                workspace_config_sha256=workspace_config_sha256,
                sources_config_sha256=sources_config_sha256,
                role_topology=request.role_topology,
                gate_strictness=request.gate_strictness,
                input_governance_required=request.input_governance_required,
                runtime_adapter_sha256=sha256_hex(adapter_bytes),
                runtime_adapter_fingerprint=adapter_payload["binding_fingerprint"],
                runtime_source_plan_sha256=sha256_hex(source_bytes),
                runtime_source_plan_fingerprint=source_plan.source_plan_fingerprint,
                created_at=now,
                accepted_transaction_id=request.request_id,
                request_fingerprint=fingerprint,
            )
            initialized_event_id = derived_id("EVT-RESET-INIT", request.request_id, fingerprint)
            contract_values["initialization_event_id"] = initialized_event_id
            contract_values["contract_fingerprint"] = run_contract_fingerprint(
                runtime=request.runtime,
                stage_specs_schema=verified.binding.stage_specs_schema,
                stage_specs_sha256=verified.binding.stage_specs_sha256,
                artifact_contracts_schema=verified.binding.artifact_contracts_schema,
                artifact_contracts_sha256=verified.binding.artifact_contracts_sha256,
                policy_pack_schema=verified.binding.policy_pack_schema,
                policy_pack_name=verified.binding.policy_pack_name,
                policy_pack_sha256=verified.binding.policy_pack_sha256,
                runtime_adapter_sha256=contract_values["runtime_adapter_sha256"],
                runtime_adapter_fingerprint=contract_values["runtime_adapter_fingerprint"],
                runtime_source_plan_sha256=contract_values["runtime_source_plan_sha256"],
                runtime_source_plan_fingerprint=contract_values["runtime_source_plan_fingerprint"],
                run_direction=request.run_direction.model_dump(mode="json"),
                workspace_config_sha256=workspace_config_sha256,
                sources_config_sha256=sources_config_sha256,
                role_topology=request.role_topology,
                gate_strictness=request.gate_strictness,
                input_governance_required=request.input_governance_required,
            )
            contract = type(verified.binding).model_validate(contract_values, strict=True)
            transition_id = derived_id("HEAD-RESET", request.request_id, fingerprint)
            reset_event_id = derived_id("EVT-RESET", request.request_id, fingerprint)
            transition = RunHeadTransitionRecord.model_validate(
                {
                    "schema_version": RunHeadTransitionRecord.schema_id,
                    "head_transition_id": transition_id,
                    "workspace_id": request.workspace_id,
                    "predecessor_run_id": request.predecessor_run_id,
                    "successor_run_id": request.successor_run_id,
                    "prior_workspace_revision": request.expected_workspace_revision,
                    "successor_workspace_revision": request.expected_workspace_revision + 1,
                    "reason_code": "run_reset",
                    "successor_disposition": "non_reference",
                    "created_at": now,
                    "transition_event_id": reset_event_id,
                    "accepted_transaction_id": request.request_id,
                    "request_fingerprint": fingerprint,
                },
                strict=True,
            )
            checkout = prepare_cross_run_checkout_effect(
                workspace=self.workspace,
                snapshot=snapshot,
                successor_run_id=request.successor_run_id,
                transaction_id=request.request_id,
                created_at=self._clock(),
            )
            unit = store.begin(
                request.successor_run_id,
                request.request_id,
                transaction_type_for("run_head_transition"),
                request.expected_store_revision,
            )
            unit.put_run(
                RunIdentity.model_validate(
                    {
                        "schema_version": RunIdentity.schema_id,
                        "run_id": request.successor_run_id,
                        "workspace_id": request.workspace_id,
                        "runtime": request.runtime,
                        "created_at": now,
                    },
                    strict=True,
                )
            )
            unit.put_workspace_run_head(
                WorkspaceRunHead.model_validate(
                    {
                        "schema_version": WorkspaceRunHead.schema_id,
                        "workspace_id": request.workspace_id,
                        "current_run_id": request.successor_run_id,
                        "updated_at": now,
                    },
                    strict=True,
                )
            )
            unit.put_run_contract_binding(contract)
            for artifact, revision, content in contract_artifacts:
                unit.put_artifact(artifact)
                unit.put_artifact_revision(revision, content)
            artifact_contracts = {
                str(item["artifact_id"]): item for item in verified.artifacts
            }
            for artifact_id in CORE_ARTIFACT_IDS:
                row = artifact_contracts[artifact_id]
                unit.put_artifact(
                    ArtifactRecord.model_validate(
                        {
                            "schema_version": ArtifactRecord.schema_id,
                            "run_id": request.successor_run_id,
                            "artifact_id": artifact_id,
                            "current_revision": 0,
                            "status": "expected",
                            "required": bool(row["required"]),
                            "path": row["path"],
                            "format": row["format"],
                        },
                        strict=True,
                    )
                )
            for position, stage_contract in enumerate(verified.stages):
                stage_id = str(stage_contract["stage_id"])
                status = "ready" if position == 0 else "pending"
                event_id = derived_id("EVT-RESET-STAGE", request.request_id, stage_id)
                stage_transition_id = derived_id("TRANSITION-RESET", request.request_id, stage_id)
                unit.put_stage_state(
                    StageState.model_validate(
                        {
                            "schema_version": StageState.schema_id,
                            "run_id": request.successor_run_id,
                            "stage_id": stage_id,
                            "status": status,
                            "revision": 0,
                            "updated_at": now,
                        },
                        strict=True,
                    )
                )
                unit.append_stage_transition(
                    StageTransitionRecord.model_validate(
                        {
                            "schema_version": StageTransitionRecord.schema_id,
                            "transition_id": stage_transition_id,
                            "run_id": request.successor_run_id,
                            "stage_id": stage_id,
                            "transition_kind": "initialize",
                            "requested_decision": None,
                            "prior_status": None,
                            "prior_revision": None,
                            "result_status": status,
                            "result_revision": 0,
                            "reason": "reset successor stage initialized",
                            "run_contract_fingerprint": contract.contract_fingerprint,
                            "actor": "system",
                            "producer_invocation_id": None,
                            "producer_tool_id": None,
                            "producer_result_status": None,
                            "producer_result_fingerprint": None,
                            "producer_implementation": None,
                            "producer_version": None,
                            "topology": None,
                            "satisfaction_source_kind": None,
                            "satisfied_by_id": None,
                            "created_at": now,
                            "transition_event_id": event_id,
                            "accepted_transaction_id": request.request_id,
                            "request_fingerprint": fingerprint,
                        },
                        strict=True,
                    )
                )
                unit.append_event(
                    self._reset_event(
                        event_id,
                        request,
                        fingerprint,
                        "stage_status_changed",
                        stage_id,
                        None,
                        bind=False,
                    )
                )
            unit.append_run_integrity_record(
                RunIntegrityRecord.model_validate(
                    {
                        "schema_version": RunIntegrityRecord.schema_id,
                        "run_id": request.successor_run_id,
                        "integrity_revision": 1,
                        "status": "clean",
                        "prior_integrity_revision": None,
                        "affected_artifact_id": None,
                        "affected_artifact_revision": None,
                        "expected_workspace_path": None,
                        "expected_sha256": None,
                        "observed_entry_kind": None,
                        "observed_sha256": None,
                        "reason_code": None,
                        "first_detected_at": None,
                        "first_detected_event_id": None,
                        "accepted_transaction_id": request.request_id,
                        "request_fingerprint": fingerprint,
                    },
                    strict=True,
                )
            )
            unit.put_run_head_transition(transition)
            unit.append_event(self._reset_event(initialized_event_id, request, fingerprint, "run_initialized", "doctor", None, bind=False))
            unit.append_event(self._reset_event(reset_event_id, request, fingerprint, "run_reset", None, transition_id, bind=True))
            stage_checkout_effect(unit, checkout)
            verifier = CoreRunDomainVerifier()
            receipt = unit.commit(
                _postcommit_observer=lambda _receipt: verifier.verify(store, request.successor_run_id)
            )
            published, _warnings = publish_checkout_effect(
                workspace=self.workspace,
                store=store,
                prepared=checkout,
            )
            if not published:
                return CoreRunResult(
                    status="commit_outcome_unknown",
                    error_code="commit_outcome_unknown",
                )
            return CoreRunResult(status="committed", receipt=receipt, primary_record_id=transition_id)

    def _reset_event(self, event_id, request, fingerprint, event_type, stage_id, primary_id, *, bind):
        return EventEnvelope.model_validate(
            {
                "schema_version": EventEnvelope.schema_id,
                "event_id": event_id,
                "run_id": request.successor_run_id,
                "event_type": event_type,
                "created_at": self._now(),
                "actor": "system",
                "transaction_id": request.request_id,
                "stage_id": stage_id,
                "decision": "continue",
                "reason": event_type.replace("_", " "),
                "metadata": {},
                "core_run_binding": CoreRunEventBinding(
                    request_id=request.request_id,
                    request_fingerprint=fingerprint,
                    effect_kind="run_head_transition",
                    primary_record_id=primary_id,
                    outcome="committed",
                ) if bind else None,
            },
            strict=True,
        )

    @staticmethod
    def _exact_revision(snapshot, reference: ArtifactRevisionReference) -> ArtifactRevision:
        matches = [
            item
            for item in snapshot.artifact_revisions
            if item.artifact_id == reference.artifact_id and item.revision == reference.revision
        ]
        if len(matches) != 1:
            raise CoreRunError("repair_history_invalid")
        return matches[0]

    @staticmethod
    def _replay(store, run_id: str, request_id: str, fingerprint: str):
        from .verifier import resolve_core_replay

        return resolve_core_replay(
            store,
            run_id=run_id,
            request_id=request_id,
            request_fingerprint=fingerprint,
        )

    @staticmethod
    def _verified_current(store, run_id: str, expected_store_revision: int):
        from .verifier import CoreRunDomainVerifier

        verified = CoreRunDomainVerifier().verify(store, run_id)
        if verified.snapshot.store_revision != expected_store_revision:
            raise CoreRunError("store_revision_conflict")
        return verified

    def _event(
        self,
        event_id: str,
        request,
        fingerprint: str,
        event_type: str,
        primary_record_id: str,
        stage_id: str | None = None,
        artifact_id: str | None = None,
        bind: bool = True,
    ) -> EventEnvelope:
        return EventEnvelope.model_validate(
            {
                "schema_version": EventEnvelope.schema_id,
                "event_id": event_id,
                "run_id": request.run_id,
                "event_type": event_type,
                "created_at": self._now(),
                "actor": "system",
                "transaction_id": request.request_id,
                "stage_id": stage_id,
                "artifact_id": artifact_id,
                "decision": "continue",
                "reason": event_type.replace("_", " "),
                "metadata": {},
                "core_run_binding": (
                    CoreRunEventBinding(
                        request_id=request.request_id,
                        request_fingerprint=fingerprint,
                        effect_kind={
                            "repair_stage_superseded": "artifact_supersession",
                            "repair_completed": "repair_complete",
                            "decision_recorded": "recovery_complete",
                        }.get(event_type, event_type),
                        primary_record_id=primary_record_id,
                        outcome="committed",
                    )
                    if bind
                    else None
                ),
            },
            strict=True,
        )

    def _now(self) -> str:
        value = self._clock()
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise CoreRunError("core_run_request_invalid")
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = [
    "CoreRunRecoveryService",
    "CoreEffect",
    "CoreEffectSubject",
    "EffectAuthorization",
    "RecoveryLegality",
    "ReopenedArtifactEpoch",
    "classify_effect_authorization",
    "classify_recovery_legality",
    "recovery_stage_rerun_permitted",
    "require_reopened_artifact_epoch",
]
