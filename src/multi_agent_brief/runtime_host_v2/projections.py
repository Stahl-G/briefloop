"""Read-only projections from one verified SQLite ControlStore snapshot."""

from __future__ import annotations

import hashlib
import html
import json
import os
from pathlib import Path
from typing import NamedTuple

from multi_agent_brief.control_store import ControlStoreError, SQLiteControlStore
from multi_agent_brief.control_store.sqlite_store import ControlStoreHistory
from multi_agent_brief.control_store.serialization import sha256_hex
from multi_agent_brief.core_run_v2.errors import CoreRunError
from multi_agent_brief.core_run_v2.next_action import classify_core_run_next_action
from multi_agent_brief.core_run_v2.policy import (
    core_role_topology_policy,
    transaction_type_for,
)
from multi_agent_brief.core_run_v2.terminal import classify_terminal_legality
from multi_agent_brief.core_run_v2.verifier import (
    CoreRunDomainVerifier,
    VerifiedCoreRun,
)

from .contracts import (
    FinalizedLocalGateBinding,
    FinalizedLocalReportBinding,
    FinalizedLocalReviewFacts,
    FinalizedLocalReviewProjection,
    LocalPresentationResult,
    LocalReaderBrief,
    LocalRunPresentation,
    RuntimeContinuationResult,
)

from .errors import RuntimeHostError


class _PresentationContext(NamedTuple):
    root: Path
    history: ControlStoreHistory
    verified: VerifiedCoreRun
    presentation: LocalRunPresentation


_FINALIZED_LOCAL_KNOWN_PACKAGE_STATES = frozenset(
    {
        "core_active",
        "auditor_ready",
        "rendered",
        "gate_blocked",
        "finalized",
        "finalized_local",
        "package_ready",
        "invalid",
    }
)
_FINALIZED_LOCAL_KNOWN_TERMINAL_STATES = frozenset(
    {
        "core_active",
        "auditor_ready",
        "rendered",
        "gate_blocked",
        "finalized",
        "finalized_local",
        "package_ready",
        "approval_incomplete",
        "authorization_missing_or_denied",
        "attempt_pending",
        "delivery_outcome_unknown",
        "delivery_failed",
        "draft_created",
        "delivered",
        "invalid",
    }
)
_FINALIZED_LOCAL_LATER_TERMINAL_STATES = frozenset(
    {
        "package_ready",
        "approval_incomplete",
        "authorization_missing_or_denied",
        "attempt_pending",
        "delivery_outcome_unknown",
        "delivery_failed",
        "draft_created",
        "delivered",
    }
)
_FINALIZED_LOCAL_INCOMPLETE_TERMINAL_STATES = frozenset(
    {"core_active", "auditor_ready", "rendered", "gate_blocked", "finalized"}
)


def _current_run_id(history: ControlStoreHistory) -> str:
    heads = {
        (
            snapshot.workspace_run_head.workspace_id,
            snapshot.workspace_run_head.current_run_id,
        )
        for snapshot in history.snapshots
        if snapshot.workspace_run_head is not None
    }
    if len(heads) != 1:
        raise RuntimeHostError("control_store_integrity_invalid")
    workspace_id, run_id = next(iter(heads))
    if workspace_id != history.workspace_id:
        raise RuntimeHostError("control_store_integrity_invalid")
    return run_id


def _reader_brief(
    history: ControlStoreHistory,
    verified: VerifiedCoreRun,
) -> LocalReaderBrief:
    snapshot = verified.snapshot
    if len(snapshot.finalizations) != 1:
        raise RuntimeHostError("reader_brief_projection_invalid")
    finalization = snapshot.finalizations[0]
    renders = [
        item
        for item in snapshot.finalize_renders
        if item.render_id == finalization.render_id
    ]
    if len(renders) != 1:
        raise RuntimeHostError("reader_brief_projection_invalid")
    render = renders[0]
    if (
        len(render.reader_artifacts) != 1
        or render.reader_artifacts[0].artifact_id != "reader_brief"
    ):
        raise RuntimeHostError("reader_brief_projection_invalid")
    reference = render.reader_artifacts[0]
    records = [
        item for item in snapshot.artifacts if item.artifact_id == "reader_brief"
    ]
    revisions = [
        item
        for item in snapshot.artifact_revisions
        if item.artifact_id == reference.artifact_id
        and item.revision == reference.revision
    ]
    if (
        len(records) != 1
        or records[0].current_revision != reference.revision
        or len(revisions) != 1
    ):
        raise RuntimeHostError("reader_brief_projection_invalid")
    revision = revisions[0]
    try:
        markdown = history.read_artifact_revision_bytes(
            snapshot.run.run_id,
            reference.artifact_id,
            reference.revision,
        )
        markdown.decode("utf-8", errors="strict")
    except (ControlStoreError, UnicodeDecodeError) as exc:
        raise RuntimeHostError("reader_brief_projection_invalid") from exc
    if sha256_hex(markdown) != revision.sha256 or len(markdown) != revision.size_bytes:
        raise RuntimeHostError("reader_brief_projection_invalid")
    return LocalReaderBrief.model_validate(
        {
            "state": "available",
            "artifact_id": "reader_brief",
            "revision": reference.revision,
            "sha256": revision.sha256,
            "markdown_utf8": markdown,
        },
        strict=True,
    )


