"""Pure total next-action classifier for one verified CoreRun snapshot."""

from __future__ import annotations

from multi_agent_brief.contracts.v2 import CoreRunNextAction
from multi_agent_brief.control_store.serialization import canonical_fingerprint

from .errors import CoreRunError
from .recovery import classify_recovery_legality
from .terminal import classify_terminal_legality
from .verifier import VerifiedCoreRun


_ROLE_BY_STAGE = {
    "source-discovery": "source-planner",
    "scout": "scout",
    "screener": "screener",
    "claim-ledger": "claim-ledger",
    "analyst": "analyst",
    "editor": "editor",
    "auditor": "auditor",
}

_REQUEST_SCHEMA_BY_STAGE = {
    "source-discovery": "briefloop.source_proposal.v2",
    "scout": "briefloop.candidate_claims_proposal.v2",
    "screener": "briefloop.screened_candidates_proposal.v2",
    "claim-ledger": "briefloop.claim_drafts_proposal.v2",
    "analyst": "briefloop.owned_artifact_submit_request.v2",
    "editor": "briefloop.owned_artifact_submit_request.v2",
    "auditor": "briefloop.audit_proposal.v2",
}


def _action(
    verified: VerifiedCoreRun,
    *,
    action_kind: str,
    effect_kind: str,
    reason_code: str,
    stage_id: str | None = None,
    role_id: str | None = None,
    request_schema_id: str | None = None,
) -> CoreRunNextAction:
    snapshot = verified.snapshot
    revisions = sorted(
        (
            {"artifact_id": item.artifact_id, "revision": item.revision}
            for item in snapshot.artifact_revisions
            if next(
                (
                    artifact.current_revision
                    for artifact in snapshot.artifacts
                    if artifact.artifact_id == item.artifact_id
                ),
                0,
            )
            == item.revision
        ),
        key=lambda item: (str(item["artifact_id"]), int(item["revision"])),
    )
    payload: dict[str, object] = {
        "schema_version": CoreRunNextAction.schema_id,
        "run_id": snapshot.run.run_id,
        "store_revision": snapshot.store_revision,
        "action_kind": action_kind,
        "effect_kind": effect_kind,
        "stage_id": stage_id,
        "role_id": role_id,
        "reason_code": reason_code,
        "input_artifacts": revisions,
        "request_schema_id": request_schema_id,
        "adapter_binding_fingerprint": verified.runtime_adapter.binding_fingerprint,
        "source_plan_fingerprint": verified.source_plan.source_plan_fingerprint,
    }
    payload["action_fingerprint"] = canonical_fingerprint(payload)
    try:
        return CoreRunNextAction.model_validate(payload, strict=True)
    except (TypeError, ValueError) as exc:
        raise CoreRunError("control_store_integrity_invalid") from exc


