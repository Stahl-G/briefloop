"""Runtime-state lifecycle: initialize, show, and check.

Owns run initialization (including run-scoped control-artifact reset),
read-only state projection, and state consistency checking.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any

from multi_agent_brief.contracts.schemas.claim_draft import ClaimDraftContract, claim_draft_diagnostics  # noqa: F401
from multi_agent_brief.feedback.feedback_contract import current_stage_feedback_blocking_reasons
from multi_agent_brief.orchestrator.fact_layer_import import summarize_fact_layer_import
from multi_agent_brief.orchestrator.runtime_state._io import (
    _read_json,
    _read_json_if_exists,
    _read_state_bytes,
    _restore_state_bytes,
    _restore_state_files,
    _snapshot_state_files,
    _write_json_atomic,
)
from multi_agent_brief.orchestrator.runtime_state._transactions import (
    _archive_finalized_state_if_needed,
    _completion_transaction_integrity_reason,
    _load_manifest_and_workflow,
    _persist_run_contamination,
)
from multi_agent_brief.orchestrator.runtime_state.artifact_registry import (
    ARTIFACT_EXPECTED,
    ARTIFACT_INVALID,
    ARTIFACT_VALID,
    _build_artifact_registry,
    _changed_artifact_events,
    interpret_frozen_artifact_integrity,
    require_frozen_artifact_integrity_pass,
)
from multi_agent_brief.orchestrator.runtime_state.completion_gates import _raise_completion_reasons
from multi_agent_brief.orchestrator.runtime_state.contracts_loader import (
    _artifact_map,
    load_artifact_contracts,
    load_stage_specs,
)
from multi_agent_brief.orchestrator.runtime_state.errors import (
    E_TRANSACTION_INTEGRITY,
    E_TRANSACTION_PARTIAL_WRITE,
    RuntimeStateError,
)
from multi_agent_brief.orchestrator.runtime_state.event_log import (
    _read_event_log_records,
    append_event,
)
from multi_agent_brief.orchestrator.runtime_state.identity import (
    _safe_previous_run_id,
    _validate_runtime_run_id,
    new_run_id,
    utc_now,
)
from multi_agent_brief.orchestrator.runtime_state.manifest import (
    PRESERVED_RUNTIME_MANIFEST_EXTENSION_KEYS,
    RUNTIME_MANIFEST_SCHEMA,
    _runtime_manifest,
)
from multi_agent_brief.orchestrator.runtime_state.paths import (
    RUNTIME_STATE_FILES,
    _require_workspace,
    _workspace_relative,
    runtime_state_paths,
)
from multi_agent_brief.orchestrator.runtime_state.trajectory import (
    TRAJECTORY_DECISION_NARROWING_STATUS,
    _trajectory_narrowing_changed,
    _workflow_with_trajectory_decision_narrowing,
)
from multi_agent_brief.orchestrator.runtime_state.workflow import (
    STAGE_BLOCKED,
    STAGE_COMPLETE,
    STAGE_PENDING,
    STAGE_READY,
    STAGE_SKIPPED,
    WORKFLOW_STATE_SCHEMA,
    _allowed_decisions_for_stage,
    _changed_workflow_events,
    _initial_workflow_state,
    _required_consumed_artifacts,
    _status_entry,
    _workflow_is_finalized,
)
from multi_agent_brief.orchestrator.run_integrity import (
    contamination_event_metadata as _run_integrity_contamination_event_metadata,
    contaminate_run_integrity_with_event_flag as _contaminate_run_integrity_with_event_flag,
    workflow_with_persistable_run_integrity as _workflow_with_persistable_run_integrity,
)
from multi_agent_brief.orchestrator_contract import (
    HISTORICAL_READ_ONLY_RUNTIMES,
    VALID_RUNTIMES,
    require_canonical_runtime,
    resolve_repo_workdir,
)
from multi_agent_brief.quality_gates.contract import current_stage_quality_gate_blocking_reasons


def _remove_reset_archive_copy(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        raise RuntimeStateError(
            "Failed to remove reset event-log archive after partial write.",
            details={"path": str(path), "reason": str(exc)},
            error_code=E_TRANSACTION_PARTIAL_WRITE,
        ) from exc


def _reset_run_scoped_control_artifact_paths(workspace: Path) -> list[Path]:
    intermediate = workspace / "output" / "intermediate"
    return [
        intermediate / "human_approval_ledger.json",
        intermediate / "release_readiness_report.json",
        intermediate / "quality_panel.json",
        intermediate / "quality_summary.md",
        intermediate / "quality_panel.html",
    ]


def _archive_reset_run_scoped_control_artifact(path: Path, *, old_run_id: str) -> Path | None:
    if not path.exists():
        return None
    archive = path.with_name(f"{path.stem}.{old_run_id}{path.suffix}")
    if archive.exists():
        archive = path.with_name(f"{path.stem}.{old_run_id}.{uuid.uuid4().hex[:8]}{path.suffix}")
    try:
        os.replace(path, archive)
    except OSError as exc:
        raise RuntimeStateError(
            "Failed to archive run-scoped control artifact during runtime reset.",
            details={"path": str(path), "archive": str(archive), "reason": str(exc)},
            error_code=E_TRANSACTION_PARTIAL_WRITE,
        ) from exc
    return archive


def _restore_reset_control_artifacts(
    snapshots: dict[Path, bytes | None],
    archived_paths: list[Path],
) -> None:
    rollback_errors: list[str] = []
    for path, data in snapshots.items():
        try:
            _restore_state_bytes(path, data)
        except RuntimeStateError as exc:
            rollback_errors.append(str(exc))
    for archived_path in archived_paths:
        try:
            archived_path.unlink(missing_ok=True)
        except OSError as exc:
            rollback_errors.append(f"Failed to remove reset control artifact archive {archived_path}: {exc}")
    if rollback_errors:
        raise RuntimeStateError(
            "Runtime state reset failed to restore run-scoped control artifacts.",
            details={"rollback_errors": rollback_errors},
            error_code=E_TRANSACTION_PARTIAL_WRITE,
        )


def initialize_runtime_state(
    *,
    workspace: str | Path,
    runtime: str,
    repo_workdir: str | Path | None = None,
    reset_state: bool = False,
    actor: str = "cli",
    recipe: str | None = None,
) -> dict[str, Any]:
    """Initialize runtime control files for a workspace."""
    try:
        runtime = require_canonical_runtime(runtime)
    except ValueError as exc:
        raise RuntimeStateError(
            "Runtime state requires one explicit canonical runtime identity.",
            details={"runtime": runtime, "valid_runtimes": list(VALID_RUNTIMES)},
            error_code=E_TRANSACTION_INTEGRITY,
        ) from exc
    ws = _require_workspace(workspace)
    repo = resolve_repo_workdir(repo_workdir, workspace=ws)
    paths = runtime_state_paths(ws)

    stages = load_stage_specs(repo)
    artifacts = load_artifact_contracts(repo)

    if reset_state:
        try:
            old_manifest = _read_json_if_exists(paths["runtime_manifest"])
        except RuntimeStateError:
            old_manifest = None
        try:
            old_workflow = _read_json_if_exists(paths["workflow_state"])
        except RuntimeStateError:
            old_workflow = None
    else:
        old_manifest = _read_json_if_exists(paths["runtime_manifest"])
        old_workflow = _read_json_if_exists(paths["workflow_state"])

    if old_manifest and not reset_state:
        existing_runtime = old_manifest.get("runtime")
        if existing_runtime in HISTORICAL_READ_ONLY_RUNTIMES:
            raise RuntimeStateError(
                "Existing runtime state uses a historical read-only runtime identity. "
                "Start a new canonical run with state init --reset-state --runtime <runtime>.",
                details={
                    "runtime": existing_runtime,
                    "valid_runtimes": list(VALID_RUNTIMES),
                },
                error_code=E_TRANSACTION_INTEGRITY,
            )
        if existing_runtime not in VALID_RUNTIMES:
            raise RuntimeStateError(
                "Existing runtime_manifest.json has an invalid runtime identity.",
                details={
                    "runtime": existing_runtime,
                    "valid_runtimes": list(VALID_RUNTIMES),
                },
                error_code=E_TRANSACTION_INTEGRITY,
            )
        if existing_runtime != runtime:
            raise RuntimeStateError(
                "Runtime identity does not match the initialized run. "
                "Use --reset-state to start a new run with a different runtime.",
                details={
                    "existing_runtime": existing_runtime,
                    "requested_runtime": runtime,
                },
                error_code=E_TRANSACTION_INTEGRITY,
            )

    paths["runtime_manifest"].parent.mkdir(parents=True, exist_ok=True)
    now = utc_now()
    created = old_manifest is None or reset_state
    previous_run_id = _safe_previous_run_id((old_manifest or {}).get("run_id")) if reset_state else None
    archived_event_log: str | None = None
    reset_contamination_reason_added = False
    reset_touched_existing_state = bool(
        reset_state
        and (
            old_manifest is not None
            or old_workflow is not None
            or paths["event_log"].exists()
        )
    )
    reset_snapshots = (
        _snapshot_state_files(paths, ("runtime_manifest", "workflow_state", "event_log"))
        if reset_state
        else {}
    )
    reset_control_artifact_paths = _reset_run_scoped_control_artifact_paths(ws) if reset_state else []
    reset_control_artifact_snapshots = (
        {path: _read_state_bytes(path) for path in reset_control_artifact_paths}
        if reset_state
        else {}
    )
    reset_archived_event_log_path: Path | None = None
    reset_archived_control_artifact_paths: list[Path] = []

    if reset_state:
        if old_manifest and _workflow_is_finalized(old_workflow):
            old_registry = _read_json(paths["artifact_registry"])
            finalize_report = _read_json(paths["runtime_manifest"].parent / "finalize_report.json")
            archive_result = _archive_finalized_state_if_needed(
                workspace=ws,
                manifest=old_manifest,
                workflow=old_workflow or {},
                artifact_registry=old_registry,
                finalize_report=finalize_report,
            )
            append_event(
                workspace=ws,
                run_id=str(old_manifest["run_id"]),
                event_type="run_archived",
                actor=actor,
                stage_id="finalize",
                reason="Finalized run archived before runtime state reset.",
                metadata={
                    "archive_path": _workspace_relative(ws, Path(str(archive_result["archive_path"]))),
                    "archive_manifest": _workspace_relative(ws, Path(str(archive_result["archive_manifest"]))),
                    "archive_manifest_sha256": archive_result["archive_manifest_sha256"],
                    "file_count": archive_result["file_count"],
                    "event_log_includes_run_archived": False,
                },
            )
        old_run_id = previous_run_id or "unknown"
        for control_artifact_path in reset_control_artifact_paths:
            archived_control_artifact = _archive_reset_run_scoped_control_artifact(
                control_artifact_path,
                old_run_id=old_run_id,
            )
            if archived_control_artifact is not None:
                reset_archived_control_artifact_paths.append(archived_control_artifact)
        if paths["event_log"].exists():
            archive = paths["event_log"].with_name(f"event_log.{old_run_id}.jsonl")
            if archive.exists():
                archive = paths["event_log"].with_name(
                    f"event_log.{old_run_id}.{uuid.uuid4().hex[:8]}.jsonl"
                )
            os.replace(paths["event_log"], archive)
            reset_archived_event_log_path = archive
            archived_event_log = _workspace_relative(ws, archive)
        archived_manifest = _archive_reset_run_scoped_control_artifact(
            paths["runtime_manifest"],
            old_run_id=old_run_id,
        )
        if archived_manifest is not None:
            reset_archived_control_artifact_paths.append(archived_manifest)
    elif old_manifest and old_manifest.get("schema_version") != RUNTIME_MANIFEST_SCHEMA:
        raise RuntimeStateError(
            "Existing runtime_manifest.json has an unsupported schema. "
            "Use --reset-state to start a new runtime state.",
            details={
                "path": str(paths["runtime_manifest"]),
                "schema_version": old_manifest.get("schema_version"),
            },
        )

    if old_manifest and not reset_state:
        run_id = _validate_runtime_run_id(
            old_manifest.get("run_id") or new_run_id(),
            path=paths["runtime_manifest"],
        )
        created_at = str(old_manifest.get("created_at") or now)
    else:
        run_id = _validate_runtime_run_id(new_run_id())
        created_at = now

    manifest = _runtime_manifest(
        run_id=run_id,
        created_at=created_at,
        updated_at=now,
        runtime=runtime,
        stages=stages,
        artifacts=artifacts,
    )
    if old_manifest and not reset_state:
        for key in PRESERVED_RUNTIME_MANIFEST_EXTENSION_KEYS:
            if key in old_manifest:
                manifest[key] = old_manifest[key]
    if recipe is not None:
        manifest["recipe"] = str(recipe)

    if old_workflow and not reset_state:
        if old_workflow.get("schema_version") != WORKFLOW_STATE_SCHEMA:
            raise RuntimeStateError(
                "Existing workflow_state.json has an unsupported schema. "
                "Use --reset-state to start a new runtime state.",
                details={
                    "path": str(paths["workflow_state"]),
                    "schema_version": old_workflow.get("schema_version"),
                },
            )
        workflow = _workflow_with_persistable_run_integrity(
            old_workflow,
            path=paths["workflow_state"],
        )
        workflow["updated_at"] = now
        workflow["run_id"] = run_id
    else:
        workflow = _initial_workflow_state(
            run_id=run_id,
            stages=stages,
            created_at=created_at,
            updated_at=now,
        )
        if reset_touched_existing_state:
            workflow, reset_contamination_reason_added = _contaminate_run_integrity_with_event_flag(
                workflow,
                reason_code="run_reset",
                message="run_reset occurred; this run is not clean single-shot reference evidence.",
                created_at=now,
                event_type="run_reset",
                metadata={
                    "previous_run_id": previous_run_id,
                    "archived_event_log": archived_event_log,
                },
            )

    try:
        _write_json_atomic(paths["runtime_manifest"], manifest)
        _write_json_atomic(paths["workflow_state"], workflow)

        if created:
            append_event(
                workspace=ws,
                run_id=run_id,
                event_type="run_reset" if reset_state else "run_initialized",
                actor=actor,
                reason="Runtime state reset." if reset_state else "Runtime state initialized.",
                metadata={
                    "runtime": runtime,
                    "previous_run_id": previous_run_id,
                    "archived_event_log": archived_event_log,
                } if reset_state else {"runtime": runtime},
            )
            if reset_state and reset_contamination_reason_added:
                reasons = (workflow.get("run_integrity") or {}).get("reasons")
                reason = reasons[-1] if isinstance(reasons, list) and reasons and isinstance(reasons[-1], dict) else {}
                append_event(
                    workspace=ws,
                    run_id=run_id,
                    event_type="run_integrity_contaminated",
                    actor=actor,
                    reason=str(reason.get("message") or "Runtime state reset contaminated run integrity."),
                    metadata=_run_integrity_contamination_event_metadata(reason),
                )
    except RuntimeStateError as exc:
        if reset_state:
            try:
                _restore_state_files(paths, reset_snapshots)
                _restore_reset_control_artifacts(
                    reset_control_artifact_snapshots,
                    reset_archived_control_artifact_paths,
                )
                _remove_reset_archive_copy(reset_archived_event_log_path)
            except RuntimeStateError as rollback_exc:
                raise RuntimeStateError(
                    "Runtime state reset partially wrote control files and failed rollback.",
                    details={
                        "event_error": str(exc),
                        "rollback_error": str(rollback_exc),
                    },
                    error_code=E_TRANSACTION_PARTIAL_WRITE,
                ) from rollback_exc
            raise RuntimeStateError(
                "Runtime state reset event append failed; control files were restored.",
                details={"event_error": str(exc), "event_details": exc.details},
                error_code=E_TRANSACTION_PARTIAL_WRITE,
            ) from exc
        raise

    return show_runtime_state(workspace=ws)


def show_runtime_state(
    *,
    workspace: str | Path,
    allow_noncanonical_runtime: bool = True,
) -> dict[str, Any]:
    ws, paths, manifest, workflow = _load_manifest_and_workflow(
        workspace,
        allow_noncanonical_runtime=allow_noncanonical_runtime,
    )
    registry = _read_json_if_exists(paths["artifact_registry"])
    event_count = 0
    if paths["event_log"].exists():
        try:
            event_count = sum(1 for _ in paths["event_log"].open(encoding="utf-8"))
        except OSError:
            event_count = 0
    state = {
        "ok": True,
        "workspace": str(ws),
        "runtime_state_files": dict(RUNTIME_STATE_FILES),
        "manifest": manifest,
        "workflow_state": workflow,
        "artifact_registry": registry,
        "event_count": event_count,
    }
    state["fact_layer_import"] = summarize_fact_layer_import(manifest, workflow, workspace=ws)
    return state


def _recompute_stage_state(
    *,
    workspace: Path,
    stages: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
    registry: dict[str, Any],
    previous_workflow: dict[str, Any],
    updated_at: str,
) -> dict[str, Any]:
    previous_statuses = previous_workflow.get("stage_statuses") or {}
    artifact_records = registry.get("artifacts") or {}
    artifacts_by_id = _artifact_map(artifacts)
    new_statuses: dict[str, dict[str, Any]] = {}
    current_stage: str | None = None
    blocked = False
    blocking_reason = ""

    for stage in stages:
        stage_id = str(stage.get("stage_id") or "")
        if not stage_id:
            continue

        previous = previous_statuses.get(stage_id) or {}
        previous_status = str(previous.get("status") or STAGE_PENDING)
        if previous_status in {STAGE_COMPLETE, STAGE_SKIPPED}:
            metadata = previous.get("metadata") if isinstance(previous.get("metadata"), dict) else None
            new_statuses[stage_id] = _status_entry(
                previous_status,
                str(previous.get("reason") or ""),
                str(previous.get("updated_at") or updated_at),
                metadata=metadata,
            )
            continue

        if current_stage is not None:
            new_statuses[stage_id] = _status_entry(
                STAGE_PENDING,
                "",
                updated_at,
            )
            continue

        last_decision = previous_workflow.get("last_decision") or {}
        if (
            previous_status == STAGE_BLOCKED
            and last_decision.get("stage_id") == stage_id
            and last_decision.get("decision") in {"request_human_review", "block_run"}
        ):
            current_stage = stage_id
            blocked = True
            blocking_reason = str(previous.get("reason") or last_decision.get("reason") or "")
            new_statuses[stage_id] = _status_entry(
                STAGE_BLOCKED,
                blocking_reason,
                updated_at,
            )
            continue

        reasons: list[str] = []
        for artifact_id in _required_consumed_artifacts(stage=stage, artifacts_by_id=artifacts_by_id):
            record = artifact_records.get(artifact_id) or {}
            if record.get("status") != ARTIFACT_VALID:
                reasons.append(
                    f"Required artifact '{artifact_id}' is {record.get('status', ARTIFACT_EXPECTED)}."
                )

        for artifact_id in stage.get("expected_artifacts") or []:
            record = artifact_records.get(str(artifact_id)) or {}
            if record.get("status") == ARTIFACT_INVALID:
                reasons.append(
                    f"Expected output artifact '{artifact_id}' is invalid."
                )

        reasons.extend(
            current_stage_feedback_blocking_reasons(
                workspace=workspace,
                current_stage=stage_id,
                stages=stages,
                artifacts=artifacts,
            )
        )
        reasons.extend(
            current_stage_quality_gate_blocking_reasons(
                workspace=workspace,
                current_stage=stage_id,
                stages=stages,
                artifacts=artifacts,
            )
        )

        if reasons:
            current_stage = stage_id
            blocked = True
            blocking_reason = " ".join(reasons)
            new_statuses[stage_id] = _status_entry(
                STAGE_BLOCKED,
                blocking_reason,
                updated_at,
            )
        else:
            current_stage = stage_id
            new_statuses[stage_id] = _status_entry(
                STAGE_READY,
                "",
                updated_at,
            )

    workflow = dict(previous_workflow)
    workflow["updated_at"] = updated_at
    workflow["current_stage"] = current_stage
    workflow["blocked"] = blocked
    workflow["blocking_reason"] = blocking_reason
    workflow["stage_statuses"] = new_statuses
    workflow["next_allowed_decisions"] = _allowed_decisions_for_stage(stages, current_stage)
    return workflow


def check_runtime_state(
    *,
    workspace: str | Path,
    repo_workdir: str | Path | None = None,
    actor: str = "cli",
) -> dict[str, Any]:
    """Refresh artifact registry and stage readiness without running stages."""
    ws = _require_workspace(workspace)
    ws, paths, manifest, workflow = _load_manifest_and_workflow(ws)
    event_records = _read_event_log_records(paths["event_log"])
    old_registry = _read_json_if_exists(paths["artifact_registry"])
    repo = resolve_repo_workdir(repo_workdir, workspace=ws)
    stages = load_stage_specs(repo)
    artifacts = load_artifact_contracts(repo)
    now = utc_now()
    run_id = str(manifest["run_id"])

    registry = _build_artifact_registry(
        workspace=ws,
        run_id=run_id,
        artifacts=artifacts,
        workflow=workflow,
        updated_at=now,
    )
    frozen_verdict = interpret_frozen_artifact_integrity(
        old_registry=old_registry,
        registry=registry,
        workflow=workflow,
        artifacts=artifacts,
        stages=stages,
        mutating_stage=str(workflow.get("current_stage") or ""),
    )
    frozen_reasons = require_frozen_artifact_integrity_pass(frozen_verdict)
    if frozen_reasons:
        if frozen_verdict.contaminates_run:
            workflow = _persist_run_contamination(
                workspace=ws,
                paths=paths,
                run_id=run_id,
                workflow=workflow,
                reason_code="frozen_artifact_changed",
                message=" ".join(frozen_reasons),
                actor=actor,
                stage_id=str(workflow.get("current_stage") or ""),
                metadata={"blocking_reasons": frozen_reasons},
            )
        _raise_completion_reasons(
            message=(
                "Runtime state integrity check failed because a frozen artifact changed"
                if frozen_verdict.contaminates_run
                else "Runtime state integrity check failed because frozen artifact integrity could not be verified"
            ),
            reasons=frozen_reasons,
            error_code=E_TRANSACTION_INTEGRITY,
            details={"stage_id": workflow.get("current_stage")},
        )
    refreshed_workflow = _recompute_stage_state(
        workspace=ws,
        stages=stages,
        artifacts=artifacts,
        registry=registry,
        previous_workflow=workflow,
        updated_at=now,
    )
    refreshed_workflow = _workflow_with_trajectory_decision_narrowing(
        workspace=ws,
        workflow=refreshed_workflow,
        stages=stages,
        event_records=event_records,
        run_id=run_id,
    )
    transaction_integrity_warning = _completion_transaction_integrity_reason(
        paths=paths,
        workflow=refreshed_workflow,
    )
    if transaction_integrity_warning:
        refreshed_workflow["blocked"] = True
        refreshed_workflow["blocking_reason"] = transaction_integrity_warning
        current_stage = refreshed_workflow.get("current_stage")
        if current_stage:
            statuses = dict(refreshed_workflow.get("stage_statuses") or {})
            statuses[str(current_stage)] = _status_entry(
                STAGE_BLOCKED,
                transaction_integrity_warning,
                now,
            )
            refreshed_workflow["stage_statuses"] = statuses

    planned_events = [
        *_changed_artifact_events(old_registry=old_registry, registry=registry),
        *_changed_workflow_events(old_workflow=workflow, workflow=refreshed_workflow),
    ]
    if _trajectory_narrowing_changed(workflow, refreshed_workflow):
        narrowing = refreshed_workflow.get("trajectory_regulation")
        if isinstance(narrowing, dict) and narrowing.get("status") == TRAJECTORY_DECISION_NARROWING_STATUS:
            planned_events.append({
                "event_type": "trajectory_decision_narrowed",
                "stage_id": narrowing.get("stage_id"),
                "reason": ", ".join(str(item) for item in narrowing.get("reasons") or []),
                "metadata": {
                    "allowed_decisions": list(narrowing.get("allowed_decisions") or []),
                    "recommended_actions": list(narrowing.get("recommended_actions") or []),
                },
            })
    for event in planned_events:
        append_event(
            workspace=ws,
            run_id=run_id,
            event_type=str(event["event_type"]),
            actor=actor,
            stage_id=event.get("stage_id"),
            artifact_id=event.get("artifact_id"),
            reason=str(event.get("reason") or ""),
            metadata=event.get("metadata") or {},
        )

    _write_json_atomic(paths["artifact_registry"], registry)
    _write_json_atomic(paths["workflow_state"], refreshed_workflow)

    control_switchboard_warning: dict[str, Any] | None = None

    try:
        from multi_agent_brief.controls.contract import ControlSwitchboardError
        from multi_agent_brief.controls.switchboard import refresh_control_switchboard_if_stale

        try:
            refresh_control_switchboard_if_stale(
                workspace=ws,
                repo_workdir=repo,
                actor=actor,
            )
        except ControlSwitchboardError as exc:
            control_switchboard_warning = {
                "error": str(exc),
                "details": exc.details,
            }
            append_event(
                workspace=ws,
                run_id=run_id,
                event_type="control_switchboard_warning",
                actor=actor,
                reason=str(exc),
                metadata=exc.details,
            )
    except ImportError:
        pass

    state = show_runtime_state(workspace=ws)
    if control_switchboard_warning is not None:
        state["control_switchboard_warning"] = control_switchboard_warning
    if transaction_integrity_warning:
        state["transaction_integrity_warning"] = {
            "error_code": E_TRANSACTION_INTEGRITY,
            "message": transaction_integrity_warning,
        }
    return state
