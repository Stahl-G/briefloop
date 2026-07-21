"""Pure total next-action classifier for one verified CoreRun snapshot."""

from __future__ import annotations

from multi_agent_brief.contracts.v2 import CoreRunNextAction
from multi_agent_brief.control_store.serialization import canonical_fingerprint
from multi_agent_brief.quality_gates.contract import GATE_IDS

from .errors import CoreRunError
from .gates import EVALUATOR_IMPLEMENTATION, EVALUATOR_VERSION
from .lineage import classify_current_lineage
from .policy import (
    REQUIRED_AUDITOR_GATES,
    SOURCE_ROUTE_OWNER_ORDER,
    core_role_topology_policy,
)
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
    "source-discovery": "briefloop.owned_artifact_submit_request.v2",
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
    source_route_id: str | None = None,
    source_provider_id: str | None = None,
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
        "source_route_id": source_route_id,
        "source_provider_id": source_provider_id,
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
            reason_code=(
                "active_repair_requires_projection_preimage_restore"
                if remaining
                else "active_repair_requires_deterministic_effect"
            ),
            request_schema_id=schema,
        )
    if recovery.state == "rerun_required" and recovery.required_rerun_transition_ids:
        return _action(
            verified,
            action_kind="deterministic",
            effect_kind="recovery_complete",
            reason_code="repair_rerun_complete",
            request_schema_id="briefloop.recovery_complete_request.v2",
        )
    rerun_stage_id: str | None = None
    if recovery.state == "rerun_required" and recovery.repair_id is not None:
        repairs = [
            item
            for item in snapshot.repair_cycles
            if item.repair_id == recovery.repair_id
        ]
        if len(repairs) != 1:
            raise CoreRunError("control_store_integrity_invalid")
        rerun_stage_id = repairs[0].owner_stage_id

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
            request_schema_id=(
                "briefloop.source_commit_request.v2"
                if invocation.role_id == "source-provider"
                else _REQUEST_SCHEMA_BY_STAGE.get(event.stage_id)
            ),
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
    if (
        terminal.terminal_state == "package_ready"
        and terminal.current_result_status == "bundle_prepared"
    ):
        return _action(
            verified,
            action_kind="complete",
            effect_kind="package_ready",
            reason_code="local_delivery_bundle_prepared",
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
        if (
            terminal.terminal_state in {"package_ready", "approval_incomplete"}
            and not terminal.approval_complete
        ):
            return _action(
                verified,
                action_kind="human_decision",
                effect_kind="internal_approval",
                reason_code="human_approval_required",
                request_schema_id="briefloop.internal_approval_request.v2",
            )
        if (
            terminal.terminal_state == "authorization_missing_or_denied"
            and terminal.approval_complete
        ):
            return _action(
                verified,
                action_kind="human_decision",
                effect_kind="delivery_authorization",
                reason_code="delivery_authorization_required",
                request_schema_id="briefloop.delivery_authorization_request.v2",
            )
        if terminal.terminal_state == "delivery_outcome_unknown":
            return _action(
                verified,
                action_kind="human_decision",
                effect_kind="delivery_reconciliation",
                reason_code="delivery_outcome_requires_reconciliation",
                request_schema_id="briefloop.delivery_authorization_request.v2",
            )
        if terminal.terminal_state in {"delivery_failed", "draft_created"}:
            return _action(
                verified,
                action_kind="human_decision",
                effect_kind="delivery_retry_authorization",
                reason_code="delivery_retry_decision_required",
                request_schema_id="briefloop.delivery_authorization_request.v2",
            )
        effects = terminal.next_effects
        if len(effects) > 1:
            effects = (
                ("finalize_complete",)
                if _has_current_finalize_gate(snapshot)
                else ("finalize_gate",)
            )
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
    ready = (
        [rerun_stage_id]
        if rerun_stage_id is not None
        else [
            str(item["stage_id"])
            for item in verified.stages
            if stages[str(item["stage_id"])].status == "ready"
        ]
    )
    if len(ready) != 1:
        if not ready and all(
            item.status in {"complete", "skipped"} for item in stages.values()
        ):
            return _action(
                verified,
                action_kind="blocked",
                effect_kind="terminal_incomplete",
                reason_code="terminal_state_incomplete",
            )
        raise CoreRunError("control_store_integrity_invalid")
    stage_id = ready[0]
    if stage_id == "source-discovery":
        return _source_discovery_action(verified)
    if stage_id == "claim-ledger":
        action = _claim_ledger_action(verified)
        if action is not None:
            return action
    if stage_id == "auditor":
        action = _auditor_action(verified)
        if action is not None:
            return action
    if _stage_has_current_effect(verified, stage_id):
        return _action(
            verified,
            action_kind="deterministic",
            effect_kind="stage_complete",
            reason_code="current_stage_effect_ready_for_completion",
            stage_id=stage_id,
            request_schema_id="briefloop.stage_complete_request.v2",
        )
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
    topology = core_role_topology_policy(verified.binding.role_topology)
    if stage_id == "analyst" and topology.analyst_editor_route == "human_assisted":
        role = "analyst" if "analyst" in verified.runtime_adapter.role_ids else "writer"
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


def _source_discovery_action(verified: VerifiedCoreRun) -> CoreRunNextAction:
    snapshot = verified.snapshot
    artifacts = {item.artifact_id: item for item in snapshot.artifacts}
    candidates = artifacts.get("source_candidates")
    if candidates is None or candidates.current_revision == 0:
        return _delegate_action(
            verified,
            stage_id="source-discovery",
            role_id="source-planner",
            request_schema_id="briefloop.owned_artifact_submit_request.v2",
        )
    if any(item.claims_eligible for item in snapshot.sources):
        return _action(
            verified,
            action_kind="deterministic",
            effect_kind="stage_complete",
            reason_code="current_stage_effect_ready_for_completion",
            stage_id="source-discovery",
            request_schema_id="briefloop.stage_complete_request.v2",
        )
    routes = [
        item
        for item in verified.source_plan.routes
        if item.route_kind != "disabled"
        and (
            snapshot.run.run_id,
            verified.source_plan.source_plan_fingerprint,
            item.route_id,
            item.provider_id,
        )
        not in verified.exhausted_source_route_keys
    ]
    if not routes:
        return _action(
            verified,
            action_kind="human_decision",
            effect_kind="source_input_required",
            reason_code="human_source_material_required",
            stage_id="source-discovery",
            request_schema_id=("briefloop.runtime_human_source_pack_request.v2"),
        )
    route = min(
        routes,
        key=lambda item: (
            0 if item.required else 1,
            SOURCE_ROUTE_OWNER_ORDER[item.execution_owner],
            item.route_id,
        ),
    )
    common = {
        "stage_id": "source-discovery",
        "source_route_id": route.route_id,
        "source_provider_id": route.provider_id,
    }
    if route.execution_owner == "deterministic":
        if "source-provider" not in verified.runtime_adapter.role_ids:
            return _action(
                verified,
                action_kind="blocked",
                effect_kind="role_unavailable",
                reason_code="runtime_role_unavailable",
                **common,
            )
        return _action(
            verified,
            action_kind="deterministic",
            effect_kind="source_acquire",
            reason_code="deterministic_source_route_required",
            request_schema_id="briefloop.source_pack_commit_request.v2",
            **common,
        )
    if route.execution_owner == "specialist":
        if "source-provider" not in verified.runtime_adapter.role_ids:
            return _action(
                verified,
                action_kind="blocked",
                effect_kind="role_unavailable",
                reason_code="runtime_role_unavailable",
                **common,
            )
        return _action(
            verified,
            action_kind="delegate",
            effect_kind="role_proposal",
            reason_code="source_provider_required",
            role_id="source-provider",
            request_schema_id="briefloop.source_commit_request.v2",
            **common,
        )
    return _action(
        verified,
        action_kind="human_decision",
        effect_kind="source_input_required",
        reason_code="human_source_material_required",
        request_schema_id="briefloop.runtime_human_source_pack_request.v2",
        **common,
    )


def _claim_ledger_action(verified: VerifiedCoreRun) -> CoreRunNextAction | None:
    snapshot = verified.snapshot
    lineage = classify_current_lineage(snapshot)
    if lineage.proposals.claim_drafts is None or snapshot.claim_freezes:
        return None
    return _action(
        verified,
        action_kind="deterministic",
        effect_kind="claim_freeze",
        reason_code="current_claim_drafts_require_freeze",
        stage_id="claim-ledger",
        request_schema_id="briefloop.claim_freeze_request.v2",
    )


def _auditor_action(verified: VerifiedCoreRun) -> CoreRunNextAction | None:
    snapshot = verified.snapshot
    lineage = classify_current_lineage(snapshot)
    if lineage.proposals.audit is None:
        return None
    promotion_revision = _current_audit_promotion_revision(verified)
    if promotion_revision is None:
        return _action(
            verified,
            action_kind="deterministic",
            effect_kind="audit_promotion",
            reason_code="current_audit_proposal_requires_promotion",
            stage_id="auditor",
            request_schema_id="briefloop.audit_promotion_request.v2",
        )
    gate = lineage.current_gate_batch
    if gate is None or gate.committed_revision <= promotion_revision:
        return _action(
            verified,
            action_kind="deterministic",
            effect_kind="gate_evaluation",
            reason_code="current_audit_promotion_requires_gate",
            stage_id="auditor",
            request_schema_id="briefloop.gate_check_request.v2",
        )
    if any(
        item.producer_implementation != EVALUATOR_IMPLEMENTATION
        or item.producer_version != EVALUATOR_VERSION
        for item in gate.evaluations
    ):
        return _action(
            verified,
            action_kind="deterministic",
            effect_kind="gate_evaluation",
            reason_code="current_gate_evaluator_requires_refresh",
            stage_id="auditor",
            request_schema_id="briefloop.gate_check_request.v2",
        )
    required = {
        item.gate_id: item
        for item in gate.evaluations
        if item.gate_id in REQUIRED_AUDITOR_GATES
    }
    if set(required) != set(REQUIRED_AUDITOR_GATES):
        raise CoreRunError("control_store_integrity_invalid")
    if any(
        item.status not in {"pass", "warning"} or item.blocking
        for item in required.values()
    ):
        return _action(
            verified,
            action_kind="blocked",
            effect_kind="auditor_gate_blocked",
            reason_code="current_auditor_gate_blocked",
            stage_id="auditor",
        )
    return _action(
        verified,
        action_kind="deterministic",
        effect_kind="stage_complete",
        reason_code="current_stage_effect_ready_for_completion",
        stage_id="auditor",
        request_schema_id="briefloop.stage_complete_request.v2",
    )


def _current_audit_promotion_revision(verified: VerifiedCoreRun) -> int | None:
    snapshot = verified.snapshot
    lineage = classify_current_lineage(snapshot)
    audit = lineage.proposals.audit
    submission = lineage.current_submissions.get("audit_report")
    if audit is None or submission is None:
        return None
    artifacts = {item.artifact_id: item for item in snapshot.artifacts}
    brief = artifacts.get("audited_brief")
    parent = submission.parent_artifact
    if (
        submission.source_proposal_id != audit.proposal_id
        or brief is None
        or parent is None
        or parent.artifact_id != brief.artifact_id
        or parent.revision != brief.current_revision
    ):
        return None
    receipts = {
        item.transaction_id: item.committed_revision for item in snapshot.transactions
    }
    try:
        return receipts[submission.accepted_transaction_id]
    except KeyError as exc:
        raise CoreRunError("control_store_integrity_invalid") from exc


def _delegate_action(
    verified: VerifiedCoreRun,
    *,
    stage_id: str,
    role_id: str,
    request_schema_id: str,
) -> CoreRunNextAction:
    if role_id not in verified.runtime_adapter.role_ids:
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
        role_id=role_id,
        request_schema_id=request_schema_id,
    )


