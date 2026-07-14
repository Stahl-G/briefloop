"""Shared transaction machinery for runtime-state operations.

Manifest/workflow loading, transaction file preflight, snapshot/rollback,
atomic writes, and run-contamination persistence used by every runtime-state
transaction module.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from pathlib import Path
from typing import Any

from multi_agent_brief.orchestrator_contract import HISTORICAL_READ_ONLY_RUNTIMES
from multi_agent_brief.orchestrator_contract import RUNTIME_CLI_CHOICE_PLACEHOLDER
from multi_agent_brief.orchestrator_contract import VALID_RUNTIMES
from multi_agent_brief.orchestrator_contract import resolve_repo_workdir
from multi_agent_brief.orchestrator.run_archive import (
    RunArchiveError,
    archive_finalized_run,
)
from multi_agent_brief.orchestrator.run_integrity import (
    contamination_event_metadata as _run_integrity_contamination_event_metadata,
    contaminate_run_integrity_with_event_flag as _contaminate_run_integrity_with_event_flag,
    workflow_with_persistable_run_integrity as _workflow_with_persistable_run_integrity,
    workflow_with_sticky_contamination_events as _workflow_with_sticky_contamination_events,
)
from multi_agent_brief.orchestrator.runtime_state._io import (
    _read_json,
    _read_json_if_exists,
    _read_state_bytes,
    _restore_state_bytes,
    _write_json_atomic,
)
from multi_agent_brief.orchestrator.runtime_state.contracts_loader import load_stage_specs
from multi_agent_brief.orchestrator.runtime_state.errors import (
    E_RUNTIME_STATE_NOT_INITIALIZED,
    E_TRANSACTION_INTEGRITY,
    E_TRANSACTION_PARTIAL_WRITE,
    RuntimeStateError,
    _wrap_archive_error,
)
from multi_agent_brief.orchestrator.runtime_state.event_log import (
    _read_event_log_records,
    append_event,
)
from multi_agent_brief.orchestrator.runtime_state.identity import (
    _validate_runtime_run_id,
    utc_now,
)
from multi_agent_brief.orchestrator.runtime_state.manifest import RUNTIME_MANIFEST_SCHEMA
from multi_agent_brief.orchestrator.runtime_state.paths import (
    _require_workspace,
    runtime_state_paths,
)
from multi_agent_brief.orchestrator.runtime_state.workflow import (
    WORKFLOW_STATE_SCHEMA,
    workflow_with_persistable_stage_completions,
)


def _archive_finalized_state_if_needed(
    *,
    workspace: Path,
    manifest: dict[str, Any],
    workflow: dict[str, Any],
    artifact_registry: dict[str, Any],
    finalize_report: dict[str, Any],
    fast_rerun_freshness_at_finalize: dict[str, Any] | None = None,
) -> dict[str, Any]:
    run_id = _validate_runtime_run_id(manifest.get("run_id") or "")
    try:
        result = archive_finalized_run(
            workspace=workspace,
            run_id=run_id,
            manifest=manifest,
            workflow=workflow,
            artifact_registry=artifact_registry,
            finalize_report=finalize_report,
            fast_rerun_freshness_at_finalize=fast_rerun_freshness_at_finalize,
        )
    except RunArchiveError as exc:
        raise _wrap_archive_error(exc) from exc
    return result

def _snapshot_file_paths(paths: list[Path]) -> dict[Path, bytes | None]:
    return {path: _read_state_bytes(path) for path in paths}

def _restore_file_paths(
    snapshots: dict[Path, bytes | None],
    *,
    rollback_message: str = "Fact layer import rollback failed after partial write.",
) -> None:
    rollback_errors: list[dict[str, str]] = []
    for path, data in snapshots.items():
        try:
            _restore_state_bytes(path, data)
        except RuntimeStateError as exc:
            rollback_errors.append({"path": str(path), "reason": str(exc)})
    if rollback_errors:
        raise RuntimeStateError(
            rollback_message,
            details={"rollback_errors": rollback_errors},
            error_code=E_TRANSACTION_PARTIAL_WRITE,
        )


def _preflight_transaction_files(paths: dict[str, Path]) -> list[dict[str, Any]]:
    paths["runtime_manifest"].parent.mkdir(parents=True, exist_ok=True)
    for key in ("runtime_manifest", "workflow_state"):
        if not paths[key].exists():
            raise RuntimeStateError(
                "Runtime state is not initialized. Run `multi-agent-brief state init "
                "--workspace <workspace> "
                f"--runtime {RUNTIME_CLI_CHOICE_PLACEHOLDER}` first.",
                details={"missing": str(paths[key])},
                error_code=E_RUNTIME_STATE_NOT_INITIALIZED,
            )
    for key in ("runtime_manifest", "workflow_state", "artifact_registry"):
        path = paths[key]
        if path.exists():
            _read_json(path)
    return _read_event_log_records(paths["event_log"])


def _completion_transaction_event_exists(
    *,
    event_records: list[dict[str, Any]],
    transaction_id: str,
) -> bool:
    for event in event_records:
        metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
        if (
            event.get("event_type") == "decision_recorded"
            and metadata.get("transaction_id") == transaction_id
        ):
            return True
    return False


def _completion_transaction_integrity_reason(
    *,
    paths: dict[str, Path],
    workflow: dict[str, Any],
) -> str:
    transaction = workflow.get("last_completion_transaction")
    if not isinstance(transaction, dict):
        return ""
    transaction_id = str(transaction.get("transaction_id") or "")
    if not transaction_id:
        return ""
    records = _read_event_log_records(paths["event_log"])
    if _completion_transaction_event_exists(event_records=records, transaction_id=transaction_id):
        return ""
    return (
        "Last completion transaction is missing its decision_recorded event: "
        f"{transaction_id}."
    )


def _persist_run_contamination(
    *,
    workspace: Path,
    paths: dict[str, Path],
    run_id: str,
    workflow: dict[str, Any],
    reason_code: str,
    message: str,
    actor: str,
    event_type: str | None = None,
    stage_id: str | None = None,
    artifact_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    contaminated, reason_added = _contaminate_run_integrity_with_event_flag(
        workflow,
        reason_code=reason_code,
        message=message,
        created_at=utc_now(),
        event_type=event_type,
        stage_id=stage_id,
        artifact_id=artifact_id,
        metadata=metadata,
    )
    if not reason_added:
        return workflow
    old_workflow_bytes = _read_state_bytes(paths["workflow_state"])
    _write_json_atomic(paths["workflow_state"], contaminated)
    reasons = (contaminated.get("run_integrity") or {}).get("reasons")
    reason = reasons[-1] if isinstance(reasons, list) and reasons and isinstance(reasons[-1], dict) else {}
    try:
        append_event(
            workspace=workspace,
            run_id=run_id,
            event_type="run_integrity_contaminated",
            actor=actor,
            stage_id=stage_id,
            artifact_id=artifact_id,
            reason=message,
            metadata=_run_integrity_contamination_event_metadata(reason),
        )
    except RuntimeStateError as exc:
        try:
            _restore_state_bytes(paths["workflow_state"], old_workflow_bytes)
        except RuntimeStateError as rollback_exc:
            raise RuntimeStateError(
                "Run integrity contamination partially wrote workflow_state.json and failed rollback.",
                details={
                    "reason_code": reason_code,
                    "stage_id": stage_id,
                    "artifact_id": artifact_id,
                    "event_error": str(exc),
                    "rollback_error": str(rollback_exc),
                },
                error_code=E_TRANSACTION_PARTIAL_WRITE,
            ) from rollback_exc
        raise RuntimeStateError(
            "Run integrity contamination event append failed; workflow_state.json was restored.",
            details={
                "reason_code": reason_code,
                "stage_id": stage_id,
                "artifact_id": artifact_id,
                "event_error": str(exc),
                "event_details": exc.details,
            },
            error_code=E_TRANSACTION_PARTIAL_WRITE,
        ) from exc
    return contaminated


def _load_manifest_and_workflow(
    workspace: str | Path,
    *,
    allow_noncanonical_runtime: bool = False,
) -> tuple[Path, dict[str, Path], dict[str, Any], dict[str, Any]]:
    ws = _require_workspace(workspace)
    paths = runtime_state_paths(ws)
    manifest = _read_json_if_exists(paths["runtime_manifest"])
    workflow = _read_json_if_exists(paths["workflow_state"])
    if manifest is None or workflow is None:
        raise RuntimeStateError(
            "Runtime state is not initialized. Run `multi-agent-brief state init "
            "--workspace <workspace> "
            f"--runtime {RUNTIME_CLI_CHOICE_PLACEHOLDER}` first.",
            details={"workspace": str(ws)},
            error_code=E_RUNTIME_STATE_NOT_INITIALIZED,
        )
    if manifest.get("schema_version") != RUNTIME_MANIFEST_SCHEMA:
        raise RuntimeStateError(
            "runtime_manifest.json has an unsupported schema.",
            details={"path": str(paths["runtime_manifest"]), "schema_version": manifest.get("schema_version")},
        )
    runtime = manifest.get("runtime")
    if not allow_noncanonical_runtime and runtime not in VALID_RUNTIMES:
        historical = runtime in HISTORICAL_READ_ONLY_RUNTIMES
        raise RuntimeStateError(
            (
                "Runtime state uses a historical read-only runtime identity. "
                "Start a new canonical run with state init --reset-state --runtime <runtime>."
                if historical
                else "runtime_manifest.json has an invalid runtime identity."
            ),
            details={"runtime": runtime, "valid_runtimes": list(VALID_RUNTIMES)},
            error_code=E_TRANSACTION_INTEGRITY,
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
    workflow = _workflow_with_persistable_run_integrity(
        workflow,
        path=paths["workflow_state"],
    )
    repo = resolve_repo_workdir(None, workspace=ws)
    workflow = workflow_with_persistable_stage_completions(
        workflow,
        stages=load_stage_specs(repo),
        path=paths["workflow_state"],
    )
    workflow = _workflow_with_sticky_contamination_events(
        workflow,
        _read_event_log_records(paths["event_log"]),
    )
    if workflow.get("run_id") is not None:
        workflow["run_id"] = _validate_runtime_run_id(
            workflow.get("run_id"),
            path=paths["workflow_state"],
        )
    return ws, paths, manifest, workflow


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_bytes_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    except OSError as exc:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise RuntimeStateError(
            f"Failed to write state file: {path}",
            details={"path": str(path), "reason": str(exc)},
        ) from exc


def _current_run_start_event_exists(event_records: list[dict[str, Any]], run_id: str) -> bool:
    return any(
        event.get("run_id") == run_id and event.get("event_type") in {"run_initialized", "run_reset"}
        for event in event_records
    )
