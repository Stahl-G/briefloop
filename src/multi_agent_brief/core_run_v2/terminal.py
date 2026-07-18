"""Pure terminal legality for dormant fresh-v2 historical verification."""

from __future__ import annotations

from dataclasses import dataclass

from multi_agent_brief.contracts.v2 import (
    DeliveryAttemptRecord,
    DeliveryAuthorizationRecord,
    DeliveryResultRecord,
)
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


__all__ = [
    "TerminalClassification",
    "TerminalEffectAuthorization",
    "TerminalEffectSubject",
    "TerminalLegality",
    "classify_terminal_effect_authorization",
    "classify_terminal_legality",
    "classify_terminal_state",
]
