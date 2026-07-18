"""Read-only domain replay for the dormant fresh-v2 core run spine."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Literal

from multi_agent_brief.contracts.v2 import (
    ArtifactRevision,
    CandidateClaimsProposal,
    ClaimDraftsProposal,
    CoreRunEventBinding,
    EventEnvelope,
    InvocationStartRequest,
    RunContractBinding,
    RuntimeAdapterBinding,
    RuntimeSourcePlanBinding,
    ScreenedCandidatesProposal,
    TransactionReceipt,
)
from multi_agent_brief.control_store import (
    ControlStoreCommitOutcomeUnknown,
    ControlStoreSnapshot,
    SQLiteControlStore,
)
from multi_agent_brief.control_store.sqlite_store import ControlStoreHistory
from multi_agent_brief.control_store.serialization import (
    canonical_fingerprint,
    canonical_json_bytes,
    sha256_hex,
)
from multi_agent_brief.intake_v2.scratch import parse_json_object
from multi_agent_brief.orchestrator.runtime_state.contracts_loader import (
    ValidatedRuntimeContractPayloads,
    validate_runtime_contract_payloads,
)
from multi_agent_brief.quality_gates.contract import GATE_IDS

from .errors import CoreRunError, CoreRunResult
from .lineage import (
    classify_current_audit_promotion,
    classify_current_lineage,
    require_current_gate_after_audit_promotion,
    verify_no_post_seal_records,
)
from .policy import (
    CLAIM_EPISTEMIC,
    CORE_ARTIFACT_IDS,
    INTERNAL_CONTRACT_ARTIFACT_IDS,
    REQUIRED_AUDITOR_GATES,
    TERMINAL_INTERNAL_ARTIFACT_IDS,
    archive_artifact_usage,
    derived_id,
    normalize_text,
    run_contract_fingerprint,
    transaction_type_for,
)
from .recovery import (
    CoreEffect,
    CoreEffectSubject,
    classify_effect_authorization,
    classify_recovery_legality,
)
from .terminal import (
    TerminalEffectSubject,
    classify_terminal_effect_authorization,
    classify_terminal_legality,
)


@dataclass(frozen=True)
class VerifiedCoreRun:
    snapshot: ControlStoreSnapshot
    binding: RunContractBinding
    contracts: ValidatedRuntimeContractPayloads
    runtime_adapter: RuntimeAdapterBinding
    source_plan: RuntimeSourcePlanBinding

    @property
    def stages(self) -> tuple[dict[str, Any], ...]:
        return self.contracts.stages

    @property
    def artifacts(self) -> tuple[dict[str, Any], ...]:
        return self.contracts.artifacts


@dataclass(frozen=True)
class _AsOfArtifactReader:
    history: ControlStoreHistory
    snapshot: ControlStoreSnapshot

    def read_artifact_revision_bytes(
        self,
        run_id: str,
        artifact_id: str,
        revision: int,
    ) -> bytes:
        if run_id != self.snapshot.run.run_id or not any(
            item.artifact_id == artifact_id and item.revision == revision
            for item in self.snapshot.artifact_revisions
        ):
            raise CoreRunError("historical_prefix_invalid")
        return self.history.read_artifact_revision_bytes(
            run_id,
            artifact_id,
            revision,
        )


HumanAssistedRouteFamily = Literal["undecided", "snapshot", "writer"]


@dataclass(frozen=True)
class HumanAssistedAnalystRoute:
    """One replayed route family plus its transient draft state."""

    route_family: HumanAssistedRouteFamily
    active_analyst_role: Literal["analyst", "writer"] | None
    editor_reserved: bool
    analyst_snapshot_revision: int
    audited_brief_revision: int
    consumed_analyst_snapshot_revision: int | None


def classify_human_assisted_analyst_route(
    snapshot: ControlStoreSnapshot,
) -> HumanAssistedAnalystRoute:
    """Replay the sticky route family and transient revision reservations."""

    artifacts = {item.artifact_id: item for item in snapshot.artifacts}
    states = {
        item.stage_id: item
        for item in snapshot.stage_states
        if item.stage_id in {"analyst", "editor"}
    }
    if set(states) != {"analyst", "editor"}:
        raise CoreRunError("control_store_integrity_invalid")
    analyst_status = states["analyst"].status
    editor_status = states["editor"].status

    def submission_history(artifact_id: str):
        artifact = artifacts.get(artifact_id)
        if artifact is None:
            raise CoreRunError("control_store_integrity_invalid")
        submissions = sorted(
            (
                item
                for item in snapshot.owned_artifact_submissions
                if item.artifact_id == artifact_id
            ),
            key=lambda item: item.artifact_revision,
        )
        if [item.artifact_revision for item in submissions] != list(
            range(1, artifact.current_revision + 1)
        ):
            raise CoreRunError("control_store_integrity_invalid")
        return tuple(submissions)

    snapshot_submissions = submission_history("analyst_draft_snapshot")
    brief_submissions = submission_history("audited_brief")
    if any(
        item.owner_stage_id != "analyst"
        or item.owner_role_id != "analyst"
        or item.parent_artifact is not None
        for item in snapshot_submissions
    ):
        raise CoreRunError("control_store_integrity_invalid")

    writer_submissions = []
    editor_submissions = []
    for submission in brief_submissions:
        owner = (submission.owner_stage_id, submission.owner_role_id)
        if owner == ("analyst", "writer"):
            if submission.parent_artifact is not None:
                raise CoreRunError("control_store_integrity_invalid")
            writer_submissions.append(submission)
        elif owner == ("editor", "editor"):
            editor_submissions.append(submission)
        else:
            raise CoreRunError("control_store_integrity_invalid")

    if writer_submissions and (snapshot_submissions or editor_submissions):
        raise CoreRunError("control_store_integrity_invalid")
    if editor_submissions and not snapshot_submissions:
        raise CoreRunError("control_store_integrity_invalid")

    route_family: HumanAssistedRouteFamily
    if writer_submissions:
        route_family = "writer"
    elif snapshot_submissions or editor_submissions:
        route_family = "snapshot"
    else:
        route_family = "undecided"

    analyst_completions = [
        item
        for item in snapshot.stage_transitions
        if item.stage_id == "analyst" and item.transition_kind == "complete"
    ]
    if len(analyst_completions) > 1:
        raise CoreRunError("control_store_integrity_invalid")
    consumed_snapshot_revision: int | None = None
    if analyst_completions:
        snapshot_bindings = [
            item
            for item in snapshot.stage_artifact_bindings
            if item.transition_id == analyst_completions[0].transition_id
            and item.artifact_id == "analyst_draft_snapshot"
            and item.usage == "produced"
        ]
        if snapshot_bindings:
            if len(snapshot_bindings) != 1:
                raise CoreRunError("control_store_integrity_invalid")
            consumed_snapshot_revision = snapshot_bindings[0].artifact_revision

    snapshot_revision = artifacts["analyst_draft_snapshot"].current_revision
    brief_revision = artifacts["audited_brief"].current_revision
    if route_family == "snapshot":
        if analyst_status not in {"ready", "complete"}:
            raise CoreRunError("control_store_integrity_invalid")
        if analyst_status == "complete" and (
            consumed_snapshot_revision is None
            or consumed_snapshot_revision != snapshot_revision
        ):
            raise CoreRunError("control_store_integrity_invalid")
        if editor_submissions:
            if consumed_snapshot_revision is None:
                raise CoreRunError("control_store_integrity_invalid")
            for submission in editor_submissions:
                parent = submission.parent_artifact
                if (
                    parent is None
                    or parent.artifact_id != "analyst_draft_snapshot"
                    or parent.revision != consumed_snapshot_revision
                ):
                    raise CoreRunError("control_store_integrity_invalid")
    elif route_family == "writer":
        if analyst_status not in {"ready", "complete"}:
            raise CoreRunError("control_store_integrity_invalid")
    elif analyst_status not in {"pending", "ready"}:
        raise CoreRunError("control_store_integrity_invalid")

    invocation_stages = _invocation_stage_map(snapshot)
    active_analyst = [
        item
        for item in snapshot.invocations
        if item.status == "active"
        and invocation_stages.get(item.invocation_id) == "analyst"
    ]
    active_editor = [
        item
        for item in snapshot.invocations
        if item.status == "active"
        and invocation_stages.get(item.invocation_id) == "editor"
    ]
    if len(active_analyst) > 1 or len(active_editor) > 1:
        raise CoreRunError("control_store_integrity_invalid")
    if any(item.role_id not in {"analyst", "writer"} for item in active_analyst):
        raise CoreRunError("control_store_integrity_invalid")
    if any(item.role_id != "editor" for item in active_editor):
        raise CoreRunError("control_store_integrity_invalid")
    if active_analyst and active_editor:
        raise CoreRunError("control_store_integrity_invalid")
    active_analyst_role: Literal["analyst", "writer"] | None = None
    if active_analyst:
        active_analyst_role = (
            "analyst" if active_analyst[0].role_id == "analyst" else "writer"
        )
    if active_analyst_role is not None:
        if analyst_status != "ready":
            raise CoreRunError("control_store_integrity_invalid")
        allowed_family = "snapshot" if active_analyst_role == "analyst" else "writer"
        if route_family not in {"undecided", allowed_family}:
            raise CoreRunError("control_store_integrity_invalid")
    if active_editor:
        if editor_status != "ready" or route_family != "snapshot":
            raise CoreRunError("control_store_integrity_invalid")

    return HumanAssistedAnalystRoute(
        route_family=route_family,
        active_analyst_role=active_analyst_role,
        editor_reserved=bool(active_editor),
        analyst_snapshot_revision=snapshot_revision,
        audited_brief_revision=brief_revision,
        consumed_analyst_snapshot_revision=consumed_snapshot_revision,
    )


def _invocation_stage_map(snapshot: ControlStoreSnapshot) -> dict[str, str]:
    stages: dict[str, str] = {}
    for event in snapshot.events:
        core = event.core_run_binding
        if core is None or core.effect_kind != "invocation_start":
            continue
        if event.stage_id is None or core.primary_record_id in stages:
            raise CoreRunError("control_store_integrity_invalid")
        stages[core.primary_record_id] = event.stage_id
    return stages


@dataclass(frozen=True)
class _CoreEffectBindingRule:
    transaction_type: str
    primary_event_types: frozenset[str]
    primary_family: str
    receipt_event_counts: tuple[tuple[str, int], ...] | None
    authoritative_relation_families: frozenset[str]


_AUTHORITATIVE_RECEIPT_RELATION_FAMILIES = frozenset(
    {
        "artifact_revisions",
        "artifact_identities",
        "source_ids",
        "proposal_ids",
        "run_contract_bindings",
        "owned_artifact_submissions",
        "stage_transitions",
        "stage_artifact_bindings",
        "stage_gate_bindings",
        "claims",
        "claim_source_bindings",
        "claim_freezes",
        "gate_evaluations",
        "gate_findings",
        "gate_artifact_bindings",
        "run_integrity_records",
        "repair_cycles",
        "artifact_supersessions",
        "repair_completions",
        "recovery_completions",
        "run_head_transitions",
        "finalize_renders",
        "finalizations",
        "run_archives",
        "run_archive_artifact_bindings",
        "package_ready_records",
        "package_artifact_bindings",
        "approvals",
        "approval_package_bindings",
        "delivery_authorizations",
        "delivery_attempts",
        "delivery_results",
    }
)


_CORE_EFFECT_BINDING_RULES = {
    "initialize": _CoreEffectBindingRule(
        transaction_type_for("initialize"),
        frozenset({"run_initialized"}),
        "run_contract_binding",
        (("run_initialized", 1),),
        frozenset(
            {
                "artifact_revisions",
                "artifact_identities",
                "run_contract_bindings",
                "stage_transitions",
                "run_integrity_records",
            }
        ),
    ),
    "invocation_start": _CoreEffectBindingRule(
        transaction_type_for("invocation_start"),
        frozenset({"role_invocation_started"}),
        "invocation",
        (("role_invocation_started", 1),),
        frozenset(),
    ),
    "owned_artifact_acceptance": _CoreEffectBindingRule(
        transaction_type_for("owned_artifact_acceptance"),
        frozenset({"owned_artifact_accepted"}),
        "owned_artifact_submission",
        (("owned_artifact_accepted", 1),),
        frozenset({"artifact_revisions", "owned_artifact_submissions"}),
    ),
    "claim_freeze": _CoreEffectBindingRule(
        transaction_type_for("claim_freeze"),
        frozenset({"claim_ledger_frozen"}),
        "claim_freeze",
        (("claim_ledger_frozen", 1),),
        frozenset(
            {
                "artifact_revisions",
                "claims",
                "claim_source_bindings",
                "claim_freezes",
            }
        ),
    ),
    "audit_promotion": _CoreEffectBindingRule(
        transaction_type_for("audit_promotion"),
        frozenset({"audit_proposal_promoted"}),
        "audit_submission",
        (("audit_proposal_promoted", 1),),
        frozenset({"artifact_revisions", "owned_artifact_submissions"}),
    ),
    "gate_evaluation": _CoreEffectBindingRule(
        transaction_type_for("gate_evaluation"),
        frozenset({"quality_gate_checked"}),
        "gate_batch",
        (("quality_gate_checked", 1),),
        frozenset(
            {
                "artifact_revisions",
                "artifact_identities",
                "gate_evaluations",
                "gate_findings",
                "gate_artifact_bindings",
            }
        ),
    ),
    "stage_transition": _CoreEffectBindingRule(
        transaction_type_for("stage_transition"),
        frozenset({"stage_status_changed", "stage_satisfied_by_topology"}),
        "stage_transition",
        None,
        frozenset(
            {"stage_transitions", "stage_artifact_bindings", "stage_gate_bindings"}
        ),
    ),
    "integrity_contamination": _CoreEffectBindingRule(
        transaction_type_for("integrity_contamination"),
        frozenset({"run_integrity_contaminated"}),
        "run_integrity_record",
        (("run_integrity_contaminated", 1), ("run_blocked", 1)),
        frozenset({"run_integrity_records"}),
    ),
    "repair_start": _CoreEffectBindingRule(
        transaction_type_for("repair_start"),
        frozenset({"repair_started"}),
        "repair_cycle",
        (("repair_started", 1),),
        frozenset({"repair_cycles"}),
    ),
    "artifact_supersession": _CoreEffectBindingRule(
        transaction_type_for("artifact_supersession"),
        frozenset({"repair_stage_superseded"}),
        "artifact_supersession",
        (("owned_artifact_accepted", 1), ("repair_stage_superseded", 1)),
        frozenset(
            {
                "artifact_revisions",
                "owned_artifact_submissions",
                "artifact_supersessions",
            }
        ),
    ),
    "repair_complete": _CoreEffectBindingRule(
        transaction_type_for("repair_complete"),
        frozenset({"repair_completed"}),
        "repair_completion",
        None,
        frozenset({"stage_transitions", "repair_completions"}),
    ),
    "recovery_complete": _CoreEffectBindingRule(
        transaction_type_for("recovery_complete"),
        frozenset({"decision_recorded"}),
        "recovery_completion",
        (("decision_recorded", 1),),
        frozenset({"recovery_completions"}),
    ),
    "run_head_transition": _CoreEffectBindingRule(
        transaction_type_for("run_head_transition"),
        frozenset({"run_reset"}),
        "run_head_transition",
        None,
        frozenset(
            {
                "artifact_revisions",
                "artifact_identities",
                "run_contract_bindings",
                "stage_transitions",
                "run_integrity_records",
                "run_head_transitions",
            }
        ),
    ),
    "finalize_render": _CoreEffectBindingRule(
        transaction_type_for("finalize_render"),
        frozenset({"owned_artifact_accepted"}),
        "finalize_render",
        (("owned_artifact_accepted", 1),),
        frozenset({"artifact_revisions", "artifact_identities", "finalize_renders"}),
    ),
    "finalize_complete": _CoreEffectBindingRule(
        transaction_type_for("finalize_complete"),
        frozenset({"stage_status_changed"}),
        "finalization",
        (
            ("stage_status_changed", 1),
            ("run_archived", 1),
            ("decision_recorded", 1),
        ),
        frozenset(
            {
                "artifact_revisions",
                "artifact_identities",
                "stage_transitions",
                "stage_artifact_bindings",
                "stage_gate_bindings",
                "finalizations",
                "run_archives",
                "run_archive_artifact_bindings",
                "package_ready_records",
                "package_artifact_bindings",
            }
        ),
    ),
    "internal_approval": _CoreEffectBindingRule(
        transaction_type_for("internal_approval"),
        frozenset({"human_approval_recorded"}),
        "internal_approval",
        (("human_approval_recorded", 1),),
        frozenset({"approvals", "approval_package_bindings"}),
    ),
    "delivery_authorization": _CoreEffectBindingRule(
        transaction_type_for("delivery_authorization"),
        frozenset({"decision_recorded"}),
        "delivery_authorization",
        (("decision_recorded", 1),),
        frozenset({"delivery_authorizations"}),
    ),
    "delivery_attempt": _CoreEffectBindingRule(
        transaction_type_for("delivery_attempt"),
        frozenset({"delivery_attempted"}),
        "delivery_attempt",
        (("delivery_attempted", 1),),
        frozenset({"delivery_attempts"}),
    ),
    "delivery_result": _CoreEffectBindingRule(
        transaction_type_for("delivery_result"),
        frozenset(
            {
                "delivery_bundle_prepared",
                "delivery_draft_created",
                "delivery_succeeded",
                "delivery_failed",
                "decision_recorded",
            }
        ),
        "delivery_result",
        None,
        frozenset({"artifact_revisions", "artifact_identities", "delivery_results"}),
    ),
}


@dataclass(frozen=True)
class _IntakeEffectRule:
    effect: CoreEffect
    event_type: str
    proposal_kind: str | None
    allowed_stages: frozenset[str]
    authoritative_relation_families: frozenset[str]


_INTAKE_EFFECT_RULES = {
    "source_evidence_intake": _IntakeEffectRule(
        CoreEffect.SOURCE_INTAKE,
        "source_evidence_committed",
        None,
        frozenset({"source-discovery"}),
        frozenset({"artifact_revisions", "artifact_identities", "source_ids"}),
    ),
    "candidate_claims_intake": _IntakeEffectRule(
        CoreEffect.PROPOSAL_INTAKE,
        "role_proposal_committed",
        "candidate",
        frozenset({"scout"}),
        frozenset({"artifact_revisions", "artifact_identities", "proposal_ids"}),
    ),
    "screened_candidates_intake": _IntakeEffectRule(
        CoreEffect.PROPOSAL_INTAKE,
        "role_proposal_committed",
        "screened",
        frozenset({"scout", "screener"}),
        frozenset({"artifact_revisions", "artifact_identities", "proposal_ids"}),
    ),
    "claim_drafts_intake": _IntakeEffectRule(
        CoreEffect.PROPOSAL_INTAKE,
        "role_proposal_committed",
        "claim_drafts",
        frozenset({"claim-ledger"}),
        frozenset({"artifact_revisions", "artifact_identities", "proposal_ids"}),
    ),
    "audit_proposal_intake": _IntakeEffectRule(
        CoreEffect.PROPOSAL_INTAKE,
        "role_proposal_committed",
        "audit",
        frozenset({"auditor"}),
        frozenset({"artifact_revisions", "artifact_identities", "proposal_ids"}),
    ),
    "intake_rejection": _IntakeEffectRule(
        CoreEffect.INTAKE_REJECTION,
        "intake_rejected",
        None,
        frozenset(
            {
                "source-discovery",
                "scout",
                "screener",
                "claim-ledger",
                "auditor",
            }
        ),
        frozenset(),
    ),
}


def _verify_authoritative_receipt_relation_families(
    receipt: TransactionReceipt,
    allowed: frozenset[str],
) -> None:
    """Reject any receipt relation family not owned by its declared effect."""

    present = frozenset(
        family
        for family in _AUTHORITATIVE_RECEIPT_RELATION_FAMILIES
        if getattr(receipt, family)
    )
    if not allowed.issubset(_AUTHORITATIVE_RECEIPT_RELATION_FAMILIES) or not (
        present <= allowed
    ):
        raise CoreRunError("control_store_integrity_invalid")


def _verified_intake_receipt_effect(
    snapshot: ControlStoreSnapshot,
    receipt: TransactionReceipt,
) -> tuple[CoreEffect, CoreEffectSubject]:
    """Verify one authoritative PR-3 intake receipt and derive its effect."""

    rule = _INTAKE_EFFECT_RULES.get(receipt.transaction_type)
    if rule is None:
        raise CoreRunError("control_store_integrity_invalid")
    _verify_authoritative_receipt_relation_families(
        receipt,
        rule.authoritative_relation_families,
    )
    events_by_id = {item.event_id: item for item in snapshot.events}
    events = [events_by_id.get(event_id) for event_id in receipt.event_ids]
    if len(events) != 1 or events[0] is None:
        raise CoreRunError("control_store_integrity_invalid")
    event = events[0]
    binding = event.intake_binding
    if (
        event.run_id != receipt.run_id
        or event.transaction_id != receipt.transaction_id
        or event.event_type != rule.event_type
        or event.stage_id not in rule.allowed_stages
        or event.core_run_binding is not None
        or binding is None
        or binding.request_id != receipt.transaction_id
    ):
        raise CoreRunError("control_store_integrity_invalid")
    if rule.effect is CoreEffect.INTAKE_REJECTION:
        if (
            binding.outcome != "rejected"
            or binding.reason_code is None
            or receipt.source_ids
            or receipt.proposal_ids
            or receipt.artifact_revisions
        ):
            raise CoreRunError("control_store_integrity_invalid")
        return rule.effect, CoreEffectSubject(stage_id=event.stage_id)
    if binding.outcome != "committed" or binding.reason_code is not None:
        raise CoreRunError("control_store_integrity_invalid")
    if rule.effect is CoreEffect.SOURCE_INTAKE:
        if binding.source_id is None or binding.proposal_id is not None:
            raise CoreRunError("control_store_integrity_invalid")
        records = [
            item for item in snapshot.sources if item.source_id == binding.source_id
        ]
        if len(records) != 1:
            raise CoreRunError("control_store_integrity_invalid")
        record = records[0]
        expected_revisions = {
            (record.content_artifact_id, record.content_artifact_revision)
        }
        if (
            record.raw_payload_artifact_id is not None
            and record.raw_payload_artifact_revision is not None
        ):
            expected_revisions.add(
                (
                    record.raw_payload_artifact_id,
                    record.raw_payload_artifact_revision,
                )
            )
        if (
            receipt.source_ids != [record.source_id]
            or receipt.proposal_ids
            or record.accepted_transaction_id != receipt.transaction_id
            or record.acquisition_event_id != event.event_id
            or record.request_fingerprint != binding.request_fingerprint
            or record.invocation_id != binding.invocation_id
            or {
                (item.artifact_id, item.revision) for item in receipt.artifact_revisions
            }
            != expected_revisions
        ):
            raise CoreRunError("control_store_integrity_invalid")
        return (
            rule.effect,
            CoreEffectSubject(
                stage_id=event.stage_id,
                artifact_id=record.content_artifact_id,
            ),
        )
    if binding.proposal_id is None or binding.source_id is not None:
        raise CoreRunError("control_store_integrity_invalid")
    records = [
        item
        for item in snapshot.accepted_proposals
        if item.proposal_id == binding.proposal_id
    ]
    if len(records) != 1:
        raise CoreRunError("control_store_integrity_invalid")
    record = records[0]
    if (
        receipt.proposal_ids != [record.proposal_id]
        or receipt.source_ids
        or record.proposal_kind != rule.proposal_kind
        or record.owner_stage_id != event.stage_id
        or record.accepted_transaction_id != receipt.transaction_id
        or record.accepted_event_id != event.event_id
        or record.request_fingerprint != binding.request_fingerprint
        or record.invocation_id != binding.invocation_id
        or [(item.artifact_id, item.revision) for item in receipt.artifact_revisions]
        != [(record.artifact_id, record.artifact_revision)]
    ):
        raise CoreRunError("control_store_integrity_invalid")
    return (
        rule.effect,
        CoreEffectSubject(
            stage_id=event.stage_id,
            artifact_id=record.artifact_id,
        ),
    )


def _receipt_effect_authorization_subject(
    snapshot: ControlStoreSnapshot,
    receipt: TransactionReceipt,
    event: EventEnvelope,
    binding: CoreRunEventBinding,
) -> tuple[CoreEffect, CoreEffectSubject, TerminalEffectSubject | None]:
    """Map one exact receipt primary to its closed semantic effect vocabulary."""

    effect_kind = binding.effect_kind
    primary_id = binding.primary_record_id
    stage_subject = CoreEffectSubject(stage_id=event.stage_id)
    if effect_kind == "initialize":
        return CoreEffect.INITIALIZE, CoreEffectSubject(), None
    if effect_kind == "invocation_start":
        records = [
            item for item in snapshot.invocations if item.invocation_id == primary_id
        ]
        if len(records) != 1 or event.stage_id is None:
            raise CoreRunError("control_store_integrity_invalid")
        return CoreEffect.INVOCATION_START, stage_subject, None
    if effect_kind in {"owned_artifact_acceptance", "audit_promotion"}:
        records = [
            item
            for item in snapshot.owned_artifact_submissions
            if item.submission_id == primary_id
        ]
        if (
            len(records) != 1
            or event.stage_id is None
            or records[0].owner_stage_id != event.stage_id
        ):
            raise CoreRunError("control_store_integrity_invalid")
        effect = (
            CoreEffect.AUDIT_PROPOSAL_PROMOTE
            if effect_kind == "audit_promotion"
            else CoreEffect.OWNED_ARTIFACT_ACCEPT
        )
        return (
            effect,
            CoreEffectSubject(
                stage_id=event.stage_id,
                artifact_id=records[0].artifact_id,
            ),
            None,
        )
    if effect_kind == "claim_freeze":
        if event.stage_id != "claim-ledger":
            raise CoreRunError("control_store_integrity_invalid")
        return CoreEffect.CLAIM_FREEZE, stage_subject, None
    if effect_kind == "gate_evaluation":
        evaluation_ids = {item.evaluation_id for item in receipt.gate_evaluations}
        records = [
            item
            for item in snapshot.gate_evaluations
            if item.evaluation_id in evaluation_ids
        ]
        stage_ids = {item.stage_id for item in records}
        if (
            not evaluation_ids
            or len(records) != len(evaluation_ids)
            or len(stage_ids) != 1
            or event.stage_id not in stage_ids
        ):
            raise CoreRunError("control_store_integrity_invalid")
        stage_id = next(iter(stage_ids))
        if stage_id == "auditor":
            effect = CoreEffect.GATE_EVALUATE
        elif stage_id == "finalize":
            effect = CoreEffect.FINALIZE_GATE
        else:
            raise CoreRunError("control_store_integrity_invalid")
        return effect, CoreEffectSubject(stage_id=stage_id), None
    if effect_kind == "stage_transition":
        records = [
            item
            for item in snapshot.stage_transitions
            if item.transition_id == primary_id
        ]
        if (
            len(records) != 1
            or event.stage_id is None
            or records[0].stage_id != event.stage_id
        ):
            raise CoreRunError("control_store_integrity_invalid")
        return CoreEffect.STAGE_COMPLETE, stage_subject, None
    if effect_kind == "integrity_contamination":
        try:
            integrity_revision = int(primary_id)
        except ValueError as exc:
            raise CoreRunError("control_store_integrity_invalid") from exc
        records = [
            item
            for item in snapshot.run_integrity_records
            if item.integrity_revision == integrity_revision
        ]
        if len(records) != 1:
            raise CoreRunError("control_store_integrity_invalid")
        return (
            CoreEffect.INTEGRITY_CONTAMINATION,
            CoreEffectSubject(
                contamination_revision=integrity_revision,
                artifact_id=records[0].affected_artifact_id,
            ),
            None,
        )
    if effect_kind == "repair_start":
        records = [
            item for item in snapshot.repair_cycles if item.repair_id == primary_id
        ]
        if len(records) != 1:
            raise CoreRunError("control_store_integrity_invalid")
        return (
            CoreEffect.REPAIR_START,
            CoreEffectSubject(
                contamination_revision=records[0].contamination_revision,
                stage_id=records[0].owner_stage_id,
                repair_id=records[0].repair_id,
            ),
            None,
        )
    if effect_kind == "artifact_supersession":
        records = [
            item
            for item in snapshot.artifact_supersessions
            if item.supersession_id == primary_id
        ]
        if len(records) != 1:
            raise CoreRunError("control_store_integrity_invalid")
        record = records[0]
        effect = (
            CoreEffect.ARTIFACT_REVERT
            if record.mode == "revert"
            else CoreEffect.ARTIFACT_SUPERSEDE
        )
        return (
            effect,
            CoreEffectSubject(
                artifact_id=record.successor_artifact.artifact_id,
                repair_id=record.repair_id,
            ),
            None,
        )
    if effect_kind == "repair_complete":
        records = [
            item
            for item in snapshot.repair_completions
            if item.repair_completion_id == primary_id
        ]
        if len(records) != 1:
            raise CoreRunError("control_store_integrity_invalid")
        return (
            CoreEffect.REPAIR_COMPLETE,
            CoreEffectSubject(
                contamination_revision=records[0].contamination_revision,
                repair_id=records[0].repair_id,
                repair_completion_id=records[0].repair_completion_id,
            ),
            None,
        )
    if effect_kind == "recovery_complete":
        records = [
            item
            for item in snapshot.recovery_completions
            if item.recovery_id == primary_id
        ]
        if len(records) != 1:
            raise CoreRunError("control_store_integrity_invalid")
        return (
            CoreEffect.RECOVERY_COMPLETE,
            CoreEffectSubject(
                contamination_revision=records[0].contamination_revision,
                repair_completion_id=records[0].repair_completion_id,
            ),
            None,
        )
    if effect_kind == "run_head_transition":
        return CoreEffect.RUN_RESET, CoreEffectSubject(), None
    if effect_kind == "finalize_render":
        records = [
            item for item in snapshot.finalize_renders if item.render_id == primary_id
        ]
        if len(records) != 1 or event.stage_id != "finalize":
            raise CoreRunError("control_store_integrity_invalid")
        return CoreEffect.FINALIZE_RENDER, stage_subject, None
    if effect_kind == "finalize_complete":
        records = [
            item
            for item in snapshot.finalizations
            if item.finalization_id == primary_id
        ]
        transitions = [
            item
            for item in snapshot.stage_transitions
            if len(records) == 1
            and item.transition_id == records[0].finalize_transition_id
        ]
        if (
            len(records) != 1
            or len(transitions) != 1
            or transitions[0].stage_id != "finalize"
            or event.stage_id != "finalize"
        ):
            raise CoreRunError("control_store_integrity_invalid")
        return CoreEffect.FINALIZE_COMPLETE, stage_subject, None
    if effect_kind == "internal_approval":
        records = [
            item for item in snapshot.approvals if item.approval_id == primary_id
        ]
        package_bindings = [
            item
            for item in snapshot.approval_package_bindings
            if item.approval_id == primary_id
            and item.accepted_transaction_id == receipt.transaction_id
        ]
        if len(records) != 1 or len(package_bindings) != 1:
            raise CoreRunError("control_store_integrity_invalid")
        record = records[0]
        return (
            CoreEffect.INTERNAL_APPROVAL,
            CoreEffectSubject(),
            TerminalEffectSubject(
                package_id=package_bindings[0].package_id,
                approval_mode=record.mode,
                approval_role=record.role,
            ),
        )
    if effect_kind == "delivery_authorization":
        records = [
            item
            for item in snapshot.delivery_authorizations
            if item.authorization_id == primary_id
        ]
        if len(records) != 1:
            raise CoreRunError("control_store_integrity_invalid")
        record = records[0]
        return (
            CoreEffect.DELIVERY_AUTHORIZE,
            CoreEffectSubject(),
            TerminalEffectSubject(
                package_id=record.package_id,
                approval_mode=record.approval_mode,
                authorization_id=record.authorization_id,
                prior_authorization_id=record.prior_authorization_id,
                retry_of_attempt_id=record.retry_of_attempt_id,
                purpose=record.purpose,
                decision=record.decision,
                target=record.target,
                channel=record.channel,
                recipient_fingerprint=record.recipient_fingerprint,
            ),
        )
    if effect_kind == "delivery_attempt":
        records = [
            item for item in snapshot.delivery_attempts if item.attempt_id == primary_id
        ]
        if len(records) != 1:
            raise CoreRunError("control_store_integrity_invalid")
        record = records[0]
        return (
            CoreEffect.DELIVERY_ATTEMPT,
            CoreEffectSubject(),
            TerminalEffectSubject(
                package_id=record.package_id,
                authorization_id=record.authorization_id,
                target=record.target,
                channel=record.channel,
                recipient_fingerprint=record.recipient_fingerprint,
                attempt_id=record.attempt_id,
                connector_operation_id=record.connector_operation_id,
            ),
        )
    if effect_kind == "delivery_result":
        records = [
            item for item in snapshot.delivery_results if item.result_id == primary_id
        ]
        if len(records) != 1:
            raise CoreRunError("control_store_integrity_invalid")
        record = records[0]
        attempts = [
            item
            for item in snapshot.delivery_attempts
            if item.attempt_id == record.attempt_id
        ]
        if len(attempts) != 1:
            raise CoreRunError("control_store_integrity_invalid")
        return (
            CoreEffect.DELIVERY_RESULT,
            CoreEffectSubject(),
            TerminalEffectSubject(
                package_id=attempts[0].package_id,
                attempt_id=record.attempt_id,
                connector_operation_id=record.connector_operation_id,
                prior_result_id=record.prior_result_id,
                reconciliation_authorization_id=(
                    record.reconciliation_authorization_id
                ),
                result_status=record.status,
            ),
        )
    raise CoreRunError("control_store_integrity_invalid")


def _integrity_observation_fingerprint(record: Any) -> str:
    return canonical_fingerprint(
        {
            "run_id": record.run_id,
            "artifact_id": record.affected_artifact_id,
            "artifact_revision": record.affected_artifact_revision,
            "expected_workspace_path": record.expected_workspace_path,
            "expected_sha256": record.expected_sha256,
            "observed_entry_kind": record.observed_entry_kind,
            "observed_sha256": record.observed_sha256,
        }
    )


def _integrity_contamination_binding_fingerprint(
    base_request_fingerprint: str,
    observation_fingerprint: str,
) -> str:
    return canonical_fingerprint(
        {
            "effect_kind": "integrity_contamination",
            "base_request_fingerprint": base_request_fingerprint,
            "observation_fingerprint": observation_fingerprint,
        }
    )


def _require_no_unowned_legacy_deliveries(
    history: ControlStoreHistory,
) -> None:
    """Reject legacy Delivery rows that have no receipt-owned revision."""

    if any(snapshot.deliveries for snapshot in history.snapshots):
        raise CoreRunError("historical_prefix_invalid")


class CoreRunDomainVerifier:
    """Replay business legality from one structurally verified Store snapshot."""

    def verify(
        self,
        store: SQLiteControlStore,
        run_id: str,
    ) -> VerifiedCoreRun:
        try:
            history = store.load_history()
        except Exception as exc:
            raise CoreRunError("control_store_integrity_invalid") from exc
        _require_no_unowned_legacy_deliveries(history)
        try:
            snapshot = history.snapshot_at_revision(run_id, history.store_revision)
        except Exception as exc:
            raise CoreRunError("control_store_integrity_invalid") from exc
        if len(snapshot.run_contract_bindings) != 1:
            raise CoreRunError("core_run_not_initialized")
        binding = snapshot.run_contract_bindings[0]
        head = snapshot.workspace_run_head
        if (
            head is None
            or head.current_run_id != run_id
            or head.workspace_id != snapshot.run.workspace_id
            or binding.run_id != run_id
            or binding.workspace_id != snapshot.run.workspace_id
            or binding.runtime != snapshot.run.runtime
        ):
            raise CoreRunError("core_run_head_mismatch")
        verified = self._verify_snapshot(history, snapshot)
        self.verify_history(history)
        return verified

    def verify_history(
        self,
        history: ControlStoreHistory,
        *,
        through_revision: int | None = None,
    ) -> None:
        """Verify every committed receipt prefix without consulting final tips."""

        _require_no_unowned_legacy_deliveries(history)
        limit = history.store_revision if through_revision is None else through_revision
        receipts = [
            item for item in history.transactions if item.committed_revision <= limit
        ]
        if [item.committed_revision for item in receipts] != list(range(1, limit + 1)):
            raise CoreRunError("historical_prefix_invalid")
        for receipt in receipts:
            try:
                snapshot = history.snapshot_at_revision(
                    receipt.run_id,
                    receipt.committed_revision,
                )
                self._verify_snapshot(history, snapshot)
                self._verify_historical_pr4b_prefix(history, snapshot, receipt)
            except CoreRunError as exc:
                if exc.code in {
                    "reset_history_invalid",
                    "archive_membership_invalid",
                    "package_membership_invalid",
                }:
                    raise
                raise CoreRunError("historical_prefix_invalid") from exc
            except Exception as exc:
                raise CoreRunError("historical_prefix_invalid") from exc

    def _verify_snapshot(
        self,
        history: ControlStoreHistory,
        snapshot: ControlStoreSnapshot,
    ) -> VerifiedCoreRun:
        if len(snapshot.run_contract_bindings) != 1:
            raise CoreRunError("core_run_not_initialized")
        binding = snapshot.run_contract_bindings[0]
        if (
            binding.run_id != snapshot.run.run_id
            or binding.workspace_id != snapshot.run.workspace_id
            or binding.runtime != snapshot.run.runtime
        ):
            raise CoreRunError("control_store_integrity_invalid")
        reader = _AsOfArtifactReader(history, snapshot)
        contracts, runtime_adapter, source_plan = self._load_contracts(reader, binding)
        self._verify_contract_fingerprint(binding)
        self._verify_receipt_bindings(snapshot)
        self._verify_checkout_revisions(history, snapshot)
        self._verify_invocation_ownership(snapshot, binding)
        classify_current_lineage(snapshot)
        verify_no_post_seal_records(snapshot)
        self._verify_artifact_graph(snapshot, contracts, binding)
        self._verify_stage_chain(reader, snapshot, contracts, binding)
        self._verify_integrity_chain(snapshot)
        self._verify_claim_chain(reader, snapshot, binding)
        self._verify_gate_chain(reader, snapshot, binding, contracts)
        return VerifiedCoreRun(
            snapshot=snapshot,
            binding=binding,
            contracts=contracts,
            runtime_adapter=runtime_adapter,
            source_plan=source_plan,
        )

    @staticmethod
    def _verify_checkout_revisions(
        history: ControlStoreHistory,
        snapshot: ControlStoreSnapshot,
    ) -> None:
        """Recompute immutable checkout truth without consulting working paths."""

        graph = (
            snapshot.checkout_revisions,
            snapshot.checkout_revision_members,
            snapshot.receipt_checkout_bindings,
        )
        if not any(graph):
            # Dormant transition: PR-4B0 receipts remain valid until PR-4B2
            # requires a binding on every lifecycle effect.
            return
        if not (
            snapshot.checkout_revisions and snapshot.receipt_checkout_bindings
        ):
            raise CoreRunError("checkout_revision_invalid")
        from datetime import datetime
        from multi_agent_brief.core_run_v2.checkout import build_checkout_revision

        members_by_revision: dict[str, list[object]] = {}
        for member in snapshot.checkout_revision_members:
            members_by_revision.setdefault(member.checkout_revision_id, []).append(member)
        receipts = {item.transaction_id: item for item in snapshot.transactions}
        for revision in snapshot.checkout_revisions:
            members = tuple(
                sorted(
                    members_by_revision.get(revision.checkout_revision_id, []),
                    key=lambda item: item.ordinal,
                )
            )
            artifact_rows = []
            for member in members:
                artifact = next(
                    (
                        item for item in snapshot.artifact_revisions
                        if item.artifact_id == member.artifact_id
                        and item.revision == member.artifact_revision
                    ),
                    None,
                )
                if artifact is None:
                    raise CoreRunError("checkout_revision_invalid")
                artifact_rows.append(artifact)
            rebuilt = build_checkout_revision(
                workspace_id=revision.workspace_id,
                run_id=revision.run_id,
                transaction_id=revision.creator_transaction_id,
                created_at=datetime.fromisoformat(
                    revision.created_at.replace("Z", "+00:00")
                ),
                artifact_revisions=tuple(artifact_rows),
                parent_checkout_revision_id=revision.parent_checkout_revision_id,
            )
            if rebuilt.record != revision or rebuilt.members != members:
                raise CoreRunError("checkout_revision_invalid")
            receipt = receipts.get(revision.creator_transaction_id)
            if receipt is None or not any(
                item.checkout_revision_id == revision.checkout_revision_id
                for item in receipt.checkout_revisions
            ):
                raise CoreRunError("checkout_revision_invalid")
        binding_by_transaction = {
            item.transaction_id: item for item in snapshot.receipt_checkout_bindings
        }
        for receipt in snapshot.transactions:
            references = receipt.receipt_checkout_bindings
            if not references:
                continue
            binding = binding_by_transaction.get(receipt.transaction_id)
            if (
                len(references) != 1
                or binding is None
                or references[0].transaction_id != receipt.transaction_id
                or binding.post_run_id != receipt.run_id
            ):
                raise CoreRunError("checkout_revision_invalid")
            if receipt.transaction_type != "core-v2-run-reset" and (
                binding.pre_run_id != receipt.run_id
                or binding.post_run_id != receipt.run_id
            ):
                raise CoreRunError("checkout_revision_invalid")

    def _verify_historical_pr4b_prefix(
        self,
        history: ControlStoreHistory,
        snapshot: ControlStoreSnapshot,
        receipt: TransactionReceipt,
    ) -> None:
        if receipt.transaction_type in _INTAKE_EFFECT_RULES:
            if (
                receipt.committed_revision <= 1
                or receipt.prior_revision != receipt.committed_revision - 1
            ):
                raise CoreRunError("historical_prefix_invalid")
            effect, subject = _verified_intake_receipt_effect(snapshot, receipt)
            try:
                pre = history.snapshot_at_revision(
                    receipt.run_id,
                    receipt.committed_revision - 1,
                )
            except Exception as exc:
                raise CoreRunError("historical_prefix_invalid") from exc
            classify_effect_authorization(pre, effect, subject).require_allowed()
            recovery = classify_recovery_legality(snapshot)
            terminal = classify_terminal_legality(snapshot)
            if recovery.state == "invalid" or terminal.terminal_state == "invalid":
                raise CoreRunError("historical_prefix_invalid")
            return
        if not receipt.transaction_type.startswith("core-v2-"):
            raise CoreRunError("historical_prefix_invalid")
        event, binding = _verified_core_receipt_binding(snapshot, receipt)
        effect, subject, terminal_subject = _receipt_effect_authorization_subject(
            snapshot,
            receipt,
            event,
            binding,
        )
        if effect is CoreEffect.INITIALIZE:
            if receipt.committed_revision != 1 or receipt.prior_revision != 0:
                raise CoreRunError("historical_prefix_invalid")
        else:
            if (
                receipt.committed_revision <= 1
                or receipt.prior_revision != receipt.committed_revision - 1
            ):
                raise CoreRunError("historical_prefix_invalid")
            if effect is CoreEffect.RUN_RESET:
                self._verify_reset_history(history, snapshot, receipt)
                transition_id = binding.primary_record_id
                transitions = [
                    item
                    for item in snapshot.run_head_transitions
                    if item.head_transition_id == transition_id
                ]
                if len(transitions) != 1:
                    raise CoreRunError("reset_history_invalid")
                pre_run_id = transitions[0].predecessor_run_id
            else:
                pre_run_id = receipt.run_id
            try:
                pre = history.snapshot_at_revision(
                    pre_run_id,
                    receipt.committed_revision - 1,
                )
            except Exception as exc:
                raise CoreRunError("historical_prefix_invalid") from exc
            classify_effect_authorization(
                pre,
                effect,
                subject,
            ).require_allowed()
            if terminal_subject is not None:
                classify_terminal_effect_authorization(
                    pre,
                    effect,
                    terminal_subject,
                ).require_allowed()

        recovery = classify_recovery_legality(snapshot)
        if recovery.state == "invalid":
            raise CoreRunError("historical_prefix_invalid")
        terminal = classify_terminal_legality(snapshot)
        if terminal.terminal_state == "invalid":
            raise CoreRunError("historical_prefix_invalid")
        if receipt.run_head_transitions:
            if effect is not CoreEffect.RUN_RESET:
                raise CoreRunError("historical_prefix_invalid")
        if receipt.finalize_renders:
            self._verify_finalize_render_prefix(history, snapshot, receipt)
        if (
            receipt.finalizations
            or receipt.run_archives
            or receipt.package_ready_records
            or receipt.run_archive_artifact_bindings
            or receipt.package_artifact_bindings
        ):
            self._verify_archive_package_reconstruction(history, snapshot, receipt)

    @staticmethod
    def _verify_reset_history(
        history: ControlStoreHistory,
        post: ControlStoreSnapshot,
        receipt: TransactionReceipt,
    ) -> None:
        if receipt.committed_revision <= 1 or len(receipt.run_head_transitions) != 1:
            raise CoreRunError("reset_history_invalid")
        transition_ref = receipt.run_head_transitions[0]
        transitions = [
            item
            for item in post.run_head_transitions
            if item.head_transition_id == transition_ref.head_transition_id
        ]
        if len(transitions) != 1:
            raise CoreRunError("reset_history_invalid")
        transition = transitions[0]
        try:
            pre = history.snapshot_at_revision(
                transition.predecessor_run_id,
                receipt.committed_revision - 1,
            )
        except Exception as exc:
            raise CoreRunError("reset_history_invalid") from exc
        pre_head = pre.workspace_run_head
        post_head = post.workspace_run_head
        if (
            receipt.transaction_type != transaction_type_for("run_head_transition")
            or receipt.run_id != transition.successor_run_id
            or transition.accepted_transaction_id != receipt.transaction_id
            or transition.prior_workspace_revision != receipt.prior_revision
            or transition.successor_workspace_revision != receipt.committed_revision
            or pre_head is None
            or pre_head.current_run_id != transition.predecessor_run_id
            or post_head is None
            or post_head.current_run_id != transition.successor_run_id
            or post.run.run_id != transition.successor_run_id
            or any(
                item.run_id != transition.successor_run_id for item in post.transactions
            )
        ):
            raise CoreRunError("reset_history_invalid")

    @staticmethod
    def _verify_finalize_render_prefix(
        history: ControlStoreHistory,
        post: ControlStoreSnapshot,
        receipt: TransactionReceipt,
    ) -> None:
        if receipt.committed_revision <= 1 or len(receipt.finalize_renders) != 1:
            raise CoreRunError("historical_prefix_invalid")
        render_ref = receipt.finalize_renders[0]
        renders = [
            item
            for item in post.finalize_renders
            if item.render_id == render_ref.render_id
        ]
        if len(renders) != 1:
            raise CoreRunError("historical_prefix_invalid")
        render = renders[0]
        try:
            pre = history.snapshot_at_revision(
                receipt.run_id,
                receipt.committed_revision - 1,
            )
        except Exception as exc:
            raise CoreRunError("historical_prefix_invalid") from exc
        pre_artifacts = {item.artifact_id: item for item in pre.artifacts}
        reader = _AsOfArtifactReader(history, pre)
        promotion = classify_current_audit_promotion(
            pre,
            reader.read_artifact_revision_bytes,
        )
        post_revisions = {
            (item.artifact_id, item.revision): item for item in post.artifact_revisions
        }
        expected_reader_refs = [
            (item.artifact_id, item.revision) for item in render.reader_artifacts
        ]
        receipt_refs = [
            (item.artifact_id, item.revision) for item in receipt.artifact_revisions
        ]
        audited = pre_artifacts.get(render.audited_brief.artifact_id)
        audit_report = pre_artifacts.get(render.audit_report.artifact_id)
        if (
            receipt.transaction_type != transaction_type_for("finalize_render")
            or expected_reader_refs != receipt_refs
            or promotion is None
            or not promotion.is_current_lineage
            or promotion.proposal_record.proposal_id != render.audit_proposal_id
            or (
                promotion.brief_revision.artifact_id,
                promotion.brief_revision.revision,
            )
            != (render.audited_brief.artifact_id, render.audited_brief.revision)
            or (
                promotion.report_revision.artifact_id,
                promotion.report_revision.revision,
            )
            != (render.audit_report.artifact_id, render.audit_report.revision)
            or audited is None
            or audited.current_revision != render.audited_brief.revision
            or audit_report is None
            or audit_report.current_revision != render.audit_report.revision
            or any(key not in post_revisions for key in expected_reader_refs)
            or any(
                key[0] in TERMINAL_INTERNAL_ARTIFACT_IDS for key in expected_reader_refs
            )
        ):
            raise CoreRunError("historical_prefix_invalid")

    @staticmethod
    def _verify_archive_package_reconstruction(
        history: ControlStoreHistory,
        post: ControlStoreSnapshot,
        receipt: TransactionReceipt,
    ) -> None:
        if receipt.committed_revision <= 1:
            raise CoreRunError("archive_membership_invalid")
        try:
            pre = history.snapshot_at_revision(
                receipt.run_id,
                receipt.committed_revision - 1,
            )
        except Exception as exc:
            raise CoreRunError("archive_membership_invalid") from exc
        if (
            len(receipt.finalizations) != 1
            or len(receipt.run_archives) != 1
            or len(receipt.package_ready_records) != 1
        ):
            raise CoreRunError("archive_membership_invalid")
        finalization_id = receipt.finalizations[0].finalization_id
        archive_id = receipt.run_archives[0].archive_id
        package_id = receipt.package_ready_records[0].package_id
        finalizations = [
            item
            for item in post.finalizations
            if item.finalization_id == finalization_id
        ]
        archives = [item for item in post.run_archives if item.archive_id == archive_id]
        packages = [
            item for item in post.package_ready_records if item.package_id == package_id
        ]
        if len(finalizations) != 1 or len(archives) != 1 or len(packages) != 1:
            raise CoreRunError("archive_membership_invalid")
        finalization = finalizations[0]
        archive = archives[0]
        package = packages[0]
        if (
            receipt.transaction_type != transaction_type_for("finalize_complete")
            or [
                (item.artifact_id, item.revision) for item in receipt.artifact_revisions
            ]
            != [
                ("core_v2_run_archive", 1),
                ("core_v2_package_manifest", 1),
            ]
            or archive.finalization_id != finalization.finalization_id
            or package.finalization_id != finalization.finalization_id
            or package.archive_id != archive.archive_id
            or any(
                item.accepted_transaction_id != receipt.transaction_id
                for item in (finalization, archive, package)
            )
        ):
            raise CoreRunError("archive_membership_invalid")

        pre_revisions = {
            (item.artifact_id, item.revision): item for item in pre.artifact_revisions
        }
        archive_members = []
        for artifact in sorted(pre.artifacts, key=lambda item: item.artifact_id):
            if (
                artifact.current_revision <= 0
                or artifact.artifact_id in TERMINAL_INTERNAL_ARTIFACT_IDS
            ):
                continue
            revision = pre_revisions.get(
                (artifact.artifact_id, artifact.current_revision)
            )
            if revision is None:
                raise CoreRunError("archive_membership_invalid")
            archive_members.append(revision)
        actual_archive_bindings = sorted(
            (
                item
                for item in post.run_archive_artifact_bindings
                if item.archive_id == archive.archive_id
            ),
            key=lambda item: item.position,
        )
        expected_archive_rows = [
            (
                receipt.run_id,
                position,
                revision.artifact_id,
                revision.revision,
                revision.sha256,
                archive_artifact_usage(revision.artifact_id),
                receipt.transaction_id,
            )
            for position, revision in enumerate(archive_members)
        ]
        actual_archive_rows = [
            (
                item.run_id,
                item.position,
                item.artifact_id,
                item.artifact_revision,
                item.artifact_sha256,
                item.usage,
                item.accepted_transaction_id,
            )
            for item in actual_archive_bindings
        ]
        if (
            archive.included_count != len(archive_members)
            or actual_archive_rows != expected_archive_rows
            or [
                (item.archive_id, item.position)
                for item in receipt.run_archive_artifact_bindings
            ]
            != [(archive.archive_id, index) for index in range(len(archive_members))]
        ):
            raise CoreRunError("archive_membership_invalid")
        archive_payload = {
            "schema_version": "briefloop.core_v2_run_archive.v1",
            "run_id": receipt.run_id,
            "finalization_id": finalization.finalization_id,
            "artifacts": [
                {
                    "artifact_id": item.artifact_id,
                    "revision": item.revision,
                    "sha256": item.sha256,
                }
                for item in archive_members
            ],
        }
        archive_bytes = canonical_json_bytes(archive_payload) + b"\n"

        renders = [
            item
            for item in pre.finalize_renders
            if item.render_id == finalization.render_id
        ]
        if len(renders) != 1:
            raise CoreRunError("package_membership_invalid")
        render = renders[0]
        pre_artifacts = {item.artifact_id: item for item in pre.artifacts}
        reader_revisions = []
        for reference in render.reader_artifacts:
            artifact = pre_artifacts.get(reference.artifact_id)
            revision = pre_revisions.get((reference.artifact_id, reference.revision))
            if (
                artifact is None
                or artifact.current_revision != reference.revision
                or revision is None
                or reference.artifact_id in TERMINAL_INTERNAL_ARTIFACT_IDS
            ):
                raise CoreRunError("package_membership_invalid")
            reader_revisions.append(revision)

        post_revisions = {
            (item.artifact_id, item.revision): item for item in post.artifact_revisions
        }
        archive_revision = post_revisions.get(
            (archive.archive_artifact.artifact_id, archive.archive_artifact.revision)
        )
        package_revision = post_revisions.get(
            (
                package.package_manifest_artifact.artifact_id,
                package.package_manifest_artifact.revision,
            )
        )
        if (
            archive_revision is None
            or package_revision is None
            or archive.archive_artifact.artifact_id != "core_v2_run_archive"
            or archive.archive_artifact.revision != 1
            or package.package_manifest_artifact.artifact_id
            != "core_v2_package_manifest"
            or package.package_manifest_artifact.revision != 1
            or archive_revision.path != "output/intermediate/core_v2_run_archive.json"
            or package_revision.path
            != "output/intermediate/core_v2_package_manifest.json"
            or archive_revision.size_bytes != len(archive_bytes)
            or archive_revision.sha256 != sha256_hex(archive_bytes)
            or archive.manifest_sha256 != archive_revision.sha256
            or archive_revision.producer_kind != "control_tool"
            or archive_revision.producer_id != "core-v2-finalize-complete"
            or package_revision.producer_kind != "control_tool"
            or package_revision.producer_id != "core-v2-finalize-complete"
        ):
            raise CoreRunError("archive_membership_invalid")
        reader_payload = [
            {
                "artifact_id": item.artifact_id,
                "revision": item.revision,
                "sha256": item.sha256,
            }
            for item in reader_revisions
        ]
        package_payload = {
            "schema_version": "briefloop.core_v2_package_manifest.v1",
            "run_id": receipt.run_id,
            "finalization_id": finalization.finalization_id,
            "archive": {
                "artifact_id": archive_revision.artifact_id,
                "revision": archive_revision.revision,
                "sha256": archive_revision.sha256,
            },
            "reader_artifacts": reader_payload,
        }
        package_bytes = canonical_json_bytes(package_payload) + b"\n"
        expected_package_members = [
            *[(item, "reader") for item in reader_revisions],
            (archive_revision, "archive"),
            (package_revision, "manifest"),
        ]
        actual_package_bindings = sorted(
            (
                item
                for item in post.package_artifact_bindings
                if item.package_id == package.package_id
            ),
            key=lambda item: item.position,
        )
        expected_package_rows = [
            (
                receipt.run_id,
                position,
                revision.artifact_id,
                revision.revision,
                revision.sha256,
                usage,
                receipt.transaction_id,
            )
            for position, (revision, usage) in enumerate(expected_package_members)
        ]
        actual_package_rows = [
            (
                item.run_id,
                item.position,
                item.artifact_id,
                item.artifact_revision,
                item.artifact_sha256,
                item.usage,
                item.accepted_transaction_id,
            )
            for item in actual_package_bindings
        ]
        if (
            package.artifact_count != len(reader_revisions) + 2
            or actual_package_rows != expected_package_rows
            or [
                (item.package_id, item.position)
                for item in receipt.package_artifact_bindings
            ]
            != [
                (package.package_id, index)
                for index in range(len(expected_package_members))
            ]
            or package_revision.size_bytes != len(package_bytes)
            or package_revision.sha256 != sha256_hex(package_bytes)
            or package.package_manifest_sha256 != package_revision.sha256
        ):
            raise CoreRunError("package_membership_invalid")
        reader = _AsOfArtifactReader(history, post)
        if (
            reader.read_artifact_revision_bytes(
                receipt.run_id,
                archive_revision.artifact_id,
                archive_revision.revision,
            )
            != archive_bytes
            or reader.read_artifact_revision_bytes(
                receipt.run_id,
                package_revision.artifact_id,
                package_revision.revision,
            )
            != package_bytes
        ):
            raise CoreRunError("package_membership_invalid")

    @staticmethod
    def _load_contracts(
        store: SQLiteControlStore | _AsOfArtifactReader,
        binding: RunContractBinding,
    ) -> tuple[
        ValidatedRuntimeContractPayloads,
        RuntimeAdapterBinding,
        RuntimeSourcePlanBinding,
    ]:
        try:
            stage_bytes = store.read_artifact_revision_bytes(
                binding.run_id,
                binding.stage_specs_artifact.artifact_id,
                binding.stage_specs_artifact.revision,
            )
            artifact_bytes = store.read_artifact_revision_bytes(
                binding.run_id,
                binding.artifact_contracts_artifact.artifact_id,
                binding.artifact_contracts_artifact.revision,
            )
            policy_bytes = store.read_artifact_revision_bytes(
                binding.run_id,
                binding.policy_pack_artifact.artifact_id,
                binding.policy_pack_artifact.revision,
            )
            adapter_bytes = store.read_artifact_revision_bytes(
                binding.run_id,
                binding.runtime_adapter_artifact.artifact_id,
                binding.runtime_adapter_artifact.revision,
            )
            source_plan_bytes = store.read_artifact_revision_bytes(
                binding.run_id,
                binding.runtime_source_plan_artifact.artifact_id,
                binding.runtime_source_plan_artifact.revision,
            )
            if (
                sha256_hex(stage_bytes) != binding.stage_specs_sha256
                or sha256_hex(artifact_bytes) != binding.artifact_contracts_sha256
                or sha256_hex(policy_bytes) != binding.policy_pack_sha256
                or sha256_hex(adapter_bytes) != binding.runtime_adapter_sha256
                or sha256_hex(source_plan_bytes) != binding.runtime_source_plan_sha256
            ):
                raise CoreRunError("core_run_contract_mismatch")
            stage_payload = parse_json_object(stage_bytes)
            artifact_payload = parse_json_object(artifact_bytes)
            policy_payload = parse_json_object(policy_bytes)
            adapter_payload = parse_json_object(adapter_bytes)
            source_plan_payload = parse_json_object(source_plan_bytes)
            runtime_adapter = RuntimeAdapterBinding.model_validate(
                adapter_payload,
                strict=True,
            )
            source_plan = RuntimeSourcePlanBinding.model_validate(
                source_plan_payload,
                strict=True,
            )
            contracts = validate_runtime_contract_payloads(
                stage_payload,
                artifact_payload,
                policy_payload,
            )
        except CoreRunError:
            raise
        except Exception as exc:
            raise CoreRunError("core_run_contract_mismatch") from exc
        if (
            stage_payload.get("schema_version") != binding.stage_specs_schema
            or artifact_payload.get("schema_version")
            != binding.artifact_contracts_schema
            or policy_payload.get("schema_version") != binding.policy_pack_schema
            or policy_payload.get("policy_pack", {}).get("name")
            != binding.policy_pack_name
            or runtime_adapter.run_id != binding.run_id
            or runtime_adapter.runtime != binding.runtime
            or runtime_adapter.binding_fingerprint
            != binding.runtime_adapter_fingerprint
            or binding.role_topology
            not in runtime_adapter.supported_role_topologies
            or source_plan.run_id != binding.run_id
            or source_plan.sources_config_sha256 != binding.sources_config_sha256
            or source_plan.source_plan_fingerprint
            != binding.runtime_source_plan_fingerprint
        ):
            raise CoreRunError("core_run_contract_mismatch")
        return contracts, runtime_adapter, source_plan

    @staticmethod
    def _verify_contract_fingerprint(binding: RunContractBinding) -> None:
        expected = run_contract_fingerprint(
            runtime=binding.runtime,
            stage_specs_schema=binding.stage_specs_schema,
            stage_specs_sha256=binding.stage_specs_sha256,
            artifact_contracts_schema=binding.artifact_contracts_schema,
            artifact_contracts_sha256=binding.artifact_contracts_sha256,
            policy_pack_schema=binding.policy_pack_schema,
            policy_pack_name=binding.policy_pack_name,
            policy_pack_sha256=binding.policy_pack_sha256,
            runtime_adapter_sha256=binding.runtime_adapter_sha256,
            runtime_adapter_fingerprint=binding.runtime_adapter_fingerprint,
            runtime_source_plan_sha256=binding.runtime_source_plan_sha256,
            runtime_source_plan_fingerprint=binding.runtime_source_plan_fingerprint,
            run_direction=binding.run_direction.model_dump(
                mode="json",
                exclude_unset=False,
            ),
            workspace_config_sha256=binding.workspace_config_sha256,
            sources_config_sha256=binding.sources_config_sha256,
            role_topology=binding.role_topology,
            gate_strictness=binding.gate_strictness,
            input_governance_required=binding.input_governance_required,
        )
        if expected != binding.contract_fingerprint:
            raise CoreRunError("core_run_contract_mismatch")

    @staticmethod
    def _verify_receipt_bindings(snapshot: ControlStoreSnapshot) -> None:
        receipts = {item.transaction_id: item for item in snapshot.transactions}
        if len(receipts) != len(snapshot.transactions):
            raise CoreRunError("control_store_integrity_invalid")
        for event in snapshot.events:
            if event.core_run_binding is None:
                continue
            receipt = receipts.get(event.transaction_id)
            if (
                receipt is None
                or not receipt.transaction_type.startswith("core-v2-")
                or event.event_id not in receipt.event_ids
            ):
                raise CoreRunError("control_store_integrity_invalid")
        for receipt in snapshot.transactions:
            if not receipt.transaction_type.startswith("core-v2-"):
                continue
            _verified_core_receipt_binding(snapshot, receipt)

    @staticmethod
    def _verify_invocation_ownership(
        snapshot: ControlStoreSnapshot,
        binding: RunContractBinding,
    ) -> None:
        invocation_stages = _invocation_stage_map(snapshot)
        invocations = {item.invocation_id: item for item in snapshot.invocations}
        if set(invocations) != set(invocation_stages):
            raise CoreRunError("control_store_integrity_invalid")
        for invocation in invocations.values():
            if (
                invocation.run_id != snapshot.run.run_id
                or invocation.runtime != snapshot.run.runtime
            ):
                raise CoreRunError("control_store_integrity_invalid")

        def require_invocation(
            invocation_id: str,
            *,
            stage_id: str,
            role_id: str,
            completed: bool,
        ) -> None:
            invocation = invocations.get(invocation_id)
            if (
                invocation is None
                or invocation_stages.get(invocation_id) != stage_id
                or invocation.role_id != role_id
                or (completed and invocation.status != "completed")
            ):
                raise CoreRunError("control_store_integrity_invalid")

        for source in snapshot.sources:
            require_invocation(
                source.invocation_id,
                stage_id="source-discovery",
                role_id="source-provider",
                completed=True,
            )

        proposal_owners = {
            "candidate": {("scout", "scout")},
            "claim_drafts": {("claim-ledger", "claim-ledger")},
            "audit": {("auditor", "auditor")},
            "screened": {("scout", "scout"), ("screener", "screener")},
        }
        for proposal in snapshot.accepted_proposals:
            allowed = proposal_owners.get(proposal.proposal_kind)
            owner = (
                proposal.owner_stage_id,
                proposal.owner_role_id,
            )
            if allowed is None or owner not in allowed:
                raise CoreRunError("control_store_integrity_invalid")
            require_invocation(
                proposal.invocation_id,
                stage_id=owner[0],
                role_id=owner[1],
                completed=True,
            )

        owned_artifact_owners = {
            "source_candidates": ("source-discovery", "source-planner"),
            "input_classification": ("input-governance", "python_tool"),
            "analyst_draft_snapshot": ("analyst", "analyst"),
            "audit_report": ("auditor", "auditor"),
        }
        for submission in snapshot.owned_artifact_submissions:
            if submission.artifact_id == "audited_brief":
                allowed = {("editor", "editor")}
                if binding.role_topology == "human_assisted":
                    allowed.add(("analyst", "writer"))
                expected = (
                    submission.owner_stage_id,
                    submission.owner_role_id,
                )
                if expected not in allowed:
                    raise CoreRunError("control_store_integrity_invalid")
            else:
                expected = owned_artifact_owners.get(submission.artifact_id)
            if (
                expected is None
                or (
                    submission.owner_stage_id,
                    submission.owner_role_id,
                )
                != expected
            ):
                raise CoreRunError("control_store_integrity_invalid")
            if submission.invocation_id is None:
                repair_owned = any(
                    item.accepted_transaction_id == submission.accepted_transaction_id
                    and item.successor_artifact.artifact_id == submission.artifact_id
                    and item.successor_artifact.revision == submission.artifact_revision
                    for item in snapshot.artifact_supersessions
                )
                if not repair_owned and (
                    submission.artifact_id != "input_classification"
                    or submission.producer_tool_id != "input-governance-v2"
                ):
                    raise CoreRunError("control_store_integrity_invalid")
            else:
                require_invocation(
                    submission.invocation_id,
                    stage_id=expected[0],
                    role_id=expected[1],
                    completed=True,
                )

    @staticmethod
    def _verify_artifact_graph(
        snapshot: ControlStoreSnapshot,
        contracts: ValidatedRuntimeContractPayloads,
        binding: RunContractBinding,
    ) -> None:
        artifacts = {item.artifact_id: item for item in snapshot.artifacts}
        revisions = {
            (item.artifact_id, item.revision): item
            for item in snapshot.artifact_revisions
        }
        contract_rows = {str(item["artifact_id"]): item for item in contracts.artifacts}
        if not set(CORE_ARTIFACT_IDS) <= set(contract_rows):
            raise CoreRunError("control_store_integrity_invalid")

        source_artifact_ids: set[str] = set()
        for source in snapshot.sources:
            source_artifact_ids.add(source.content_artifact_id)
            if source.raw_payload_artifact_id is not None:
                source_artifact_ids.add(source.raw_payload_artifact_id)
        proposal_artifact_ids = {
            item.artifact_id for item in snapshot.accepted_proposals
        }
        terminal_artifact_ids = {
            reference.artifact_id
            for render in snapshot.finalize_renders
            for reference in render.reader_artifacts
        }
        terminal_artifact_ids.update(
            archive.archive_artifact.artifact_id for archive in snapshot.run_archives
        )
        terminal_artifact_ids.update(
            package.package_manifest_artifact.artifact_id
            for package in snapshot.package_ready_records
        )
        terminal_artifact_ids.update(
            evaluation.report_artifact.artifact_id
            for evaluation in snapshot.gate_evaluations
            if evaluation.stage_id == "finalize"
        )
        terminal_artifact_ids.update(
            result.evidence_artifact.artifact_id
            for result in snapshot.delivery_results
            if result.evidence_artifact is not None
        )
        expected_ids = (
            set(CORE_ARTIFACT_IDS)
            | set(INTERNAL_CONTRACT_ARTIFACT_IDS)
            | source_artifact_ids
            | proposal_artifact_ids
            | terminal_artifact_ids
        )
        if set(artifacts) != expected_ids:
            raise CoreRunError("control_store_integrity_invalid")

        for artifact_id in CORE_ARTIFACT_IDS:
            artifact = artifacts[artifact_id]
            contract = contract_rows[artifact_id]
            if (
                artifact.path != contract["path"]
                or artifact.format != contract["format"]
                or artifact.required is not contract["required"]
            ):
                raise CoreRunError("control_store_integrity_invalid")

        contract_refs = {
            binding.stage_specs_artifact.artifact_id: (
                binding.stage_specs_artifact.revision,
                binding.stage_specs_sha256,
            ),
            binding.artifact_contracts_artifact.artifact_id: (
                binding.artifact_contracts_artifact.revision,
                binding.artifact_contracts_sha256,
            ),
            binding.policy_pack_artifact.artifact_id: (
                binding.policy_pack_artifact.revision,
                binding.policy_pack_sha256,
            ),
            binding.runtime_adapter_artifact.artifact_id: (
                binding.runtime_adapter_artifact.revision,
                binding.runtime_adapter_sha256,
            ),
            binding.runtime_source_plan_artifact.artifact_id: (
                binding.runtime_source_plan_artifact.revision,
                binding.runtime_source_plan_sha256,
            ),
        }
        if set(contract_refs) != set(INTERNAL_CONTRACT_ARTIFACT_IDS):
            raise CoreRunError("control_store_integrity_invalid")

        expected_producers: dict[tuple[str, int], tuple[str, str]] = {}

        def bind_producer(
            artifact_id: str,
            revision_number: int,
            producer: tuple[str, str],
        ) -> None:
            key = (artifact_id, revision_number)
            prior = expected_producers.get(key)
            if prior is not None and prior != producer:
                raise CoreRunError("control_store_integrity_invalid")
            expected_producers[key] = producer

        for artifact_id, (revision_number, digest) in contract_refs.items():
            revision = revisions.get((artifact_id, revision_number))
            artifact = artifacts[artifact_id]
            if (
                revision is None
                or revision.sha256 != digest
                or artifact.current_revision != revision_number
                or artifact.format != "json"
                or not artifact.required
            ):
                raise CoreRunError("control_store_integrity_invalid")
            bind_producer(
                artifact_id,
                revision_number,
                ("control_tool", "core-v2-initializer"),
            )

        for source in snapshot.sources:
            bind_producer(
                source.content_artifact_id,
                source.content_artifact_revision,
                ("workflow_stage", "source-discovery"),
            )
            if source.raw_payload_artifact_id is not None:
                bind_producer(
                    source.raw_payload_artifact_id,
                    source.raw_payload_artifact_revision,  # type: ignore[arg-type]
                    ("workflow_stage", "source-discovery"),
                )
        for proposal in snapshot.accepted_proposals:
            bind_producer(
                proposal.artifact_id,
                proposal.artifact_revision,
                ("workflow_stage", proposal.owner_stage_id),
            )
        for submission in snapshot.owned_artifact_submissions:
            producer = (
                ("control_tool", "audit-proposal-promoter-v2")
                if submission.source_proposal_id is not None
                else (
                    "workflow_stage",
                    submission.owner_role_id,
                )
                if submission.invocation_id is not None
                else ("control_tool", submission.owner_role_id)
            )
            bind_producer(
                submission.artifact_id,
                submission.artifact_revision,
                producer,
            )
        for freeze in snapshot.claim_freezes:
            bind_producer(
                freeze.ledger_artifact.artifact_id,
                freeze.ledger_artifact.revision,
                ("control_tool", "claim-freeze-v2"),
            )
        for evaluation in snapshot.gate_evaluations:
            bind_producer(
                evaluation.report_artifact.artifact_id,
                evaluation.report_artifact.revision,
                ("control_tool", "core-v2-preloaded-quality-gates"),
            )
        for render in snapshot.finalize_renders:
            for reference in render.reader_artifacts:
                bind_producer(
                    reference.artifact_id,
                    reference.revision,
                    ("control_tool", "core-v2-finalize-render"),
                )
        for archive in snapshot.run_archives:
            bind_producer(
                archive.archive_artifact.artifact_id,
                archive.archive_artifact.revision,
                ("control_tool", "core-v2-finalize-complete"),
            )
        for package in snapshot.package_ready_records:
            bind_producer(
                package.package_manifest_artifact.artifact_id,
                package.package_manifest_artifact.revision,
                ("control_tool", "core-v2-finalize-complete"),
            )
        for result in snapshot.delivery_results:
            if (
                result.evidence_artifact is not None
                and result.evidence_artifact.artifact_id
                not in {
                    item.package_manifest_artifact.artifact_id
                    for item in snapshot.package_ready_records
                }
            ):
                bind_producer(
                    result.evidence_artifact.artifact_id,
                    result.evidence_artifact.revision,
                    ("control_tool", "core-v2-delivery-result"),
                )

        if set(revisions) != set(expected_producers):
            raise CoreRunError("control_store_integrity_invalid")
        for key, revision in revisions.items():
            if (
                not revision.frozen
                or (revision.producer_kind, revision.producer_id)
                != expected_producers[key]
            ):
                raise CoreRunError("control_store_integrity_invalid")

    @staticmethod
    def _verify_stage_chain(
        store: SQLiteControlStore,
        snapshot: ControlStoreSnapshot,
        contracts: ValidatedRuntimeContractPayloads,
        binding: RunContractBinding,
    ) -> None:
        lineage = classify_current_lineage(snapshot)
        audit_promotion = classify_current_audit_promotion(
            snapshot,
            store.read_artifact_revision_bytes,
        )
        analyst_route = (
            classify_human_assisted_analyst_route(snapshot)
            if binding.role_topology == "human_assisted"
            else None
        )
        stage_ids = [str(item["stage_id"]) for item in contracts.stages]
        states = {state.stage_id: state for state in snapshot.stage_states}
        if set(states) != set(stage_ids):
            raise CoreRunError("control_store_integrity_invalid")
        transitions: dict[str, list[object]] = {stage_id: [] for stage_id in stage_ids}
        for transition in snapshot.stage_transitions:
            if transition.stage_id not in transitions:
                raise CoreRunError("control_store_integrity_invalid")
            transitions[transition.stage_id].append(transition)
        for stage_id in stage_ids:
            rows = sorted(
                transitions[stage_id],
                key=lambda item: item.result_revision,  # type: ignore[attr-defined]
            )
            if not rows or rows[0].transition_kind != "initialize":
                raise CoreRunError("control_store_integrity_invalid")
            for revision, row in enumerate(rows):
                if (
                    row.result_revision != revision
                    or row.run_contract_fingerprint != binding.contract_fingerprint
                ):
                    raise CoreRunError("control_store_integrity_invalid")
                if revision and (
                    row.prior_revision != revision - 1
                    or row.prior_status != rows[revision - 1].result_status
                ):
                    raise CoreRunError("control_store_integrity_invalid")
            state = states[stage_id]
            if (
                state.revision != rows[-1].result_revision
                or state.status != rows[-1].result_status
            ):
                raise CoreRunError("control_store_integrity_invalid")

        transition_by_id = {
            item.transition_id: item for item in snapshot.stage_transitions
        }
        revisions = {
            (item.artifact_id, item.revision): item
            for item in snapshot.artifact_revisions
        }
        artifact_bindings: dict[str, list[object]] = {}
        for artifact_binding in snapshot.stage_artifact_bindings:
            transition = transition_by_id.get(artifact_binding.transition_id)
            revision = revisions.get(
                (
                    artifact_binding.artifact_id,
                    artifact_binding.artifact_revision,
                )
            )
            if (
                transition is None
                or transition.transition_kind
                not in {"complete", "satisfied_by_topology"}
                or revision is None
                or revision.sha256 != artifact_binding.artifact_sha256
                or artifact_binding.accepted_transaction_id
                != transition.accepted_transaction_id
            ):
                raise CoreRunError("control_store_integrity_invalid")
            artifact_bindings.setdefault(transition.transition_id, []).append(
                artifact_binding
            )
        for values in artifact_bindings.values():
            positions = sorted(item.position for item in values)  # type: ignore[attr-defined]
            if positions != list(range(len(positions))):
                raise CoreRunError("control_store_integrity_invalid")

        evaluations = {item.evaluation_id: item for item in snapshot.gate_evaluations}
        gate_bindings: dict[str, list[object]] = {}
        for gate_binding in snapshot.stage_gate_bindings:
            transition = transition_by_id.get(gate_binding.transition_id)
            evaluation = evaluations.get(gate_binding.evaluation_id)
            if (
                transition is None
                or transition.stage_id not in {"auditor", "finalize"}
                or transition.transition_kind != "complete"
                or evaluation is None
                or evaluation.gate_id != gate_binding.gate_id
                or evaluation.stage_id != transition.stage_id
                or gate_binding.accepted_transaction_id
                != transition.accepted_transaction_id
            ):
                raise CoreRunError("control_store_integrity_invalid")
            gate_bindings.setdefault(gate_binding.transition_id, []).append(
                gate_binding
            )

        artifacts = {item.artifact_id: item for item in snapshot.artifacts}

        def current_revision(artifact_id: str):
            artifact = artifacts.get(artifact_id)
            if artifact is None or artifact.current_revision <= 0:
                raise CoreRunError("control_store_integrity_invalid")
            revision = revisions.get((artifact_id, artifact.current_revision))
            if revision is None:
                raise CoreRunError("control_store_integrity_invalid")
            return revision

        def current_proposal(kind: str):
            values = [
                item
                for item in snapshot.accepted_proposals
                if item.proposal_kind == kind
                and artifacts.get(item.artifact_id) is not None
                and artifacts[item.artifact_id].current_revision
                == item.artifact_revision
            ]
            if len(values) != 1:
                raise CoreRunError("control_store_integrity_invalid")
            return values[0]

        def proposal_revision(kind: str):
            proposal = current_proposal(kind)
            revision = revisions.get((proposal.artifact_id, proposal.artifact_revision))
            if revision is None:
                raise CoreRunError("control_store_integrity_invalid")
            return revision

        def expected_artifacts_for(transition):
            stage_id = transition.stage_id
            kind = transition.transition_kind
            if kind == "satisfied_by_topology":
                if stage_id == "screener" and binding.role_topology in {
                    "default",
                    "human_assisted",
                }:
                    return (
                        (proposal_revision("candidate"), "consumed"),
                        (proposal_revision("screened"), "produced"),
                    )
                if stage_id == "editor" and binding.role_topology == "human_assisted":
                    return ((current_revision("audited_brief"), "topology_required"),)
                raise CoreRunError("control_store_integrity_invalid")
            if kind != "complete":
                return ()
            if stage_id == "doctor":
                return ()
            if stage_id == "source-discovery":
                eligible_sources = sorted(
                    (item for item in snapshot.sources if item.claims_eligible),
                    key=lambda item: item.source_id,
                )
                if not eligible_sources:
                    raise CoreRunError("control_store_integrity_invalid")
                source_revisions = []
                for source in eligible_sources:
                    revision = revisions.get(
                        (
                            source.content_artifact_id,
                            source.content_artifact_revision,
                        )
                    )
                    if revision is None or revision.sha256 != source.content_sha256:
                        raise CoreRunError("control_store_integrity_invalid")
                    source_revisions.append((revision, "consumed"))
                return (
                    (current_revision("source_candidates"), "produced"),
                    *source_revisions,
                )
            if stage_id == "input-governance":
                if not binding.input_governance_required:
                    return ()
                return ((current_revision("input_classification"), "produced"),)
            if stage_id == "scout":
                selected = [(proposal_revision("candidate"), "produced")]
                if binding.role_topology in {"default", "human_assisted"}:
                    selected.append(
                        (proposal_revision("screened"), "topology_required")
                    )
                return tuple(selected)
            if stage_id == "screener":
                if binding.role_topology != "strict":
                    raise CoreRunError("control_store_integrity_invalid")
                return (
                    (proposal_revision("screened"), "produced"),
                    (proposal_revision("candidate"), "consumed"),
                )
            if stage_id == "claim-ledger":
                if len(snapshot.claim_freezes) != 1:
                    raise CoreRunError("control_store_integrity_invalid")
                freeze = snapshot.claim_freezes[0]
                draft = revisions.get(
                    (
                        freeze.claim_drafts_artifact.artifact_id,
                        freeze.claim_drafts_artifact.revision,
                    )
                )
                ledger = revisions.get(
                    (
                        freeze.ledger_artifact.artifact_id,
                        freeze.ledger_artifact.revision,
                    )
                )
                if draft is None or ledger is None:
                    raise CoreRunError("control_store_integrity_invalid")
                return ((draft, "consumed"), (ledger, "produced"))
            if stage_id == "analyst":
                if binding.role_topology == "human_assisted":
                    bound_ids = {
                        item.artifact_id
                        for item in artifact_bindings.get(transition.transition_id, [])
                    }
                    transaction_shape = {
                        (item.stage_id, item.transition_kind)
                        for item in snapshot.stage_transitions
                        if item.accepted_transaction_id
                        == transition.accepted_transaction_id
                    }
                    if bound_ids == {"audited_brief"}:
                        if (
                            analyst_route is None
                            or analyst_route.route_family != "writer"
                            or transaction_shape
                            != {
                                ("analyst", "complete"),
                                ("editor", "satisfied_by_topology"),
                                ("auditor", "activate"),
                            }
                        ):
                            raise CoreRunError("control_store_integrity_invalid")
                        return (
                            (
                                current_revision("audited_brief"),
                                "topology_required",
                            ),
                        )
                    if bound_ids == {"analyst_draft_snapshot"}:
                        if (
                            analyst_route is None
                            or analyst_route.route_family != "snapshot"
                            or transaction_shape
                            != {
                                ("analyst", "complete"),
                                ("editor", "activate"),
                            }
                        ):
                            raise CoreRunError("control_store_integrity_invalid")
                        return (
                            (
                                current_revision("analyst_draft_snapshot"),
                                "produced",
                            ),
                        )
                    raise CoreRunError("control_store_integrity_invalid")
                return ((current_revision("analyst_draft_snapshot"), "produced"),)
            if stage_id == "editor":
                return (
                    (current_revision("audited_brief"), "produced"),
                    (current_revision("analyst_draft_snapshot"), "consumed"),
                )
            if stage_id == "auditor":
                selected = [
                    (current_revision("claim_ledger"), "consumed"),
                    (current_revision("audited_brief"), "consumed"),
                    (current_revision("audit_report"), "produced"),
                    (
                        current_revision("auditor_quality_gate_report"),
                        "produced",
                    ),
                ]
                analyst = artifacts.get("analyst_draft_snapshot")
                if analyst is not None and analyst.current_revision:
                    selected.append(
                        (current_revision("analyst_draft_snapshot"), "consumed")
                    )
                return tuple(selected)
            if stage_id == "finalize":
                finalizations = [
                    item
                    for item in snapshot.finalizations
                    if item.finalize_transition_id == transition.transition_id
                    and item.accepted_transaction_id
                    == transition.accepted_transaction_id
                ]
                if len(finalizations) != 1:
                    raise CoreRunError("control_store_integrity_invalid")
                finalization = finalizations[0]
                renders = [
                    item
                    for item in snapshot.finalize_renders
                    if item.render_id == finalization.render_id
                ]
                archives = [
                    item
                    for item in snapshot.run_archives
                    if item.finalization_id == finalization.finalization_id
                    and item.accepted_transaction_id
                    == transition.accepted_transaction_id
                ]
                packages = [
                    item
                    for item in snapshot.package_ready_records
                    if item.finalization_id == finalization.finalization_id
                    and item.accepted_transaction_id
                    == transition.accepted_transaction_id
                ]
                selected_evaluations = [
                    evaluations.get(evaluation_id)
                    for evaluation_id in finalization.finalize_gate_evaluation_ids
                ]
                if (
                    len(renders) != 1
                    or len(archives) != 1
                    or len(packages) != 1
                    or any(item is None for item in selected_evaluations)
                    or any(
                        item.stage_id != "finalize"
                        or item.gate_batch_id != finalization.finalize_gate_batch_id
                        or item.status not in {"pass", "warning"}
                        or item.blocking
                        for item in selected_evaluations
                        if item is not None
                    )
                ):
                    raise CoreRunError("control_store_integrity_invalid")

                consumed: dict[tuple[str, int], object] = {}

                def bind_current_consumed(artifact_id: str, revision_number: int):
                    revision = revisions.get((artifact_id, revision_number))
                    artifact = artifacts.get(artifact_id)
                    if (
                        revision is None
                        or artifact is None
                        or artifact.current_revision != revision_number
                    ):
                        raise CoreRunError("control_store_integrity_invalid")
                    consumed[(artifact_id, revision_number)] = revision

                render = renders[0]
                for reference in render.reader_artifacts:
                    bind_current_consumed(reference.artifact_id, reference.revision)
                selected_ids = set(finalization.finalize_gate_evaluation_ids)
                for gate_input in snapshot.gate_artifact_bindings:
                    if gate_input.evaluation_id in selected_ids:
                        bind_current_consumed(
                            gate_input.artifact_id,
                            gate_input.artifact_revision,
                        )
                if not consumed:
                    raise CoreRunError("control_store_integrity_invalid")

                archive = archives[0]
                package = packages[0]
                produced = (
                    current_revision(archive.archive_artifact.artifact_id),
                    current_revision(package.package_manifest_artifact.artifact_id),
                )
                if (
                    archive.archive_artifact.revision != produced[0].revision
                    or package.package_manifest_artifact.revision
                    != produced[1].revision
                    or set(consumed)
                    & {(item.artifact_id, item.revision) for item in produced}
                ):
                    raise CoreRunError("control_store_integrity_invalid")
                return (
                    *((item, "consumed") for item in consumed.values()),
                    *((item, "produced") for item in produced),
                )
            raise CoreRunError("control_store_integrity_invalid")

        for transition in snapshot.stage_transitions:
            expected = sorted(
                expected_artifacts_for(transition),
                key=lambda item: (item[0].artifact_id, item[0].revision),
            )
            actual = sorted(
                artifact_bindings.get(transition.transition_id, []),
                key=lambda item: item.position,
            )
            expected_signature = [
                (
                    position,
                    revision.artifact_id,
                    revision.revision,
                    revision.sha256,
                    usage,
                )
                for position, (revision, usage) in enumerate(expected)
            ]
            actual_signature = [
                (
                    item.position,
                    item.artifact_id,
                    item.artifact_revision,
                    item.artifact_sha256,
                    item.usage,
                )
                for item in actual
            ]
            if actual_signature != expected_signature:
                raise CoreRunError("control_store_integrity_invalid")

            if (
                transition.stage_id == "editor"
                and transition.transition_kind == "complete"
            ):
                produced = [
                    revision
                    for revision, usage in expected
                    if revision.artifact_id == "audited_brief" and usage == "produced"
                ]
                consumed = [
                    revision
                    for revision, usage in expected
                    if revision.artifact_id == "analyst_draft_snapshot"
                    and usage == "consumed"
                ]
                submissions = [
                    item
                    for item in snapshot.owned_artifact_submissions
                    if len(produced) == 1
                    and item.artifact_id == produced[0].artifact_id
                    and item.artifact_revision == produced[0].revision
                    and item.owner_stage_id == "editor"
                    and item.owner_role_id == "editor"
                ]
                if (
                    len(produced) != 1
                    or len(consumed) != 1
                    or len(submissions) != 1
                    or submissions[0].parent_artifact is None
                    or submissions[0].parent_artifact.artifact_id
                    != consumed[0].artifact_id
                    or submissions[0].parent_artifact.revision != consumed[0].revision
                ):
                    raise CoreRunError("control_store_integrity_invalid")

            actual_gates = {
                (item.gate_id, item.evaluation_id)
                for item in gate_bindings.get(transition.transition_id, [])
            }
            if (
                transition.stage_id == "auditor"
                and transition.transition_kind == "complete"
            ):
                if audit_promotion is None:
                    raise CoreRunError("control_store_integrity_invalid")
                try:
                    require_current_gate_after_audit_promotion(
                        audit_promotion=audit_promotion,
                        gate_batch=lineage.current_gate_batch,
                    )
                except CoreRunError as exc:
                    raise CoreRunError("control_store_integrity_invalid") from exc
                gate_report = current_revision("auditor_quality_gate_report")
                required = {
                    (
                        item.gate_id,
                        item.evaluation_id,
                    )
                    for item in snapshot.gate_evaluations
                    if item.gate_id in REQUIRED_AUDITOR_GATES
                    and item.report_artifact.artifact_id == gate_report.artifact_id
                    and item.report_artifact.revision == gate_report.revision
                    and item.status in {"pass", "warning"}
                    and not item.blocking
                }
                if {gate_id for gate_id, _evaluation_id in required} != set(
                    REQUIRED_AUDITOR_GATES
                ):
                    raise CoreRunError("control_store_integrity_invalid")
                expected_gates = required
                audit_revision = current_revision("audit_report")
                brief_revision = current_revision("audited_brief")
                if (
                    audit_promotion is None
                    or not audit_promotion.is_current_lineage
                    or audit_promotion.report_revision != audit_revision
                    or audit_promotion.brief_revision != brief_revision
                    or audit_promotion.proposal.decision == "fail"
                    or any(
                        finding.severity == "error"
                        for finding in audit_promotion.proposal.findings
                    )
                ):
                    raise CoreRunError("control_store_integrity_invalid")
            elif (
                transition.stage_id == "finalize"
                and transition.transition_kind == "complete"
            ):
                finalizations = [
                    item
                    for item in snapshot.finalizations
                    if item.finalize_transition_id == transition.transition_id
                    and item.accepted_transaction_id
                    == transition.accepted_transaction_id
                ]
                if len(finalizations) != 1:
                    raise CoreRunError("control_store_integrity_invalid")
                finalization = finalizations[0]
                finalize_evaluations = [
                    evaluations.get(evaluation_id)
                    for evaluation_id in finalization.finalize_gate_evaluation_ids
                ]
                if any(item is None for item in finalize_evaluations):
                    raise CoreRunError("control_store_integrity_invalid")
                complete_batch = [
                    item
                    for item in snapshot.gate_evaluations
                    if item.stage_id == "finalize"
                    and item.gate_batch_id == finalization.finalize_gate_batch_id
                ]
                report_refs = {
                    (
                        item.report_artifact.artifact_id,
                        item.report_artifact.revision,
                    )
                    for item in complete_batch
                }
                if len(report_refs) != 1:
                    raise CoreRunError("control_store_integrity_invalid")
                report_artifact_id, report_revision_number = next(iter(report_refs))
                report_revision = current_revision(report_artifact_id)
                expected_gates = {
                    (item.gate_id, item.evaluation_id)
                    for item in finalize_evaluations
                    if item is not None
                    and item.stage_id == "finalize"
                    and item.gate_batch_id == finalization.finalize_gate_batch_id
                    and item.status in {"pass", "warning"}
                    and not item.blocking
                }
                if (
                    report_artifact_id != "finalize_quality_gate_report"
                    or report_revision.revision != report_revision_number
                    or len(expected_gates) != len(finalize_evaluations)
                    or {item.gate_id for item in complete_batch} != set(GATE_IDS)
                    or {item.evaluation_id for item in complete_batch}
                    != set(finalization.finalize_gate_evaluation_ids)
                ):
                    raise CoreRunError("control_store_integrity_invalid")
            else:
                expected_gates = set()
            if actual_gates != expected_gates:
                raise CoreRunError("control_store_integrity_invalid")

        invocation_stages = {
            event.core_run_binding.primary_record_id: event.stage_id
            for event in snapshot.events
            if event.core_run_binding is not None
            and event.core_run_binding.effect_kind == "invocation_start"
        }
        invocations = {item.invocation_id: item for item in snapshot.invocations}
        producer_roles = {
            "source-discovery": {"source-planner"},
            "scout": {"scout"},
            "screener": {"screener"},
            "claim-ledger": {"claim-ledger"},
            "analyst": {"analyst"},
            "editor": {"editor"},
            "auditor": {"auditor"},
        }
        for transition in snapshot.stage_transitions:
            if transition.transition_kind == "initialize":
                continue
            if transition.transition_kind == "activate":
                if (
                    transition.producer_invocation_id is not None
                    or transition.producer_tool_id is not None
                ):
                    raise CoreRunError("control_store_integrity_invalid")
                continue
            if transition.stage_id == "doctor":
                if (
                    transition.producer_invocation_id is not None
                    or transition.producer_tool_id != "core-v2-doctor"
                    or transition.producer_result_status != "pass"
                    or transition.producer_implementation != "core-v2-doctor"
                    or transition.producer_version != "1"
                    or transition.producer_result_fingerprint is None
                ):
                    raise CoreRunError("control_store_integrity_invalid")
                continue
            if transition.stage_id == "input-governance":
                if binding.input_governance_required:
                    if (
                        transition.producer_tool_id != "input-governance-v2"
                        or transition.producer_invocation_id is not None
                    ):
                        raise CoreRunError("control_store_integrity_invalid")
                elif (
                    transition.producer_tool_id is not None
                    or transition.producer_invocation_id is not None
                ):
                    raise CoreRunError("control_store_integrity_invalid")
                continue
            if (
                transition.stage_id == "finalize"
                and transition.transition_kind == "complete"
            ):
                if (
                    transition.producer_invocation_id is not None
                    or transition.producer_tool_id != "core-v2-finalize-complete"
                ):
                    raise CoreRunError("control_store_integrity_invalid")
                continue
            if (
                transition.transition_kind == "satisfied_by_topology"
                and transition.stage_id == "screener"
            ):
                expected_roles = {"scout"}
                expected_invocation_stage = "scout"
            elif (
                transition.transition_kind == "satisfied_by_topology"
                and transition.stage_id == "editor"
            ):
                expected_roles = {"writer"}
                expected_invocation_stage = "analyst"
            elif (
                transition.stage_id == "analyst"
                and binding.role_topology == "human_assisted"
            ):
                bound_ids = {
                    item.artifact_id
                    for item in artifact_bindings.get(transition.transition_id, [])
                }
                expected_roles = (
                    {"writer"} if bound_ids == {"audited_brief"} else {"analyst"}
                )
                expected_invocation_stage = "analyst"
            else:
                expected_roles = producer_roles.get(transition.stage_id, set())
                expected_invocation_stage = transition.stage_id
            invocation = invocations.get(transition.producer_invocation_id or "")
            if (
                invocation is None
                or invocation.status != "completed"
                or invocation.role_id not in expected_roles
                or invocation_stages.get(invocation.invocation_id)
                != expected_invocation_stage
            ):
                raise CoreRunError("control_store_integrity_invalid")
        ready = [
            stage_id for stage_id in stage_ids if states[stage_id].status == "ready"
        ]
        if snapshot.finalizations:
            if ready or any(
                states[stage_id].status not in {"complete", "skipped"}
                for stage_id in stage_ids
            ):
                raise CoreRunError("control_store_integrity_invalid")
        else:
            if len(ready) != 1:
                raise CoreRunError("control_store_integrity_invalid")
            first_unfinished = next(
                (
                    stage_id
                    for stage_id in stage_ids
                    if states[stage_id].status not in {"complete", "skipped"}
                ),
                None,
            )
            if ready[0] != first_unfinished:
                raise CoreRunError("control_store_integrity_invalid")

        expected_initial_artifacts = set(CORE_ARTIFACT_IDS)
        if not expected_initial_artifacts <= {
            item.artifact_id for item in snapshot.artifacts
        }:
            raise CoreRunError("control_store_integrity_invalid")
        initial = {
            item.artifact_id
            for item in snapshot.artifacts
            if item.current_revision == 0
        }
        if not initial <= expected_initial_artifacts:
            raise CoreRunError("control_store_integrity_invalid")

    @staticmethod
    def _verify_integrity_chain(snapshot: ControlStoreSnapshot) -> None:
        rows = sorted(
            snapshot.run_integrity_records,
            key=lambda item: item.integrity_revision,
        )
        if not rows or rows[0].status != "clean" or rows[0].integrity_revision != 1:
            raise CoreRunError("control_store_integrity_invalid")
        contaminated = False
        for revision, row in enumerate(rows, start=1):
            if row.integrity_revision != revision:
                raise CoreRunError("control_store_integrity_invalid")
            if row.status == "contaminated":
                contaminated = True
            elif contaminated:
                raise CoreRunError("control_store_integrity_invalid")

    @staticmethod
    def _verify_claim_chain(
        store: SQLiteControlStore,
        snapshot: ControlStoreSnapshot,
        binding: RunContractBinding,
    ) -> None:
        if not snapshot.claim_freezes:
            if snapshot.claims or snapshot.claim_source_bindings:
                raise CoreRunError("control_store_integrity_invalid")
            return
        if len(snapshot.claim_freezes) != 1:
            raise CoreRunError("control_store_integrity_invalid")
        freeze = snapshot.claim_freezes[0]
        proposals = {item.proposal_id: item for item in snapshot.accepted_proposals}
        artifacts = {item.artifact_id: item for item in snapshot.artifacts}
        drafts_record = proposals.get(freeze.claim_drafts_proposal_id)
        screened_record = proposals.get(freeze.screened_proposal_id)
        candidate_record = proposals.get(freeze.candidate_proposal_id)
        drafts_artifact = (
            artifacts.get(drafts_record.artifact_id)
            if drafts_record is not None
            else None
        )
        screened_artifact = (
            artifacts.get(screened_record.artifact_id)
            if screened_record is not None
            else None
        )
        candidate_artifact = (
            artifacts.get(candidate_record.artifact_id)
            if candidate_record is not None
            else None
        )
        if (
            drafts_record is None
            or drafts_record.proposal_kind != "claim_drafts"
            or drafts_artifact is None
            or drafts_artifact.current_revision != drafts_record.artifact_revision
            or screened_record is None
            or screened_record.proposal_kind != "screened"
            or screened_artifact is None
            or screened_artifact.current_revision != screened_record.artifact_revision
            or candidate_record is None
            or candidate_record.proposal_kind != "candidate"
            or candidate_artifact is None
            or candidate_artifact.current_revision != candidate_record.artifact_revision
            or drafts_record.parent_proposal_id != screened_record.proposal_id
            or screened_record.parent_proposal_id != candidate_record.proposal_id
            or freeze.run_contract_fingerprint != binding.contract_fingerprint
            or freeze.normalization_policy != "sorted_sequential_v2"
        ):
            raise CoreRunError("control_store_integrity_invalid")
        try:
            drafts_bytes = store.read_artifact_revision_bytes(
                snapshot.run.run_id,
                drafts_record.artifact_id,
                drafts_record.artifact_revision,
            )
            screened_bytes = store.read_artifact_revision_bytes(
                snapshot.run.run_id,
                screened_record.artifact_id,
                screened_record.artifact_revision,
            )
            candidate_bytes = store.read_artifact_revision_bytes(
                snapshot.run.run_id,
                candidate_record.artifact_id,
                candidate_record.artifact_revision,
            )
            drafts = ClaimDraftsProposal.model_validate(
                parse_json_object(drafts_bytes),
                strict=True,
            )
            screened = ScreenedCandidatesProposal.model_validate(
                parse_json_object(screened_bytes),
                strict=True,
            )
            candidates = CandidateClaimsProposal.model_validate(
                parse_json_object(candidate_bytes),
                strict=True,
            )
        except Exception as exc:
            raise CoreRunError("control_store_integrity_invalid") from exc
        if (
            sha256_hex(drafts_bytes) != drafts_record.proposal_sha256
            or sha256_hex(screened_bytes) != screened_record.proposal_sha256
            or sha256_hex(candidate_bytes) != candidate_record.proposal_sha256
            or freeze.claim_drafts_sha256 != sha256_hex(drafts_bytes)
            or drafts.proposal_id != drafts_record.proposal_id
            or drafts.screened_candidates_proposal_id != screened.proposal_id
            or screened.proposal_id != screened_record.proposal_id
            or screened.candidate_claims_proposal_id != candidates.proposal_id
            or candidates.proposal_id != candidate_record.proposal_id
        ):
            raise CoreRunError("control_store_integrity_invalid")

        candidate_sources = {
            item.candidate_id: item.source_id for item in candidates.candidates
        }
        decisions = {item.candidate_id: item.decision for item in screened.decisions}
        if set(candidate_sources) != set(decisions):
            raise CoreRunError("control_store_integrity_invalid")
        selected_source_ids = {
            candidate_sources[candidate_id]
            for candidate_id, decision in decisions.items()
            if decision == "selected"
        }
        sources = {item.source_id: item for item in snapshot.sources}
        transaction_revisions = {
            item.transaction_id: item.committed_revision
            for item in snapshot.transactions
        }
        drafts_revision = transaction_revisions.get(
            drafts_record.accepted_transaction_id
        )
        freeze_revision = transaction_revisions.get(freeze.accepted_transaction_id)
        if drafts_revision is None or freeze_revision is None:
            raise CoreRunError("control_store_integrity_invalid")

        canonical_drafts = sorted(
            drafts.drafts,
            key=lambda item: (
                tuple(sorted(item.source_ids)),
                normalize_text(item.statement),
                normalize_text(item.evidence_text),
                item.claim_type,
                item.draft_id,
            ),
        )
        claims = sorted(snapshot.claims, key=lambda item: item.ordinal)
        if (
            len(claims) != freeze.claim_count
            or len(claims) != len(canonical_drafts)
            or [item.ordinal for item in claims] != list(range(1, len(claims) + 1))
            or [item.claim_id for item in claims]
            != [f"CL-{index:04d}" for index in range(1, len(claims) + 1)]
            or any(item.freeze_id != freeze.freeze_id for item in claims)
        ):
            raise CoreRunError("control_store_integrity_invalid")
        by_claim: dict[str, list[object]] = defaultdict(list)
        for source_binding in snapshot.claim_source_bindings:
            by_claim[source_binding.claim_id].append(source_binding)
        ledger_claims: list[dict[str, object]] = []
        duplicate_statements: dict[str, list[str]] = defaultdict(list)
        for claim, draft in zip(claims, canonical_drafts):
            source_ids = tuple(sorted(draft.source_ids))
            if not source_ids:
                raise CoreRunError("control_store_integrity_invalid")
            for source_id in source_ids:
                source = sources.get(source_id)
                source_revision = (
                    None
                    if source is None
                    else transaction_revisions.get(source.accepted_transaction_id)
                )
                if (
                    source is None
                    or not source.claims_eligible
                    or source_id not in selected_source_ids
                    or source_revision is None
                    or not source_revision < drafts_revision < freeze_revision
                ):
                    raise CoreRunError("control_store_integrity_invalid")
            statement = normalize_text(draft.statement)
            evidence = normalize_text(draft.evidence_text)
            duplicate_statements[statement.casefold()].append(draft.draft_id)
            expected_claim = {
                "schema_version": claim.schema_id,
                "run_id": snapshot.run.run_id,
                "claim_id": claim.claim_id,
                "freeze_id": freeze.freeze_id,
                "ordinal": claim.ordinal,
                "claim_drafts_proposal_id": drafts.proposal_id,
                "draft_id": draft.draft_id,
                "statement": statement,
                "evidence_text": evidence,
                "primary_source_id": source_ids[0],
                "claim_type": draft.claim_type,
                "confidence": "medium",
                "requires_audit": True,
                "epistemic_type": CLAIM_EPISTEMIC[draft.claim_type],
                "evidence_relation": "direct",
                "applicability_reason": None,
                "limitations": [],
                "metadata": {"source_ids": list(source_ids)},
                "created_at": freeze.frozen_at,
                "accepted_transaction_id": freeze.accepted_transaction_id,
            }
            if claim.model_dump(mode="json", exclude_unset=False) != expected_claim:
                raise CoreRunError("control_store_integrity_invalid")
            source_bindings = sorted(
                by_claim.get(claim.claim_id, []),
                key=lambda item: item.position,  # type: ignore[attr-defined]
            )
            expected_bindings = (
                [
                    {
                        "schema_version": source_binding.schema_id,
                        "run_id": snapshot.run.run_id,
                        "claim_id": claim.claim_id,
                        "source_id": source_id,
                        "position": position,
                        "citation_role": "primary" if position == 0 else "additional",
                        "claim_drafts_proposal_id": drafts.proposal_id,
                        "accepted_transaction_id": freeze.accepted_transaction_id,
                    }
                    for position, (source_binding, source_id) in enumerate(
                        zip(source_bindings, source_ids)
                    )
                ]
                if len(source_bindings) == len(source_ids)
                else []
            )
            if [
                item.model_dump(mode="json", exclude_unset=False)
                for item in source_bindings
            ] != expected_bindings:
                raise CoreRunError("control_store_integrity_invalid")
            primary = sources[source_ids[0]]
            locator = primary.locator.model_dump(mode="json", exclude_unset=False)
            ledger_claims.append(
                {
                    "claim_id": claim.claim_id,
                    "statement": statement,
                    "source_id": source_ids[0],
                    "evidence_text": evidence,
                    "source_url": locator.get("url", locator.get("path", "")),
                    "source_type": primary.retrieval_source_type,
                    "claim_type": draft.claim_type,
                    "confidence": "medium",
                    "requires_audit": True,
                    "created_by": "claim-ledger",
                    "used_in_sections": [],
                    "metadata": {
                        "source_ids": list(source_ids),
                        "source_title": primary.title,
                        "source_category": primary.source_category,
                        "published_at": primary.published_at,
                        "retrieved_at": primary.retrieved_at,
                        "underlying_evidence_type": primary.underlying_evidence_type,
                    },
                    "schema_version": "v2",
                    "epistemic_type": CLAIM_EPISTEMIC[draft.claim_type],
                    "evidence_relation": "direct",
                    "applicability_reason": "",
                    "limitations": [],
                }
            )
        warnings = [
            {
                "warning_type": "lexical_duplicate_statement",
                "draft_ids": sorted(draft_ids),
            }
            for draft_ids in duplicate_statements.values()
            if len(draft_ids) > 1
        ]
        warnings.sort(key=lambda item: item["draft_ids"])
        if [
            item.model_dump(mode="json", exclude_unset=False)
            for item in freeze.warnings
        ] != warnings or freeze.warning_count != len(warnings):
            raise CoreRunError("control_store_integrity_invalid")
        ledger_bytes = canonical_json_bytes({"claims": ledger_claims}) + b"\n"
        try:
            stored_ledger = store.read_artifact_revision_bytes(
                snapshot.run.run_id,
                freeze.ledger_artifact.artifact_id,
                freeze.ledger_artifact.revision,
            )
        except Exception as exc:
            raise CoreRunError("control_store_integrity_invalid") from exc
        if (
            stored_ledger != ledger_bytes
            or freeze.ledger_sha256 != sha256_hex(ledger_bytes)
            or freeze.claim_drafts_artifact.artifact_id != drafts_record.artifact_id
            or freeze.claim_drafts_artifact.revision != drafts_record.artifact_revision
        ):
            raise CoreRunError("control_store_integrity_invalid")

    @staticmethod
    def _verify_gate_chain(
        store: _AsOfArtifactReader,
        snapshot: ControlStoreSnapshot,
        binding: RunContractBinding,
        contracts: ValidatedRuntimeContractPayloads,
    ) -> None:
        if not snapshot.gate_evaluations:
            if snapshot.gate_findings or snapshot.gate_artifact_bindings:
                raise CoreRunError("control_store_integrity_invalid")
            return
        evaluations = {item.evaluation_id: item for item in snapshot.gate_evaluations}
        if len(evaluations) != len(snapshot.gate_evaluations):
            raise CoreRunError("control_store_integrity_invalid")
        policy_version = f"{binding.policy_pack_name}:{binding.policy_pack_sha256[:16]}"
        findings = {
            (item.evaluation_id, item.finding_id): item
            for item in snapshot.gate_findings
        }
        bindings_by_evaluation: dict[str, list[object]] = {}
        revisions = {
            (item.artifact_id, item.revision): item
            for item in snapshot.artifact_revisions
        }
        for artifact_binding in snapshot.gate_artifact_bindings:
            evaluation = evaluations.get(artifact_binding.evaluation_id)
            revision = revisions.get(
                (
                    artifact_binding.artifact_id,
                    artifact_binding.artifact_revision,
                )
            )
            if (
                evaluation is None
                or revision is None
                or revision.sha256 != artifact_binding.artifact_sha256
                or artifact_binding.accepted_transaction_id
                != evaluation.accepted_transaction_id
            ):
                raise CoreRunError("control_store_integrity_invalid")
            bindings_by_evaluation.setdefault(
                artifact_binding.evaluation_id,
                [],
            ).append(artifact_binding)
        batch_keys = sorted(
            {(item.stage_id, item.gate_batch_id) for item in evaluations.values()}
        )
        seen_findings: set[tuple[str, str]] = set()
        seen_bindings: set[tuple[str, int]] = set()
        report_revisions: dict[str, list[int]] = {
            "auditor": [],
            "finalize": [],
        }
        from .gates import _gate_finding_record, _replay_gate_outcomes

        for stage_id, batch_id in batch_keys:
            ordered_evaluations = sorted(
                (
                    item
                    for item in evaluations.values()
                    if item.stage_id == stage_id and item.gate_batch_id == batch_id
                ),
                key=lambda item: item.gate_id,
            )
            if len(ordered_evaluations) != len(GATE_IDS) or {
                item.gate_id for item in ordered_evaluations
            } != set(GATE_IDS):
                raise CoreRunError("control_store_integrity_invalid")
            report_refs = {
                (item.report_artifact.artifact_id, item.report_artifact.revision)
                for item in ordered_evaluations
            }
            expected_report_artifact = (
                "auditor_quality_gate_report"
                if stage_id == "auditor"
                else "finalize_quality_gate_report"
            )
            if (
                len(report_refs) != 1
                or {item.stage_id for item in ordered_evaluations} != {stage_id}
                or len({item.evaluation_event_id for item in ordered_evaluations}) != 1
                or len({item.accepted_transaction_id for item in ordered_evaluations})
                != 1
                or len({item.request_fingerprint for item in ordered_evaluations}) != 1
                or any(
                    item.policy_version != policy_version
                    or item.run_contract_fingerprint != binding.contract_fingerprint
                    or item.producer_implementation != "core-v2-preloaded-quality-gates"
                    or item.producer_version != "1"
                    for item in ordered_evaluations
                )
            ):
                raise CoreRunError("control_store_integrity_invalid")

            ordered_findings: list[object] = []
            canonical_bindings: list[tuple[object, ...]] | None = None
            first_bindings: tuple[object, ...] = ()
            for evaluation in ordered_evaluations:
                selected_findings = []
                for finding_id in evaluation.finding_ids:
                    key = (evaluation.evaluation_id, finding_id)
                    finding = findings.get(key)
                    if finding is None or finding.gate_id != evaluation.gate_id:
                        raise CoreRunError("control_store_integrity_invalid")
                    seen_findings.add(key)
                    selected_findings.append(finding)
                if evaluation.blocking != (
                    evaluation.status in {"fail", "unavailable", "invalid"}
                ):
                    raise CoreRunError("control_store_integrity_invalid")
                ordered_findings.extend(selected_findings)

                selected_bindings = tuple(
                    sorted(
                        bindings_by_evaluation.get(evaluation.evaluation_id, []),
                        key=lambda item: item.position,  # type: ignore[attr-defined]
                    )
                )
                if [item.position for item in selected_bindings] != list(
                    range(len(selected_bindings))
                ):
                    raise CoreRunError("control_store_integrity_invalid")
                signature = [
                    (
                        item.position,
                        item.artifact_id,
                        item.artifact_revision,
                        item.artifact_sha256,
                        item.usage,
                    )
                    for item in selected_bindings
                ]
                if canonical_bindings is None:
                    canonical_bindings = signature
                    first_bindings = selected_bindings
                elif signature != canonical_bindings:
                    raise CoreRunError("control_store_integrity_invalid")
                seen_bindings.update(
                    (item.evaluation_id, item.position) for item in selected_bindings
                )
            if not canonical_bindings:
                raise CoreRunError("control_store_integrity_invalid")

            report_artifact_id, report_revision_number = next(iter(report_refs))
            transaction_id = ordered_evaluations[0].accepted_transaction_id
            receipts = [
                item
                for item in snapshot.transactions
                if item.transaction_id == transaction_id
            ]
            if len(receipts) != 1:
                raise CoreRunError("control_store_integrity_invalid")
            receipt = receipts[0]
            try:
                batch_snapshot = store.history.snapshot_at_revision(
                    snapshot.run.run_id,
                    receipt.committed_revision,
                )
            except Exception as exc:
                raise CoreRunError("control_store_integrity_invalid") from exc
            batch_reader = _AsOfArtifactReader(store.history, batch_snapshot)
            report_revisions[stage_id].append(report_revision_number)
            try:
                report_bytes = batch_reader.read_artifact_revision_bytes(
                    snapshot.run.run_id,
                    report_artifact_id,
                    report_revision_number,
                )
                replayed = _replay_gate_outcomes(
                    batch_reader,
                    batch_snapshot,
                    binding,
                    stage_id=stage_id,
                    stages=tuple(dict(item) for item in contracts.stages),
                    artifacts=tuple(dict(item) for item in contracts.artifacts),
                    artifact_bindings=first_bindings,  # type: ignore[arg-type]
                )
            except Exception as exc:
                raise CoreRunError("control_store_integrity_invalid") from exc
            expected_report = {
                "schema_version": "briefloop.gate_report.v2",
                "run_id": snapshot.run.run_id,
                "stage_id": stage_id,
                "gate_batch_id": batch_id,
                "policy_version": policy_version,
                "run_contract_fingerprint": binding.contract_fingerprint,
                "input_artifacts": [
                    {
                        "artifact_id": item[1],
                        "revision": item[2],
                        "sha256": item[3],
                        "usage": item[4],
                    }
                    for item in canonical_bindings
                ],
                "evaluations": [
                    item.model_dump(mode="json", exclude_unset=False)
                    for item in ordered_evaluations
                ],
                "findings": [
                    item.model_dump(mode="json", exclude_unset=False)
                    for item in ordered_findings
                ],
            }
            report_revision = revisions.get(
                (report_artifact_id, report_revision_number)
            )
            batch_artifacts = {
                item.artifact_id: item for item in batch_snapshot.artifacts
            }
            report_record = batch_artifacts.get(report_artifact_id)
            if (
                report_artifact_id != expected_report_artifact
                or report_revision is None
                or report_record is None
                or report_record.current_revision != report_revision_number
                or report_revision.producer_kind != "control_tool"
                or report_revision.producer_id != "core-v2-preloaded-quality-gates"
                or report_revision.size_bytes != len(report_bytes)
                or report_revision.sha256 != sha256_hex(report_bytes)
                or report_bytes != canonical_json_bytes(expected_report) + b"\n"
            ):
                raise CoreRunError("control_store_integrity_invalid")

            evaluation_ids = [item.evaluation_id for item in ordered_evaluations]
            finding_refs = sorted(
                (
                    finding.evaluation_id,
                    finding.finding_id,
                )
                for finding in ordered_findings
            )
            binding_refs = sorted(
                (item.evaluation_id, item.position)
                for item in snapshot.gate_artifact_bindings
                if item.evaluation_id in set(evaluation_ids)
            )
            event_id = ordered_evaluations[0].evaluation_event_id
            events = [
                item for item in batch_snapshot.events if item.event_id == event_id
            ]
            if (
                receipt.transaction_type != transaction_type_for("gate_evaluation")
                or receipt.event_ids != [event_id]
                or sorted(item.evaluation_id for item in receipt.gate_evaluations)
                != sorted(evaluation_ids)
                or sorted(
                    (item.evaluation_id, item.finding_id)
                    for item in receipt.gate_findings
                )
                != finding_refs
                or sorted(
                    (item.evaluation_id, item.position)
                    for item in receipt.gate_artifact_bindings
                )
                != binding_refs
                or [
                    (item.artifact_id, item.revision)
                    for item in receipt.artifact_revisions
                ]
                != [(report_artifact_id, report_revision_number)]
                or len(events) != 1
                or events[0].stage_id != stage_id
                or events[0].artifact_id != report_artifact_id
                or events[0].transaction_id != transaction_id
            ):
                raise CoreRunError("control_store_integrity_invalid")
            for evaluation in ordered_evaluations:
                forced_status, raw_findings = replayed[evaluation.gate_id]
                expected_findings = [
                    _gate_finding_record(
                        run_id=snapshot.run.run_id,
                        evaluation_id=evaluation.evaluation_id,
                        gate_id=evaluation.gate_id,
                        position=position,
                        raw=raw,
                        accepted_transaction_id=evaluation.accepted_transaction_id,
                    )
                    for position, raw in enumerate(raw_findings, start=1)
                ]
                actual_findings = [
                    findings[(evaluation.evaluation_id, finding_id)]
                    for finding_id in evaluation.finding_ids
                ]
                replay_blocking = any(
                    item.blocking_level == "blocking" for item in expected_findings
                )
                replay_status = (
                    forced_status
                    if forced_status is not None
                    else (
                        "fail"
                        if replay_blocking
                        else ("warning" if expected_findings else "pass")
                    )
                )
                if (
                    actual_findings != expected_findings
                    or evaluation.status != replay_status
                    or evaluation.blocking != replay_blocking
                ):
                    raise CoreRunError("control_store_integrity_invalid")

        if (
            seen_findings != set(findings)
            or seen_bindings
            != {
                (item.evaluation_id, item.position)
                for item in snapshot.gate_artifact_bindings
            }
            or any(
                sorted(values) != list(range(1, len(values) + 1))
                for values in report_revisions.values()
            )
        ):
            raise CoreRunError("control_store_integrity_invalid")


def _verified_core_receipt_binding(
    snapshot: ControlStoreSnapshot,
    receipt: TransactionReceipt,
) -> tuple[EventEnvelope, CoreRunEventBinding]:
    """Bind one core transaction to its exact effect and primary record."""

    events = {event.event_id: event for event in snapshot.events}
    receipt_events = [events.get(event_id) for event_id in receipt.event_ids]
    if any(
        event is None
        or event.run_id != receipt.run_id
        or event.transaction_id != receipt.transaction_id
        for event in receipt_events
    ):
        raise CoreRunError("control_store_integrity_invalid")
    owned_events = [event for event in receipt_events if event is not None]
    bound = [event for event in owned_events if event.core_run_binding is not None]
    if len(bound) != 1:
        raise CoreRunError("control_store_integrity_invalid")
    event = bound[0]
    binding = event.core_run_binding
    if binding is None:
        raise CoreRunError("control_store_integrity_invalid")
    rule = _CORE_EFFECT_BINDING_RULES.get(binding.effect_kind)
    if (
        rule is None
        or receipt.run_id != snapshot.run.run_id
        or receipt.transaction_type != rule.transaction_type
        or event.event_type not in rule.primary_event_types
        or binding.request_id != receipt.transaction_id
        or event.transaction_id != receipt.transaction_id
    ):
        raise CoreRunError("control_store_integrity_invalid")
    _verify_authoritative_receipt_relation_families(
        receipt,
        rule.authoritative_relation_families,
    )
    _verify_core_receipt_event_set(
        snapshot,
        receipt,
        rule,
        owned_events,
    )

    primary_id = binding.primary_record_id
    fingerprint = binding.request_fingerprint
    transaction_id = receipt.transaction_id

    if rule.primary_family == "run_contract_binding":
        refs = [item.run_id for item in receipt.run_contract_bindings]
        records = [
            item for item in snapshot.run_contract_bindings if item.run_id == primary_id
        ]
        if (
            refs != [primary_id]
            or primary_id != receipt.run_id
            or len(records) != 1
            or records[0].accepted_transaction_id != transaction_id
            or records[0].request_fingerprint != fingerprint
            or records[0].initialization_event_id != event.event_id
        ):
            raise CoreRunError("control_store_integrity_invalid")
    elif rule.primary_family == "invocation":
        records = [
            item for item in snapshot.invocations if item.invocation_id == primary_id
        ]
        expected_fingerprint = (
            None
            if len(records) != 1 or event.stage_id is None
            else canonical_fingerprint(
                {
                    "schema_version": InvocationStartRequest.schema_id,
                    "request_id": transaction_id,
                    "run_id": receipt.run_id,
                    "stage_id": event.stage_id,
                    "role_id": records[0].role_id,
                    "runtime": records[0].runtime,
                    "expected_store_revision": receipt.prior_revision,
                }
            )
        )
        if (
            len(records) != 1
            or fingerprint != expected_fingerprint
            or primary_id != derived_id("INV", transaction_id, fingerprint)
            or records[0].run_id != receipt.run_id
            or event.stage_id is None
        ):
            raise CoreRunError("control_store_integrity_invalid")
    elif rule.primary_family in {
        "owned_artifact_submission",
        "audit_submission",
    }:
        refs = [item.submission_id for item in receipt.owned_artifact_submissions]
        records = [
            item
            for item in snapshot.owned_artifact_submissions
            if item.submission_id == primary_id
        ]
        if (
            refs != [primary_id]
            or len(records) != 1
            or records[0].accepted_transaction_id != transaction_id
            or records[0].request_fingerprint != fingerprint
            or records[0].accepted_event_id != event.event_id
            or (
                rule.primary_family == "audit_submission"
                and (
                    records[0].artifact_id != "audit_report"
                    or records[0].source_proposal_id is None
                )
            )
            or (
                rule.primary_family == "owned_artifact_submission"
                and records[0].artifact_id == "audit_report"
            )
        ):
            raise CoreRunError("control_store_integrity_invalid")
    elif rule.primary_family == "claim_freeze":
        refs = [item.freeze_id for item in receipt.claim_freezes]
        records = [
            item for item in snapshot.claim_freezes if item.freeze_id == primary_id
        ]
        if (
            refs != [primary_id]
            or len(records) != 1
            or records[0].accepted_transaction_id != transaction_id
            or records[0].request_fingerprint != fingerprint
            or records[0].freeze_event_id != event.event_id
        ):
            raise CoreRunError("control_store_integrity_invalid")
    elif rule.primary_family == "gate_batch":
        evaluation_ids = [item.evaluation_id for item in receipt.gate_evaluations]
        records = [
            item
            for item in snapshot.gate_evaluations
            if item.evaluation_id in evaluation_ids
        ]
        if (
            not evaluation_ids
            or len(records) != len(evaluation_ids)
            or {item.gate_batch_id for item in records} != {primary_id}
            or any(
                item.accepted_transaction_id != transaction_id
                or item.request_fingerprint != fingerprint
                or item.evaluation_event_id != event.event_id
                for item in records
            )
        ):
            raise CoreRunError("control_store_integrity_invalid")
    elif rule.primary_family == "stage_transition":
        transition_ids = [item.transition_id for item in receipt.stage_transitions]
        records = [
            item
            for item in snapshot.stage_transitions
            if item.transition_id in transition_ids
        ]
        primary = [item for item in records if item.transition_id == primary_id]
        if (
            primary_id not in transition_ids
            or len(records) != len(transition_ids)
            or len(primary) != 1
            or primary[0].transition_event_id != event.event_id
            or any(
                item.accepted_transaction_id != transaction_id
                or item.request_fingerprint != fingerprint
                for item in records
            )
        ):
            raise CoreRunError("control_store_integrity_invalid")
    elif rule.primary_family == "run_integrity_record":
        try:
            integrity_revision = int(primary_id)
        except ValueError as exc:
            raise CoreRunError("control_store_integrity_invalid") from exc
        refs = [item.integrity_revision for item in receipt.run_integrity_records]
        records = [
            item
            for item in snapshot.run_integrity_records
            if item.integrity_revision == integrity_revision
        ]
        if len(records) != 1:
            raise CoreRunError("control_store_integrity_invalid")
        record = records[0]
        observation_fingerprint = _integrity_observation_fingerprint(record)
        expected_binding_fingerprint = _integrity_contamination_binding_fingerprint(
            record.request_fingerprint,
            observation_fingerprint,
        )
        if (
            refs != [integrity_revision]
            or record.status != "contaminated"
            or record.accepted_transaction_id != transaction_id
            or record.first_detected_event_id != event.event_id
            or fingerprint != expected_binding_fingerprint
        ):
            raise CoreRunError("control_store_integrity_invalid")
    elif rule.primary_family == "repair_cycle":
        refs = [item.repair_id for item in receipt.repair_cycles]
        records = [
            item for item in snapshot.repair_cycles if item.repair_id == primary_id
        ]
        if (
            refs != [primary_id]
            or len(records) != 1
            or records[0].accepted_transaction_id != transaction_id
            or records[0].request_fingerprint != fingerprint
            or records[0].start_event_id != event.event_id
        ):
            raise CoreRunError("control_store_integrity_invalid")
    elif rule.primary_family == "artifact_supersession":
        refs = [item.supersession_id for item in receipt.artifact_supersessions]
        records = [
            item
            for item in snapshot.artifact_supersessions
            if item.supersession_id == primary_id
        ]
        if (
            refs != [primary_id]
            or len(records) != 1
            or records[0].accepted_transaction_id != transaction_id
            or records[0].request_fingerprint != fingerprint
            or records[0].accepted_event_id != event.event_id
            or len(receipt.owned_artifact_submissions) != 1
            or len(receipt.artifact_revisions) != 1
        ):
            raise CoreRunError("control_store_integrity_invalid")
    elif rule.primary_family == "repair_completion":
        refs = [item.repair_completion_id for item in receipt.repair_completions]
        records = [
            item
            for item in snapshot.repair_completions
            if item.repair_completion_id == primary_id
        ]
        if (
            refs != [primary_id]
            or len(records) != 1
            or records[0].accepted_transaction_id != transaction_id
            or records[0].request_fingerprint != fingerprint
            or records[0].completion_event_id != event.event_id
            or sorted(item.transition_id for item in receipt.stage_transitions)
            != sorted(records[0].reopened_transition_ids)
        ):
            raise CoreRunError("control_store_integrity_invalid")
    elif rule.primary_family == "recovery_completion":
        refs = [item.recovery_id for item in receipt.recovery_completions]
        records = [
            item
            for item in snapshot.recovery_completions
            if item.recovery_id == primary_id
        ]
        if (
            refs != [primary_id]
            or len(records) != 1
            or records[0].accepted_transaction_id != transaction_id
            or records[0].request_fingerprint != fingerprint
            or records[0].completion_event_id != event.event_id
        ):
            raise CoreRunError("control_store_integrity_invalid")
    elif rule.primary_family == "run_head_transition":
        refs = [item.head_transition_id for item in receipt.run_head_transitions]
        records = [
            item
            for item in snapshot.run_head_transitions
            if item.head_transition_id == primary_id
        ]
        if (
            refs != [primary_id]
            or len(records) != 1
            or records[0].accepted_transaction_id != transaction_id
            or records[0].request_fingerprint != fingerprint
            or records[0].transition_event_id != event.event_id
            or records[0].successor_run_id != snapshot.run.run_id
        ):
            raise CoreRunError("control_store_integrity_invalid")
    elif rule.primary_family == "finalize_render":
        refs = [item.render_id for item in receipt.finalize_renders]
        records = [
            item for item in snapshot.finalize_renders if item.render_id == primary_id
        ]
        if (
            refs != [primary_id]
            or len(records) != 1
            or records[0].accepted_transaction_id != transaction_id
            or records[0].request_fingerprint != fingerprint
            or records[0].render_event_id != event.event_id
            or len(receipt.artifact_revisions) != len(records[0].reader_artifacts)
        ):
            raise CoreRunError("control_store_integrity_invalid")
    elif rule.primary_family == "finalization":
        refs = [item.finalization_id for item in receipt.finalizations]
        records = [
            item
            for item in snapshot.finalizations
            if item.finalization_id == primary_id
        ]
        if (
            refs != [primary_id]
            or len(records) != 1
            or records[0].accepted_transaction_id != transaction_id
            or records[0].request_fingerprint != fingerprint
            or records[0].finalization_event_id != event.event_id
            or [item.transition_id for item in receipt.stage_transitions]
            != [records[0].finalize_transition_id]
            or len(receipt.run_archives) != 1
            or len(receipt.package_ready_records) != 1
        ):
            raise CoreRunError("control_store_integrity_invalid")
    elif rule.primary_family == "internal_approval":
        refs = [item.approval_id for item in receipt.approvals]
        records = [
            item for item in snapshot.approvals if item.approval_id == primary_id
        ]
        bindings = [
            item
            for item in snapshot.approval_package_bindings
            if item.approval_id == primary_id
        ]
        if (
            refs != [primary_id]
            or len(records) != 1
            or len(bindings) != 1
            or records[0].event_id != event.event_id
            or bindings[0].accepted_transaction_id != transaction_id
        ):
            raise CoreRunError("control_store_integrity_invalid")
    elif rule.primary_family == "delivery_authorization":
        refs = [item.authorization_id for item in receipt.delivery_authorizations]
        records = [
            item
            for item in snapshot.delivery_authorizations
            if item.authorization_id == primary_id
        ]
        if (
            refs != [primary_id]
            or len(records) != 1
            or records[0].accepted_transaction_id != transaction_id
            or records[0].request_fingerprint != fingerprint
            or records[0].authorization_event_id != event.event_id
        ):
            raise CoreRunError("control_store_integrity_invalid")
    elif rule.primary_family == "delivery_attempt":
        refs = [item.attempt_id for item in receipt.delivery_attempts]
        records = [
            item for item in snapshot.delivery_attempts if item.attempt_id == primary_id
        ]
        if (
            refs != [primary_id]
            or len(records) != 1
            or records[0].accepted_transaction_id != transaction_id
            or records[0].request_fingerprint != fingerprint
            or records[0].attempt_event_id != event.event_id
        ):
            raise CoreRunError("control_store_integrity_invalid")
    elif rule.primary_family == "delivery_result":
        refs = [item.result_id for item in receipt.delivery_results]
        records = [
            item for item in snapshot.delivery_results if item.result_id == primary_id
        ]
        if (
            refs != [primary_id]
            or len(records) != 1
            or records[0].accepted_transaction_id != transaction_id
            or records[0].request_fingerprint != fingerprint
            or records[0].result_event_id != event.event_id
        ):
            raise CoreRunError("control_store_integrity_invalid")
    else:  # pragma: no cover - the frozen table exhausts this branch.
        raise CoreRunError("control_store_integrity_invalid")
    return event, binding


def _verify_core_receipt_event_set(
    snapshot: ControlStoreSnapshot,
    receipt: TransactionReceipt,
    rule: _CoreEffectBindingRule,
    events: list[EventEnvelope],
) -> None:
    """Require the receipt's complete event set to match its domain effect."""

    actual_counts = Counter(event.event_type for event in events)
    by_id = {event.event_id: event for event in events}
    if len(by_id) != len(events) or set(by_id) != set(receipt.event_ids):
        raise CoreRunError("control_store_integrity_invalid")
    if rule.receipt_event_counts is not None:
        if actual_counts != Counter(dict(rule.receipt_event_counts)):
            raise CoreRunError("control_store_integrity_invalid")
        if rule.primary_family == "run_integrity_record":
            records = [
                item
                for item in snapshot.run_integrity_records
                if item.accepted_transaction_id == receipt.transaction_id
                and item.status == "contaminated"
            ]
            if len(records) != 1:
                raise CoreRunError("control_store_integrity_invalid")
            record = records[0]
            expected = {
                record.first_detected_event_id: "run_integrity_contaminated",
                derived_id(
                    "EVT-BLOCK",
                    receipt.transaction_id,
                    _integrity_observation_fingerprint(record),
                ): "run_blocked",
            }
            if {
                event_id: item.event_type for event_id, item in by_id.items()
            } != expected:
                raise CoreRunError("control_store_integrity_invalid")
        elif rule.primary_family == "artifact_supersession":
            supersessions = [
                item
                for item in snapshot.artifact_supersessions
                if item.accepted_transaction_id == receipt.transaction_id
            ]
            submissions = [
                item
                for item in snapshot.owned_artifact_submissions
                if item.accepted_transaction_id == receipt.transaction_id
            ]
            if len(supersessions) != 1 or len(submissions) != 1:
                raise CoreRunError("control_store_integrity_invalid")
            expected = {
                supersessions[0].accepted_event_id: "repair_stage_superseded",
                submissions[0].accepted_event_id: "owned_artifact_accepted",
            }
            if {
                event_id: item.event_type for event_id, item in by_id.items()
            } != expected:
                raise CoreRunError("control_store_integrity_invalid")
        elif rule.primary_family == "finalization":
            finalizations = [
                item
                for item in snapshot.finalizations
                if item.accepted_transaction_id == receipt.transaction_id
            ]
            archives = [
                item
                for item in snapshot.run_archives
                if item.accepted_transaction_id == receipt.transaction_id
            ]
            packages = [
                item
                for item in snapshot.package_ready_records
                if item.accepted_transaction_id == receipt.transaction_id
            ]
            if len(finalizations) != 1 or len(archives) != 1 or len(packages) != 1:
                raise CoreRunError("control_store_integrity_invalid")
            expected = {
                finalizations[0].finalization_event_id: "stage_status_changed",
                archives[0].archive_event_id: "run_archived",
                packages[0].package_event_id: "decision_recorded",
            }
            if {
                event_id: item.event_type for event_id, item in by_id.items()
            } != expected:
                raise CoreRunError("control_store_integrity_invalid")
        elif rule.primary_family == "gate_batch":
            evaluations = [
                item
                for item in snapshot.gate_evaluations
                if item.accepted_transaction_id == receipt.transaction_id
            ]
            if len(events) != 1 or not evaluations:
                raise CoreRunError("control_store_integrity_invalid")
            expected_decision = (
                "block" if any(item.blocking for item in evaluations) else "continue"
            )
            if events[0].decision != expected_decision:
                raise CoreRunError("control_store_integrity_invalid")
        return
    if rule.primary_family == "repair_completion":
        repair_events = [
            item for item in events if item.event_type == "repair_completed"
        ]
        transition_events = [
            item for item in events if item.event_type == "stage_status_changed"
        ]
        transition_ids = [item.transition_id for item in receipt.stage_transitions]
        transitions = [
            item
            for item in snapshot.stage_transitions
            if item.transition_id in transition_ids
        ]
        if (
            len(repair_events) != 1
            or len(transitions) != len(transition_ids)
            or len(transition_events) != len(transitions)
            or {item.transition_event_id for item in transitions}
            != {item.event_id for item in transition_events}
            or set(receipt.event_ids)
            != {
                *{item.event_id for item in repair_events},
                *{item.transition_event_id for item in transitions},
            }
        ):
            raise CoreRunError("control_store_integrity_invalid")
        return
    if rule.primary_family == "run_head_transition":
        reset_events = [item for item in events if item.event_type == "run_reset"]
        initialized_events = [
            item for item in events if item.event_type == "run_initialized"
        ]
        stage_events = [
            item for item in events if item.event_type == "stage_status_changed"
        ]
        transition_ids = [item.transition_id for item in receipt.stage_transitions]
        transitions = [
            item
            for item in snapshot.stage_transitions
            if item.transition_id in transition_ids
        ]
        contracts = [
            item
            for item in snapshot.run_contract_bindings
            if item.accepted_transaction_id == receipt.transaction_id
        ]
        head_transitions = [
            item
            for item in snapshot.run_head_transitions
            if item.accepted_transaction_id == receipt.transaction_id
        ]
        if (
            len(reset_events) != 1
            or len(initialized_events) != 1
            or len(transitions) != len(transition_ids)
            or len(stage_events) != len(transitions)
            or len(contracts) != 1
            or len(head_transitions) != 1
            or {item.transition_event_id for item in transitions}
            != {item.event_id for item in stage_events}
            or contracts[0].initialization_event_id != initialized_events[0].event_id
            or head_transitions[0].transition_event_id != reset_events[0].event_id
            or set(receipt.event_ids)
            != {
                reset_events[0].event_id,
                initialized_events[0].event_id,
                *{item.transition_event_id for item in transitions},
            }
        ):
            raise CoreRunError("control_store_integrity_invalid")
        return
    if rule.primary_family == "delivery_result":
        results = [
            item
            for item in snapshot.delivery_results
            if item.accepted_transaction_id == receipt.transaction_id
        ]
        expected_event_type = {
            "bundle_prepared": "delivery_bundle_prepared",
            "draft_created": "delivery_draft_created",
            "succeeded": "delivery_succeeded",
            "failed": "delivery_failed",
            "outcome_unknown": "decision_recorded",
        }
        if (
            len(events) != 1
            or len(results) != 1
            or events[0].event_id != results[0].result_event_id
            or events[0].event_type != expected_event_type[results[0].status]
        ):
            raise CoreRunError("control_store_integrity_invalid")
        return
    if rule.primary_family != "stage_transition":
        raise CoreRunError("control_store_integrity_invalid")

    transition_ids = [item.transition_id for item in receipt.stage_transitions]
    transitions = [
        item
        for item in snapshot.stage_transitions
        if item.transition_id in transition_ids
    ]
    if len(transitions) != len(transition_ids):
        raise CoreRunError("control_store_integrity_invalid")
    expected_event_ids = {item.transition_event_id for item in transitions}
    if (
        len(expected_event_ids) != len(transitions)
        or set(receipt.event_ids) != expected_event_ids
    ):
        raise CoreRunError("control_store_integrity_invalid")
    expected_counts = Counter(
        "stage_satisfied_by_topology"
        if item.transition_kind == "satisfied_by_topology"
        else "stage_status_changed"
        for item in transitions
    )
    if actual_counts != expected_counts:
        raise CoreRunError("control_store_integrity_invalid")
    if any(
        by_id[item.transition_event_id].stage_id != item.stage_id
        or by_id[item.transition_event_id].event_type
        != (
            "stage_satisfied_by_topology"
            if item.transition_kind == "satisfied_by_topology"
            else "stage_status_changed"
        )
        for item in transitions
    ):
        raise CoreRunError("control_store_integrity_invalid")


