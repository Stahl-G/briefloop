"""Canonical read-only state machine for contaminated-run recovery."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from multi_agent_brief.orchestrator.run_integrity import (
    RUN_INTEGRITY_CLEAN,
    RUN_INTEGRITY_CONTAMINATED_REPAIRED,
    interpret_run_integrity,
    project_for_read,
)
from multi_agent_brief.orchestrator.runtime_state.artifact_registry import (
    ARTIFACT_REGISTRY_SCHEMA,
)
from multi_agent_brief.orchestrator.runtime_state.contracts_loader import (
    _stage_ids,
    load_stage_specs,
)
from multi_agent_brief.orchestrator.runtime_state.control_context import (
    load_control_object,
)
from multi_agent_brief.orchestrator.runtime_state.errors import RuntimeStateError
from multi_agent_brief.orchestrator.runtime_state.event_log import (
    read_event_log_records_strict,
)
from multi_agent_brief.orchestrator.runtime_state.manifest import (
    RUNTIME_MANIFEST_SCHEMA,
)
from multi_agent_brief.orchestrator.runtime_state.paths import runtime_state_paths
from multi_agent_brief.orchestrator.runtime_state.workflow import WORKFLOW_STATE_SCHEMA
from multi_agent_brief.orchestrator_contract import resolve_repo_workdir


RECOVERY_STATE_SCHEMA = "briefloop.recovery_state.v1"

RECOVERY_NOT_APPLICABLE = "not_applicable"
RECOVERY_AWAITING = "awaiting_recovery"
RECOVERY_IN_PROGRESS = "repair_in_progress"
RECOVERY_RERUN_PENDING = "downstream_rerun_pending"
RECOVERY_FINALIZE_RENDER_REQUIRED = "finalize_render_required"
RECOVERY_FINALIZE_COMPLETION_PENDING = "finalize_completion_pending"
RECOVERY_COMPLETED_NON_REFERENCE = "completed_non_reference"
RECOVERY_INVALID = "invalid_recovery_state"

RECOVERY_STATUSES = {
    RECOVERY_NOT_APPLICABLE,
    RECOVERY_AWAITING,
    RECOVERY_IN_PROGRESS,
    RECOVERY_RERUN_PENDING,
    RECOVERY_FINALIZE_RENDER_REQUIRED,
    RECOVERY_FINALIZE_COMPLETION_PENDING,
    RECOVERY_COMPLETED_NON_REFERENCE,
    RECOVERY_INVALID,
}

ACTION_NONE = "none"
ACTION_REQUEST_DECISION = "request_recovery_decision"
ACTION_COMPLETE_ACTIVE_REPAIR = "complete_active_repair"
ACTION_RERUN_FROM_STAGE = "rerun_from_stage"
ACTION_RUN_FINALIZE = "run_finalize"
ACTION_RUN_FINALIZE_COMPLETE = "run_finalize_gate_or_finalize_complete"
ACTION_INSPECT_INVALID = "inspect_invalid_recovery"
ACTION_START_NEW_RUN = "start_new_run"
ACTION_INSPECT_DELIVERY = "inspect_delivery_truth"


@dataclass(frozen=True)
class RecoveryContext:
    run_id: str
    workflow: Mapping[str, Any]
    event_records: Sequence[Mapping[str, Any]]
    stage_ids: Sequence[str]
    artifact_registry: Mapping[str, Any] | None
    finalize_report: Mapping[str, Any] | None


def evaluate_recovery_state(
    *,
    workspace: str | Path,
    repo_workdir: str | Path | None = None,
) -> dict[str, Any]:
    """Load current control records and return the canonical recovery state."""

    ws = Path(workspace).expanduser().resolve()
    try:
        context = _load_recovery_context(workspace=ws, repo_workdir=repo_workdir)
        return interpret_recovery_state(context)
    except RuntimeStateError as exc:
        return _state(
            status=RECOVERY_INVALID,
            reason_code="control_context_invalid",
            reason=str(exc),
            details={"error_code": exc.error_code, **exc.details},
        )


def interpret_recovery_state(context: RecoveryContext) -> dict[str, Any]:
    """Interpret validated records without reading or writing the workspace."""

    event_error = _event_identity_error(context.event_records)
    if event_error:
        return _invalid(context, "event_identity_invalid", event_error)

    workflow_run_id = _text(context.workflow.get("run_id"))
    if workflow_run_id != context.run_id:
        return _invalid(
            context,
            "workflow_run_id_mismatch",
            "workflow_state.run_id does not match runtime_manifest.run_id.",
        )

    integrity = project_for_read(
        interpret_run_integrity(
            context.workflow.get("run_integrity"),
            field_present="run_integrity" in context.workflow,
        )
    )
    if integrity.get("status") == "unknown":
        return _invalid(context, "run_integrity_invalid", "run_integrity is invalid.")

    current_events = [event for event in context.event_records if _text(event.get("run_id")) == context.run_id]
    owner_revision, owner_revision_error = _latest_owner_revision(
        current_events,
        stage_ids=context.stage_ids,
    )
    if owner_revision_error:
        return _invalid(context, "owner_revision_binding_invalid", owner_revision_error)
    contaminations = [
        event for event in current_events if event.get("event_type") == "run_integrity_contaminated"
    ]
    if not contaminations:
        if integrity.get("status") == RUN_INTEGRITY_CLEAN:
            return _state(
                status=RECOVERY_NOT_APPLICABLE,
                reason_code="no_current_contamination",
                reason="No current-run contamination event exists.",
                run_id=context.run_id,
                current_stage=_text(context.workflow.get("current_stage")),
                reference_eligible=True,
                owner_revision=owner_revision,
            )
        reason_code = (
            "legacy_recovery_unbound"
            if integrity.get("status") == RUN_INTEGRITY_CONTAMINATED_REPAIRED
            else "contamination_event_missing"
        )
        return _invalid(
            context,
            reason_code,
            "Non-clean run_integrity has no current-run contamination event.",
        )

    if context.artifact_registry is None:
        return _invalid(
            context,
            "artifact_registry_missing_for_recovery",
            "artifact_registry.json is required for contaminated recovery.",
        )

    latest_contamination = contaminations[-1]
    contamination_event_id = _text(latest_contamination.get("event_id"))
    contamination_index = context.event_records.index(latest_contamination)
    active_repair = context.workflow.get("active_repair")
    if isinstance(active_repair, Mapping):
        active_error = _active_repair_binding_error(
            active_repair=active_repair,
            event_records=context.event_records,
            run_id=context.run_id,
            contamination_event_id=contamination_event_id,
        )
        if active_error:
            return _invalid(context, "active_repair_binding_invalid", active_error)
        return _state(
            status=RECOVERY_IN_PROGRESS,
            reason_code="active_repair_bound",
            reason="A current-run repair transaction is active.",
            run_id=context.run_id,
            contamination_event_id=contamination_event_id,
            repair_start_transaction_id=_text(active_repair.get("repair_start_transaction_id")),
            owner_stage=_text(active_repair.get("repair_owner")),
            artifact_id=_active_repair_artifact_id(active_repair),
            rerun_start_stage=_text(active_repair.get("must_rerun_from")),
            current_stage=_text(context.workflow.get("current_stage")),
            recommended_recovery_action=ACTION_COMPLETE_ACTIVE_REPAIR,
            owner_revision=owner_revision,
        )

    recovery_events = [
        event
        for event in context.event_records[contamination_index + 1 :]
        if event.get("event_type") in {"repair_completed", "repair_stage_superseded"}
        and _text(event.get("run_id")) == context.run_id
    ]
    if not recovery_events:
        finalized = context.workflow.get("current_stage") is None
        return _state(
            status=RECOVERY_AWAITING,
            reason_code=(
                "finalized_run_contaminated_new_run_required"
                if finalized
                else "contamination_unrecovered"
            ),
            reason=(
                "A finalized run was contaminated; start a new run."
                if finalized
                else "Current-run contamination has no bound recovery transaction."
            ),
            run_id=context.run_id,
            contamination_event_id=contamination_event_id,
            current_stage=_text(context.workflow.get("current_stage")),
            recommended_recovery_action=(
                ACTION_START_NEW_RUN if finalized else ACTION_REQUEST_DECISION
            ),
            owner_revision=owner_revision,
        )

    for event in recovery_events:
        binding_error = _recovery_event_binding_error(
            event,
            contamination_event_id=contamination_event_id,
            stage_ids=context.stage_ids,
        )
        if binding_error:
            return _invalid(context, "recovery_event_binding_invalid", binding_error)

    recovery_event = recovery_events[-1]
    metadata = _metadata(recovery_event)
    pointer_error = _repair_pointer_error(
        context.workflow.get("last_repair_transaction"),
        event=recovery_event,
        run_id=context.run_id,
    )
    if pointer_error:
        return _invalid(context, "repair_pointer_invalid", pointer_error)

    rerun_start_stage = _text(metadata.get("rerun_start_stage"))
    current_stage = _text(context.workflow.get("current_stage"))
    if current_stage:
        if current_stage not in context.stage_ids:
            return _invalid(context, "current_stage_invalid", "Current stage is not canonical.")
        if context.stage_ids.index(current_stage) < context.stage_ids.index(rerun_start_stage):
            return _invalid(
                context,
                "current_stage_precedes_recovery_rerun",
                "Current stage precedes the bound recovery rerun start stage.",
            )
        if current_stage != "finalize":
            return _bound_recovery_state(
                context=context,
                event=recovery_event,
                contamination_event_id=contamination_event_id,
                status=RECOVERY_RERUN_PENDING,
                reason_code="downstream_rerun_required",
                reason="Downstream stages must rerun from the recorded stage.",
                action=ACTION_RERUN_FROM_STAGE,
            )

    report_state = _finalize_report_state(
        report=context.finalize_report,
        run_id=context.run_id,
        contamination_event_id=contamination_event_id,
        recovery_transaction_id=_text(metadata.get("transaction_id")),
        rerun_start_stage=rerun_start_stage,
    )
    if current_stage == "finalize":
        if report_state[0] != "current_pass":
            return _bound_recovery_state(
                context=context,
                event=recovery_event,
                contamination_event_id=contamination_event_id,
                status=RECOVERY_FINALIZE_RENDER_REQUIRED,
                reason_code=report_state[1],
                reason=report_state[2],
                action=ACTION_RUN_FINALIZE,
            )
        return _bound_recovery_state(
            context=context,
            event=recovery_event,
            contamination_event_id=contamination_event_id,
            status=RECOVERY_FINALIZE_COMPLETION_PENDING,
            reason_code="finalize_completion_required",
            reason="Current recovery-bound finalize output must pass gate and finalize-complete.",
            action=ACTION_RUN_FINALIZE_COMPLETE,
            render_transaction_id=_text((context.finalize_report or {}).get("finalize_transaction_id")),
        )

    if current_stage:
        return _invalid(context, "terminal_stage_invalid", "Unexpected terminal recovery stage.")
    if report_state[0] != "current_pass":
        return _invalid(context, "terminal_finalize_report_invalid", report_state[2])
    completion = context.workflow.get("last_completion_transaction")
    completion_error = _finalize_completion_binding_error(
        completion,
        event_records=context.event_records,
        run_id=context.run_id,
        contamination_event_id=contamination_event_id,
        recovery_transaction_id=_text(metadata.get("transaction_id")),
        render_transaction_id=_text((context.finalize_report or {}).get("finalize_transaction_id")),
    )
    if completion_error:
        return _invalid(context, "finalize_completion_binding_invalid", completion_error)
    return _bound_recovery_state(
        context=context,
        event=recovery_event,
        contamination_event_id=contamination_event_id,
        status=RECOVERY_COMPLETED_NON_REFERENCE,
        reason_code="recovery_completed_non_reference",
        reason="Recovery reached a bound terminal finalize without restoring reference eligibility.",
        action=ACTION_INSPECT_DELIVERY,
        render_transaction_id=_text((context.finalize_report or {}).get("finalize_transaction_id")),
        finalize_completion_transaction_id=_text(completion.get("transaction_id")) if isinstance(completion, Mapping) else "",
    )


def recovery_stale_artifact_baselines(state: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Return current event-derived stale baselines from a recovery projection."""

    owner_revision = state.get("owner_revision")
    values = (
        owner_revision.get("stale_artifact_baselines")
        if isinstance(owner_revision, Mapping)
        else state.get("stale_artifact_baselines")
    )
    if not isinstance(values, Mapping):
        return {}
    return {
        str(artifact_id): dict(record)
        for artifact_id, record in values.items()
        if isinstance(record, Mapping)
    }


