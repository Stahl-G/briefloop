"""Pure terminal legality for dormant fresh-v2 historical verification."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
from typing import Callable

from multi_agent_brief.contracts.v2 import (
    DeliveryAttemptRecord,
    DeliveryAuthorizationRecord,
    DeliveryResultRecord,
    DeliveryResultObservation,
    InternalApprovalRequest,
    DeliveryAuthorizationRequest,
    DeliveryAttemptRequest,
    DeliveryResultRequest,
    Approval,
    ApprovalPackageBinding,
    ArtifactRecord,
    ArtifactRevision,
    CoreRunEventBinding,
    EventEnvelope,
    FinalizationRecord,
    FinalizeCompleteRequest,
    FinalizeRenderRecord,
    FinalizeRenderRequest,
    PackageArtifactBinding,
    PackageReadyRecord,
    RunArchiveArtifactBinding,
    RunArchiveRecord,
    StageArtifactBinding,
    StageGateBinding,
    StageState,
    StageTransitionRecord,
)
from multi_agent_brief.control_store import ControlStoreError, SQLiteControlStore
from multi_agent_brief.control_store.serialization import (
    canonical_fingerprint,
    canonical_json_bytes,
    sha256_hex,
)
from multi_agent_brief.intake_v2.errors import IntakeError
from multi_agent_brief.intake_v2.scratch import ScratchReader, parse_json_object
from multi_agent_brief.control_store.sqlite_store import ControlStoreSnapshot
from multi_agent_brief.product.release_approval import RELEASE_MODES

from .errors import CoreRunError
from .recovery import CoreEffect


@dataclass(frozen=True)
class TerminalClassification:
    state: str
    package_id: str | None = None
    result_id: str | None = None


@dataclass(frozen=True)
class TerminalLegality:
    """Pure terminal legality from one immutable Store snapshot."""

    package_state: str
    approval_mode: str | None = None
    required_roles: tuple[str, ...] = ()
    latest_decision_by_role: tuple[tuple[str, str], ...] = ()
    approval_complete: bool = False
    current_authorization_id: str | None = None
    authorization_current: bool = False
    attempt_id_for_current_authorization: str | None = None
    current_result_id: str | None = None
    current_result_status: str | None = None
    next_effects: tuple[str, ...] = ()
    terminal_state: str = "invalid"
    package_id: str | None = None


@dataclass(frozen=True)
class TerminalEffectSubject:
    """Receipt-owned terminal fields needed for pre-prefix authorization."""

    package_id: str | None = None
    approval_mode: str | None = None
    approval_role: str | None = None
    authorization_id: str | None = None
    prior_authorization_id: str | None = None
    retry_of_attempt_id: str | None = None
    purpose: str | None = None
    decision: str | None = None
    target: str | None = None
    channel: str | None = None
    recipient_fingerprint: str | None = None
    attempt_id: str | None = None
    connector_operation_id: str | None = None
    prior_result_id: str | None = None
    reconciliation_authorization_id: str | None = None
    result_status: str | None = None


@dataclass(frozen=True)
class TerminalEffectAuthorization:
    decision: str
    reason_code: str = "terminal_effect_invalid"

    def require_allowed(self) -> "TerminalEffectAuthorization":
        if self.decision != "allow":
            raise CoreRunError(self.reason_code)
        return self


@dataclass(frozen=True)
class _TerminalApproval:
    valid: bool
    required_roles: tuple[str, ...] = ()
    latest_decisions: tuple[tuple[str, str], ...] = ()
    complete: bool = False


@dataclass(frozen=True)
class _TerminalTupleClassification:
    """The sole private terminal authority for one exact delivery tuple."""

    package_id: str
    target: str
    channel: str
    recipient_fingerprint: str
    valid: bool
    authorizations: tuple[DeliveryAuthorizationRecord, ...] = ()
    current_authorization: DeliveryAuthorizationRecord | None = None
    ordered_attempts: tuple[DeliveryAttemptRecord, ...] = ()
    result_tips: tuple[tuple[str, DeliveryResultRecord | None], ...] = ()
    consumed_reconciliation_authorizations: tuple[str, ...] = ()
    approvals_by_mode: tuple[tuple[str, _TerminalApproval], ...] = ()

    @property
    def latest_consumed_attempt(self) -> DeliveryAttemptRecord | None:
        return self.ordered_attempts[-1] if self.ordered_attempts else None

    def authorization(
        self, authorization_id: str | None
    ) -> DeliveryAuthorizationRecord | None:
        return next(
            (
                item
                for item in self.authorizations
                if item.authorization_id == authorization_id
            ),
            None,
        )

    def attempt_for_authorization(
        self, authorization_id: str | None
    ) -> DeliveryAttemptRecord | None:
        return next(
            (
                item
                for item in self.ordered_attempts
                if item.authorization_id == authorization_id
            ),
            None,
        )

    def result_tip(self, attempt_id: str | None) -> DeliveryResultRecord | None:
        return next(
            (tip for item_id, tip in self.result_tips if item_id == attempt_id),
            None,
        )

    def approval(self, mode: str) -> _TerminalApproval:
        return next(
            (
                approval
                for item_mode, approval in self.approvals_by_mode
                if item_mode == mode
            ),
            _TerminalApproval(False),
        )

    def authorization_can_record(
        self, authorization: DeliveryAuthorizationRecord
    ) -> bool:
        """Check candidate authorization semantics against this immutable prefix."""

        if not self.valid:
            return False
        latest_attempt = self.latest_consumed_attempt
        if authorization.purpose == "initial_attempt":
            return authorization.retry_of_attempt_id is None and latest_attempt is None
        if authorization.retry_of_attempt_id is None or latest_attempt is None:
            return False
        if authorization.retry_of_attempt_id != latest_attempt.attempt_id:
            return False
        tip = self.result_tip(latest_attempt.attempt_id)
        if tip is None:
            return False
        if authorization.purpose == "result_reconciliation":
            return tip.status == "outcome_unknown"
        return authorization.purpose == "retry_attempt" and tip.status in {
            "draft_created",
            "failed",
            "outcome_unknown",
        }


def _terminal_tuple(
    snapshot: ControlStoreSnapshot,
    *,
    package_id: str,
    target: str,
    channel: str,
    recipient_fingerprint: str,
) -> _TerminalTupleClassification:
    """Classify one exact tuple once; all terminal consumers share this view."""

    chain = tuple(
        item
        for item in snapshot.delivery_authorizations
        if item.package_id == package_id
        and item.target == target
        and item.channel == channel
        and item.recipient_fingerprint == recipient_fingerprint
    )
    by_id = {item.authorization_id: item for item in chain}
    if len(by_id) != len(chain):
        return _TerminalTupleClassification(
            package_id, target, channel, recipient_fingerprint, False
        )
    if not chain:
        return _TerminalTupleClassification(
            package_id, target, channel, recipient_fingerprint, True
        )
    referenced = {
        item.prior_authorization_id
        for item in chain
        if item.prior_authorization_id is not None
    }
    tips = [item for item in chain if item.authorization_id not in referenced]
    if len(tips) != 1:
        return _TerminalTupleClassification(
            package_id, target, channel, recipient_fingerprint, False
        )
    newest_to_oldest: list[DeliveryAuthorizationRecord] = []
    current: DeliveryAuthorizationRecord | None = tips[0]
    while current is not None:
        if current in newest_to_oldest:
            return _TerminalTupleClassification(
                package_id, target, channel, recipient_fingerprint, False
            )
        newest_to_oldest.append(current)
        current = (
            None
            if current.prior_authorization_id is None
            else by_id.get(current.prior_authorization_id)
        )
        if current is None and newest_to_oldest[-1].prior_authorization_id is not None:
            return _TerminalTupleClassification(
                package_id, target, channel, recipient_fingerprint, False
            )
    if {item.authorization_id for item in newest_to_oldest} != set(by_id):
        return _TerminalTupleClassification(
            package_id, target, channel, recipient_fingerprint, False
        )
    authorizations = tuple(reversed(newest_to_oldest))
    position = {
        item.authorization_id: index for index, item in enumerate(authorizations)
    }
    attempts = [
        item for item in snapshot.delivery_attempts if item.authorization_id in by_id
    ]
    attempts_by_authorization: dict[str, DeliveryAttemptRecord] = {}
    for attempt in attempts:
        authorization = by_id[attempt.authorization_id]
        if (
            attempt.authorization_id in attempts_by_authorization
            or authorization.purpose not in {"initial_attempt", "retry_attempt"}
            or attempt.package_id != package_id
            or attempt.target != target
            or attempt.channel != channel
            or attempt.recipient_fingerprint != recipient_fingerprint
        ):
            return _TerminalTupleClassification(
                package_id, target, channel, recipient_fingerprint, False
            )
        attempts_by_authorization[attempt.authorization_id] = attempt
    ordered_attempts = tuple(
        sorted(attempts, key=lambda item: position[item.authorization_id])
    )
    result_tips: list[tuple[str, DeliveryResultRecord | None]] = []
    consumed_reconciliations: list[str] = []
    for attempt in ordered_attempts:
        results = tuple(
            item
            for item in snapshot.delivery_results
            if item.attempt_id == attempt.attempt_id
        )
        by_result_id = {item.result_id: item for item in results}
        referenced_results = {
            item.prior_result_id for item in results if item.prior_result_id is not None
        }
        tips_for_attempt = [
            item for item in results if item.result_id not in referenced_results
        ]
        if len(by_result_id) != len(results) or len(tips_for_attempt) != (
            1 if results else 0
        ):
            return _TerminalTupleClassification(
                package_id, target, channel, recipient_fingerprint, False
            )
        tip = tips_for_attempt[0] if tips_for_attempt else None
        if tip is not None:
            tip_to_root: list[str] = []
            current_result: DeliveryResultRecord | None = tip
            while current_result is not None:
                if current_result.result_id in tip_to_root:
                    return _TerminalTupleClassification(
                        package_id, target, channel, recipient_fingerprint, False
                    )
                tip_to_root.append(current_result.result_id)
                current_result = (
                    None
                    if current_result.prior_result_id is None
                    else by_result_id.get(current_result.prior_result_id)
                )
                if (
                    current_result is None
                    and by_result_id[tip_to_root[-1]].prior_result_id is not None
                ):
                    return _TerminalTupleClassification(
                        package_id, target, channel, recipient_fingerprint, False
                    )
            if set(tip_to_root) != set(by_result_id):
                return _TerminalTupleClassification(
                    package_id, target, channel, recipient_fingerprint, False
                )
        for result in results:
            if result.connector_operation_id != attempt.connector_operation_id:
                return _TerminalTupleClassification(
                    package_id, target, channel, recipient_fingerprint, False
                )
            if (
                result.prior_result_id is not None
                and result.prior_result_id not in by_result_id
            ):
                return _TerminalTupleClassification(
                    package_id, target, channel, recipient_fingerprint, False
                )
            if result.reconciliation_authorization_id is not None:
                reconciliation = by_id.get(result.reconciliation_authorization_id)
                if (
                    reconciliation is None
                    or reconciliation.purpose != "result_reconciliation"
                    or reconciliation.retry_of_attempt_id != attempt.attempt_id
                    or result.reconciliation_authorization_id
                    in consumed_reconciliations
                ):
                    return _TerminalTupleClassification(
                        package_id, target, channel, recipient_fingerprint, False
                    )
                consumed_reconciliations.append(result.reconciliation_authorization_id)
        result_tips.append((attempt.attempt_id, tip))
    return _TerminalTupleClassification(
        package_id,
        target,
        channel,
        recipient_fingerprint,
        True,
        authorizations,
        authorizations[-1],
        ordered_attempts,
        tuple(result_tips),
        tuple(consumed_reconciliations),
        tuple(
            (
                mode,
                _approval_policy_details(
                    snapshot,
                    package_id=package_id,
                    approval_mode=mode,
                ),
            )
            for mode in {item.approval_mode for item in authorizations}
        ),
    )


def _approval_policy_details(
    snapshot: ControlStoreSnapshot,
    *,
    package_id: str,
    approval_mode: str,
) -> _TerminalApproval:
    config = RELEASE_MODES.get(approval_mode)
    if config is None:
        return _TerminalApproval(False)
    required_roles = tuple(config["required_roles"])
    approvals = {item.approval_id: item for item in snapshot.approvals}
    tx_revision = {
        item.transaction_id: item.committed_revision for item in snapshot.transactions
    }
    latest: dict[str, tuple[int, str]] = {}
    for binding in snapshot.approval_package_bindings:
        if binding.package_id != package_id:
            continue
        approval = approvals.get(binding.approval_id)
        if approval is None:
            return _TerminalApproval(False)
        if approval.mode != approval_mode:
            continue
        if approval.role not in required_roles:
            return _TerminalApproval(False)
        revision = tx_revision.get(binding.accepted_transaction_id)
        if revision is None:
            return _TerminalApproval(False)
        if revision > latest.get(approval.role, (-1, ""))[0]:
            latest[approval.role] = (revision, approval.decision)
    complete = all(
        role in latest and latest[role][1] == "approve" for role in required_roles
    )
    decisions = tuple(
        (role, latest[role][1]) for role in required_roles if role in latest
    )
    return _TerminalApproval(True, required_roles, decisions, complete)


def classify_terminal_effect_authorization(
    snapshot: ControlStoreSnapshot,
    effect: CoreEffect,
    subject: TerminalEffectSubject,
) -> TerminalEffectAuthorization:
    """Authorize one terminal receipt from the immutable prefix before it."""

    def deny() -> TerminalEffectAuthorization:
        return TerminalEffectAuthorization("deny")

    def allow() -> TerminalEffectAuthorization:
        return TerminalEffectAuthorization("allow")

    if effect is CoreEffect.FINALIZE_RENDER:
        finalize = next(
            (item for item in snapshot.stage_states if item.stage_id == "finalize"),
            None,
        )
        return (
            allow()
            if finalize is not None
            and finalize.status == "ready"
            and not snapshot.finalize_renders
            and not snapshot.finalizations
            else deny()
        )
    if effect is CoreEffect.FINALIZE_COMPLETE:
        finalize = next(
            (item for item in snapshot.stage_states if item.stage_id == "finalize"),
            None,
        )
        return (
            allow()
            if finalize is not None
            and finalize.status == "ready"
            and len(snapshot.finalize_renders) == 1
            and not snapshot.finalizations
            and not snapshot.package_ready_records
            else deny()
        )

    packages = [
        item
        for item in snapshot.package_ready_records
        if item.package_id == subject.package_id
    ]
    if len(packages) != 1:
        return deny()
    if effect is CoreEffect.INTERNAL_APPROVAL:
        config = RELEASE_MODES.get(subject.approval_mode or "")
        if config is None or subject.approval_role not in tuple(
            config["required_roles"]
        ):
            return deny()
        return allow()
    if effect is CoreEffect.DELIVERY_AUTHORIZE:
        required = (
            subject.authorization_id,
            subject.approval_mode,
            subject.purpose,
            subject.decision,
            subject.target,
            subject.channel,
            subject.recipient_fingerprint,
        )
        if any(item is None for item in required):
            return deny()
        if any(
            item.authorization_id == subject.authorization_id
            for item in snapshot.delivery_authorizations
        ):
            return deny()
        tuple_state = _terminal_tuple(
            snapshot,
            package_id=subject.package_id or "",
            target=subject.target or "",
            channel=subject.channel or "",
            recipient_fingerprint=subject.recipient_fingerprint or "",
        )
        if not tuple_state.valid or subject.prior_authorization_id != (
            tuple_state.current_authorization.authorization_id
            if tuple_state.current_authorization is not None
            else None
        ):
            return deny()
        candidate = DeliveryAuthorizationRecord.model_construct(
            authorization_id=subject.authorization_id,
            package_id=subject.package_id,
            approval_mode=subject.approval_mode,
            retry_of_attempt_id=subject.retry_of_attempt_id,
            purpose=subject.purpose,
            target=subject.target,
            channel=subject.channel,
            recipient_fingerprint=subject.recipient_fingerprint,
        )
        return allow() if tuple_state.authorization_can_record(candidate) else deny()
    if effect is CoreEffect.DELIVERY_ATTEMPT:
        authorizations = [
            item
            for item in snapshot.delivery_authorizations
            if item.authorization_id == subject.authorization_id
        ]
        if len(authorizations) != 1:
            return deny()
        authorization = authorizations[0]
        tuple_state = _terminal_tuple(
            snapshot,
            package_id=authorization.package_id,
            target=authorization.target,
            channel=authorization.channel,
            recipient_fingerprint=authorization.recipient_fingerprint,
        )
        approval = tuple_state.approval(authorization.approval_mode)
        exact = (
            subject.package_id == authorization.package_id
            and subject.target == authorization.target
            and subject.channel == authorization.channel
            and subject.recipient_fingerprint == authorization.recipient_fingerprint
        )
        unused = (
            tuple_state.attempt_for_authorization(authorization.authorization_id)
            is None
        )
        unique_operation = not any(
            item.connector_operation_id == subject.connector_operation_id
            for item in snapshot.delivery_attempts
        )
        legal = (
            subject.attempt_id is not None
            and subject.connector_operation_id is not None
            and tuple_state.valid
            and tuple_state.current_authorization == authorization
            and authorization.decision == "authorize"
            and authorization.purpose in {"initial_attempt", "retry_attempt"}
            and tuple_state.authorization_can_record(authorization)
            and approval.valid
            and approval.complete
            and exact
            and unused
            and unique_operation
        )
        return allow() if legal else deny()
    if effect is not CoreEffect.DELIVERY_RESULT:
        return deny()
    attempts = [
        item
        for item in snapshot.delivery_attempts
        if item.attempt_id == subject.attempt_id
    ]
    if len(attempts) != 1:
        return deny()
    attempt = attempts[0]
    if (
        subject.package_id != attempt.package_id
        or subject.connector_operation_id != attempt.connector_operation_id
        or (attempt.target == "local" and subject.result_status != "bundle_prepared")
        or (attempt.target != "local" and subject.result_status == "bundle_prepared")
    ):
        return deny()
    tuple_state = _terminal_tuple(
        snapshot,
        package_id=attempt.package_id,
        target=attempt.target,
        channel=attempt.channel,
        recipient_fingerprint=attempt.recipient_fingerprint,
    )
    if not tuple_state.valid:
        return deny()
    tip = tuple_state.result_tip(attempt.attempt_id)
    if subject.reconciliation_authorization_id is None:
        return allow() if tip is None and subject.prior_result_id is None else deny()
    authorization = tuple_state.authorization(subject.reconciliation_authorization_id)
    if authorization is None or tip is None:
        return deny()
    approval = tuple_state.approval(authorization.approval_mode)
    legal = (
        subject.prior_result_id == tip.result_id
        and tip.status == "outcome_unknown"
        and tuple_state.current_authorization == authorization
        and authorization.decision == "authorize"
        and authorization.purpose == "result_reconciliation"
        and authorization.retry_of_attempt_id == attempt.attempt_id
        and authorization.package_id == attempt.package_id
        and authorization.target == attempt.target
        and authorization.channel == attempt.channel
        and authorization.recipient_fingerprint == attempt.recipient_fingerprint
        and approval.valid
        and approval.complete
        and authorization.authorization_id
        not in tuple_state.consumed_reconciliation_authorizations
    )
    return allow() if legal else deny()


def classify_terminal_legality(
    snapshot: ControlStoreSnapshot,
    *,
    authorization_id: str | None = None,
) -> TerminalLegality:
    """Derive approval, authorization, attempt and result legality without I/O."""

    if not snapshot.finalize_renders:
        finalize = next(
            (item for item in snapshot.stage_states if item.stage_id == "finalize"),
            None,
        )
        state = (
            "auditor_ready"
            if finalize is not None and finalize.status == "ready"
            else "core_active"
        )
        return TerminalLegality(
            state,
            terminal_state=state,
            next_effects=("finalize_render",) if state == "auditor_ready" else (),
        )
    if not snapshot.finalizations:
        gates = [
            item for item in snapshot.gate_evaluations if item.stage_id == "finalize"
        ]
        state = "gate_blocked" if any(item.blocking for item in gates) else "rendered"
        return TerminalLegality(
            state,
            terminal_state=state,
            next_effects=("finalize_gate", "finalize_complete")
            if state == "rendered"
            else (),
        )
    if not snapshot.package_ready_records:
        return TerminalLegality("finalized", terminal_state="finalized")
    if len(snapshot.package_ready_records) != 1:
        return TerminalLegality("invalid")
    package = snapshot.package_ready_records[0]
    authorizations = tuple(
        item
        for item in snapshot.delivery_authorizations
        if item.package_id == package.package_id
    )
    tuples: list[_TerminalTupleClassification] = []
    for target, channel, recipient_fingerprint in {
        (item.target, item.channel, item.recipient_fingerprint)
        for item in authorizations
    }:
        tuple_state = _terminal_tuple(
            snapshot,
            package_id=package.package_id,
            target=target,
            channel=channel,
            recipient_fingerprint=recipient_fingerprint,
        )
        if not tuple_state.valid:
            return TerminalLegality("invalid", package_id=package.package_id)
        tuples.append(tuple_state)
    auth_tips = [
        item.current_authorization
        for item in tuples
        if item.current_authorization is not None
    ]
    tx_revision = {
        item.transaction_id: item.committed_revision for item in snapshot.transactions
    }
    selected = None
    if authorization_id is not None:
        selected = next(
            (
                item
                for item in authorizations
                if item.authorization_id == authorization_id
            ),
            None,
        )
    elif auth_tips:
        selected = max(
            auth_tips,
            key=lambda item: tx_revision.get(item.accepted_transaction_id, -1),
        )
    if selected is None:
        observed_modes = sorted(
            {
                approval.mode
                for approval in snapshot.approvals
                if any(
                    binding.approval_id == approval.approval_id
                    and binding.package_id == package.package_id
                    for binding in snapshot.approval_package_bindings
                )
            }
        )
        if len(observed_modes) > 1:
            return TerminalLegality("invalid", package_id=package.package_id)
        if observed_modes:
            mode = observed_modes[0]
            approval = _approval_policy_details(
                snapshot,
                package_id=package.package_id,
                approval_mode=mode,
            )
            if not approval.valid:
                return TerminalLegality("invalid", package_id=package.package_id)
            return TerminalLegality(
                "package_ready",
                mode,
                approval.required_roles,
                approval.latest_decisions,
                approval.complete,
                next_effects=("delivery_authorization",)
                if approval.complete
                else ("approval",),
                terminal_state=(
                    "authorization_missing_or_denied"
                    if approval.complete
                    else "approval_incomplete"
                ),
                package_id=package.package_id,
            )
        return TerminalLegality(
            "package_ready",
            terminal_state="package_ready",
            package_id=package.package_id,
            next_effects=("approval", "authorization"),
        )
    tuple_state = next(
        (
            item
            for item in tuples
            if item.authorization(selected.authorization_id) is not None
        ),
        None,
    )
    if tuple_state is None:
        return TerminalLegality("invalid", package_id=package.package_id)
    approval = tuple_state.approval(selected.approval_mode)
    if not approval.valid:
        return TerminalLegality("invalid", package_id=package.package_id)
    required_roles = approval.required_roles
    latest_decisions = approval.latest_decisions
    approval_complete = approval.complete
    is_current = tuple_state.current_authorization == selected
    attempt = tuple_state.attempt_for_authorization(selected.authorization_id)
    if attempt is None and selected.purpose == "result_reconciliation":
        attempt = next(
            (
                item
                for item in tuple_state.ordered_attempts
                if item.attempt_id == selected.retry_of_attempt_id
            ),
            None,
        )
        if attempt is None:
            return TerminalLegality("invalid", package_id=package.package_id)
    if attempt is None:
        state = (
            "package_ready"
            if approval_complete and is_current and selected.decision == "authorize"
            else "approval_incomplete"
            if not approval_complete
            else "authorization_missing_or_denied"
        )
        return TerminalLegality(
            "package_ready",
            selected.approval_mode,
            required_roles,
            latest_decisions,
            approval_complete,
            selected.authorization_id,
            is_current,
            next_effects=("delivery_attempt",) if state == "package_ready" else (),
            terminal_state=state,
            package_id=package.package_id,
        )
    tip = tuple_state.result_tip(attempt.attempt_id)
    if tip is None:
        state = "attempt_pending"
        return TerminalLegality(
            "package_ready",
            selected.approval_mode,
            required_roles,
            latest_decisions,
            approval_complete,
            selected.authorization_id,
            is_current,
            attempt.attempt_id,
            next_effects=("delivery_result",),
            terminal_state=state,
            package_id=package.package_id,
        )
    state = {
        "bundle_prepared": "package_ready",
        "draft_created": "draft_created",
        "outcome_unknown": "delivery_outcome_unknown",
        "failed": "delivery_failed",
        "succeeded": "delivered",
    }[tip.status]
    if (attempt.target == "local" and tip.status != "bundle_prepared") or (
        attempt.target != "local" and tip.status == "bundle_prepared"
    ):
        return TerminalLegality("invalid", package_id=package.package_id)
    return TerminalLegality(
        "package_ready",
        selected.approval_mode,
        required_roles,
        latest_decisions,
        approval_complete,
        selected.authorization_id,
        is_current,
        attempt.attempt_id,
        tip.result_id,
        tip.status,
        terminal_state=state,
        package_id=package.package_id,
    )


def classify_terminal_state(snapshot: ControlStoreSnapshot) -> TerminalClassification:
    """Compatibility projection over the one pure terminal legality owner."""

    legality = classify_terminal_legality(snapshot)
    return TerminalClassification(
        legality.terminal_state,
        legality.package_id,
        legality.current_result_id,
    )


_Clock = Callable[[], datetime]


class CoreRunTerminalService:
    """Typed approval and delivery terminal transactions."""

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
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._scratch = ScratchReader(self.workspace)
        from .integrity import RunIntegrityService

        self._integrity = RunIntegrityService(self.workspace, clock=self._clock)

    def record_internal_approval(self, request: InternalApprovalRequest):
        return self._public(self._record_internal_approval, request)

    def authorize_delivery(self, request: DeliveryAuthorizationRequest):
        return self._public(self._authorize_delivery, request)

    def record_delivery_attempt(self, request: DeliveryAttemptRequest):
        return self._public(self._record_delivery_attempt, request)

    def record_delivery_result(self, request: DeliveryResultRequest):
        return self._public(self._record_delivery_result, request)

    def accept_finalize_render(self, request: FinalizeRenderRequest):
        return self._public(self._accept_finalize_render, request)

    def complete_finalize(self, request: FinalizeCompleteRequest):
        return self._public(self._complete_finalize, request)

    @staticmethod
    def _public(operation, request):
        from .errors import core_run_failure_result

        try:
            return operation(request)
        except (CoreRunError, ControlStoreError) as exc:
            return core_run_failure_result(exc)

    def _open_verified(self, request):
        from .verifier import CoreRunDomainVerifier, resolve_core_replay

        fingerprint = canonical_fingerprint(
            request.model_dump(mode="json", exclude_unset=False)
        )
        store = SQLiteControlStore.open(
            self.workspace / "briefloop.db",
            clock=self._clock,
        )
        replay = resolve_core_replay(
            store,
            run_id=request.run_id,
            request_id=request.request_id,
            request_fingerprint=fingerprint,
        )
        if replay is not None:
            store.close()
            return None, None, fingerprint, replay
        verifier = CoreRunDomainVerifier()
        verified = verifier.verify(store, request.run_id)
        if verified.snapshot.store_revision != request.expected_store_revision:
            store.close()
            raise CoreRunError("store_revision_conflict")
        try:
            blocked = self._integrity.require_clean(
                store,
                verified,
                request_id=request.request_id,
                request_fingerprint=fingerprint,
                expected_store_revision=request.expected_store_revision,
            )
        except (CoreRunError, ControlStoreError):
            store.close()
            raise
        if blocked is not None:
            store.close()
            return None, None, fingerprint, blocked
        return store, (verifier, verified), fingerprint, None

    def _record_internal_approval(self, request: InternalApprovalRequest):
        from multi_agent_brief.product.release_approval import APPROVAL_BOUNDARY

        from .checkout import prepare_checkout_effect, stage_checkout_effect
        from .errors import CoreRunResult
        from .policy import derived_id, transaction_type_for

        store, context, fingerprint, replay = self._open_verified(request)
        if replay is not None:
            return replay
        assert store is not None and context is not None
        verifier, verified = context
        try:
            classify_terminal_effect_authorization(
                verified.snapshot,
                CoreEffect.INTERNAL_APPROVAL,
                TerminalEffectSubject(
                    package_id=request.package_id,
                    approval_mode=request.mode,
                    approval_role=request.role,
                ),
            ).require_allowed()
            now = self._now()
            event_id = derived_id("EVT-APPROVAL", request.request_id, fingerprint)
            approval = Approval.model_validate(
                {
                    "schema_version": Approval.schema_id,
                    "approval_id": request.approval_id,
                    "run_id": request.run_id,
                    "mode": request.mode,
                    "role": request.role,
                    "decision": request.decision,
                    "reason": request.reason,
                    "actor_id": request.actor_id,
                    "recorded_at": now,
                    "boundary": APPROVAL_BOUNDARY,
                    "event_id": event_id,
                },
                strict=True,
            )
            package_binding = ApprovalPackageBinding.model_validate(
                {
                    "schema_version": ApprovalPackageBinding.schema_id,
                    "run_id": request.run_id,
                    "approval_id": request.approval_id,
                    "package_id": request.package_id,
                    "accepted_transaction_id": request.request_id,
                },
                strict=True,
            )
            event = self._event(
                event_id=event_id,
                event_type="human_approval_recorded",
                request=request,
                fingerprint=fingerprint,
                effect_kind="internal_approval",
                primary_record_id=request.approval_id,
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
                transaction_type_for("internal_approval"),
                request.expected_store_revision,
            )
            unit.put_approval(approval)
            unit.put_approval_package_binding(package_binding)
            unit.append_event(event)
            stage_checkout_effect(unit, checkout)
            receipt = unit.commit(
                _postcommit_observer=lambda _receipt: verifier.verify(
                    store, request.run_id
                )
            )
            return CoreRunResult(
                status="committed",
                receipt=receipt,
                primary_record_id=request.approval_id,
            )
        finally:
            store.close()

    def _accept_finalize_render(self, request: FinalizeRenderRequest):
        from pydantic import ValidationError

        from multi_agent_brief.outputs.reader_final_gate import detect_reader_residue

        from .checkout import (
            prepare_checkout_effect,
            publish_checkout_effect,
            stage_checkout_effect,
        )
        from .errors import CoreRunResult
        from .lineage import classify_current_audit_promotion
        from .policy import derived_id, transaction_type_for

        store, context, fingerprint, replay = self._open_verified(request)
        if replay is not None:
            return replay
        assert store is not None and context is not None
        verifier, verified = context
        try:
            classify_terminal_effect_authorization(
                verified.snapshot,
                CoreEffect.FINALIZE_RENDER,
                TerminalEffectSubject(),
            ).require_allowed()
            promotion = classify_current_audit_promotion(
                verified.snapshot, store.read_artifact_revision_bytes
            )
            if (
                promotion is None
                or not promotion.is_current_lineage
                or promotion.proposal_record.proposal_id != request.audit_proposal_id
                or request.expected_audited_brief.artifact_id
                != promotion.brief_revision.artifact_id
                or request.expected_audited_brief.revision
                != promotion.brief_revision.revision
                or request.expected_audit_report.artifact_id
                != promotion.report_revision.artifact_id
                or request.expected_audit_report.revision
                != promotion.report_revision.revision
            ):
                raise CoreRunError("finalize_input_invalid")
            artifacts = {item.artifact_id: item for item in verified.snapshot.artifacts}
            contracts = {str(item["artifact_id"]): item for item in verified.artifacts}
            if set(request.reader_scratch_inputs) != {"reader_brief"}:
                raise CoreRunError("finalize_input_invalid")
            rows: list[tuple[ArtifactRecord, ArtifactRevision, bytes]] = []
            residue_fingerprints: list[dict[str, object]] = []
            now = self._now()
            for artifact_id in sorted(request.reader_scratch_inputs):
                try:
                    content = self._scratch.read(request.reader_scratch_inputs[artifact_id])
                    if sha256_hex(content) != request.expected_reader_sha256[artifact_id]:
                        raise CoreRunError("finalize_input_invalid")
                    text = content.decode("utf-8")
                except (
                    IntakeError,
                    OSError,
                    RuntimeError,
                    UnicodeError,
                    ValueError,
                ) as exc:
                    raise CoreRunError("finalize_input_invalid") from exc
                residue = detect_reader_residue(text, artifact_id)
                if residue.status != "pass":
                    raise CoreRunError("finalize_input_invalid")
                prior = artifacts.get(artifact_id)
                expected_revision = request.expected_reader_revisions[artifact_id]
                if prior is None:
                    contract = contracts.get(artifact_id)
                    if contract is None or expected_revision != 0:
                        raise CoreRunError("finalize_input_invalid")
                    record = ArtifactRecord.model_validate(
                        {
                            "schema_version": ArtifactRecord.schema_id,
                            "run_id": request.run_id,
                            "artifact_id": artifact_id,
                            "current_revision": 1,
                            "status": "valid",
                            "required": bool(contract["required"]),
                            "path": str(contract["path"]),
                            "format": str(contract["format"]),
                        },
                        strict=True,
                    )
                else:
                    if prior.current_revision != expected_revision:
                        raise CoreRunError("finalize_input_invalid")
                    record = prior.model_copy(
                        update={"current_revision": expected_revision + 1, "status": "valid"}
                    )
                revision = ArtifactRevision.model_validate(
                    {
                        "schema_version": ArtifactRevision.schema_id,
                        "run_id": request.run_id,
                        "artifact_id": artifact_id,
                        "revision": record.current_revision,
                        "path": record.path,
                        "sha256": sha256_hex(content),
                        "size_bytes": len(content),
                        "frozen": True,
                        "producer_kind": "control_tool",
                        "producer_id": "core-v2-finalize-render",
                        "created_at": now,
                    },
                    strict=True,
                )
                rows.append((record, revision, content))
                residue_fingerprints.append(
                    {"artifact_id": artifact_id, "sha256": revision.sha256, "status": residue.status}
                )
            render_id = derived_id("RENDER", request.request_id, fingerprint)
            event_id = derived_id("EVT-RENDER", request.request_id, fingerprint)
            render = FinalizeRenderRecord.model_validate(
                {
                    "schema_version": FinalizeRenderRecord.schema_id,
                    "render_id": render_id,
                    "run_id": request.run_id,
                    "audit_proposal_id": request.audit_proposal_id,
                    "audited_brief": request.expected_audited_brief,
                    "audit_report": request.expected_audit_report,
                    "reader_artifacts": [
                        {"artifact_id": revision.artifact_id, "revision": revision.revision}
                        for _record, revision, _content in rows
                    ],
                    "reader_clean_status": "pass",
                    "policy_result_fingerprint": canonical_fingerprint(residue_fingerprints),
                    "run_contract_fingerprint": verified.binding.contract_fingerprint,
                    "created_at": now,
                    "render_event_id": event_id,
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
                additional_revisions=tuple(item[1] for item in rows),
            )
            unit = store.begin(
                request.run_id,
                request.request_id,
                transaction_type_for("finalize_render"),
                request.expected_store_revision,
            )
            for record, revision, content in rows:
                unit.put_artifact(record)
                unit.put_artifact_revision(revision, content)
            unit.put_finalize_render(render)
            unit.append_event(
                self._event(
                    event_id=event_id,
                    event_type="owned_artifact_accepted",
                    request=request,
                    fingerprint=fingerprint,
                    effect_kind="finalize_render",
                    primary_record_id=render_id,
                )
            )
            stage_checkout_effect(unit, checkout)
            receipt = unit.commit(
                _postcommit_observer=lambda _receipt: verifier.verify(store, request.run_id)
            )
            published, _warnings = publish_checkout_effect(
                workspace=self.workspace, store=store, prepared=checkout
            )
            if not published:
                return CoreRunResult(status="commit_outcome_unknown", error_code="commit_outcome_unknown")
            return CoreRunResult(status="committed", receipt=receipt, primary_record_id=render_id)
        except ValidationError as exc:
            raise CoreRunError("finalize_input_invalid") from exc
        finally:
            store.close()

    def _authorize_delivery(self, request: DeliveryAuthorizationRequest):
        from .checkout import prepare_checkout_effect, stage_checkout_effect
        from .errors import CoreRunResult
        from .policy import derived_id, transaction_type_for

        store, context, fingerprint, replay = self._open_verified(request)
        if replay is not None:
            return replay
        assert store is not None and context is not None
        verifier, verified = context
        try:
            authorization_id = derived_id(
                "AUTH", request.request_id, fingerprint
            )
            classify_terminal_effect_authorization(
                verified.snapshot,
                CoreEffect.DELIVERY_AUTHORIZE,
                TerminalEffectSubject(
                    package_id=request.package_id,
                    authorization_id=authorization_id,
                    approval_mode=request.approval_mode,
                    prior_authorization_id=request.prior_authorization_id,
                    retry_of_attempt_id=request.retry_of_attempt_id,
                    purpose=request.purpose,
                    decision=request.decision,
                    target=request.target,
                    channel=request.channel,
                    recipient_fingerprint=request.recipient_fingerprint,
                ),
            ).require_allowed()
            event_id = derived_id("EVT-AUTH", request.request_id, fingerprint)
            now = self._now()
            record = DeliveryAuthorizationRecord.model_validate(
                {
                    "schema_version": DeliveryAuthorizationRecord.schema_id,
                    "authorization_id": authorization_id,
                    "run_id": request.run_id,
                    "package_id": request.package_id,
                    "prior_authorization_id": request.prior_authorization_id,
                    "approval_mode": request.approval_mode,
                    "retry_of_attempt_id": request.retry_of_attempt_id,
                    "purpose": request.purpose,
                    "decision": request.decision,
                    "target": request.target,
                    "channel": request.channel,
                    "recipient_fingerprint": request.recipient_fingerprint,
                    "actor_id": request.actor_id,
                    "reason": request.reason,
                    "recorded_at": now,
                    "authorization_event_id": event_id,
                    "accepted_transaction_id": request.request_id,
                    "request_fingerprint": fingerprint,
                },
                strict=True,
            )
            event = self._event(
                event_id=event_id,
                event_type="decision_recorded",
                request=request,
                fingerprint=fingerprint,
                effect_kind="delivery_authorization",
                primary_record_id=authorization_id,
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
                transaction_type_for("delivery_authorization"),
                request.expected_store_revision,
            )
            unit.put_delivery_authorization(record)
            unit.append_event(event)
            stage_checkout_effect(unit, checkout)
            receipt = unit.commit(
                _postcommit_observer=lambda _receipt: verifier.verify(
                    store, request.run_id
                )
            )
            return CoreRunResult(status="committed", receipt=receipt, primary_record_id=authorization_id)
        finally:
            store.close()

    def _complete_finalize(self, request: FinalizeCompleteRequest):
        from multi_agent_brief.quality_gates.contract import GATE_IDS

        from .checkout import (
            prepare_checkout_effect,
            publish_checkout_effect,
            stage_checkout_effect,
        )
        from .errors import CoreRunResult
        from .policy import archive_artifact_usage, derived_id, transaction_type_for
        from .recovery import classify_recovery_legality

        store, context, fingerprint, replay = self._open_verified(request)
        if replay is not None:
            return replay
        assert store is not None and context is not None
        verifier, verified = context
        try:
            classify_terminal_effect_authorization(
                verified.snapshot,
                CoreEffect.FINALIZE_COMPLETE,
                TerminalEffectSubject(),
            ).require_allowed()
            snapshot = verified.snapshot
            renders = [item for item in snapshot.finalize_renders if item.render_id == request.render_id]
            stage = next((item for item in snapshot.stage_states if item.stage_id == "finalize"), None)
            selected = sorted(
                (
                    item
                    for item in snapshot.gate_evaluations
                    if item.evaluation_id in set(request.gate_evaluation_ids)
                ),
                key=lambda item: item.gate_id,
            )
            legality = classify_recovery_legality(snapshot)
            expected_recovery_id = legality.recovery_id if legality.state == "recovered_current" else None
            if (
                len(renders) != 1
                or stage is None
                or stage.status != "ready"
                or stage.revision != request.expected_finalize_stage_revision
                or request.gate_evaluation_ids != sorted(set(request.gate_evaluation_ids))
                or len(selected) != len(request.gate_evaluation_ids)
                or {item.gate_id for item in selected} != set(GATE_IDS)
                or len({item.gate_batch_id for item in selected}) != 1
                or any(item.stage_id != "finalize" or item.blocking or item.status not in {"pass", "warning"} for item in selected)
                or request.recovery_id != expected_recovery_id
            ):
                raise CoreRunError("finalize_gate_blocked")
            render = renders[0]
            now = self._now()
            finalization_id = derived_id("FINALIZATION", request.request_id, fingerprint)
            transition_id = derived_id("TRANSITION-FINALIZE", request.request_id, fingerprint)
            final_event_id = derived_id("EVT-FINALIZED", request.request_id, fingerprint)
            archive_id = derived_id("ARCHIVE", request.request_id, fingerprint)
            archive_event_id = derived_id("EVT-ARCHIVE", request.request_id, fingerprint)
            package_id = derived_id("PACKAGE", request.request_id, fingerprint)
            package_event_id = derived_id("EVT-PACKAGE", request.request_id, fingerprint)
            revisions = {(item.artifact_id, item.revision): item for item in snapshot.artifact_revisions}
            current = sorted(
                (
                    revisions[(artifact.artifact_id, artifact.current_revision)]
                    for artifact in snapshot.artifacts
                    if artifact.current_revision > 0
                ),
                key=lambda item: (item.artifact_id, item.revision),
            )
            archive_bytes = canonical_json_bytes(
                {
                    "schema_version": "briefloop.core_v2_run_archive.v1",
                    "run_id": request.run_id,
                    "finalization_id": finalization_id,
                    "artifacts": [
                        {"artifact_id": item.artifact_id, "revision": item.revision, "sha256": item.sha256}
                        for item in current
                    ],
                }
            ) + b"\n"
            archive_revision = ArtifactRevision.model_validate(
                {
                    "schema_version": ArtifactRevision.schema_id,
                    "run_id": request.run_id,
                    "artifact_id": "core_v2_run_archive",
                    "revision": 1,
                    "path": "output/intermediate/core_v2_run_archive.json",
                    "sha256": sha256_hex(archive_bytes),
                    "size_bytes": len(archive_bytes),
                    "frozen": True,
                    "producer_kind": "control_tool",
                    "producer_id": "core-v2-finalize-complete",
                    "created_at": now,
                },
                strict=True,
            )
            reader_revisions = [revisions[(item.artifact_id, item.revision)] for item in render.reader_artifacts]
            package_bytes = canonical_json_bytes(
                {
                    "schema_version": "briefloop.core_v2_package_manifest.v1",
                    "run_id": request.run_id,
                    "finalization_id": finalization_id,
                    "archive": {"artifact_id": archive_revision.artifact_id, "revision": 1, "sha256": archive_revision.sha256},
                    "reader_artifacts": [
                        {"artifact_id": item.artifact_id, "revision": item.revision, "sha256": item.sha256}
                        for item in reader_revisions
                    ],
                }
            ) + b"\n"
            package_revision = ArtifactRevision.model_validate(
                {
                    "schema_version": ArtifactRevision.schema_id,
                    "run_id": request.run_id,
                    "artifact_id": "core_v2_package_manifest",
                    "revision": 1,
                    "path": "output/intermediate/core_v2_package_manifest.json",
                    "sha256": sha256_hex(package_bytes),
                    "size_bytes": len(package_bytes),
                    "frozen": True,
                    "producer_kind": "control_tool",
                    "producer_id": "core-v2-finalize-complete",
                    "created_at": now,
                },
                strict=True,
            )
            transition = StageTransitionRecord.model_validate(
                {
                    "schema_version": StageTransitionRecord.schema_id,
                    "transition_id": transition_id,
                    "run_id": request.run_id,
                    "stage_id": "finalize",
                    "transition_kind": "complete",
                    "requested_decision": "continue",
                    "prior_status": stage.status,
                    "prior_revision": stage.revision,
                    "result_status": "complete",
                    "result_revision": stage.revision + 1,
                    "reason": "Finalize Gate passed and immutable package was created",
                    "run_contract_fingerprint": verified.binding.contract_fingerprint,
                    "actor": "system",
                    "producer_invocation_id": None,
                    "producer_tool_id": "core-v2-finalize-complete",
                    "producer_result_status": None,
                    "producer_result_fingerprint": None,
                    "producer_implementation": None,
                    "producer_version": None,
                    "topology": None,
                    "satisfaction_source_kind": None,
                    "satisfied_by_id": None,
                    "created_at": now,
                    "transition_event_id": final_event_id,
                    "accepted_transaction_id": request.request_id,
                    "request_fingerprint": fingerprint,
                },
                strict=True,
            )
            finalization = FinalizationRecord.model_validate(
                {
                    "schema_version": FinalizationRecord.schema_id,
                    "finalization_id": finalization_id,
                    "run_id": request.run_id,
                    "render_id": render.render_id,
                    "finalize_transition_id": transition_id,
                    "finalize_gate_batch_id": selected[0].gate_batch_id,
                    "finalize_gate_evaluation_ids": request.gate_evaluation_ids,
                    "recovery_id": request.recovery_id,
                    "integrity_revision": snapshot.run_integrity_records[-1].integrity_revision,
                    "finalized_at": now,
                    "finalization_event_id": final_event_id,
                    "accepted_transaction_id": request.request_id,
                    "request_fingerprint": fingerprint,
                },
                strict=True,
            )
            archive = RunArchiveRecord.model_validate(
                {
                    "schema_version": RunArchiveRecord.schema_id,
                    "archive_id": archive_id,
                    "run_id": request.run_id,
                    "finalization_id": finalization_id,
                    "archive_artifact": {"artifact_id": archive_revision.artifact_id, "revision": 1},
                    "manifest_sha256": archive_revision.sha256,
                    "included_count": len(current),
                    "created_at": now,
                    "archive_event_id": archive_event_id,
                    "accepted_transaction_id": request.request_id,
                    "request_fingerprint": fingerprint,
                },
                strict=True,
            )
            package_members = [*reader_revisions, archive_revision, package_revision]
            package = PackageReadyRecord.model_validate(
                {
                    "schema_version": PackageReadyRecord.schema_id,
                    "package_id": package_id,
                    "run_id": request.run_id,
                    "finalization_id": finalization_id,
                    "archive_id": archive_id,
                    "package_manifest_artifact": {"artifact_id": package_revision.artifact_id, "revision": 1},
                    "package_manifest_sha256": package_revision.sha256,
                    "artifact_count": len(package_members),
                    "created_at": now,
                    "package_event_id": package_event_id,
                    "accepted_transaction_id": request.request_id,
                    "request_fingerprint": fingerprint,
                },
                strict=True,
            )
            checkout = prepare_checkout_effect(
                workspace=self.workspace,
                snapshot=snapshot,
                transaction_id=request.request_id,
                created_at=self._clock(),
                additional_revisions=(archive_revision, package_revision),
            )
            unit = store.begin(request.run_id, request.request_id, transaction_type_for("finalize_complete"), request.expected_store_revision)
            unit.put_stage_state(StageState.model_validate({"schema_version": StageState.schema_id, "run_id": request.run_id, "stage_id": "finalize", "status": "complete", "revision": stage.revision + 1, "updated_at": now}, strict=True))
            unit.append_stage_transition(transition)
            first_gate = selected[0].evaluation_id
            consumed = {
                (item.artifact_id, item.artifact_revision): revisions[(item.artifact_id, item.artifact_revision)]
                for item in snapshot.gate_artifact_bindings
                if item.evaluation_id == first_gate
            }
            consumed.update({(item.artifact_id, item.revision): item for item in reader_revisions})
            transition_inputs = sorted(
                [*((item, "consumed") for item in consumed.values()), (archive_revision, "produced"), (package_revision, "produced")],
                key=lambda item: (item[0].artifact_id, item[0].revision),
            )
            for position, (revision, usage) in enumerate(transition_inputs):
                unit.put_stage_artifact_binding(StageArtifactBinding.model_validate({"schema_version": StageArtifactBinding.schema_id, "run_id": request.run_id, "transition_id": transition_id, "position": position, "artifact_id": revision.artifact_id, "artifact_revision": revision.revision, "artifact_sha256": revision.sha256, "usage": usage, "accepted_transaction_id": request.request_id}, strict=True))
            for evaluation in selected:
                unit.put_stage_gate_binding(StageGateBinding.model_validate({"schema_version": StageGateBinding.schema_id, "run_id": request.run_id, "transition_id": transition_id, "gate_id": evaluation.gate_id, "evaluation_id": evaluation.evaluation_id, "accepted_transaction_id": request.request_id}, strict=True))
            for artifact_id, revision, content in ((archive_revision.artifact_id, archive_revision, archive_bytes), (package_revision.artifact_id, package_revision, package_bytes)):
                unit.put_artifact(ArtifactRecord.model_validate({"schema_version": ArtifactRecord.schema_id, "run_id": request.run_id, "artifact_id": artifact_id, "current_revision": 1, "status": "valid", "required": True, "path": revision.path, "format": "json"}, strict=True))
                unit.put_artifact_revision(revision, content)
            unit.put_finalization(finalization)
            unit.put_run_archive(archive)
            for position, revision in enumerate(current):
                unit.put_run_archive_artifact_binding(RunArchiveArtifactBinding.model_validate({"schema_version": RunArchiveArtifactBinding.schema_id, "run_id": request.run_id, "archive_id": archive_id, "position": position, "artifact_id": revision.artifact_id, "artifact_revision": revision.revision, "artifact_sha256": revision.sha256, "usage": archive_artifact_usage(revision.artifact_id), "accepted_transaction_id": request.request_id}, strict=True))
            unit.put_package_ready(package)
            for position, revision in enumerate(package_members):
                usage = "archive" if revision.artifact_id == archive_revision.artifact_id else "manifest" if revision.artifact_id == package_revision.artifact_id else "reader"
                unit.put_package_artifact_binding(PackageArtifactBinding.model_validate({"schema_version": PackageArtifactBinding.schema_id, "run_id": request.run_id, "package_id": package_id, "position": position, "artifact_id": revision.artifact_id, "artifact_revision": revision.revision, "artifact_sha256": revision.sha256, "usage": usage, "accepted_transaction_id": request.request_id}, strict=True))
            unit.append_event(self._event(event_id=final_event_id, event_type="stage_status_changed", request=request, fingerprint=fingerprint, effect_kind="finalize_complete", primary_record_id=finalization_id))
            for event_id, event_type, primary_id in ((archive_event_id, "run_archived", archive_id), (package_event_id, "decision_recorded", package_id)):
                unit.append_event(self._event(event_id=event_id, event_type=event_type, request=request, fingerprint=fingerprint, effect_kind="finalize_complete", primary_record_id=primary_id, bind=False))
            stage_checkout_effect(unit, checkout)
            receipt = unit.commit(_postcommit_observer=lambda _receipt: verifier.verify(store, request.run_id))
            published, _warnings = publish_checkout_effect(workspace=self.workspace, store=store, prepared=checkout)
            if not published:
                return CoreRunResult(status="commit_outcome_unknown", error_code="commit_outcome_unknown")
            return CoreRunResult(status="committed", receipt=receipt, primary_record_id=finalization_id)
        finally:
            store.close()

    def _record_delivery_attempt(self, request: DeliveryAttemptRequest):
        from .checkout import prepare_checkout_effect, stage_checkout_effect
        from .errors import CoreRunResult
        from .policy import derived_id, transaction_type_for

        store, context, fingerprint, replay = self._open_verified(request)
        if replay is not None:
            return replay
        assert store is not None and context is not None
        verifier, verified = context
        try:
            authorization = next(
                (
                    item
                    for item in verified.snapshot.delivery_authorizations
                    if item.authorization_id == request.authorization_id
                ),
                None,
            )
            if authorization is None or authorization.package_id != request.package_id:
                raise CoreRunError("delivery_authorization_invalid")
            attempt_id = derived_id("ATTEMPT", request.request_id, fingerprint)
            classify_terminal_effect_authorization(
                verified.snapshot,
                CoreEffect.DELIVERY_ATTEMPT,
                TerminalEffectSubject(
                    package_id=request.package_id,
                    authorization_id=request.authorization_id,
                    target=authorization.target,
                    channel=authorization.channel,
                    recipient_fingerprint=authorization.recipient_fingerprint,
                    attempt_id=attempt_id,
                    connector_operation_id=request.connector_operation_id,
                ),
            ).require_allowed()
            event_id = derived_id("EVT-ATTEMPT", request.request_id, fingerprint)
            record = DeliveryAttemptRecord.model_validate(
                {
                    "schema_version": DeliveryAttemptRecord.schema_id,
                    "attempt_id": attempt_id,
                    "run_id": request.run_id,
                    "package_id": request.package_id,
                    "authorization_id": request.authorization_id,
                    "target": authorization.target,
                    "channel": authorization.channel,
                    "recipient_fingerprint": authorization.recipient_fingerprint,
                    "connector_operation_id": request.connector_operation_id,
                    "connector_request_fingerprint": request.connector_request_fingerprint,
                    "created_at": self._now(),
                    "attempt_event_id": event_id,
                    "accepted_transaction_id": request.request_id,
                    "request_fingerprint": fingerprint,
                },
                strict=True,
            )
            event = self._event(
                event_id=event_id,
                event_type="delivery_attempted",
                request=request,
                fingerprint=fingerprint,
                effect_kind="delivery_attempt",
                primary_record_id=attempt_id,
            )
            checkout = prepare_checkout_effect(
                workspace=self.workspace,
                snapshot=verified.snapshot,
                transaction_id=request.request_id,
                created_at=self._clock(),
            )
            unit = store.begin(request.run_id, request.request_id, transaction_type_for("delivery_attempt"), request.expected_store_revision)
            unit.put_delivery_attempt(record)
            unit.append_event(event)
            stage_checkout_effect(unit, checkout)
            receipt = unit.commit(_postcommit_observer=lambda _receipt: verifier.verify(store, request.run_id))
            return CoreRunResult(status="committed", receipt=receipt, primary_record_id=attempt_id)
        finally:
            store.close()

    def _record_delivery_result(self, request: DeliveryResultRequest):
        from pydantic import ValidationError

        from .checkout import prepare_checkout_effect, stage_checkout_effect
        from .errors import CoreRunResult
        from .policy import derived_id, transaction_type_for

        store, context, fingerprint, replay = self._open_verified(request)
        if replay is not None:
            return replay
        assert store is not None and context is not None
        verifier, verified = context
        try:
            if request.observation_input_path is None or request.expected_observation_sha256 is None:
                raise CoreRunError("delivery_result_invalid")
            try:
                observation_bytes = self._scratch.read(request.observation_input_path)
                if sha256_hex(observation_bytes) != request.expected_observation_sha256:
                    raise CoreRunError("delivery_result_invalid")
                observation = DeliveryResultObservation.model_validate(
                    parse_json_object(observation_bytes), strict=True
                )
            except (
                IntakeError,
                OSError,
                RuntimeError,
                ValidationError,
                ValueError,
            ) as exc:
                raise CoreRunError("delivery_result_invalid") from exc
            attempt = next((item for item in verified.snapshot.delivery_attempts if item.attempt_id == request.attempt_id), None)
            if (
                attempt is None
                or observation.attempt_id != attempt.attempt_id
                or observation.adapter_id != verified.runtime_adapter.adapter_id
                or observation.adapter_version
                != verified.runtime_adapter.adapter_version
                or observation.connector_operation_id != attempt.connector_operation_id
                or observation.connector_request_fingerprint != attempt.connector_request_fingerprint
            ):
                raise CoreRunError("delivery_result_invalid")
            classify_terminal_effect_authorization(
                verified.snapshot,
                CoreEffect.DELIVERY_RESULT,
                TerminalEffectSubject(
                    package_id=attempt.package_id,
                    attempt_id=request.attempt_id,
                    connector_operation_id=attempt.connector_operation_id,
                    prior_result_id=request.prior_result_id,
                    reconciliation_authorization_id=request.reconciliation_authorization_id,
                    result_status=observation.status,
                ),
            ).require_allowed()
            result_id = derived_id("RESULT", request.request_id, fingerprint)
            event_id = derived_id("EVT-RESULT", request.request_id, fingerprint)
            record = DeliveryResultRecord.model_validate(
                {
                    "schema_version": DeliveryResultRecord.schema_id,
                    "result_id": result_id,
                    "run_id": request.run_id,
                    "attempt_id": request.attempt_id,
                    "prior_result_id": request.prior_result_id,
                    "reconciliation_authorization_id": request.reconciliation_authorization_id,
                    "status": observation.status,
                    "adapter_id": observation.adapter_id,
                    "adapter_version": observation.adapter_version,
                    "connector_operation_id": observation.connector_operation_id,
                    "evidence_sha256": observation.evidence_sha256,
                    "evidence_artifact": None,
                    "recorded_at": self._now(),
                    "result_event_id": event_id,
                    "accepted_transaction_id": request.request_id,
                    "request_fingerprint": fingerprint,
                },
                strict=True,
            )
            event_type = {
                "bundle_prepared": "delivery_bundle_prepared",
                "draft_created": "delivery_draft_created",
                "succeeded": "delivery_succeeded",
                "failed": "delivery_failed",
                "outcome_unknown": "decision_recorded",
            }[observation.status]
            event = self._event(event_id=event_id, event_type=event_type, request=request, fingerprint=fingerprint, effect_kind="delivery_result", primary_record_id=result_id)
            checkout = prepare_checkout_effect(workspace=self.workspace, snapshot=verified.snapshot, transaction_id=request.request_id, created_at=self._clock())
            unit = store.begin(request.run_id, request.request_id, transaction_type_for("delivery_result"), request.expected_store_revision)
            unit.put_delivery_result(record)
            unit.append_event(event)
            stage_checkout_effect(unit, checkout)
            receipt = unit.commit(_postcommit_observer=lambda _receipt: verifier.verify(store, request.run_id))
            return CoreRunResult(status="committed", receipt=receipt, primary_record_id=result_id)
        finally:
            store.close()

    def _event(self, *, event_id, event_type, request, fingerprint, effect_kind, primary_record_id, bind=True):
        return EventEnvelope.model_validate(
            {
                "schema_version": EventEnvelope.schema_id,
                "event_id": event_id,
                "run_id": request.run_id,
                "event_type": event_type,
                "created_at": self._now(),
                "actor": "system",
                "transaction_id": request.request_id,
                "stage_id": "finalize" if effect_kind.startswith("finalize_") else None,
                "decision": "continue",
                "reason": effect_kind,
                "metadata": {},
                "core_run_binding": (
                    CoreRunEventBinding(
                        request_id=request.request_id,
                        request_fingerprint=fingerprint,
                        effect_kind=effect_kind,
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
    "CoreRunTerminalService",
    "TerminalClassification",
    "TerminalEffectAuthorization",
    "TerminalEffectSubject",
    "TerminalLegality",
    "classify_terminal_effect_authorization",
    "classify_terminal_legality",
    "classify_terminal_state",
]
