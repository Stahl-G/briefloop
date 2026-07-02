"""Event-log helpers for Orchestrator runtime state."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from multi_agent_brief.orchestrator.run_integrity import (
    workflow_with_persistable_run_integrity as _workflow_with_persistable_run_integrity,
)
from multi_agent_brief.orchestrator.runtime_state._io import (
    _append_jsonl,
    _read_json_if_exists,
)
from multi_agent_brief.orchestrator.runtime_state.errors import (
    E_RUNTIME_STATE_NOT_INITIALIZED,
    E_TRANSACTION_INTEGRITY,
    RuntimeStateError,
)
from multi_agent_brief.orchestrator.runtime_state.identity import _validate_runtime_run_id, utc_now
from multi_agent_brief.orchestrator.runtime_state.manifest import RUNTIME_MANIFEST_SCHEMA
from multi_agent_brief.orchestrator.runtime_state.paths import (
    _require_workspace,
    _workspace_relative,
    runtime_state_paths,
)
from multi_agent_brief.orchestrator.runtime_state.workflow import WORKFLOW_STATE_SCHEMA


EVENT_LOG_SCHEMA = "multi-agent-brief-event-log/v1"

EVENT_TYPES = {
    "run_initialized",
    "handoff_written",
    "artifact_observed",
    "artifact_validated",
    "stage_status_changed",
    "stage_satisfied_by_topology",
    "decision_recorded",
    "feedback_issue_created",
    "feedback_issue_planned",
    "feedback_issue_resolved",
    "repair_plan_created",
    "repair_plan_completed",
    "repair_started",
    "repair_completed",
    "quality_gate_checked",
    "quality_gate_blocked",
    "quality_gate_passed",
    "provenance_graph_built",
    "provenance_graph_validated",
    "provenance_graph_invalid",
    "audience_profile_snapshot_created",
    "control_switchboard_built",
    "control_switchboard_warning",
    "control_selection_recorded",
    "control_selection_validated",
    "improvement_proposed",
    "improvement_approved",
    "improvement_rejected",
    "improvement_reverted",
    "improvement_memory_snapshot_created",
    "delivery_attempted",
    "delivery_succeeded",
    "delivery_failed",
    "human_approval_ledger_initialized",
    "human_approval_recorded",
    "release_readiness_checked",
    "fact_layer_imported",
    "claim_ledger_frozen",
    "claim_ledger_metadata_enriched",
    "trajectory_decision_narrowed",
    "run_archived",
    "run_blocked",
    "run_integrity_contaminated",
    "run_reset",
}

ACTORS = {"cli", "orchestrator", "runtime", "system"}


def _read_event_log_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise RuntimeStateError(
            f"Failed to read event log: {path}",
            details={"path": str(path), "reason": str(exc)},
        ) from exc
    if raw and not raw.endswith(b"\n"):
        raise RuntimeStateError(
            f"Event log is not newline-terminated: {path}",
            details={"path": str(path)},
            error_code=E_TRANSACTION_INTEGRITY,
        )

    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RuntimeStateError(
            f"Event log is not valid UTF-8: {path}",
            details={"path": str(path), "reason": str(exc)},
            error_code=E_TRANSACTION_INTEGRITY,
        ) from exc

    records: list[dict[str, Any]] = []
    for lineno, line in enumerate(decoded.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RuntimeStateError(
                f"Invalid JSON event log line {lineno}: {path}",
                details={"path": str(path), "line": lineno, "reason": str(exc)},
                error_code=E_TRANSACTION_INTEGRITY,
            ) from exc
        if not isinstance(payload, dict):
            raise RuntimeStateError(
                f"Event log line {lineno} must contain an object: {path}",
                details={"path": str(path), "line": lineno},
                error_code=E_TRANSACTION_INTEGRITY,
            )
        schema_version = payload.get("schema_version")
        if schema_version != EVENT_LOG_SCHEMA:
            raise RuntimeStateError(
                f"Unsupported event log schema on line {lineno}: {schema_version}",
                details={"path": str(path), "line": lineno, "schema_version": schema_version},
                error_code=E_TRANSACTION_INTEGRITY,
            )
        event_type = payload.get("event_type")
        if event_type not in EVENT_TYPES:
            raise RuntimeStateError(
                f"Unknown event type on event log line {lineno}: {event_type}",
                details={"path": str(path), "line": lineno, "event_type": event_type},
                error_code=E_TRANSACTION_INTEGRITY,
            )
        actor = payload.get("actor")
        if actor not in ACTORS:
            raise RuntimeStateError(
                f"Unknown event actor on event log line {lineno}: {actor}",
                details={"path": str(path), "line": lineno, "actor": actor},
                error_code=E_TRANSACTION_INTEGRITY,
            )
        records.append(payload)
    return records


def read_event_log_records_strict(path: str | Path) -> list[dict[str, Any]]:
    """Read event log records with the runtime transaction-integrity checks."""

    return _read_event_log_records(Path(path))


def append_event(
    *,
    workspace: str | Path,
    run_id: str,
    event_type: str,
    actor: str,
    stage_id: str | None = None,
    artifact_id: str | None = None,
    decision: str | None = None,
    reason: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if event_type not in EVENT_TYPES:
        raise RuntimeStateError(
            f"Unknown event type: {event_type}",
            details={"event_type": event_type},
        )
    if actor not in ACTORS:
        raise RuntimeStateError(
            f"Unknown event actor: {actor}",
            details={"actor": actor},
        )
    safe_run_id = _validate_runtime_run_id(run_id)
    ws = Path(workspace).expanduser().resolve()
    event = {
        "schema_version": EVENT_LOG_SCHEMA,
        "event_id": uuid.uuid4().hex,
        "run_id": safe_run_id,
        "created_at": utc_now(),
        "event_type": event_type,
        "actor": actor,
        "stage_id": stage_id,
        "artifact_id": artifact_id,
        "decision": decision,
        "reason": reason,
        "metadata": metadata or {},
    }
    _append_jsonl(runtime_state_paths(ws)["event_log"], event)
    return event


def _load_handoff_runtime_state(workspace: str | Path) -> tuple[Path, dict[str, Path], dict[str, Any]]:
    ws = _require_workspace(workspace)
    paths = runtime_state_paths(ws)
    manifest = _read_json_if_exists(paths["runtime_manifest"])
    workflow = _read_json_if_exists(paths["workflow_state"])
    if manifest is None or workflow is None:
        raise RuntimeStateError(
            "Runtime state is not initialized. Run `multi-agent-brief state init --workspace <workspace>` first.",
            details={"workspace": str(ws)},
            error_code=E_RUNTIME_STATE_NOT_INITIALIZED,
        )
    if manifest.get("schema_version") != RUNTIME_MANIFEST_SCHEMA:
        raise RuntimeStateError(
            "runtime_manifest.json has an unsupported schema.",
            details={"path": str(paths["runtime_manifest"]), "schema_version": manifest.get("schema_version")},
        )
    manifest["run_id"] = _validate_runtime_run_id(
        manifest.get("run_id"),
        path=paths["runtime_manifest"],
    )
    if workflow.get("schema_version") != WORKFLOW_STATE_SCHEMA:
        raise RuntimeStateError(
            "workflow_state.json has an unsupported schema.",
            details={"path": str(paths["workflow_state"]), "schema_version": workflow.get("schema_version")},
        )
    _workflow_with_persistable_run_integrity(workflow, path=paths["workflow_state"])
    if workflow.get("run_id") is not None:
        _validate_runtime_run_id(
            workflow.get("run_id"),
            path=paths["workflow_state"],
        )
    return ws, paths, manifest


def record_handoff_written(
    *,
    workspace: str | Path,
    handoff_markdown: str | Path,
    handoff_json: str | Path,
    actor: str = "cli",
) -> dict[str, Any]:
    ws, _paths, manifest = _load_handoff_runtime_state(workspace)
    run_id = str(manifest["run_id"])
    return append_event(
        workspace=ws,
        run_id=run_id,
        event_type="handoff_written",
        actor=actor,
        reason="Runtime handoff artifacts written.",
        metadata={
            "handoff_markdown": _workspace_relative(ws, Path(handoff_markdown)),
            "handoff_json": _workspace_relative(ws, Path(handoff_json)),
        },
    )