def finalize_recovery_binding(state: Mapping[str, Any]) -> dict[str, Any]:
    """Return lineage for a finalize render in the current recovery cycle."""

    if state.get("status") != RECOVERY_FINALIZE_RENDER_REQUIRED:
        return {}
    return {
        "status": "bound_non_reference_recovery",
        "run_id": _text(state.get("run_id")),
        "contamination_event_id": _text(state.get("contamination_event_id")),
        "recovery_transaction_id": _text(state.get("recovery_transaction_id")),
        "rerun_start_stage": _text(state.get("rerun_start_stage")),
        "reference_eligible": False,
    }


def _load_recovery_context(
    *,
    workspace: Path,
    repo_workdir: str | Path | None,
) -> RecoveryContext:
    paths = runtime_state_paths(workspace)
    manifest = load_control_object(
        paths["runtime_manifest"], expected_schema=RUNTIME_MANIFEST_SCHEMA
    )
    workflow = load_control_object(
        paths["workflow_state"], expected_schema=WORKFLOW_STATE_SCHEMA
    )
    registry = load_control_object(
        paths["artifact_registry"],
        expected_schema=ARTIFACT_REGISTRY_SCHEMA,
        required=False,
    )
    report = load_control_object(
        paths["runtime_manifest"].parent / "finalize_report.json",
        required=False,
    )
    event_records = read_event_log_records_strict(paths["event_log"])
    repo = resolve_repo_workdir(repo_workdir, workspace=workspace)
    stages = load_stage_specs(repo)
    run_id = _text((manifest or {}).get("run_id"))
    if not run_id:
        raise RuntimeStateError("runtime_manifest.json run_id is required.")
    return RecoveryContext(
        run_id=run_id,
        workflow=workflow or {},
        event_records=event_records,
        stage_ids=_stage_ids(stages),
        artifact_registry=registry,
        finalize_report=report,
    )