def _local_run_presentation(
    history: ControlStoreHistory,
    verified: VerifiedCoreRun,
) -> LocalRunPresentation:
    snapshot = verified.snapshot
    action = classify_core_run_next_action(verified)
    terminal = classify_terminal_legality(snapshot)
    topology = core_role_topology_policy(verified.binding.role_topology)
    stages = snapshot.stage_states
    completed = sum(item.status in {"complete", "skipped"} for item in stages)
    exact_local_terminal = (
        action.action_kind == "complete"
        and action.effect_kind == "finalized_local"
        and action.reason_code == "local_finalization_complete"
        and terminal.terminal_state == "finalized_local"
    )
    if exact_local_terminal:
        view_state = "finalized"
        reader = _reader_brief(history, verified)
    else:
        reader = LocalReaderBrief(state="unavailable")
        if action.action_kind in {"human_decision", "blocked"} or (
            action.action_kind == "complete"
            and action.effect_kind == "run_terminated"
        ):
            view_state = "needs_attention"
        elif completed == 0:
            view_state = "setup"
        else:
            view_state = "running"
    authorization = (
        snapshot.run_execution_authorizations[0]
        if len(snapshot.run_execution_authorizations) == 1
        else None
    )
    return LocalRunPresentation.model_validate(
        {
            "schema_version": LocalRunPresentation.schema_id,
            "boundary": (
                "read_only_projection_not_gate_approval_delivery_or_runtime_authority"
            ),
            "run_id": snapshot.run.run_id,
            "store_revision": snapshot.store_revision,
            "runtime": snapshot.run.runtime,
            "execution_topology": topology.topology,
            "executor_display": topology.role_executor_route,
            "execution_topology_display": topology.topology_display,
            "context_independence": topology.context_display,
            "review_mode": topology.review_display,
            "role_stages": [
                str(item["stage_id"])
                for item in verified.stages
                if item.get("role_id") is not None
            ],
            "completion_target": (
                authorization.completion_target if authorization is not None else None
            ),
            "view_state": view_state,
            "completed_stages": completed,
            "total_stages": len(stages),
            "current_stage": action.stage_id,
            "current_role": action.role_id,
            "reason_code": action.reason_code,
            "terminal_state": terminal.terminal_state,
            "next_action": {
                "action_kind": action.action_kind,
                "effect_kind": action.effect_kind,
                "stage_id": action.stage_id,
                "role_id": action.role_id,
                "reason_code": action.reason_code,
            },
            "reader_brief": reader.model_dump(mode="python"),
            "summary": {
                "accepted_source_count": len(snapshot.sources),
                "claim_count": len(snapshot.claims),
                "finalization_count": len(snapshot.finalizations),
                "gates": [
                    {
                        "gate_id": item.gate_id,
                        "evaluation_id": item.evaluation_id,
                        "stage_id": item.stage_id,
                        "status": item.status,
                        "blocking": item.blocking,
                    }
                    for item in sorted(
                        snapshot.gate_evaluations,
                        key=lambda entry: (
                            entry.stage_id,
                            entry.gate_id,
                            entry.evaluation_id,
                        ),
                    )
                ],
                "receipt_ids": [item.transaction_id for item in snapshot.transactions],
            },
            "presentation": {"status": "not_requested"},
        },
        strict=True,
    )