def _has_current_finalize_gate(snapshot) -> bool:
    artifacts = {item.artifact_id: item for item in snapshot.artifacts}
    report = artifacts.get("finalize_quality_gate_report")
    if report is None or report.current_revision < 1:
        return False
    current = [
        item
        for item in snapshot.gate_evaluations
        if item.stage_id == "finalize"
        and item.report_artifact.artifact_id == report.artifact_id
        and item.report_artifact.revision == report.current_revision
    ]
    return (
        len(current) == len(GATE_IDS)
        and {item.gate_id for item in current} == set(GATE_IDS)
        and len({item.gate_batch_id for item in current}) == 1
        and all(
            item.status in {"pass", "warning"} and not item.blocking for item in current
        )
    )


def _stage_has_current_effect(verified: VerifiedCoreRun, stage_id: str) -> bool:
    snapshot = verified.snapshot
    artifacts = {item.artifact_id: item for item in snapshot.artifacts}

    def current(artifact_id: str) -> bool:
        item = artifacts.get(artifact_id)
        return item is not None and item.current_revision > 0

    if stage_id == "doctor":
        return any(
            item.stage_id == "doctor"
            and item.transition_kind == "doctor_result"
            and item.result_status == "ready"
            for item in snapshot.stage_transitions
        )
    if stage_id == "source-discovery":
        return current("source_candidates") and any(
            item.claims_eligible for item in snapshot.sources
        )
    if stage_id == "input-governance":
        return not verified.binding.input_governance_required or current(
            "input_classification"
        )
    lineage = classify_current_lineage(snapshot)
    if stage_id == "scout":
        try:
            lineage.current_proposal("candidate")
            if not core_role_topology_policy(
                verified.binding.role_topology
            ).separate_screener_stage:
                lineage.current_proposal("screened")
        except CoreRunError:
            return False
        return True
    if stage_id == "screener":
        try:
            lineage.current_proposal("screened")
        except CoreRunError:
            return False
        return True
    if stage_id == "claim-ledger":
        return len(snapshot.claim_freezes) == 1 and current("claim_ledger")
    if stage_id == "analyst":
        return any(
            item.owner_stage_id == "analyst"
            and item.artifact_revision == artifacts[item.artifact_id].current_revision
            for item in snapshot.owned_artifact_submissions
            if item.artifact_id in {"analyst_draft_snapshot", "audited_brief"}
            and item.artifact_id in artifacts
        )
    if stage_id == "editor":
        return any(
            item.owner_stage_id == "editor"
            and item.artifact_id == "audited_brief"
            and item.artifact_revision == artifacts["audited_brief"].current_revision
            for item in snapshot.owned_artifact_submissions
        )
    if stage_id == "auditor":
        return (
            current("audit_report")
            and current("auditor_quality_gate_report")
            and lineage.current_gate_batch is not None
        )
    return False


__all__ = ["classify_core_run_next_action"]