def resolve_core_replay(
    store: SQLiteControlStore,
    *,
    run_id: str,
    request_id: str,
    request_fingerprint: str,
) -> CoreRunResult | None:
    """Return one exact receipt-owned replay before current-state checks."""

    try:
        receipt = store.load_transaction_receipt(run_id, request_id)
    except Exception as exc:
        raise ControlStoreCommitOutcomeUnknown("commit_outcome_unknown") from exc
    if receipt is None:
        return None
    try:
        history = store.load_history()
        verifier = CoreRunDomainVerifier()
        verifier.verify_history(
            history,
            through_revision=receipt.committed_revision,
        )
        snapshot = history.snapshot_at_revision(
            run_id,
            receipt.committed_revision,
        )
        event, binding = _verified_core_receipt_binding(snapshot, receipt)
        if binding.request_id != request_id:
            raise CoreRunError("control_store_integrity_invalid")
        if binding.effect_kind == "integrity_contamination":
            try:
                integrity_revision = int(binding.primary_record_id)
            except ValueError as exc:
                raise CoreRunError("control_store_integrity_invalid") from exc
            records = [
                item
                for item in snapshot.run_integrity_records
                if item.integrity_revision == integrity_revision
            ]
            if len(records) != 1:
                raise CoreRunError("control_store_integrity_invalid")
            record = records[0]
            if record.request_fingerprint != request_fingerprint:
                raise CoreRunError("submission_replay_conflict")
            expected_binding_fingerprint = _integrity_contamination_binding_fingerprint(
                request_fingerprint,
                _integrity_observation_fingerprint(record),
            )
            if binding.request_fingerprint != expected_binding_fingerprint:
                raise CoreRunError("control_store_integrity_invalid")
        elif binding.request_fingerprint != request_fingerprint:
            raise CoreRunError("submission_replay_conflict")
        if binding.outcome == "blocked":
            replay = CoreRunResult(
                status="blocked",
                receipt=receipt,
                error_code=event.reason or "core_run_integrity_blocked",
                primary_record_id=binding.primary_record_id,
            )
        else:
            replay = CoreRunResult(
                status="replayed",
                receipt=receipt,
                primary_record_id=binding.primary_record_id,
            )
        from .checkout import recover_checkout_replay

        return recover_checkout_replay(store=store, replay=replay)
    except CoreRunError as exc:
        if exc.code in {
            "submission_replay_conflict",
            "historical_prefix_invalid",
            "reset_history_invalid",
            "archive_membership_invalid",
            "package_membership_invalid",
        }:
            raise
        raise ControlStoreCommitOutcomeUnknown("commit_outcome_unknown") from exc
    except ControlStoreCommitOutcomeUnknown:
        raise
    except Exception as exc:
        raise ControlStoreCommitOutcomeUnknown("commit_outcome_unknown") from exc


__all__ = [
    "CoreRunDomainVerifier",
    "VerifiedCoreRun",
    "resolve_core_replay",
]