def _presentation_context_from_history(
    root: Path,
    history: ControlStoreHistory,
    *,
    run_id: str,
    require_current_head: bool,
) -> _PresentationContext:
    """Project one explicitly selected run from one already-loaded history."""

    try:
        verified = CoreRunDomainVerifier().verify_loaded_history(
            history,
            run_id,
            require_current_head=require_current_head,
        )
        presentation = _local_run_presentation(history, verified)
    except RuntimeHostError:
        raise
    except (ControlStoreError, CoreRunError, RuntimeError, ValueError) as exc:
        raise RuntimeHostError("control_store_integrity_invalid") from exc
    return _PresentationContext(root, history, verified, presentation)


def _load_presentation_context(
    workspace: str | Path,
    *,
    run_id: str | None = None,
) -> _PresentationContext:
    """Load and verify one immutable Store history for all presentation facts."""

    try:
        root = Path(workspace).expanduser().resolve(strict=True)
        with SQLiteControlStore.open(root / "briefloop.db") as store:
            history = store.load_history()
        selected_run_id = run_id if run_id is not None else _current_run_id(history)
    except RuntimeHostError:
        raise
    except (ControlStoreError, CoreRunError, OSError, RuntimeError, ValueError) as exc:
        raise RuntimeHostError("control_store_integrity_invalid") from exc
    return _presentation_context_from_history(
        root,
        history,
        run_id=selected_run_id,
        require_current_head=run_id is None,
    )


def build_local_run_presentation(
    workspace: str | Path,
) -> LocalRunPresentation:
    """Build the strict local read model from one verified Store history."""

    return _load_presentation_context(workspace).presentation


def _exact_finalized_local_action(verified: VerifiedCoreRun):
    """Consume, but never recompute, the Core terminal decision."""

    try:
        action = classify_core_run_next_action(verified)
        terminal = classify_terminal_legality(verified.snapshot)
    except (CoreRunError, RuntimeError, ValueError) as exc:
        raise RuntimeHostError("control_store_integrity_invalid") from exc
    package_state = terminal.package_state
    if (
        package_state == "invalid"
        or package_state not in _FINALIZED_LOCAL_KNOWN_PACKAGE_STATES
    ):
        raise RuntimeHostError("control_store_integrity_invalid")
    exact_action = (
        action.action_kind == "complete"
        and action.effect_kind == "finalized_local"
        and action.reason_code == "local_finalization_complete"
    )
    terminal_state = terminal.terminal_state
    exact_terminal = terminal_state == "finalized_local"
    if (
        terminal_state == "invalid"
        or terminal_state not in _FINALIZED_LOCAL_KNOWN_TERMINAL_STATES
    ):
        raise RuntimeHostError("control_store_integrity_invalid")
    if exact_action != exact_terminal:
        raise RuntimeHostError("control_store_integrity_invalid")
    if exact_action:
        return action, terminal
    if terminal_state in _FINALIZED_LOCAL_LATER_TERMINAL_STATES:
        raise RuntimeHostError("run_not_finalized_local")
    if not verified.snapshot.finalizations:
        raise RuntimeHostError("run_not_finalized_local")
    if terminal_state in _FINALIZED_LOCAL_INCOMPLETE_TERMINAL_STATES:
        # A consistent but incomplete retained local finalization continues to
        # the receipt/record lineage classifier below.
        return action, terminal
    raise RuntimeHostError("control_store_integrity_invalid")


def _single_receipt(
    snapshot,
    *,
    transaction_id: str,
    transaction_type: str,
    relation: str,
    reference_field: str,
    record_id: str,
) -> object:
    """Bind one retained record to exactly its accepted transaction receipt."""

    receipts = [
        item for item in snapshot.transactions if item.transaction_id == transaction_id
    ]
    if (
        len(receipts) != 1
        or receipts[0].run_id != snapshot.run.run_id
        or receipts[0].transaction_type != transaction_type
    ):
        raise RuntimeHostError("finalized_local_lineage_invalid")
    references = getattr(receipts[0], relation)
    identifiers = [getattr(item, reference_field) for item in references]
    if identifiers != [record_id]:
        raise RuntimeHostError("finalized_local_lineage_invalid")
    return receipts[0]