def _event_identity_error(records: Sequence[Mapping[str, Any]]) -> str:
    seen: set[str] = set()
    for event in records:
        event_id = _text(event.get("event_id"))
        if not event_id:
            return "event_log contains an event without event_id."
        if event_id in seen:
            return f"event_log contains duplicate event_id: {event_id}."
        seen.add(event_id)
    return ""


def _active_repair_binding_error(
    *,
    active_repair: Mapping[str, Any],
    event_records: Sequence[Mapping[str, Any]],
    run_id: str,
    contamination_event_id: str,
) -> str:
    required = {
        "run_id": run_id,
        "contamination_event_id": contamination_event_id,
    }
    for key, expected in required.items():
        if _text(active_repair.get(key)) != expected:
            return f"active_repair.{key} is not bound to the current recovery cycle."
    start_transaction_id = _text(active_repair.get("repair_start_transaction_id"))
    started_event_id = _text(active_repair.get("repair_started_event_id"))
    if not start_transaction_id or not started_event_id:
        return "active_repair start transaction/event identity is required."
    event = next((item for item in event_records if _text(item.get("event_id")) == started_event_id), None)
    if event is None or event.get("event_type") != "repair_started" or _text(event.get("run_id")) != run_id:
        return "active_repair repair_started event is missing or invalid."
    metadata = _metadata(event)
    if _text(metadata.get("transaction_id")) != start_transaction_id:
        return "active_repair transaction does not match repair_started event."
    if _text(metadata.get("contamination_event_id")) != contamination_event_id:
        return "repair_started event is not bound to the current contamination event."
    if not _text(active_repair.get("repair_owner")):
        return "active_repair repair_owner is required."
    return ""


