"""Pure recovery legality for dormant fresh-v2 historical verification."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from multi_agent_brief.contracts.v2 import (
    RecoveryCompletionRecord,
    RepairCompletionRecord,
    RepairCycleRecord,
)
from multi_agent_brief.control_store.sqlite_store import ControlStoreSnapshot

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


__all__ = [
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