def _finalized_local_lineage(
    history: ControlStoreHistory,
    verified: VerifiedCoreRun,
    *,
    require_current_head: bool,
):
    """Return the exact Core receipts and records that bind finalized-local."""

    snapshot = verified.snapshot
    current_run_id = _current_run_id(history)
    if (
        snapshot.workspace_id != history.workspace_id
        or snapshot.store_revision != history.store_revision
        or (require_current_head and snapshot.run.run_id != current_run_id)
        or (
            not require_current_head
            and snapshot.workspace_run_head is not None
            and snapshot.workspace_run_head.current_run_id != current_run_id
        )
    ):
        raise RuntimeHostError("control_store_integrity_invalid")
    finalizations = [
        item for item in snapshot.finalizations if item.run_id == snapshot.run.run_id
    ]
    if len(finalizations) != 1 or len(snapshot.finalizations) != 1:
        raise RuntimeHostError("finalized_local_lineage_invalid")
    finalization = finalizations[0]
    finalization_receipt = _single_receipt(
        snapshot,
        transaction_id=finalization.accepted_transaction_id,
        transaction_type=transaction_type_for("finalize_complete"),
        relation="finalizations",
        reference_field="finalization_id",
        record_id=finalization.finalization_id,
    )
    if [item.transition_id for item in finalization_receipt.stage_transitions] != [
        finalization.finalize_transition_id
    ]:
        raise RuntimeHostError("finalized_local_lineage_invalid")
    renders = [
        item
        for item in snapshot.finalize_renders
        if item.run_id == snapshot.run.run_id
        and item.render_id == finalization.render_id
    ]
    if len(renders) != 1:
        raise RuntimeHostError("finalized_local_lineage_invalid")
    render = renders[0]
    render_receipt = _single_receipt(
        snapshot,
        transaction_id=render.accepted_transaction_id,
        transaction_type=transaction_type_for("finalize_render"),
        relation="finalize_renders",
        reference_field="render_id",
        record_id=render.render_id,
    )
    declared_ids = set(finalization.finalize_gate_evaluation_ids)
    gates = [
        item for item in snapshot.gate_evaluations if item.evaluation_id in declared_ids
    ]
    if (
        len(gates) != len(declared_ids)
        or {item.evaluation_id for item in gates} != declared_ids
        or any(
            item.run_id != snapshot.run.run_id
            or item.gate_batch_id != finalization.finalize_gate_batch_id
            or item.stage_id != "finalize"
            for item in gates
        )
    ):
        raise RuntimeHostError("finalized_local_lineage_invalid")
    stage_bindings = [
        item
        for item in snapshot.stage_gate_bindings
        if item.transition_id == finalization.finalize_transition_id
    ]
    expected_bindings = {(item.gate_id, item.evaluation_id) for item in gates}
    if (
        len(stage_bindings) != len(expected_bindings)
        or {(item.gate_id, item.evaluation_id) for item in stage_bindings}
        != expected_bindings
        or any(
            item.run_id != snapshot.run.run_id
            or item.accepted_transaction_id != finalization.accepted_transaction_id
            for item in stage_bindings
        )
    ):
        raise RuntimeHostError("finalized_local_lineage_invalid")
    if {
        (item.transition_id, item.gate_id)
        for item in finalization_receipt.stage_gate_bindings
    } != {(finalization.finalize_transition_id, item.gate_id) for item in gates}:
        raise RuntimeHostError("finalized_local_lineage_invalid")
    bindings: list[FinalizedLocalGateBinding] = []
    for gate in gates:
        receipts = [
            item
            for item in snapshot.transactions
            if item.transaction_id == gate.accepted_transaction_id
        ]
        if (
            len(receipts) != 1
            or receipts[0].run_id != snapshot.run.run_id
            or receipts[0].transaction_type != transaction_type_for("gate_evaluation")
            or gate.evaluation_id
            not in [item.evaluation_id for item in receipts[0].gate_evaluations]
        ):
            raise RuntimeHostError("finalized_local_lineage_invalid")
        try:
            bindings.append(
                FinalizedLocalGateBinding.model_validate(
                    {
                        "schema_version": FinalizedLocalGateBinding.schema_id,
                        "evaluation_id": gate.evaluation_id,
                        "gate_batch_id": gate.gate_batch_id,
                        "gate_id": gate.gate_id,
                        "stage_id": gate.stage_id,
                        "accepted_transaction_id": gate.accepted_transaction_id,
                    },
                    strict=True,
                )
            )
        except ValueError as exc:
            raise RuntimeHostError("finalized_local_lineage_invalid") from exc
    return (
        finalization,
        finalization_receipt,
        render,
        render_receipt,
        sorted(
            bindings,
            key=lambda item: (item.gate_id, item.evaluation_id),
        ),
    )


