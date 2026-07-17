"""Pure terminal legality for dormant fresh-v2 historical verification."""

from __future__ import annotations

from dataclasses import dataclass

from multi_agent_brief.contracts.v2 import (
    DeliveryAuthorizationRecord,
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


def _authorization_chain(
    snapshot: ControlStoreSnapshot,
    *,
    package_id: str,
    target: str,
    channel: str,
    recipient_fingerprint: str,
) -> list[DeliveryAuthorizationRecord]:
    return [
        item
        for item in snapshot.delivery_authorizations
        if item.package_id == package_id
        and item.target == target
        and item.channel == channel
        and item.recipient_fingerprint == recipient_fingerprint
    ]


def _unique_authorization_tip(
    chain: list[DeliveryAuthorizationRecord],
) -> DeliveryAuthorizationRecord | None:
    if not chain:
        return None
    referenced = {
        item.prior_authorization_id
        for item in chain
        if item.prior_authorization_id is not None
    }
    tips = [item for item in chain if item.authorization_id not in referenced]
    return tips[0] if len(tips) == 1 else None


def _approval_policy_complete(
    snapshot: ControlStoreSnapshot,
    *,
    package_id: str,
    approval_mode: str,
) -> tuple[bool, bool]:
    config = RELEASE_MODES.get(approval_mode)
    if config is None:
        return False, False
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
            return False, False
        if approval.mode != approval_mode:
            continue
        if approval.role not in required_roles:
            return False, False
        revision = tx_revision.get(binding.accepted_transaction_id)
        if revision is None:
            return False, False
        if revision > latest.get(approval.role, (-1, ""))[0]:
            latest[approval.role] = (revision, approval.decision)
    complete = all(
        role in latest and latest[role][1] == "approve" for role in required_roles
    )
    return True, complete


def _unique_result_tip(snapshot: ControlStoreSnapshot, attempt_id: str):
    results = [
        item for item in snapshot.delivery_results if item.attempt_id == attempt_id
    ]
    if not results:
        return None
    referenced = {
        item.prior_result_id for item in results if item.prior_result_id is not None
    }
    tips = [item for item in results if item.result_id not in referenced]
    return tips[0] if len(tips) == 1 else None


def _authorization_purpose_is_legal(
    snapshot: ControlStoreSnapshot,
    authorization: DeliveryAuthorizationRecord,
) -> bool:
    if authorization.purpose == "initial_attempt":
        return authorization.retry_of_attempt_id is None
    if authorization.retry_of_attempt_id is None:
        return False
    attempts = [
        item
        for item in snapshot.delivery_attempts
        if item.attempt_id == authorization.retry_of_attempt_id
    ]
    if len(attempts) != 1:
        return False
    attempt = attempts[0]
    if (
        attempt.package_id != authorization.package_id
        or attempt.target != authorization.target
        or attempt.channel != authorization.channel
        or attempt.recipient_fingerprint != authorization.recipient_fingerprint
    ):
        return False
    tip = _unique_result_tip(snapshot, attempt.attempt_id)
    if tip is None:
        return False
    if authorization.purpose == "result_reconciliation":
        return tip.status == "outcome_unknown"
    return authorization.purpose == "retry_attempt" and tip.status in {
        "draft_created",
        "failed",
        "outcome_unknown",
    }


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
        chain = _authorization_chain(
            snapshot,
            package_id=subject.package_id or "",
            target=subject.target or "",
            channel=subject.channel or "",
            recipient_fingerprint=subject.recipient_fingerprint or "",
        )
        tip = _unique_authorization_tip(chain)
        if (chain and tip is None) or subject.prior_authorization_id != (
            tip.authorization_id if tip is not None else None
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
        return (
            allow() if _authorization_purpose_is_legal(snapshot, candidate) else deny()
        )
    if effect is CoreEffect.DELIVERY_ATTEMPT:
        authorizations = [
            item
            for item in snapshot.delivery_authorizations
            if item.authorization_id == subject.authorization_id
        ]
        if len(authorizations) != 1:
            return deny()
        authorization = authorizations[0]
        chain = _authorization_chain(
            snapshot,
            package_id=authorization.package_id,
            target=authorization.target,
            channel=authorization.channel,
            recipient_fingerprint=authorization.recipient_fingerprint,
        )
        tip = _unique_authorization_tip(chain)
        policy_valid, approval_complete = _approval_policy_complete(
            snapshot,
            package_id=authorization.package_id,
            approval_mode=authorization.approval_mode,
        )
        exact = (
            subject.package_id == authorization.package_id
            and subject.target == authorization.target
            and subject.channel == authorization.channel
            and subject.recipient_fingerprint == authorization.recipient_fingerprint
        )
        unused = not any(
            item.authorization_id == authorization.authorization_id
            for item in snapshot.delivery_attempts
        )
        unique_operation = not any(
            item.connector_operation_id == subject.connector_operation_id
            for item in snapshot.delivery_attempts
        )
        legal = (
            subject.attempt_id is not None
            and subject.connector_operation_id is not None
            and tip is not None
            and tip.authorization_id == authorization.authorization_id
            and authorization.decision == "authorize"
            and authorization.purpose in {"initial_attempt", "retry_attempt"}
            and _authorization_purpose_is_legal(snapshot, authorization)
            and policy_valid
            and approval_complete
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
        subject.connector_operation_id != attempt.connector_operation_id
        or (attempt.target == "local" and subject.result_status != "bundle_prepared")
        or (attempt.target != "local" and subject.result_status == "bundle_prepared")
    ):
        return deny()
    results = [
        item
        for item in snapshot.delivery_results
        if item.attempt_id == attempt.attempt_id
    ]
    if subject.reconciliation_authorization_id is None:
        return allow() if not results and subject.prior_result_id is None else deny()
    tip = _unique_result_tip(snapshot, attempt.attempt_id)
    authorizations = [
        item
        for item in snapshot.delivery_authorizations
        if item.authorization_id == subject.reconciliation_authorization_id
    ]
    if len(authorizations) != 1 or tip is None:
        return deny()
    authorization = authorizations[0]
    chain = _authorization_chain(
        snapshot,
        package_id=authorization.package_id,
        target=authorization.target,
        channel=authorization.channel,
        recipient_fingerprint=authorization.recipient_fingerprint,
    )
    auth_tip = _unique_authorization_tip(chain)
    policy_valid, approval_complete = _approval_policy_complete(
        snapshot,
        package_id=authorization.package_id,
        approval_mode=authorization.approval_mode,
    )
    legal = (
        subject.prior_result_id == tip.result_id
        and tip.status == "outcome_unknown"
        and auth_tip is not None
        and auth_tip.authorization_id == authorization.authorization_id
        and authorization.decision == "authorize"
        and authorization.purpose == "result_reconciliation"
        and authorization.retry_of_attempt_id == attempt.attempt_id
        and authorization.package_id == attempt.package_id
        and authorization.target == attempt.target
        and authorization.channel == attempt.channel
        and authorization.recipient_fingerprint == attempt.recipient_fingerprint
        and policy_valid
        and approval_complete
        and not any(
            item.reconciliation_authorization_id == authorization.authorization_id
            for item in snapshot.delivery_results
        )
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
    authorizations = [
        item
        for item in snapshot.delivery_authorizations
        if item.package_id == package.package_id
    ]
    chains: dict[tuple[str, str, str], list[DeliveryAuthorizationRecord]] = {}
    for item in authorizations:
        chains.setdefault(
            (item.target, item.channel, item.recipient_fingerprint), []
        ).append(item)
    auth_tips: list[DeliveryAuthorizationRecord] = []
    for chain in chains.values():
        referenced_auth = {
            item.prior_authorization_id
            for item in chain
            if item.prior_authorization_id is not None
        }
        tips = [item for item in chain if item.authorization_id not in referenced_auth]
        if len(tips) != 1:
            return TerminalLegality("invalid", package_id=package.package_id)
        auth_tips.extend(tips)
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
    required_roles = tuple(RELEASE_MODES[selected.approval_mode]["required_roles"])
    approvals = {item.approval_id: item for item in snapshot.approvals}
    latest: dict[str, tuple[int, str]] = {}
    for binding in snapshot.approval_package_bindings:
        approval = approvals.get(binding.approval_id)
        if (
            approval is None
            or binding.package_id != package.package_id
            or approval.mode != selected.approval_mode
        ):
            continue
        if approval.role not in required_roles:
            return TerminalLegality("invalid", package_id=package.package_id)
        revision = tx_revision.get(binding.accepted_transaction_id, -1)
        if revision > latest.get(approval.role, (-1, ""))[0]:
            latest[approval.role] = (revision, approval.decision)
    latest_decisions = tuple(
        (role, latest[role][1]) for role in required_roles if role in latest
    )
    approval_complete = all(
        role in latest and latest[role][1] == "approve" for role in required_roles
    )
    is_current = any(
        item.authorization_id == selected.authorization_id for item in auth_tips
    )
    attempts = [
        item
        for item in snapshot.delivery_attempts
        if item.authorization_id == selected.authorization_id
    ]
    if len(attempts) > 1:
        return TerminalLegality("invalid", package_id=package.package_id)
    if not attempts and selected.retry_of_attempt_id is not None:
        referenced_attempts = [
            item
            for item in snapshot.delivery_attempts
            if item.attempt_id == selected.retry_of_attempt_id
        ]
        if len(referenced_attempts) != 1:
            return TerminalLegality("invalid", package_id=package.package_id)
        referenced_results = [
            item
            for item in snapshot.delivery_results
            if item.attempt_id == selected.retry_of_attempt_id
        ]
        referenced_result_ids = {
            item.prior_result_id
            for item in referenced_results
            if item.prior_result_id is not None
        }
        referenced_tips = [
            item
            for item in referenced_results
            if item.result_id not in referenced_result_ids
        ]
        if len(referenced_tips) != 1:
            return TerminalLegality("invalid", package_id=package.package_id)
        if selected.purpose == "result_reconciliation":
            if referenced_tips[0].status != "outcome_unknown" and not (
                len(referenced_results) > 1
                and referenced_tips[0].reconciliation_authorization_id
                == selected.authorization_id
            ):
                return TerminalLegality("invalid", package_id=package.package_id)
            attempts = referenced_attempts
        elif referenced_tips[0].status not in {
            "draft_created",
            "failed",
            "outcome_unknown",
        }:
            return TerminalLegality("invalid", package_id=package.package_id)
    if not attempts:
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
    attempt = attempts[0]
    results = [
        item
        for item in snapshot.delivery_results
        if item.attempt_id == attempt.attempt_id
    ]
    if not results:
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
    referenced_results = {
        item.prior_result_id for item in results if item.prior_result_id is not None
    }
    tips = [item for item in results if item.result_id not in referenced_results]
    if len(tips) != 1:
        return TerminalLegality("invalid", package_id=package.package_id)
    tip = tips[0]
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
