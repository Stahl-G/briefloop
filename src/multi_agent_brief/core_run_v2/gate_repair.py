"""Deterministic, Store-owned bounded Gate repair lifecycle."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
from typing import Callable, Literal

from multi_agent_brief.contracts.v2 import (
    ArtifactRevisionReference,
    CoreRunEventBinding,
    EventEnvelope,
    GateFindingReference,
    GateRepairCycleRecord,
    GateRepairOutcomeRecord,
    GateEvaluationRecord,
    StageState,
    StageTransitionRecord,
)
from multi_agent_brief.control_store import ControlStoreError, ControlStoreSnapshot
from multi_agent_brief.control_store.serialization import canonical_fingerprint
from multi_agent_brief.control_store.sqlite_store import SQLiteControlStore

from .checkout import prepare_checkout_effect, stage_checkout_effect
from .errors import CoreRunError, CoreRunResult, core_run_failure_result
from .lineage import classify_current_lineage
from .policy import derived_id, transaction_type_for


GateRepairClassification = Literal[
    "eligible",
    "not_authorized",
    "budget_exhausted",
    "source_or_non_editor_block",
    "mixed_or_ambiguous_scope",
    "invalid",
    "active",
    "passed",
    "failed_after_attempt",
    "not_required",
]


@dataclass(frozen=True)
class CurrentBlockingGateBatch:
    gate_batch_id: str
    stage_id: Literal["auditor", "finalize"]
    evaluation_ids: tuple[str, ...]
    finding_references: tuple[GateFindingReference, ...]
    target_artifact: ArtifactRevisionReference


@dataclass(frozen=True)
class GateRepairLegality:
    state: GateRepairClassification
    reason_code: str | None = None
    current_block: CurrentBlockingGateBatch | None = None
    cycle: GateRepairCycleRecord | None = None


def _current_blocking_gate_batch(
    snapshot: ControlStoreSnapshot,
) -> CurrentBlockingGateBatch | None:
    """Derive the one current blocking auditor/finalize Gate batch."""

    artifacts = {item.artifact_id: item for item in snapshot.artifacts}
    revisions = {
        (item.artifact_id, item.revision): item for item in snapshot.artifact_revisions
    }
    candidates: list[
        tuple[
            int,
            Literal["auditor", "finalize"],
            str,
            tuple[object, ...],
        ]
    ] = []
    transaction_revisions = {
        item.transaction_id: item.committed_revision for item in snapshot.transactions
    }
    for stage_id in ("auditor", "finalize"):
        report = artifacts.get(f"{stage_id}_quality_gate_report")
        if report is None or report.current_revision <= 0:
            continue
        evaluations = tuple(
            sorted(
                (
                    item
                    for item in snapshot.gate_evaluations
                    if item.stage_id == stage_id
                    and item.report_artifact.artifact_id == report.artifact_id
                    and item.report_artifact.revision == report.current_revision
                ),
                key=lambda item: item.gate_id,
            )
        )
        if not evaluations or not any(item.blocking for item in evaluations):
            continue
        batch_ids = {item.gate_batch_id for item in evaluations}
        transaction_ids = {item.accepted_transaction_id for item in evaluations}
        if len(batch_ids) != 1 or len(transaction_ids) != 1:
            raise CoreRunError("control_store_integrity_invalid")
        transaction_id = next(iter(transaction_ids))
        committed_revision = transaction_revisions.get(transaction_id)
        if committed_revision is None:
            raise CoreRunError("control_store_integrity_invalid")
        candidates.append(
            (
                committed_revision,
                stage_id,
                next(iter(batch_ids)),
                evaluations,
            )
        )
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    _revision, stage_id, batch_id, evaluations = candidates[-1]
    blocking = tuple(item for item in evaluations if item.blocking)
    findings_by_id = {item.finding_id: item for item in snapshot.gate_findings}
    references: list[GateFindingReference] = []
    target_keys: set[tuple[str, int]] = set()
    for evaluation in blocking:
        if not evaluation.finding_ids:
            raise CoreRunError("control_store_integrity_invalid")
        evaluation_bindings = [
            item
            for item in snapshot.gate_artifact_bindings
            if item.evaluation_id == evaluation.evaluation_id
        ]
        if stage_id == "auditor":
            bindings = [
                item
                for item in evaluation_bindings
                if item.artifact_id == "audited_brief"
            ]
            if len(bindings) != 1:
                raise CoreRunError("control_store_integrity_invalid")
            binding = bindings[0]
            revision = revisions.get((binding.artifact_id, binding.artifact_revision))
            if revision is None or revision.sha256 != binding.artifact_sha256:
                raise CoreRunError("control_store_integrity_invalid")
            target_keys.add((binding.artifact_id, binding.artifact_revision))
        else:
            bound_keys = {
                (item.artifact_id, item.artifact_revision)
                for item in evaluation_bindings
            }
            renders = [
                item
                for item in snapshot.finalize_renders
                if {
                    (reference.artifact_id, reference.revision)
                    for reference in item.reader_artifacts
                }.issubset(bound_keys)
                and (
                    item.audit_report.artifact_id,
                    item.audit_report.revision,
                )
                in bound_keys
            ]
            if len(renders) != 1:
                raise CoreRunError("control_store_integrity_invalid")
            target_keys.add(
                (
                    renders[0].audited_brief.artifact_id,
                    renders[0].audited_brief.revision,
                )
            )
        for finding_id in evaluation.finding_ids:
            finding = findings_by_id.get(finding_id)
            if (
                finding is None
                or finding.evaluation_id != evaluation.evaluation_id
                or finding.accepted_transaction_id != evaluation.accepted_transaction_id
            ):
                raise CoreRunError("control_store_integrity_invalid")
            if finding.blocking_level == "blocking":
                references.append(
                    GateFindingReference(
                        evaluation_id=evaluation.evaluation_id,
                        finding_id=finding.finding_id,
                    )
                )
    if len(target_keys) != 1 or not references:
        raise CoreRunError("control_store_integrity_invalid")
    artifact_id, artifact_revision = next(iter(target_keys))
    return CurrentBlockingGateBatch(
        gate_batch_id=batch_id,
        stage_id=stage_id,
        evaluation_ids=tuple(sorted(item.evaluation_id for item in blocking)),
        finding_references=tuple(
            sorted(references, key=lambda item: (item.evaluation_id, item.finding_id))
        ),
        target_artifact=ArtifactRevisionReference(
            artifact_id=artifact_id,
            revision=artifact_revision,
        ),
    )


def classify_gate_repair_legality(
    snapshot: ControlStoreSnapshot,
) -> GateRepairLegality:
    """Classify the sole preauthorized Gate repair without reading free text."""

    cycles = list(snapshot.gate_repair_cycles)
    bindings = list(snapshot.gate_repair_artifact_bindings)
    outcomes = list(snapshot.gate_repair_outcomes)
    if len(cycles) > 1 or len(bindings) > 1 or len(outcomes) > 1:
        return GateRepairLegality("invalid", "control_store_integrity_invalid")
    try:
        current = _current_blocking_gate_batch(snapshot)
    except CoreRunError:
        return GateRepairLegality("invalid", "control_store_integrity_invalid")
    if cycles:
        cycle = cycles[0]
        if (
            any(item.gate_repair_id != cycle.gate_repair_id for item in bindings)
            or any(item.gate_repair_id != cycle.gate_repair_id for item in outcomes)
            or cycle.run_id != snapshot.run.run_id
        ):
            return GateRepairLegality("invalid", "control_store_integrity_invalid")
        if outcomes:
            outcome = outcomes[0]
            if outcome.disposition == "blocked":
                return GateRepairLegality(
                    "failed_after_attempt",
                    "gate_repair_failed_after_attempt",
                    current,
                    cycle,
                )
            if (
                current is not None
                and current.gate_batch_id != outcome.replacement_gate_batch_id
            ):
                return GateRepairLegality(
                    "failed_after_attempt",
                    "gate_repair_failed_after_attempt",
                    current,
                    cycle,
                )
            return GateRepairLegality("passed", None, current, cycle)
        if current is not None and current.gate_batch_id != cycle.source_gate_batch_id:
            return GateRepairLegality(
                "failed_after_attempt",
                "gate_repair_failed_after_attempt",
                current,
                cycle,
            )
        return GateRepairLegality("active", None, current, cycle)
    if current is None:
        return GateRepairLegality("not_required")
    authorizations = list(snapshot.run_execution_authorizations)
    if (
        len(authorizations) != 1
        or authorizations[0].completion_target != "finalized_local"
    ):
        return GateRepairLegality(
            "not_authorized",
            "gate_repair_not_authorized",
            current,
        )
    if authorizations[0].repair_budget != 1:
        return GateRepairLegality(
            "budget_exhausted",
            "gate_repair_budget_exhausted",
            current,
        )
    contamination_lifecycle_present = (
        any(item.status == "contaminated" for item in snapshot.run_integrity_records)
        or bool(snapshot.repair_cycles)
        or bool(snapshot.artifact_supersessions)
        or bool(snapshot.repair_completions)
        or bool(snapshot.recovery_completions)
    )
    if (
        (
            snapshot.invocations
            and any(item.status == "active" for item in snapshot.invocations)
        )
        or contamination_lifecycle_present
        or snapshot.finalizations
    ):
        return GateRepairLegality(
            "invalid",
            "control_store_integrity_invalid",
            current,
        )
    if (
        snapshot.package_ready_records
        or snapshot.approvals
        or snapshot.delivery_authorizations
        or snapshot.delivery_attempts
        or snapshot.delivery_results
    ):
        return GateRepairLegality(
            "invalid",
            "control_store_integrity_invalid",
            current,
        )
    findings_by_key = {
        (item.evaluation_id, item.finding_id): item for item in snapshot.gate_findings
    }
    selected = [
        findings_by_key.get((item.evaluation_id, item.finding_id))
        for item in current.finding_references
    ]
    if any(item is None for item in selected):
        return GateRepairLegality(
            "invalid",
            "control_store_integrity_invalid",
            current,
        )
    findings = [item for item in selected if item is not None]
    exact_editor = [
        item.blocking_level == "blocking"
        and item.repair_owner == "editor"
        and item.stage_id == "editor"
        and item.artifact_id == "audited_brief"
        and item.source_id is None
        and item.claim_id is None
        for item in findings
    ]
    if all(exact_editor):
        return GateRepairLegality("eligible", None, current)
    explicit_non_editor = any(
        item.repair_owner
        in {
            "source-provider",
            "source-planner",
            "scout",
            "screener",
            "claim-ledger",
            "human",
            "none",
        }
        or item.source_id is not None
        or item.claim_id is not None
        for item in findings
    )
    if explicit_non_editor and not any(exact_editor):
        return GateRepairLegality(
            "source_or_non_editor_block",
            "gate_repair_source_or_non_editor_block",
            current,
        )
    return GateRepairLegality(
        "mixed_or_ambiguous_scope",
        "gate_repair_mixed_or_ambiguous_scope",
        current,
    )


def gate_repair_stage_rerun_permitted(
    snapshot: ControlStoreSnapshot,
    stage_id: str,
) -> bool:
    """Return the narrow active-cycle permission for one reopened stage."""

    legality = classify_gate_repair_legality(snapshot)
    if legality.state != "active" or legality.cycle is None:
        return False
    transitions = {item.transition_id: item for item in snapshot.stage_transitions}
    reopens = [
        transitions.get(transition_id)
        for transition_id in legality.cycle.reopened_transition_ids
    ]
    return any(
        item is not None
        and item.stage_id == stage_id
        and item.transition_kind in {"gate_repair_reopen", "gate_repair_reset"}
        for item in reopens
    )


def active_gate_repair_context(
    snapshot: ControlStoreSnapshot,
) -> dict[str, object] | None:
    """Return exact safe editor context for the active unbound repair cycle."""

    legality = classify_gate_repair_legality(snapshot)
    if (
        legality.state != "active"
        or legality.cycle is None
        or snapshot.gate_repair_artifact_bindings
    ):
        return None
    cycle = legality.cycle
    findings = {
        (item.evaluation_id, item.finding_id): item for item in snapshot.gate_findings
    }
    selected = []
    for reference in cycle.blocking_findings:
        finding = findings.get((reference.evaluation_id, reference.finding_id))
        if finding is None:
            raise CoreRunError("control_store_integrity_invalid")
        selected.append(
            {
                "evaluation_id": finding.evaluation_id,
                "finding_id": finding.finding_id,
                "finding_type": finding.finding_type,
                "category": finding.category,
                "recommendation": finding.recommendation,
            }
        )
    return {
        "gate_repair_id": cycle.gate_repair_id,
        "source_stage_id": cycle.source_stage_id,
        "source_gate_batch_id": cycle.source_gate_batch_id,
        "target_artifact": cycle.target_artifact.model_dump(
            mode="json",
            exclude_unset=False,
        ),
        "findings": selected,
    }


def gate_repair_outcome_for_batch(
    snapshot: ControlStoreSnapshot,
    *,
    stage_id: Literal["auditor", "finalize"],
    gate_batch_id: str,
    evaluations: tuple[GateEvaluationRecord, ...],
    request_id: str,
    request_fingerprint: str,
    completed_at: str,
    completion_event_id: str,
) -> GateRepairOutcomeRecord | None:
    """Derive the optional cycle outcome written by an ordinary Gate UoW."""

    if (
        len(snapshot.gate_repair_cycles) != 1
        or len(snapshot.gate_repair_artifact_bindings) != 1
        or snapshot.gate_repair_outcomes
    ):
        return None
    cycle = snapshot.gate_repair_cycles[0]
    binding = snapshot.gate_repair_artifact_bindings[0]
    if (
        binding.gate_repair_id != cycle.gate_repair_id
        or not evaluations
        or {item.stage_id for item in evaluations} != {stage_id}
        or {item.gate_batch_id for item in evaluations} != {gate_batch_id}
    ):
        raise CoreRunError("control_store_integrity_invalid")
    blocking = any(item.blocking for item in evaluations)
    if not blocking and stage_id != cycle.source_stage_id:
        return None
    return GateRepairOutcomeRecord.model_validate(
        {
            "schema_version": GateRepairOutcomeRecord.schema_id,
            "outcome_id": derived_id(
                "GATE-REPAIR-OUTCOME",
                request_id,
                request_fingerprint,
            ),
            "run_id": cycle.run_id,
            "gate_repair_id": cycle.gate_repair_id,
            "replacement_gate_batch_id": gate_batch_id,
            "replacement_stage_id": stage_id,
            "evaluation_ids": sorted(item.evaluation_id for item in evaluations),
            "disposition": "blocked" if blocking else "passed",
            "completed_at": completed_at,
            "completion_event_id": completion_event_id,
            "accepted_transaction_id": request_id,
            "request_fingerprint": request_fingerprint,
        },
        strict=True,
    )


def gate_repair_request_fingerprint(
    *,
    request_id: str,
    run_id: str,
    action_fingerprint: str,
    expected_store_revision: int,
) -> str:
    return canonical_fingerprint(
        {
            "schema_version": "briefloop.gate_repair_start_request.v2",
            "request_id": request_id,
            "run_id": run_id,
            "action_fingerprint": action_fingerprint,
            "expected_store_revision": expected_store_revision,
        }
    )


_Clock = Callable[[], datetime]


class GateRepairService:
    """Commit the sole parameter-free, Core-derived Gate repair start."""

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

    def start(
        self,
        *,
        request_id: str,
        run_id: str,
        action_fingerprint: str,
        expected_store_revision: int,
    ) -> CoreRunResult:
        try:
            return self._start(
                request_id=request_id,
                run_id=run_id,
                action_fingerprint=action_fingerprint,
                expected_store_revision=expected_store_revision,
            )
        except (CoreRunError, ControlStoreError) as exc:
            return core_run_failure_result(exc)

    def _start(
        self,
        *,
        request_id: str,
        run_id: str,
        action_fingerprint: str,
        expected_store_revision: int,
    ) -> CoreRunResult:
        from .next_action import classify_core_run_next_action
        from .verifier import CoreRunDomainVerifier, resolve_core_replay

        fingerprint = gate_repair_request_fingerprint(
            request_id=request_id,
            run_id=run_id,
            action_fingerprint=action_fingerprint,
            expected_store_revision=expected_store_revision,
        )
        with SQLiteControlStore.open(
            self.workspace / "briefloop.db",
            clock=self._clock,
        ) as store:
            replay = resolve_core_replay(
                store,
                run_id=run_id,
                request_id=request_id,
                request_fingerprint=fingerprint,
            )
            if replay is not None:
                return replay
            verifier = CoreRunDomainVerifier()
            verified = verifier.verify(store, run_id)
            action = classify_core_run_next_action(verified)
            if (
                action.action_kind != "deterministic"
                or action.effect_kind != "gate_repair_start"
                or action.action_fingerprint != action_fingerprint
                or action.store_revision != expected_store_revision
                or verified.snapshot.store_revision != expected_store_revision
            ):
                raise CoreRunError("stage_not_current")
            legality = classify_gate_repair_legality(verified.snapshot)
            if legality.state != "eligible" or legality.current_block is None:
                raise CoreRunError("gate_repair_scope_invalid")
            block = legality.current_block
            authorization = verified.snapshot.run_execution_authorizations[0]
            now_value = self._clock()
            if not isinstance(now_value, datetime) or now_value.tzinfo is None:
                raise CoreRunError("core_run_request_invalid")
            now = now_value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
            gate_repair_id = derived_id("GATE-REPAIR", request_id, fingerprint)
            start_event_id = derived_id(
                "EVT-GATE-REPAIR",
                request_id,
                fingerprint,
            )
            states = {item.stage_id: item for item in verified.snapshot.stage_states}
            transition_rows: list[StageTransitionRecord] = []
            state_rows: list[StageState] = []
            events: list[EventEnvelope] = []

            def transition(
                stage_id: str,
                *,
                kind: str,
                status: str,
                reason: str,
            ) -> None:
                prior = states.get(stage_id)
                if prior is None:
                    raise CoreRunError("control_store_integrity_invalid")
                transition_id = derived_id(
                    "TRN-GATE-REPAIR",
                    request_id,
                    stage_id,
                    str(prior.revision + 1),
                )
                event_id = derived_id(
                    "EVT-GATE-REPAIR-STAGE",
                    transition_id,
                    fingerprint,
                )
                transition_rows.append(
                    StageTransitionRecord.model_validate(
                        {
                            "schema_version": StageTransitionRecord.schema_id,
                            "transition_id": transition_id,
                            "run_id": run_id,
                            "stage_id": stage_id,
                            "transition_kind": kind,
                            "requested_decision": "continue",
                            "prior_status": prior.status,
                            "prior_revision": prior.revision,
                            "result_status": status,
                            "result_revision": prior.revision + 1,
                            "reason": reason,
                            "run_contract_fingerprint": (
                                verified.binding.contract_fingerprint
                            ),
                            "actor": "system",
                            "created_at": now,
                            "transition_event_id": event_id,
                            "accepted_transaction_id": request_id,
                            "request_fingerprint": fingerprint,
                        },
                        strict=True,
                    )
                )
                state_rows.append(
                    StageState.model_validate(
                        {
                            "schema_version": StageState.schema_id,
                            "run_id": run_id,
                            "stage_id": stage_id,
                            "status": status,
                            "revision": prior.revision + 1,
                            "updated_at": now,
                        },
                        strict=True,
                    )
                )
                events.append(
                    EventEnvelope.model_validate(
                        {
                            "schema_version": EventEnvelope.schema_id,
                            "event_id": event_id,
                            "run_id": run_id,
                            "event_type": "stage_status_changed",
                            "created_at": now,
                            "actor": "system",
                            "transaction_id": request_id,
                            "stage_id": stage_id,
                            "decision": "continue",
                            "reason": reason,
                            "metadata": {},
                        },
                        strict=True,
                    )
                )

            transition(
                "editor",
                kind="gate_repair_reopen",
                status="ready",
                reason="preauthorized editor Gate repair opened",
            )
            transition(
                "auditor",
                kind="gate_repair_reset",
                status="pending",
                reason="auditor reset for preauthorized Gate repair",
            )
            finalize = states.get("finalize")
            if finalize is None:
                raise CoreRunError("control_store_integrity_invalid")
            if finalize.status != "pending":
                transition(
                    "finalize",
                    kind="gate_repair_reset",
                    status="pending",
                    reason="finalize reset for preauthorized Gate repair",
                )
            cycle = GateRepairCycleRecord.model_validate(
                {
                    "schema_version": GateRepairCycleRecord.schema_id,
                    "gate_repair_id": gate_repair_id,
                    "run_id": run_id,
                    "authorization_id": authorization.authorization_id,
                    "repair_ordinal": 1,
                    "source_gate_batch_id": block.gate_batch_id,
                    "source_stage_id": block.stage_id,
                    "blocking_evaluation_ids": list(block.evaluation_ids),
                    "blocking_findings": [
                        item.model_dump(mode="json", exclude_unset=False)
                        for item in block.finding_references
                    ],
                    "repair_owner": "editor",
                    "target_artifact": block.target_artifact.model_dump(
                        mode="json",
                        exclude_unset=False,
                    ),
                    "reopened_transition_ids": [
                        item.transition_id
                        for item in sorted(
                            transition_rows,
                            key=lambda transition: transition.transition_id,
                        )
                    ],
                    "started_at": now,
                    "start_event_id": start_event_id,
                    "accepted_transaction_id": request_id,
                    "request_fingerprint": fingerprint,
                },
                strict=True,
            )
            events.append(
                EventEnvelope.model_validate(
                    {
                        "schema_version": EventEnvelope.schema_id,
                        "event_id": start_event_id,
                        "run_id": run_id,
                        "event_type": "gate_repair_started",
                        "created_at": now,
                        "actor": "system",
                        "transaction_id": request_id,
                        "stage_id": "editor",
                        "artifact_id": "audited_brief",
                        "decision": "continue",
                        "reason": "preauthorized editor-only Gate repair started",
                        "metadata": {},
                        "core_run_binding": CoreRunEventBinding(
                            request_id=request_id,
                            request_fingerprint=fingerprint,
                            effect_kind="gate_repair_start",
                            primary_record_id=gate_repair_id,
                            outcome="committed",
                        ),
                    },
                    strict=True,
                )
            )
            checkout = prepare_checkout_effect(
                workspace=self.workspace,
                snapshot=verified.snapshot,
                transaction_id=request_id,
                created_at=now_value,
            )
            unit = store.begin(
                run_id,
                request_id,
                transaction_type_for("gate_repair_start"),
                expected_store_revision,
            )
            unit.put_gate_repair_cycle(cycle)
            for item in transition_rows:
                unit.append_stage_transition(item)
            for item in state_rows:
                unit.put_stage_state(item)
            for item in events:
                unit.append_event(item)
            stage_checkout_effect(unit, checkout)
            receipt = unit.commit(
                _postcommit_observer=lambda _receipt: verifier.verify(store, run_id)
            )
            return CoreRunResult(
                status="committed",
                receipt=receipt,
                primary_record_id=gate_repair_id,
            )


__all__ = [
    "CurrentBlockingGateBatch",
    "GateRepairLegality",
    "GateRepairService",
    "active_gate_repair_context",
    "classify_gate_repair_legality",
    "gate_repair_request_fingerprint",
    "gate_repair_outcome_for_batch",
    "gate_repair_stage_rerun_permitted",
]