def _finalized_local_report(
    history: ControlStoreHistory,
    verified: VerifiedCoreRun,
    render,
    render_receipt,
) -> FinalizedLocalReportBinding:
    """Bind the one selected reader revision to immutable history bytes only."""

    snapshot = verified.snapshot
    references = [
        item for item in render.reader_artifacts if item.artifact_id == "reader_brief"
    ]
    if len(references) != 1:
        raise RuntimeHostError("final_report_revision_invalid")
    reference = references[0]
    records = [
        item
        for item in snapshot.artifacts
        if item.run_id == snapshot.run.run_id and item.artifact_id == "reader_brief"
    ]
    revisions = [
        item
        for item in snapshot.artifact_revisions
        if item.run_id == snapshot.run.run_id
        and item.artifact_id == reference.artifact_id
        and item.revision == reference.revision
    ]
    if (
        len(records) != 1
        or len(revisions) != 1
        or records[0].current_revision != reference.revision
        or records[0].status != "valid"
        or records[0].path != revisions[0].path
        or not revisions[0].frozen
    ):
        raise RuntimeHostError("final_report_revision_invalid")
    revision = revisions[0]
    try:
        markdown = history.read_artifact_revision_bytes(
            snapshot.run.run_id,
            reference.artifact_id,
            reference.revision,
        )
        markdown.decode("utf-8", errors="strict")
    except (ControlStoreError, UnicodeDecodeError) as exc:
        raise RuntimeHostError("final_report_revision_invalid") from exc
    if sha256_hex(markdown) != revision.sha256 or len(markdown) != revision.size_bytes:
        raise RuntimeHostError("final_report_revision_invalid")
    try:
        return FinalizedLocalReportBinding.model_validate(
            {
                "schema_version": FinalizedLocalReportBinding.schema_id,
                "render_id": render.render_id,
                "render_receipt_id": render_receipt.transaction_id,
                "artifact_id": reference.artifact_id,
                "artifact_revision": reference.revision,
                "relative_path": revision.path,
                "sha256": revision.sha256,
                "size_bytes": revision.size_bytes,
                "markdown_utf8": markdown,
            },
            strict=True,
        )
    except ValueError as exc:
        raise RuntimeHostError("final_report_revision_invalid") from exc


def _finalized_local_review_projection_from_context(
    context: _PresentationContext,
    *,
    require_current_head: bool,
) -> FinalizedLocalReviewProjection:
    """Build one finalized-local projection from one verified history context."""

    action, terminal = _exact_finalized_local_action(context.verified)
    (
        finalization,
        finalization_receipt,
        render,
        render_receipt,
        gate_bindings,
    ) = _finalized_local_lineage(
        context.history,
        context.verified,
        require_current_head=require_current_head,
    )
    if not (
        action.action_kind == "complete"
        and action.effect_kind == "finalized_local"
        and action.reason_code == "local_finalization_complete"
        and terminal.terminal_state == "finalized_local"
    ):
        raise RuntimeHostError("control_store_integrity_invalid")
    report = _finalized_local_report(
        context.history,
        context.verified,
        render,
        render_receipt,
    )
    facts_payload: dict[str, object] = {
        "schema_version": FinalizedLocalReviewFacts.schema_id,
        "boundary": (
            "read_only_projection_not_runtime_gate_approval_delivery_or_provider_authority"
        ),
        "workspace_id": context.verified.snapshot.workspace_id,
        "run_id": context.verified.snapshot.run.run_id,
        "store_revision": context.verified.snapshot.store_revision,
        "terminal_state": "finalized_local",
        "terminal_action_fingerprint": action.action_fingerprint,
        "finalization_id": finalization.finalization_id,
        "finalization_receipt_id": finalization_receipt.transaction_id,
        "finalize_gate_batch_id": finalization.finalize_gate_batch_id,
        "gate_bindings": [
            item.model_dump(mode="json", exclude_unset=False) for item in gate_bindings
        ],
        "report": report.model_dump(mode="python", exclude_unset=False),
    }
    canonical_facts_payload = dict(facts_payload)
    canonical_facts_payload["report"] = report.model_dump(
        mode="json", exclude_unset=False
    )
    facts_payload["facts_fingerprint"] = FinalizedLocalReviewFacts.fingerprint_for(
        canonical_facts_payload
    )
    try:
        facts = FinalizedLocalReviewFacts.model_validate(facts_payload, strict=True)
        return FinalizedLocalReviewProjection.model_validate(
            {
                "schema_version": FinalizedLocalReviewProjection.schema_id,
                "facts": facts.model_dump(mode="python", exclude_unset=False),
                "local_run": context.presentation.model_dump(
                    mode="python", exclude_unset=False
                ),
            },
            strict=True,
        )
    except ValueError as exc:
        raise RuntimeHostError("finalized_local_lineage_invalid") from exc