def _recovery_event_binding_error(
    event: Mapping[str, Any],
    *,
    contamination_event_id: str,
    stage_ids: Sequence[str],
) -> str:
    metadata = _metadata(event)
    if not _text(metadata.get("transaction_id")):
        return "Recovery event transaction_id is required."
    if _text(metadata.get("contamination_event_id")) != contamination_event_id:
        return "Recovery event is not bound to the latest contamination event."
    owner_stage = _text(metadata.get("owner_stage"))
    rerun_stage = _text(metadata.get("rerun_start_stage"))
    if owner_stage not in stage_ids:
        return "Recovery event owner_stage is not canonical."
    if rerun_stage not in stage_ids:
        return "Recovery event rerun_start_stage is not canonical."
    if stage_ids.index(rerun_stage) <= stage_ids.index(owner_stage):
        return "Recovery rerun_start_stage must follow owner_stage."
    baselines = metadata.get("stale_artifact_baselines")
    if not isinstance(baselines, Mapping):
        return "Recovery event stale_artifact_baselines must be an object."
    return ""


def _repair_pointer_error(
    pointer: Any,
    *,
    event: Mapping[str, Any],
    run_id: str,
) -> str:
    if not isinstance(pointer, Mapping):
        return "workflow.last_repair_transaction is required."
    metadata = _metadata(event)
    expected = {
        "transaction_id": metadata.get("transaction_id"),
        "run_id": run_id,
        "contamination_event_id": metadata.get("contamination_event_id"),
        "owner_stage": metadata.get("owner_stage"),
        "artifact_id": metadata.get("artifact_id"),
        "rerun_start_stage": metadata.get("rerun_start_stage"),
    }
    for key, value in expected.items():
        if _text(pointer.get(key)) != _text(value):
            return f"workflow.last_repair_transaction.{key} does not match recovery event."
    return ""


