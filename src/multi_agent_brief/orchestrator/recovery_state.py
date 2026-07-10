"""Canonical recovery-state evaluation for contaminated runs."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from multi_agent_brief.orchestrator.run_integrity import (
    RUN_INTEGRITY_CONTAMINATED,
    RUN_INTEGRITY_CONTAMINATED_REPAIRED,
)


RECOVERY_NONE = "none"
RECOVERY_AWAITING = "awaiting_human_or_supersede"
RECOVERY_RERUN_PENDING = "downstream_rerun_pending"
RECOVERY_READY_FOR_FINALIZE = "ready_for_finalize"
RECOVERY_COMPLETED_NON_REFERENCE = "completed_non_reference"
RECOVERY_INVALID = "invalid_recovery_state"

_RECOVERY_EVENT_DECISIONS = {
    "repair_stage_superseded": "supersede_stage",
    "repair_completed": "repair_complete",
}


def recovery_stage_order(stages: Sequence[Mapping[str, Any]]) -> list[str]:
    """Return canonical recovery order from stage-spec authority."""

    return [
        stage_id
        for stage in stages
        if (stage_id := _clean_text(stage.get("stage_id")))
    ]


def evaluate_recovery_truth(
    *,
    workflow: Mapping[str, Any] | None,
    workflow_status: str,
    event_records: Sequence[Mapping[str, Any]],
    run_integrity: Mapping[str, Any],
    run_id: str,
    current_stage: str,
    stage_order: Sequence[str],
) -> dict[str, Any]:
    """Evaluate recovery progress from bound current-run transaction records."""

    superseded_stages, stale_stages = _stage_metadata_diagnostics(workflow)
    base = {
        "status": RECOVERY_NONE,
        "rerun_start_stage": "",
        "last_recovery_event_type": "",
        "last_recovery_transaction_id": "",
        "superseded_stages": superseded_stages,
        "stale_stages": stale_stages,
        "diagnostics_only_stage_metadata": True,
        "finalize_allowed": False,
        "delivery_allowed": False,
    }
    integrity_status = _clean_text(run_integrity.get("status"))
    if workflow_status != "present" or not isinstance(workflow, Mapping) or not _clean_text(run_id):
        if integrity_status in {"", "clean", "pass", "ok"}:
            return base
        return {**base, "status": RECOVERY_INVALID, "reason_code": "recovery_control_context_invalid"}
    if integrity_status not in {
        RUN_INTEGRITY_CONTAMINATED,
        RUN_INTEGRITY_CONTAMINATED_REPAIRED,
    }:
        return base
    workflow_run_id = _clean_text(workflow.get("run_id"))
    if not workflow_run_id or workflow_run_id != _clean_text(run_id):
        return {
            **base,
            "status": RECOVERY_INVALID,
            "reason_code": "recovery_run_id_binding_invalid",
        }

    current_run_records = [
        record
        for record in event_records
        if isinstance(record, Mapping) and _clean_text(record.get("run_id")) == _clean_text(run_id)
    ]
    contamination_index = _latest_event_index(
        current_run_records,
        lambda record: _clean_text(record.get("event_type")) == "run_integrity_contaminated",
    )
    recovery_index = _latest_event_index(
        current_run_records,
        lambda record: _clean_text(record.get("event_type")) in _RECOVERY_EVENT_DECISIONS,
    )
    recovery_event = current_run_records[recovery_index] if recovery_index is not None else None
    recovery_metadata = _event_metadata(recovery_event)
    recovery_fields = {
        "rerun_start_stage": _clean_text(recovery_metadata.get("next_stage")),
        "last_recovery_event_type": (
            _clean_text(recovery_event.get("event_type"))
            if isinstance(recovery_event, Mapping)
            else ""
        ),
        "last_recovery_transaction_id": _clean_text(recovery_metadata.get("transaction_id")),
    }

    if contamination_index is None:
        if recovery_index is None and integrity_status == RUN_INTEGRITY_CONTAMINATED:
            return {**base, "status": RECOVERY_AWAITING}
        return {
            **base,
            **recovery_fields,
            "status": RECOVERY_INVALID,
            "reason_code": "recovery_without_contamination_event",
        }
    if recovery_index is None or recovery_index < contamination_index:
        status = (
            RECOVERY_AWAITING
            if integrity_status == RUN_INTEGRITY_CONTAMINATED
            else RECOVERY_INVALID
        )
        reason_code = (
            "recovery_required"
            if status == RECOVERY_AWAITING
            else "terminal_recovery_transaction_missing"
        )
        return {**base, "status": status, "reason_code": reason_code}
    if not _recovery_transaction_binds(
        event=recovery_event,
        metadata=recovery_metadata,
        last_repair_transaction=workflow.get("last_repair_transaction"),
    ):
        return {
            **base,
            **recovery_fields,
            "status": RECOVERY_INVALID,
            "reason_code": "recovery_transaction_binding_invalid",
        }

    rerun_start_stage = recovery_fields["rerun_start_stage"]
    if integrity_status == RUN_INTEGRITY_CONTAMINATED:
        if not _stage_at_or_after(
            current_stage=_clean_text(current_stage),
            rerun_start_stage=rerun_start_stage,
            stage_order=stage_order,
        ):
            return {
                **base,
                **recovery_fields,
                "status": RECOVERY_INVALID,
                "reason_code": "recovery_workflow_progress_invalid",
            }
        if _clean_text(current_stage) == "finalize":
            return {
                **base,
                **recovery_fields,
                "status": RECOVERY_READY_FOR_FINALIZE,
                "finalize_allowed": True,
            }
        return {**base, **recovery_fields, "status": RECOVERY_RERUN_PENDING}

    finalize_index = _latest_event_index(
        current_run_records,
        lambda record: (
            _clean_text(record.get("event_type")) == "decision_recorded"
            and _clean_text(record.get("stage_id")) == "finalize"
            and _clean_text(record.get("decision")) == "finalize"
        ),
    )
    finalize_event = current_run_records[finalize_index] if finalize_index is not None else None
    if (
        finalize_index is None
        or finalize_index < recovery_index
        or not _finalize_transaction_binds(
            event=finalize_event,
            last_completion_transaction=workflow.get("last_completion_transaction"),
        )
    ):
        return {
            **base,
            **recovery_fields,
            "status": RECOVERY_INVALID,
            "reason_code": "terminal_finalize_binding_invalid",
        }
    return {
        **base,
        **recovery_fields,
        "status": RECOVERY_COMPLETED_NON_REFERENCE,
        "delivery_allowed": True,
    }


def _stage_metadata_diagnostics(
    workflow: Mapping[str, Any] | None,
) -> tuple[list[str], list[str]]:
    superseded: list[str] = []
    stale: list[str] = []
    statuses = workflow.get("stage_statuses") if isinstance(workflow, Mapping) else None
    if not isinstance(statuses, Mapping):
        return superseded, stale
    for stage_id, value in statuses.items():
        if not isinstance(value, Mapping):
            continue
        metadata = value.get("metadata")
        if not isinstance(metadata, Mapping):
            continue
        if metadata.get("superseded") is True:
            superseded.append(str(stage_id))
        if metadata.get("stale_after_supersede") is True:
            stale.append(str(stage_id))
    return sorted(superseded), sorted(stale)


def _latest_event_index(
    records: Sequence[Mapping[str, Any]],
    predicate: Any,
) -> int | None:
    latest: int | None = None
    for index, record in enumerate(records):
        if predicate(record):
            latest = index
    return latest


def _event_metadata(event: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if not isinstance(event, Mapping):
        return {}
    metadata = event.get("metadata")
    return metadata if isinstance(metadata, Mapping) else {}


def _recovery_transaction_binds(
    *,
    event: Mapping[str, Any] | None,
    metadata: Mapping[str, Any],
    last_repair_transaction: Any,
) -> bool:
    if not isinstance(event, Mapping) or not isinstance(last_repair_transaction, Mapping):
        return False
    transaction_id = _clean_text(metadata.get("transaction_id"))
    if not transaction_id or transaction_id != _clean_text(last_repair_transaction.get("transaction_id")):
        return False
    event_type = _clean_text(event.get("event_type"))
    if _RECOVERY_EVENT_DECISIONS.get(event_type) != _clean_text(
        last_repair_transaction.get("decision")
    ):
        return False
    if _clean_text(event.get("stage_id")) != _clean_text(last_repair_transaction.get("stage_id")):
        return False
    return bool(_clean_text(metadata.get("next_stage")))


def _finalize_transaction_binds(
    *,
    event: Mapping[str, Any] | None,
    last_completion_transaction: Any,
) -> bool:
    if not isinstance(event, Mapping) or not isinstance(last_completion_transaction, Mapping):
        return False
    metadata = _event_metadata(event)
    transaction_id = _clean_text(last_completion_transaction.get("transaction_id"))
    if not transaction_id:
        return False
    return (
        _clean_text(event.get("stage_id")) == "finalize"
        and _clean_text(event.get("decision")) == "finalize"
        and _clean_text(metadata.get("transaction_id")) == transaction_id
        and _clean_text(last_completion_transaction.get("stage_id")) == "finalize"
        and _clean_text(last_completion_transaction.get("decision")) == "finalize"
    )


def _stage_at_or_after(
    *,
    current_stage: str,
    rerun_start_stage: str,
    stage_order: Sequence[str],
) -> bool:
    if not current_stage or not rerun_start_stage or current_stage == "unknown":
        return False
    if current_stage == rerun_start_stage:
        return True
    normalized_order = [_clean_text(stage) for stage in stage_order]
    try:
        return normalized_order.index(current_stage) >= normalized_order.index(rerun_start_stage)
    except ValueError:
        return False


def _clean_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""