def build_finalized_local_review_projection_from_history(
    workspace: str | Path,
    history: ControlStoreHistory,
    *,
    run_id: str,
    require_current_head: bool,
) -> FinalizedLocalReviewProjection:
    """Project one explicit run without reopening its verified Store history."""

    try:
        root = Path(workspace).expanduser().resolve(strict=True)
        context = _presentation_context_from_history(
            root,
            history,
            run_id=run_id,
            require_current_head=require_current_head,
        )
        return _finalized_local_review_projection_from_context(
            context,
            require_current_head=require_current_head,
        )
    except RuntimeHostError as exc:
        if str(exc) == "reader_brief_projection_invalid":
            raise RuntimeHostError("final_report_revision_invalid") from exc
        raise
    except OSError as exc:
        raise RuntimeHostError("control_store_integrity_invalid") from exc


def build_finalized_local_review_projection(
    workspace: str | Path,
    *,
    run_id: str | None = None,
) -> FinalizedLocalReviewProjection:
    """Project one exact finalized-local lineage from one verified Store history."""

    try:
        context = (
            _load_presentation_context(workspace)
            if run_id is None
            else _load_presentation_context(workspace, run_id=run_id)
        )
    except RuntimeHostError as exc:
        if str(exc) == "reader_brief_projection_invalid":
            raise RuntimeHostError("final_report_revision_invalid") from exc
        raise
    return _finalized_local_review_projection_from_context(
        context,
        require_current_head=run_id is None,
    )


def build_store_status_projection(workspace: str | Path) -> dict[str, object]:
    """Project operator state without reading any JSON control projection."""

    context = _load_presentation_context(workspace)
    root = context.root
    verified = context.verified
    local = context.presentation
    action = classify_core_run_next_action(verified)
    topology = core_role_topology_policy(verified.binding.role_topology)
    ready = [
        item.stage_id
        for item in verified.snapshot.stage_states
        if item.status == "ready"
    ]
    return {
        "schema_version": "briefloop.sqlite_status_projection.v2",
        "ok": True,
        "workspace": str(root),
        "read_only": True,
        "authority": "sqlite_control_store",
        "run_id": verified.snapshot.run.run_id,
        "runtime": verified.snapshot.run.runtime,
        "execution_topology": topology.topology,
        "executor_display": topology.role_executor_route,
        "execution_topology_display": topology.topology_display,
        "context_independence": topology.context_display,
        "review_mode": topology.review_display,
        "role_stages": topology.role_stages_display,
        "store_revision": verified.snapshot.store_revision,
        "current_stage": ready[0] if len(ready) == 1 else None,
        "stage_states": [
            item.model_dump(mode="json", exclude_unset=False)
            for item in verified.snapshot.stage_states
        ],
        "next_action": action.model_dump(mode="json", exclude_unset=False),
        "terminal_state": local.terminal_state,
        "view_state": local.view_state,
        "package_ready": local.terminal_state
        in {
            "package_ready",
            "approval_incomplete",
            "authorization_missing_or_denied",
            "attempt_pending",
            "delivery_outcome_unknown",
            "delivery_failed",
            "draft_created",
            "delivered",
        },
        "delivered": local.terminal_state == "delivered",
        "projection_source": {
            "store_revision": verified.snapshot.store_revision,
            "receipt_ids": [
                item.transaction_id for item in verified.snapshot.transactions
            ],
        },
    }