def _finalize_report_state(
    *,
    report: Mapping[str, Any] | None,
    run_id: str,
    contamination_event_id: str,
    recovery_transaction_id: str,
    rerun_start_stage: str,
) -> tuple[str, str, str]:
    if report is None:
        return "missing", "finalize_report_missing", "A recovery-bound finalize report is required."
    binding = report.get("recovery_binding")
    expected = {
        "status": "bound_non_reference_recovery",
        "run_id": run_id,
        "contamination_event_id": contamination_event_id,
        "recovery_transaction_id": recovery_transaction_id,
        "rerun_start_stage": rerun_start_stage,
    }
    if not isinstance(binding, Mapping) or any(
        _text(binding.get(key)) != value for key, value in expected.items()
    ) or binding.get("reference_eligible") is not False:
        return "stale", "finalize_report_recovery_unbound", "Finalize report is not bound to the current recovery."
    reader_clean = report.get("reader_clean")
    if (
        report.get("status") != "pass"
        or not isinstance(reader_clean, Mapping)
        or reader_clean.get("status") != "pass"
        or report.get("delivery_promotion") != "promoted"
    ):
        return "failed", "finalize_report_failed", "Current recovery-bound finalize report did not pass."
    if not _text(report.get("finalize_transaction_id")):
        return "failed", "render_transaction_missing", "Finalize report transaction ID is required."
    return "current_pass", "finalize_report_current", "Current recovery-bound finalize report passed."


