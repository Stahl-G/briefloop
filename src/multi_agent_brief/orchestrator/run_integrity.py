"""Shared run integrity helpers for runtime control surfaces."""

from __future__ import annotations

from typing import Any


RUN_INTEGRITY_CLEAN = "clean"
RUN_INTEGRITY_CONTAMINATED = "contaminated"
RUN_INTEGRITY_UNKNOWN = "unknown"
PERSISTED_RUN_INTEGRITY_STATUSES = {RUN_INTEGRITY_CLEAN, RUN_INTEGRITY_CONTAMINATED}


def clean_run_integrity() -> dict[str, Any]:
    """Return the canonical clean run-integrity payload."""

    return {
        "status": RUN_INTEGRITY_CLEAN,
        "reference_eligible": True,
        "clean_single_shot": True,
        "reasons": [],
    }


def normalize_run_integrity(value: Any, *, missing: bool = False) -> dict[str, Any]:
    """Normalize persisted run integrity without introducing derived statuses.

    v0.8.0 keeps persisted workflow state to ``clean`` or ``contaminated``.
    Derived states such as ``unknown`` and ``incomplete`` belong to status and
    timing projections, not workflow_state.json.
    """

    if missing:
        return clean_run_integrity()
    if not isinstance(value, dict):
        raise ValueError("workflow_state.run_integrity must be an object.")
    status = value.get("status")
    if status not in PERSISTED_RUN_INTEGRITY_STATUSES:
        raise ValueError("workflow_state.run_integrity.status is invalid.")
    reasons = value.get("reasons")
    if not isinstance(reasons, list):
        reasons = []
    normalized_reasons = [item for item in reasons if isinstance(item, dict)]
    return {
        "status": status,
        "reference_eligible": False if status == RUN_INTEGRITY_CONTAMINATED else bool(value.get("reference_eligible", True)),
        "clean_single_shot": False if status == RUN_INTEGRITY_CONTAMINATED else bool(value.get("clean_single_shot", True)),
        "reasons": normalized_reasons,
    }


def classify_run_integrity(value: Any, *, missing: bool = False) -> dict[str, Any]:
    """Return a read-side run-integrity projection.

    Missing legacy ``run_integrity`` fields can be treated as clean for
    backcompat. Malformed persisted values are not reference-clean evidence and
    are classified as derived ``unknown`` without writing that status back to
    workflow_state.json.
    """

    if missing:
        return clean_run_integrity()
    if not isinstance(value, dict):
        return unknown_run_integrity(
            reason_code="run_integrity_malformed",
            message="workflow_state.run_integrity is missing or not an object.",
        )
    status = value.get("status")
    if status not in PERSISTED_RUN_INTEGRITY_STATUSES:
        return unknown_run_integrity(
            reason_code="run_integrity_invalid_status",
            message="workflow_state.run_integrity.status is invalid.",
        )
    return normalize_run_integrity(value)


def workflow_with_run_integrity(workflow: dict[str, Any]) -> dict[str, Any]:
    """Return a shallow workflow copy with normalized run integrity."""

    updated = dict(workflow)
    updated["run_integrity"] = normalize_run_integrity(
        updated.get("run_integrity"),
        missing="run_integrity" not in updated,
    )
    return updated


def contaminate_run_integrity_with_event_flag(
    workflow: dict[str, Any],
    *,
    reason_code: str,
    message: str,
    created_at: str,
    event_type: str | None = None,
    stage_id: str | None = None,
    artifact_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], bool]:
    """Mark a workflow contaminated and report whether a new reason was added."""

    updated = workflow_with_run_integrity(workflow)
    integrity = normalize_run_integrity(updated.get("run_integrity"))
    reason: dict[str, Any] = {
        "reason_code": reason_code,
        "message": message,
        "created_at": created_at,
    }
    if event_type:
        reason["event_type"] = event_type
    if stage_id:
        reason["stage_id"] = stage_id
    if artifact_id:
        reason["artifact_id"] = artifact_id
    if metadata:
        reason["metadata"] = metadata
    existing = integrity.get("reasons") if isinstance(integrity.get("reasons"), list) else []
    already_present = any(
        isinstance(item, dict)
        and item.get("reason_code") == reason_code
        and item.get("message") == message
        and item.get("stage_id") == stage_id
        and item.get("artifact_id") == artifact_id
        for item in existing
    )
    if already_present:
        return workflow, False
    integrity.update({
        "status": RUN_INTEGRITY_CONTAMINATED,
        "reference_eligible": False,
        "clean_single_shot": False,
        "reasons": [*existing, reason],
    })
    updated["run_integrity"] = integrity
    updated["updated_at"] = created_at
    return updated, True


def contamination_event_metadata(reason: dict[str, Any]) -> dict[str, Any]:
    """Return event metadata for a run-integrity contamination reason."""

    return {
        "reason_code": reason.get("reason_code"),
        "message": reason.get("message"),
        "reference_eligible": False,
        "clean_single_shot": False,
        "stage_id": reason.get("stage_id"),
        "artifact_id": reason.get("artifact_id"),
        "details": reason.get("metadata") if isinstance(reason.get("metadata"), dict) else {},
    }


def is_reference_eligible(value: Any) -> bool:
    return bool(classify_run_integrity(value).get("reference_eligible"))


def is_clean_single_shot(value: Any) -> bool:
    return bool(classify_run_integrity(value).get("clean_single_shot"))


def unknown_run_integrity(*, reason_code: str, message: str) -> dict[str, Any]:
    """Return a derived unknown integrity payload for read-only surfaces."""

    return {
        "status": "unknown",
        "reference_eligible": False,
        "clean_single_shot": False,
        "reasons": [{"reason_code": reason_code, "message": message}],
    }