def classify_core_run_next_action(verified: VerifiedCoreRun) -> CoreRunNextAction:
    """Return exactly one legal category without consulting mutable files."""

    snapshot = verified.snapshot
    recovery = classify_recovery_legality(snapshot)
    if recovery.state == "invalid":
        raise CoreRunError("control_store_integrity_invalid")
    if recovery.state == "blocked":
        return _action(
            verified,
            action_kind="deterministic",
            effect_kind="repair_start",
            reason_code="contamination_requires_repair",
            request_schema_id="briefloop.repair_start_request.v2",
        )
    if recovery.state == "active_repair":
        superseded = {
            item.prior_artifact.artifact_id
            for item in snapshot.artifact_supersessions
            if item.repair_id == recovery.repair_id
        }
        remaining = set(recovery.permitted_artifact_ids) - superseded
        effect = "artifact_supersede" if remaining else "repair_complete"
        schema = (
            "briefloop.artifact_supersede_request.v2"
            if remaining
            else "briefloop.repair_complete_request.v2"
        )
        return _action(
            verified,
            action_kind="deterministic",
            effect_kind=effect,
            reason_code="active_repair_requires_deterministic_effect",
            request_schema_id=schema,
        )
    if recovery.state == "rerun_required" and not recovery.required_rerun_transition_ids:
        return _action(
            verified,
            action_kind="deterministic",
            effect_kind="recovery_complete",
            reason_code="repair_rerun_complete",
            request_schema_id="briefloop.recovery_complete_request.v2",
        )

    active = [item for item in snapshot.invocations if item.status == "active"]
    if len(active) > 1:
        raise CoreRunError("control_store_integrity_invalid")
    if active:
        invocation = active[0]
        event = next(
            (
                item
                for item in snapshot.events
                if item.event_type == "role_invocation_started"
                and item.core_run_binding is not None
                and item.core_run_binding.primary_record_id == invocation.invocation_id
            ),
            None,
        )
        if event is None or event.stage_id is None:
            raise CoreRunError("control_store_integrity_invalid")
        return _action(
            verified,
            action_kind="deterministic",
            effect_kind="invocation_accept_or_fail",
            reason_code="active_invocation_reserved",
            stage_id=event.stage_id,
            request_schema_id=_REQUEST_SCHEMA_BY_STAGE.get(event.stage_id),
        )

    terminal = classify_terminal_legality(snapshot)
    if terminal.terminal_state == "invalid":
        raise CoreRunError("control_store_integrity_invalid")
    if terminal.terminal_state == "delivered":
        return _action(
            verified,
            action_kind="complete",
            effect_kind="delivered",
            reason_code="delivery_succeeded",
        )
    if terminal.terminal_state in {
        "auditor_ready",
        "rendered",
        "gate_blocked",
        "finalized",
        "package_ready",
        "approval_incomplete",
        "authorization_missing_or_denied",
        "attempt_pending",
        "delivery_outcome_unknown",
        "delivery_failed",
        "draft_created",
    }:
        if terminal.terminal_state in {"package_ready", "approval_incomplete"} and not terminal.approval_complete:
            return _action(
                verified,
                action_kind="human_decision",
                effect_kind="internal_approval",
                reason_code="human_approval_required",
                request_schema_id="briefloop.internal_approval_request.v2",
            )
        effects = terminal.next_effects
        if len(effects) > 1:
            # Rendered permits a Gate before completion; Gate is the sole first effect.
            effects = ("finalize_gate",)
        if effects:
            effect = effects[0]
            schema = {
                "finalize_render": "briefloop.finalize_render_request.v2",
                "finalize_gate": "briefloop.gate_check_request.v2",
                "finalize_complete": "briefloop.finalize_complete_request.v2",
                "delivery_attempt": "briefloop.delivery_attempt_request.v2",
                "delivery_result": "briefloop.delivery_result_request.v2",
            }.get(effect)
            return _action(
                verified,
                action_kind="deterministic",
                effect_kind=effect,
                reason_code="terminal_effect_required",
                stage_id="finalize" if effect.startswith("finalize") else None,
                request_schema_id=schema,
            )

    stages = {item.stage_id: item for item in snapshot.stage_states}
    ready = [
        str(item["stage_id"])
        for item in verified.stages
        if stages[str(item["stage_id"])].status == "ready"
    ]
    if len(ready) != 1:
        if not ready and all(item.status in {"complete", "skipped"} for item in stages.values()):
            return _action(
                verified,
                action_kind="blocked",
                effect_kind="terminal_incomplete",
                reason_code="terminal_state_incomplete",
            )
        raise CoreRunError("control_store_integrity_invalid")
    stage_id = ready[0]
    if stage_id in {"doctor", "input-governance"}:
        effect = "doctor_check" if stage_id == "doctor" else "owned_artifact_acceptance"
        schema = (
            "briefloop.integrity_check_request.v2"
            if stage_id == "doctor"
            else "briefloop.owned_artifact_submit_request.v2"
        )
        return _action(
            verified,
            action_kind="deterministic",
            effect_kind=effect,
            reason_code="deterministic_stage_effect_required",
            stage_id=stage_id,
            request_schema_id=schema,
        )

    role = _ROLE_BY_STAGE.get(stage_id)
    if stage_id == "analyst" and verified.binding.role_topology == "human_assisted":
        role = "writer"
    if role is None or role not in verified.runtime_adapter.role_ids:
        return _action(
            verified,
            action_kind="blocked",
            effect_kind="role_unavailable",
            reason_code="runtime_role_unavailable",
            stage_id=stage_id,
        )
    return _action(
        verified,
        action_kind="delegate",
        effect_kind="role_proposal",
        reason_code="role_proposal_required",
        stage_id=stage_id,
        role_id=role,
        request_schema_id=_REQUEST_SCHEMA_BY_STAGE[stage_id],
    )


__all__ = ["classify_core_run_next_action"]