def build_runtime_continuation_result(
    verified,
    action,
    *,
    status: str,
    reason_code: str | None = None,
    envelope_path: str | None = None,
    transaction_ids: tuple[str, ...] = (),
    violations: tuple[dict[str, str], ...] = (),
    presentation: LocalPresentationResult | None = None,
) -> RuntimeContinuationResult:
    """Build one friendly result from the same verified snapshot and action."""

    stages = verified.snapshot.stage_states
    completed = sum(item.status in {"complete", "skipped"} for item in stages)
    return RuntimeContinuationResult.model_validate(
        {
            "schema_version": RuntimeContinuationResult.schema_id,
            "run_id": verified.snapshot.run.run_id,
            "store_revision": verified.snapshot.store_revision,
            "status": status,
            "reason_code": reason_code,
            "current_stage": action.stage_id,
            "current_role": action.role_id,
            "completed_stages": completed,
            "total_stages": len(stages),
            "violations": list(violations),
            "trace": {
                "next_action": action.model_dump(mode="json", exclude_unset=False),
                "envelope_path": envelope_path,
                "transaction_ids": list(transaction_ids),
            },
            "presentation": (
                presentation.model_dump(mode="json", exclude_unset=False)
                if presentation is not None
                else None
            ),
        },
        strict=True,
    )


def build_quality_projection_from_local_run(
    local: LocalRunPresentation,
) -> dict[str, object]:
    """Project Quality Panel facts from the strict one-history read model."""

    available = bool(
        local.view_state == "finalized"
        or local.terminal_state
        in {
            "package_ready",
            "approval_incomplete",
            "authorization_missing_or_denied",
            "attempt_pending",
            "delivery_outcome_unknown",
            "delivery_failed",
            "draft_created",
            "delivered",
        }
    )
    if not available:
        return {
            "ok": False,
            "status": "projection_not_available",
            "reason_code": "final_reader_not_available",
            "authority": "sqlite_control_store",
            "run_id": local.run_id,
            "store_revision": local.store_revision,
        }
    return {
        "ok": True,
        "schema_version": "briefloop.sqlite_quality_panel_projection.v2",
        "boundary": "projection_only_not_gate_or_delivery_authority",
        "authority": "sqlite_control_store",
        "run_id": local.run_id,
        "store_revision": local.store_revision,
        "package_ready": local.terminal_state != "finalized_local",
        "finalized_local": local.terminal_state == "finalized_local",
        "delivered": local.terminal_state == "delivered",
        "execution_topology": local.execution_topology,
        "executor_display": local.executor_display,
        "execution_topology_display": local.execution_topology_display,
        "context_independence": local.context_independence,
        "review_mode": local.review_mode,
        "role_stages": local.role_stages,
        "next_action": local.next_action.model_dump(mode="json", exclude_unset=False),
        "projection_source": {
            "store_revision": local.store_revision,
            "receipt_ids": local.summary.receipt_ids,
        },
    }


def build_store_quality_projection(workspace: str | Path) -> dict[str, object]:
    """Return the Store-derived Quality Panel input or a typed unavailable result."""

    return build_quality_projection_from_local_run(
        _load_presentation_context(workspace).presentation
    )


def write_store_quality_projection(workspace: str | Path) -> dict[str, object]:
    """Write replaceable JSON/HTML views after deriving all facts from Store."""

    root = Path(workspace).expanduser().resolve(strict=True)
    payload = build_store_quality_projection(root)
    if not payload.get("ok"):
        return payload
    target_dir = root / "output" / "intermediate"
    target_dir.mkdir(parents=True, exist_ok=True)
    json_bytes = (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    rendered = (
        '<!doctype html><html><head><meta charset="utf-8">'
        "<title>BriefLoop Quality Panel</title></head><body>"
        "<h1>BriefLoop Quality Panel</h1>"
        "<p>Projection only; not Gate, approval, or delivery authority.</p>"
        f"<pre>{html.escape(json_bytes.decode('utf-8'))}</pre>"
        "</body></html>\n"
    ).encode("utf-8")
    json_path = target_dir / "quality_panel.json"
    html_path = target_dir / "quality_panel.html"
    _replace_projection(json_path, json_bytes)
    _replace_projection(html_path, rendered)
    return {
        **payload,
        "quality_panel": json_path.relative_to(root).as_posix(),
        "quality_panel_sha256": hashlib.sha256(json_bytes).hexdigest(),
        "quality_panel_html": html_path.relative_to(root).as_posix(),
        "quality_panel_html_sha256": hashlib.sha256(rendered).hexdigest(),
    }


def _replace_projection(path: Path, payload: bytes) -> None:
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        with temporary.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise RuntimeHostError("projection_write_failed") from exc


__all__ = [
    "build_finalized_local_review_projection",
    "build_finalized_local_review_projection_from_history",
    "build_local_run_presentation",
    "build_quality_projection_from_local_run",
    "build_store_quality_projection",
    "build_store_status_projection",
    "build_runtime_continuation_result",
    "write_store_quality_projection",
]