def _finalize_completion_binding_error(
    pointer: Any,
    *,
    event_records: Sequence[Mapping[str, Any]],
    run_id: str,
    contamination_event_id: str,
    recovery_transaction_id: str,
    render_transaction_id: str,
) -> str:
    if not isinstance(pointer, Mapping):
        return "workflow.last_completion_transaction is required."
    completion_id = _text(pointer.get("transaction_id"))
    expected = {
        "run_id": run_id,
        "stage_id": "finalize",
        "decision": "finalize",
        "render_transaction_id": render_transaction_id,
        "recovery_transaction_id": recovery_transaction_id,
        "contamination_event_id": contamination_event_id,
    }
    if not completion_id:
        return "Finalize completion transaction ID is required."
    for key, value in expected.items():
        if _text(pointer.get(key)) != value:
            return f"workflow.last_completion_transaction.{key} is not bound."
    event = next(
        (
            item
            for item in event_records
            if item.get("event_type") == "decision_recorded"
            and _text(item.get("run_id")) == run_id
            and _text(_metadata(item).get("transaction_id")) == completion_id
        ),
        None,
    )
    if event is None or _text(event.get("stage_id")) != "finalize" or _text(event.get("decision")) != "finalize":
        return "Bound finalize completion event is missing."
    metadata = _metadata(event)
    for key in ("render_transaction_id", "recovery_transaction_id", "contamination_event_id"):
        if _text(metadata.get(key)) != expected[key]:
            return f"Finalize completion event {key} is not bound."
    return ""


def _bound_recovery_state(
    *,
    context: RecoveryContext,
    event: Mapping[str, Any],
    contamination_event_id: str,
    status: str,
    reason_code: str,
    reason: str,
    action: str,
    render_transaction_id: str = "",
    finalize_completion_transaction_id: str = "",
) -> dict[str, Any]:
    metadata = _metadata(event)
    return _state(
        status=status,
        reason_code=reason_code,
        reason=reason,
        run_id=context.run_id,
        contamination_event_id=contamination_event_id,
        recovery_transaction_id=_text(metadata.get("transaction_id")),
        recovery_event_type=_text(event.get("event_type")),
        repair_start_transaction_id=_text(metadata.get("repair_start_transaction_id")),
        owner_stage=_text(metadata.get("owner_stage")),
        artifact_id=_text(metadata.get("artifact_id")),
        rerun_start_stage=_text(metadata.get("rerun_start_stage")),
        current_stage=_text(context.workflow.get("current_stage")),
        render_transaction_id=render_transaction_id,
        finalize_completion_transaction_id=finalize_completion_transaction_id,
        recommended_recovery_action=action,
        stale_artifact_baselines=metadata.get("stale_artifact_baselines"),
        owner_revision=_owner_revision_projection(event),
    )


