"""Shared run integrity helpers for runtime control surfaces."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


RUN_INTEGRITY_CLEAN = "clean"
RUN_INTEGRITY_CONTAMINATED = "contaminated"
RUN_INTEGRITY_CONTAMINATED_REPAIRED = "contaminated_repaired"
RUN_INTEGRITY_UNKNOWN = "unknown"
PERSISTED_RUN_INTEGRITY_STATUSES = {
    RUN_INTEGRITY_CLEAN,
    RUN_INTEGRITY_CONTAMINATED,
    RUN_INTEGRITY_CONTAMINATED_REPAIRED,
}


@dataclass(frozen=True)
class RunIntegrityVerdict:
    """Single interpreter result for persisted and read-side run integrity."""

    kind: str
    value: dict[str, Any]
    reason_code: str | None = None
    message: str | None = None


def _clean_run_integrity() -> dict[str, Any]:
    """Return the canonical clean run-integrity payload."""

    return {
        "status": RUN_INTEGRITY_CLEAN,
        "reference_eligible": True,
        "clean_single_shot": True,
        "reasons": [],
    }


def _unknown_run_integrity(*, reason_code: str, message: str) -> dict[str, Any]:
    """Return a derived unknown integrity payload for read-only surfaces."""

    return {
        "status": RUN_INTEGRITY_UNKNOWN,
        "reference_eligible": False,
        "clean_single_shot": False,
        "reasons": [{"reason_code": reason_code, "message": message}],
    }


def _degraded(reason_code: str, message: str) -> RunIntegrityVerdict:
    return RunIntegrityVerdict(
        kind="degraded",
        value=_unknown_run_integrity(reason_code=reason_code, message=message),
        reason_code=reason_code,
        message=message,
    )


def interpret_run_integrity(
    value: Any,
    *,
    field_present: bool,
    unavailable_reason: dict[str, str] | None = None,
) -> RunIntegrityVerdict:
    """Interpret a run_integrity field once for read and write adapters.

    Missing legacy fields are backcompat-clean. Present malformed fields are
    degraded for read surfaces and rejected by ``require_persistable``.
    """

    if unavailable_reason:
        return _degraded(
            unavailable_reason.get("reason_code") or "run_integrity_unavailable",
            unavailable_reason.get("message") or "workflow_state.run_integrity is unavailable.",
        )
    if not field_present:
        return RunIntegrityVerdict(kind="canonical", value=_clean_run_integrity())
    if not isinstance(value, dict):
        return _degraded(
            "run_integrity_malformed",
            "workflow_state.run_integrity is missing or not an object.",
        )
    status = value.get("status")
    if status not in PERSISTED_RUN_INTEGRITY_STATUSES:
        return _degraded(
            "run_integrity_invalid_status",
            "workflow_state.run_integrity.status is invalid.",
        )
    reasons = value.get("reasons", [])
    if not isinstance(reasons, list) or any(not isinstance(item, dict) for item in reasons):
        return _degraded(
            "run_integrity_invalid_reasons",
            "workflow_state.run_integrity.reasons must be a list of objects.",
        )
    if status == RUN_INTEGRITY_CLEAN:
        if value.get("reference_eligible", True) is not True:
            return _degraded(
                "run_integrity_clean_not_reference_eligible",
                "workflow_state.run_integrity clean runs must be reference eligible.",
            )
        if value.get("clean_single_shot", True) is not True:
            return _degraded(
                "run_integrity_clean_not_single_shot",
                "workflow_state.run_integrity clean runs must be clean single-shot.",
            )
        return RunIntegrityVerdict(
            kind="canonical",
            value={
                "status": RUN_INTEGRITY_CLEAN,
                "reference_eligible": True,
                "clean_single_shot": True,
                "reasons": reasons,
            },
        )
    if value.get("reference_eligible", False) is not False:
        return _degraded(
            "run_integrity_contaminated_reference_eligible",
            "workflow_state.run_integrity non-clean runs must not be reference eligible.",
        )
    if value.get("clean_single_shot", False) is not False:
        return _degraded(
            "run_integrity_contaminated_single_shot",
            "workflow_state.run_integrity non-clean runs must not be clean single-shot.",
        )
    return RunIntegrityVerdict(
        kind="canonical",
        value={
            "status": status,
            "reference_eligible": False,
            "clean_single_shot": False,
            "reasons": reasons,
        },
    )


def project_for_read(verdict: RunIntegrityVerdict) -> dict[str, Any]:
    """Return the read-side projection for a run-integrity verdict."""

    return dict(verdict.value)


def require_persistable(
    verdict: RunIntegrityVerdict,
    *,
    path: str | Path = "workflow_state.json",
) -> dict[str, Any]:
    """Return canonical persisted payload or fail closed for malformed fields."""

    if verdict.kind != "canonical":
        from multi_agent_brief.orchestrator.runtime_state.errors import (
            E_TRANSACTION_INTEGRITY,
            RuntimeStateError,
        )

        raise RuntimeStateError(
            "workflow_state.run_integrity is malformed.",
            details={"path": str(path), "reason": verdict.message or verdict.reason_code or "invalid run_integrity"},
            error_code=E_TRANSACTION_INTEGRITY,
        )
    return dict(verdict.value)


def workflow_with_persistable_run_integrity(workflow: dict[str, Any], *, path: str | Path) -> dict[str, Any]:
    """Return a shallow workflow copy with write-safe run integrity."""

    updated = dict(workflow)
    updated["run_integrity"] = require_persistable(
        interpret_run_integrity(
            updated.get("run_integrity"),
            field_present="run_integrity" in updated,
        ),
        path=path,
    )
    return updated


def workflow_with_sticky_contamination_events(
    workflow: dict[str, Any],
    event_records: list[dict[str, Any]],
) -> dict[str, Any]:
    """Return workflow with run-integrity contamination replayed from events."""

    updated = workflow_with_persistable_run_integrity(
        workflow,
        path="workflow_state.run_integrity",
    )
    run_id = str(updated.get("run_id") or "").strip()
    contamination_events = [
        event
        for event in event_records
        if (
            isinstance(event, dict)
            and event.get("event_type") == "run_integrity_contaminated"
            and (not run_id or str(event.get("run_id") or "").strip() == run_id)
        )
    ]
    if not contamination_events:
        return updated

    integrity = require_persistable(
        interpret_run_integrity(updated.get("run_integrity"), field_present=True),
        path="workflow_state.run_integrity",
    )
    existing = integrity.get("reasons") if isinstance(integrity.get("reasons"), list) else []
    reasons = [item for item in existing if isinstance(item, dict)]
    seen = {
        (
            str(item.get("reason_code") or ""),
            str(item.get("message") or ""),
            str(item.get("stage_id") or ""),
            str(item.get("artifact_id") or ""),
        )
        for item in reasons
    }

    for event in contamination_events:
        metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
        reason = {
            "reason_code": str(metadata.get("reason_code") or "run_integrity_contaminated"),
            "message": str(
                metadata.get("message")
                or event.get("reason")
                or "Run integrity contamination event was recorded."
            ),
            "created_at": str(event.get("created_at") or ""),
        }
        stage_id = metadata.get("stage_id") or event.get("stage_id")
        artifact_id = metadata.get("artifact_id") or event.get("artifact_id")
        if stage_id:
            reason["stage_id"] = str(stage_id)
        if artifact_id:
            reason["artifact_id"] = str(artifact_id)
        details = metadata.get("details")
        if isinstance(details, dict) and details:
            reason["metadata"] = details
        marker = (
            reason["reason_code"],
            reason["message"],
            str(reason.get("stage_id") or ""),
            str(reason.get("artifact_id") or ""),
        )
        if marker in seen:
            continue
        seen.add(marker)
        reasons.append(reason)

    sticky_status = (
        RUN_INTEGRITY_CONTAMINATED
        if integrity.get("status") == RUN_INTEGRITY_CLEAN
        else str(integrity.get("status"))
    )
    integrity.update({
        "status": sticky_status,
        "reference_eligible": False,
        "clean_single_shot": False,
        "reasons": reasons,
    })
    updated["run_integrity"] = integrity
    return updated


def finalize_run_integrity(workflow: dict[str, Any]) -> dict[str, Any]:
    """Mark a completed contaminated run as repaired but not reference-eligible."""

    updated = workflow_with_persistable_run_integrity(
        workflow,
        path="workflow_state.run_integrity",
    )
    integrity = require_persistable(
        interpret_run_integrity(updated.get("run_integrity"), field_present=True),
        path="workflow_state.run_integrity",
    )
    if integrity.get("status") == RUN_INTEGRITY_CLEAN:
        return updated
    integrity.update({
        "status": RUN_INTEGRITY_CONTAMINATED_REPAIRED,
        "reference_eligible": False,
        "clean_single_shot": False,
    })
    updated["run_integrity"] = integrity
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

    updated = workflow_with_persistable_run_integrity(workflow, path="workflow_state.run_integrity")
    integrity = require_persistable(
        interpret_run_integrity(updated.get("run_integrity"), field_present=True),
        path="workflow_state.run_integrity",
    )
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