def _state(
    *,
    status: str,
    reason_code: str,
    reason: str,
    run_id: str = "",
    contamination_event_id: str = "",
    recovery_transaction_id: str = "",
    recovery_event_type: str = "",
    repair_start_transaction_id: str = "",
    owner_stage: str = "",
    artifact_id: str = "",
    rerun_start_stage: str = "",
    current_stage: str = "",
    render_transaction_id: str = "",
    finalize_completion_transaction_id: str = "",
    recommended_recovery_action: str = ACTION_INSPECT_INVALID,
    stale_artifact_baselines: Any = None,
    reference_eligible: bool = False,
    details: Mapping[str, Any] | None = None,
    owner_revision: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    blocks = {
        RECOVERY_NOT_APPLICABLE: (False, False, False),
        RECOVERY_AWAITING: (True, True, True),
        RECOVERY_IN_PROGRESS: (True, True, True),
        RECOVERY_RERUN_PENDING: (True, True, True),
        RECOVERY_FINALIZE_RENDER_REQUIRED: (False, True, True),
        RECOVERY_FINALIZE_COMPLETION_PENDING: (True, False, True),
        RECOVERY_COMPLETED_NON_REFERENCE: (True, True, False),
        RECOVERY_INVALID: (True, True, True),
    }[status]
    return {
        "schema_version": RECOVERY_STATE_SCHEMA,
        "runtime_effect": "read_only_recovery_projection",
        "status": status,
        "reason_code": reason_code,
        "reason": reason,
        "run_id": run_id,
        "contamination_event_id": contamination_event_id,
        "recovery_transaction_id": recovery_transaction_id,
        "recovery_event_type": recovery_event_type,
        "repair_start_transaction_id": repair_start_transaction_id,
        "owner_stage": owner_stage,
        "artifact_id": artifact_id,
        "rerun_start_stage": rerun_start_stage,
        "current_stage": current_stage,
        "render_transaction_id": render_transaction_id,
        "finalize_completion_transaction_id": finalize_completion_transaction_id,
        "recovery_blocks_finalize": blocks[0],
        "recovery_blocks_finalize_complete": blocks[1],
        "recovery_blocks_delivery": blocks[2],
        "recommended_recovery_action": recommended_recovery_action,
        "reference_eligible": reference_eligible,
        "stale_artifact_baselines": (
            dict(stale_artifact_baselines)
            if isinstance(stale_artifact_baselines, Mapping)
            else {}
        ),
        "owner_revision": dict(owner_revision or _empty_owner_revision()),
        "details": dict(details or {}),
    }


def _invalid(context: RecoveryContext, reason_code: str, reason: str) -> dict[str, Any]:
    return _state(
        status=RECOVERY_INVALID,
        reason_code=reason_code,
        reason=reason,
        run_id=context.run_id,
        current_stage=_text(context.workflow.get("current_stage")),
    )


def _metadata(event: Mapping[str, Any]) -> Mapping[str, Any]:
    value = event.get("metadata")
    return value if isinstance(value, Mapping) else {}


def _latest_owner_revision(
    events: Sequence[Mapping[str, Any]],
    *,
    stage_ids: Sequence[str],
) -> tuple[dict[str, Any], str]:
    revisions = [
        event
        for event in events
        if event.get("event_type") in {"repair_completed", "repair_stage_superseded"}
    ]
    if not revisions:
        return _empty_owner_revision(), ""
    event = revisions[-1]
    metadata = _metadata(event)
    transaction_id = _text(metadata.get("transaction_id"))
    owner_stage = _text(metadata.get("owner_stage"))
    rerun_stage = _text(metadata.get("rerun_start_stage"))
    baselines = metadata.get("stale_artifact_baselines")
    if not transaction_id:
        return _empty_owner_revision(), "Owner revision transaction_id is required."
    if owner_stage not in stage_ids:
        return _empty_owner_revision(), "Owner revision owner_stage is not canonical."
    if rerun_stage not in stage_ids or stage_ids.index(rerun_stage) <= stage_ids.index(owner_stage):
        return _empty_owner_revision(), "Owner revision rerun_start_stage is not canonical."
    if not isinstance(baselines, Mapping):
        return _empty_owner_revision(), "Owner revision stale_artifact_baselines must be an object."
    return _owner_revision_projection(event), ""


def _owner_revision_projection(event: Mapping[str, Any]) -> dict[str, Any]:
    metadata = _metadata(event)
    baselines = metadata.get("stale_artifact_baselines")
    return {
        "status": "present",
        "event_id": _text(event.get("event_id")),
        "event_type": _text(event.get("event_type")),
        "transaction_id": _text(metadata.get("transaction_id")),
        "owner_stage": _text(metadata.get("owner_stage")),
        "artifact_id": _text(metadata.get("artifact_id")),
        "rerun_start_stage": _text(metadata.get("rerun_start_stage")),
        "stale_artifact_baselines": dict(baselines) if isinstance(baselines, Mapping) else {},
    }


def _empty_owner_revision() -> dict[str, Any]:
    return {
        "status": "none",
        "event_id": "",
        "event_type": "",
        "transaction_id": "",
        "owner_stage": "",
        "artifact_id": "",
        "rerun_start_stage": "",
        "stale_artifact_baselines": {},
    }


def _active_repair_artifact_id(active_repair: Mapping[str, Any]) -> str:
    source = active_repair.get("source")
    return _text(source.get("artifact_id")) if isinstance(source, Mapping) else ""


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""
