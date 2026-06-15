"""Runtime state and artifact registry support for the Orchestrator."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import uuid
from pathlib import Path
from typing import Any

from multi_agent_brief.feedback.feedback_contract import (
    current_stage_feedback_blocking_reasons,
)
from multi_agent_brief.core.claim_ledger import ClaimLedger
from multi_agent_brief.quality_gates.contract import (
    current_stage_quality_gate_blocking_reasons,
)
from multi_agent_brief.orchestrator_contract import (
    DECISION_VOCABULARY,
    resolve_repo_workdir,
)
from multi_agent_brief.orchestrator.runtime_state._io import (
    _append_jsonl,
    _read_json,
    _read_json_if_exists,
    _read_state_bytes,
    _restore_state_bytes,
    _restore_state_files,
    _sha256_file,
    _snapshot_state_files,
    _write_json_atomic,
)
from multi_agent_brief.orchestrator.runtime_state.contracts_loader import (
    _artifact_map,
    _stage_ids,
    load_artifact_contracts,
    load_stage_specs,
)
from multi_agent_brief.orchestrator.runtime_state.completion_gates import (
    _completion_artifact_gate_reasons,
    _fast_rerun_finalize_freshness_snapshot,
    _fast_rerun_import_freshness_snapshot,
    _finalize_completion_reasons,
    _quality_gate_pass_reasons,
    _raise_completion_reasons,
    _source_discovery_evidence_reasons,
)
from multi_agent_brief.orchestrator.runtime_state.errors import (
    E_ARTIFACT_INVALID,
    E_COMPLETION_TRANSACTION_REQUIRED,
    E_FACT_LAYER_IMPORT_INVALID,
    E_ILLEGAL_TRANSITION,
    E_QUALITY_GATE_REQUIRED,
    E_READER_FINAL_GATE_FAILED,
    E_REQUIRED_ARTIFACT_MISSING,
    E_RUN_ARCHIVE_FAILED,
    E_RUNTIME_STATE_NOT_INITIALIZED,
    E_STAGE_ALREADY_COMPLETED,
    E_STAGE_MISMATCH,
    E_TRANSACTION_INTEGRITY,
    E_TRANSACTION_PARTIAL_WRITE,
    RuntimeStateError,
    _wrap_archive_error,
)
from multi_agent_brief.orchestrator.runtime_state.artifact_registry import (
    ARTIFACT_EXPECTED,
    ARTIFACT_INVALID,
    ARTIFACT_MISSING,
    ARTIFACT_REGISTRY_SCHEMA,
    ARTIFACT_VALID,
    _artifact_registry_path,
    _artifact_registry_sha,
    _build_artifact_registry,
    _changed_artifact_events,
    _frozen_artifact_integrity_reasons,
)
from multi_agent_brief.orchestrator.runtime_state.event_log import (
    ACTORS,
    EVENT_LOG_SCHEMA,
    EVENT_TYPES,
    _read_event_log_records,
    append_event,
    read_event_log_records_strict,
    record_handoff_written,
)
from multi_agent_brief.orchestrator.runtime_state.identity import (
    _safe_previous_run_id,
    _unsafe_runtime_run_id,
    _validate_runtime_run_id,
    new_run_id,
    utc_now,
)
from multi_agent_brief.orchestrator.runtime_state.manifest import (
    PRESERVED_RUNTIME_MANIFEST_EXTENSION_KEYS,
    RUNTIME_MANIFEST_SCHEMA,
    _assert_manifest_extensions_preserved,
    _preserved_manifest_extensions,
    _runtime_manifest,
)
from multi_agent_brief.orchestrator.runtime_state.paths import (
    RUNTIME_STATE_FILES,
    _require_workspace,
    _workspace_relative,
    runtime_state_paths,
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
    _current_stage_index,
    _initial_workflow_state,
    _next_stage_id,
    _required_consumed_artifacts,
    _stage_is_complete_or_skipped,
    _stage_status,
    _status_entry,
    _workflow_after_completion,
    _workflow_is_finalized,
)
from multi_agent_brief.orchestrator.run_archive import (
    E_RUN_ARCHIVE_CONFLICT,
    RUN_ARCHIVE_FACT_LAYER_SCHEMA,
    RUN_ARCHIVE_SCHEMA,
    RunArchiveError,
    archive_finalized_run,
    preflight_finalized_run_archive,
)
from multi_agent_brief.orchestrator.fact_layer_import import summarize_fact_layer_import
from multi_agent_brief.orchestrator.run_integrity import (
    RUN_INTEGRITY_CLEAN,
    RUN_INTEGRITY_CONTAMINATED,
    contamination_event_metadata as _run_integrity_contamination_event_metadata,
    contaminate_run_integrity_with_event_flag as _contaminate_run_integrity_with_event_flag,
    normalize_run_integrity as _normalize_run_integrity,
    workflow_with_run_integrity as _workflow_with_run_integrity,
)


__all__ = [
    "E_FACT_LAYER_IMPORT_INVALID",
    "E_RUNTIME_STATE_NOT_INITIALIZED",
    "E_STAGE_MISMATCH",
    "RUNTIME_MANIFEST_SCHEMA",
    "RUNTIME_STATE_FILES",
    "RuntimeStateError",
    "append_event",
    "check_runtime_state",
    "complete_finalize_transaction",
    "complete_stage_transaction",
    "import_fact_layer_transaction",
    "initialize_runtime_state",
    "load_artifact_contracts",
    "load_stage_specs",
    "new_run_id",
    "read_event_log_records_strict",
    "record_decision",
    "record_handoff_written",
    "runtime_state_paths",
    "show_runtime_state",
    "utc_now",
]


FACT_LAYER_IMPORT_SCHEMA = "mabw.fact_layer_import.v1"
FACT_LAYER_IMPORT_REQUIRED_ARTIFACT_IDS = (
    "durable_source_evidence_or_source_pack",
    "input_classification",
    "candidate_claims",
    "screened_candidates",
    "claim_ledger",
)
FACT_LAYER_IMPORT_FORBIDDEN_ARTIFACT_IDS = {"source_candidates", "source_plan"}
FACT_LAYER_IMPORT_SOURCE_PACK_ARTIFACT_ID = "durable_source_evidence_or_source_pack"
FACT_LAYER_IMPORT_SINGLETON_PATHS = {
    "input_classification": "output/input_classification.json",
    "candidate_claims": "output/intermediate/candidate_claims.json",
    "screened_candidates": "output/intermediate/screened_candidates.json",
    "claim_ledger": "output/intermediate/claim_ledger.json",
}

def _checked_workflow_with_run_integrity(workflow: dict[str, Any], *, path: Path) -> dict[str, Any]:
    try:
        return _workflow_with_run_integrity(workflow)
    except ValueError as exc:
        raise RuntimeStateError(
            "workflow_state.run_integrity is malformed.",
            details={"path": str(path), "reason": str(exc)},
            error_code=E_TRANSACTION_INTEGRITY,
        ) from exc

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


def _resolve_fact_layer_archive_manifest(path: str | Path) -> Path:
    raw = Path(path).expanduser()
    resolved = raw.resolve()
    if resolved.is_dir():
        resolved = resolved / "manifest.json"
    return resolved


def _path_text_is_unsafe(path_text: str) -> bool:
    return (
        not path_text
        or path_text.startswith("/")
        or bool(re.match(r"^[A-Za-z]:[\\/]", path_text))
        or Path(path_text).is_absolute()
        or any(part in {"", ".", ".."} for part in Path(path_text).parts)
    )


def _target_workspace_path(workspace: Path, rel_path: str) -> Path:
    if _path_text_is_unsafe(rel_path):
        raise RuntimeStateError(
            "Fact layer import path must be workspace-relative.",
            details={"path": rel_path},
            error_code=E_FACT_LAYER_IMPORT_INVALID,
        )
    target = (workspace / rel_path).resolve()
    try:
        target.relative_to(workspace)
    except ValueError as exc:
        raise RuntimeStateError(
            "Fact layer import target escapes the workspace.",
            details={"path": rel_path},
            error_code=E_FACT_LAYER_IMPORT_INVALID,
        ) from exc
    return target


def _source_archive_path(archive_root: Path, rel_path: str) -> Path:
    if _path_text_is_unsafe(rel_path):
        raise RuntimeStateError(
            "Fact layer archive path must be archive-relative.",
            details={"path": rel_path},
            error_code=E_FACT_LAYER_IMPORT_INVALID,
        )
    source = (archive_root / rel_path).resolve()
    try:
        source.relative_to(archive_root)
    except ValueError as exc:
        raise RuntimeStateError(
            "Fact layer archive path escapes the archive root.",
            details={"path": rel_path},
            error_code=E_FACT_LAYER_IMPORT_INVALID,
        ) from exc
    return source


def _reject_source_plan_fact_layer_record(*, artifact_id: str, archive_path: str, original_path: str) -> None:
    if artifact_id in FACT_LAYER_IMPORT_FORBIDDEN_ARTIFACT_IDS:
        raise RuntimeStateError(
            "source_candidates/source_plan artifacts cannot be imported as frozen fact layer evidence.",
            details={"artifact_id": artifact_id},
            error_code=E_FACT_LAYER_IMPORT_INVALID,
        )
    for label, path_text in (("archive_path", archive_path), ("original_path", original_path)):
        if Path(path_text).name == "source_candidates.yaml":
            raise RuntimeStateError(
                "source_candidates.yaml is a source plan and cannot be imported as fact layer evidence.",
                details={"artifact_id": artifact_id, label: path_text},
                error_code=E_FACT_LAYER_IMPORT_INVALID,
            )


def _archive_fact_layer_path_for(original_path: str) -> str:
    return f"fact_layer/{original_path}"


def _validate_fact_layer_import_record_scope(
    *,
    artifact_id: str,
    archive_path: str,
    original_path: str,
    nested_in_source_pack: bool,
) -> None:
    allowed_ids = {FACT_LAYER_IMPORT_SOURCE_PACK_ARTIFACT_ID, *FACT_LAYER_IMPORT_SINGLETON_PATHS}
    if artifact_id not in allowed_ids:
        raise RuntimeStateError(
            "Run archive fact_layer contains an unsupported artifact_id for import.",
            details={"artifact_id": artifact_id},
            error_code=E_FACT_LAYER_IMPORT_INVALID,
        )

    if artifact_id == FACT_LAYER_IMPORT_SOURCE_PACK_ARTIFACT_ID:
        if not nested_in_source_pack:
            raise RuntimeStateError(
                "Durable source evidence must be imported from the source pack file list.",
                details={"artifact_id": artifact_id},
                error_code=E_FACT_LAYER_IMPORT_INVALID,
            )
        if not original_path.startswith("input/sources/"):
            raise RuntimeStateError(
                "Durable source evidence imports must target input/sources/.",
                details={"artifact_id": artifact_id, "original_path": original_path},
                error_code=E_FACT_LAYER_IMPORT_INVALID,
            )
        if not archive_path.startswith("fact_layer/input/sources/"):
            raise RuntimeStateError(
                "Durable source evidence archive paths must stay under fact_layer/input/sources/.",
                details={"artifact_id": artifact_id, "archive_path": archive_path},
                error_code=E_FACT_LAYER_IMPORT_INVALID,
            )
        return

    expected_original_path = FACT_LAYER_IMPORT_SINGLETON_PATHS[artifact_id]
    if nested_in_source_pack:
        raise RuntimeStateError(
            "Singleton fact layer artifacts cannot be imported from a files list.",
            details={"artifact_id": artifact_id},
            error_code=E_FACT_LAYER_IMPORT_INVALID,
        )
    if original_path != expected_original_path:
        raise RuntimeStateError(
            "Singleton fact layer artifact targets do not match the import contract.",
            details={
                "artifact_id": artifact_id,
                "expected_original_path": expected_original_path,
                "actual_original_path": original_path,
            },
            error_code=E_FACT_LAYER_IMPORT_INVALID,
        )
    expected_archive_path = _archive_fact_layer_path_for(expected_original_path)
    if archive_path != expected_archive_path:
        raise RuntimeStateError(
            "Singleton fact layer archive paths do not match the import contract.",
            details={
                "artifact_id": artifact_id,
                "expected_archive_path": expected_archive_path,
                "actual_archive_path": archive_path,
            },
            error_code=E_FACT_LAYER_IMPORT_INVALID,
        )


def _require_fact_layer_file_record(
    *,
    workspace: Path,
    archive_root: Path,
    record: dict[str, Any],
    artifact_id: str,
    nested_in_source_pack: bool = False,
) -> dict[str, Any]:
    archive_path = str(record.get("archive_path") or "")
    original_path = str(record.get("original_path") or "")
    sha256 = str(record.get("sha256") or "")
    size_bytes = record.get("size_bytes")
    _reject_source_plan_fact_layer_record(
        artifact_id=artifact_id,
        archive_path=archive_path,
        original_path=original_path,
    )
    if not archive_path or not original_path or not sha256:
        raise RuntimeStateError(
            "Fact layer artifact record is missing path or hash fields.",
            details={"artifact_id": artifact_id, "record": record},
            error_code=E_FACT_LAYER_IMPORT_INVALID,
        )
    _validate_fact_layer_import_record_scope(
        artifact_id=artifact_id,
        archive_path=archive_path,
        original_path=original_path,
        nested_in_source_pack=nested_in_source_pack,
    )
    source = _source_archive_path(archive_root, archive_path)
    target = _target_workspace_path(workspace, original_path)
    if not source.exists() or not source.is_file():
        raise RuntimeStateError(
            "Fact layer archive file is missing.",
            details={"artifact_id": artifact_id, "archive_path": archive_path},
            error_code=E_FACT_LAYER_IMPORT_INVALID,
        )
    actual_sha = _sha256_file(source)
    if actual_sha != sha256:
        raise RuntimeStateError(
            "Fact layer archive file hash does not match manifest.",
            details={
                "artifact_id": artifact_id,
                "archive_path": archive_path,
                "expected_sha256": sha256,
                "actual_sha256": actual_sha,
            },
            error_code=E_FACT_LAYER_IMPORT_INVALID,
        )
    if isinstance(size_bytes, int) and source.stat().st_size != size_bytes:
        raise RuntimeStateError(
            "Fact layer archive file size does not match manifest.",
            details={
                "artifact_id": artifact_id,
                "archive_path": archive_path,
                "expected_size_bytes": size_bytes,
                "actual_size_bytes": source.stat().st_size,
            },
            error_code=E_FACT_LAYER_IMPORT_INVALID,
        )
    return {
        "artifact_id": artifact_id,
        "archive_path": archive_path,
        "workspace_path": original_path,
        "source_path": source,
        "target_path": target,
        "sha256": sha256,
        "size_bytes": source.stat().st_size,
    }


def _read_fact_layer_import_plan(
    *,
    workspace: Path,
    archive: str | Path,
) -> dict[str, Any]:
    manifest_path = _resolve_fact_layer_archive_manifest(archive)
    if not manifest_path.exists() or not manifest_path.is_file():
        raise RuntimeStateError(
            "Run archive manifest not found for fact layer import.",
            details={"archive": str(archive), "manifest_path": str(manifest_path)},
            error_code=E_FACT_LAYER_IMPORT_INVALID,
        )
    archive_root = manifest_path.parent
    try:
        archive_manifest = _read_json(manifest_path)
    except RuntimeStateError as exc:
        raise RuntimeStateError(
            "Run archive manifest is unreadable for fact layer import.",
            details={"manifest_path": str(manifest_path), "reason": str(exc)},
            error_code=E_FACT_LAYER_IMPORT_INVALID,
        ) from exc
    if archive_manifest.get("schema_version") != RUN_ARCHIVE_SCHEMA:
        raise RuntimeStateError(
            "Run archive manifest has unsupported schema.",
            details={
                "manifest_path": str(manifest_path),
                "schema_version": archive_manifest.get("schema_version"),
            },
            error_code=E_FACT_LAYER_IMPORT_INVALID,
        )
    integrity = archive_manifest.get("run_integrity") if isinstance(archive_manifest.get("run_integrity"), dict) else {}
    if (
        integrity.get("status") != RUN_INTEGRITY_CLEAN
        or integrity.get("reference_eligible") is not True
        or integrity.get("clean_single_shot") is not True
    ):
        raise RuntimeStateError(
            "Only clean reference-eligible run archives can be imported as a frozen fact layer.",
            details={"run_integrity": integrity},
            error_code=E_FACT_LAYER_IMPORT_INVALID,
        )
    fact_layer = archive_manifest.get("fact_layer") if isinstance(archive_manifest.get("fact_layer"), dict) else None
    if not fact_layer or fact_layer.get("schema_version") != RUN_ARCHIVE_FACT_LAYER_SCHEMA:
        raise RuntimeStateError(
            "Run archive manifest does not contain a supported fact_layer projection.",
            details={"manifest_path": str(manifest_path)},
            error_code=E_FACT_LAYER_IMPORT_INVALID,
        )
    if fact_layer.get("status") != "complete" or fact_layer.get("missing_artifact_ids"):
        raise RuntimeStateError(
            "Run archive fact_layer is incomplete and cannot be imported.",
            details={
                "status": fact_layer.get("status"),
                "missing_artifact_ids": fact_layer.get("missing_artifact_ids"),
            },
            error_code=E_FACT_LAYER_IMPORT_INVALID,
        )

    artifacts = fact_layer.get("artifacts")
    if not isinstance(artifacts, list):
        raise RuntimeStateError(
            "Run archive fact_layer artifacts must be a list.",
            details={"manifest_path": str(manifest_path)},
            error_code=E_FACT_LAYER_IMPORT_INVALID,
        )
    seen_ids: set[str] = set()
    import_files: list[dict[str, Any]] = []
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            raise RuntimeStateError(
                "Run archive fact_layer contains an invalid artifact record.",
                details={"artifact": artifact},
                error_code=E_FACT_LAYER_IMPORT_INVALID,
            )
        artifact_id = str(artifact.get("artifact_id") or "")
        if not artifact_id:
            raise RuntimeStateError(
                "Run archive fact_layer artifact is missing artifact_id.",
                details={"artifact": artifact},
                error_code=E_FACT_LAYER_IMPORT_INVALID,
            )
        _reject_source_plan_fact_layer_record(
            artifact_id=artifact_id,
            archive_path=str(artifact.get("archive_path") or ""),
            original_path=str(artifact.get("original_path") or ""),
        )
        if artifact_id in seen_ids and artifact_id != "durable_source_evidence_or_source_pack":
            raise RuntimeStateError(
                "Run archive fact_layer contains duplicate non-pack artifact records.",
                details={"artifact_id": artifact_id},
                error_code=E_FACT_LAYER_IMPORT_INVALID,
            )
        seen_ids.add(artifact_id)
        files = artifact.get("files")
        if isinstance(files, list):
            if artifact_id != FACT_LAYER_IMPORT_SOURCE_PACK_ARTIFACT_ID:
                raise RuntimeStateError(
                    "Only durable source evidence can be imported from a files list.",
                    details={"artifact_id": artifact_id},
                    error_code=E_FACT_LAYER_IMPORT_INVALID,
                )
            if not files:
                raise RuntimeStateError(
                    "Run archive fact_layer source pack is empty.",
                    details={"artifact_id": artifact_id},
                    error_code=E_FACT_LAYER_IMPORT_INVALID,
                )
            for file_record in files:
                if not isinstance(file_record, dict):
                    raise RuntimeStateError(
                        "Run archive fact_layer source pack contains an invalid file record.",
                        details={"artifact_id": artifact_id},
                        error_code=E_FACT_LAYER_IMPORT_INVALID,
                    )
                import_files.append(
                    _require_fact_layer_file_record(
                        workspace=workspace,
                        archive_root=archive_root,
                        record=file_record,
                        artifact_id=artifact_id,
                        nested_in_source_pack=True,
                    )
                )
        else:
            import_files.append(
                _require_fact_layer_file_record(
                    workspace=workspace,
                    archive_root=archive_root,
                    record=artifact,
                    artifact_id=artifact_id,
                    nested_in_source_pack=False,
                )
            )

    missing_required = sorted(set(FACT_LAYER_IMPORT_REQUIRED_ARTIFACT_IDS) - seen_ids)
    if missing_required:
        raise RuntimeStateError(
            "Run archive fact_layer is missing required artifact records.",
            details={"missing_artifact_ids": missing_required},
            error_code=E_FACT_LAYER_IMPORT_INVALID,
        )
    if not import_files:
        raise RuntimeStateError(
            "Run archive fact_layer has no importable files.",
            details={"manifest_path": str(manifest_path)},
            error_code=E_FACT_LAYER_IMPORT_INVALID,
        )
    _reject_duplicate_fact_layer_import_targets(import_files)

    return {
        "archive_manifest": archive_manifest,
        "archive_manifest_path": manifest_path,
        "archive_manifest_sha256": _sha256_file(manifest_path),
        "archive_root": archive_root,
        "fact_layer": fact_layer,
        "fact_layer_sha256": hashlib.sha256(
            json.dumps(fact_layer, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "import_files": import_files,
        "required_artifact_ids": list(FACT_LAYER_IMPORT_REQUIRED_ARTIFACT_IDS),
    }


def _snapshot_file_paths(paths: list[Path]) -> dict[Path, bytes | None]:
    return {path: _read_state_bytes(path) for path in paths}


def _restore_file_paths(snapshots: dict[Path, bytes | None]) -> None:
    rollback_errors: list[dict[str, str]] = []
    for path, data in snapshots.items():
        try:
            _restore_state_bytes(path, data)
        except RuntimeStateError as exc:
            rollback_errors.append({"path": str(path), "reason": str(exc)})
    if rollback_errors:
        raise RuntimeStateError(
            "Fact layer import rollback failed after partial write.",
            details={"rollback_errors": rollback_errors},
            error_code=E_TRANSACTION_PARTIAL_WRITE,
        )


def _copy_import_files(import_files: list[dict[str, Any]]) -> None:
    for record in import_files:
        source = record["source_path"]
        target = record["target_path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(source, target)
        except OSError as exc:
            raise RuntimeStateError(
                "Failed to copy fact layer archive file into workspace.",
                details={
                    "archive_path": record["archive_path"],
                    "workspace_path": record["workspace_path"],
                    "reason": str(exc),
                },
                error_code=E_TRANSACTION_PARTIAL_WRITE,
            ) from exc
        copied_sha = _sha256_file(target)
        if copied_sha != record["sha256"]:
            raise RuntimeStateError(
                "Imported fact layer file hash mismatch after copy.",
                details={
                    "workspace_path": record["workspace_path"],
                    "expected_sha256": record["sha256"],
                    "actual_sha256": copied_sha,
                },
                error_code=E_TRANSACTION_PARTIAL_WRITE,
            )


def _reject_existing_fact_layer_import_targets(import_files: list[dict[str, Any]]) -> None:
    existing = [
        {
            "workspace_path": record["workspace_path"],
            "sha256": _sha256_file(record["target_path"]) if record["target_path"].is_file() else "",
        }
        for record in import_files
        if record["target_path"].exists()
    ]
    if existing:
        raise RuntimeStateError(
            "Fact layer import target files already exist; use a fresh workspace so import cannot overwrite user files.",
            details={"existing_targets": existing},
            error_code=E_FACT_LAYER_IMPORT_INVALID,
        )


def _reject_existing_fact_layer_import_leftovers(workspace: Path, import_files: list[dict[str, Any]]) -> None:
    allowed_targets = {record["target_path"].resolve() for record in import_files}
    leftovers: list[str] = []

    for root in (workspace / "input" / "sources", workspace / "output"):
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            resolved = path.resolve()
            if resolved not in allowed_targets:
                leftovers.append(_workspace_relative(workspace, path))

    source_candidates = workspace / "source_candidates.yaml"
    if source_candidates.exists() and source_candidates.is_file():
        leftovers.append("source_candidates.yaml")

    if leftovers:
        raise RuntimeStateError(
            "Fact layer import requires a clean target workspace without existing source/output leftovers.",
            details={"existing_leftovers": leftovers},
            error_code=E_FACT_LAYER_IMPORT_INVALID,
        )


def _reject_duplicate_fact_layer_import_targets(import_files: list[dict[str, Any]]) -> None:
    seen_targets: dict[str, str] = {}
    duplicates: list[dict[str, str]] = []
    for record in import_files:
        workspace_path = str(record["workspace_path"])
        if workspace_path in seen_targets:
            duplicates.append({
                "workspace_path": workspace_path,
                "first_artifact_id": seen_targets[workspace_path],
                "duplicate_artifact_id": str(record["artifact_id"]),
            })
        else:
            seen_targets[workspace_path] = str(record["artifact_id"])
    if duplicates:
        raise RuntimeStateError(
            "Run archive fact_layer contains duplicate import targets.",
            details={"duplicate_targets": duplicates},
            error_code=E_FACT_LAYER_IMPORT_INVALID,
        )


def _imported_required_artifact_reasons(registry: dict[str, Any]) -> list[str]:
    records = registry.get("artifacts") if isinstance(registry.get("artifacts"), dict) else {}
    reasons: list[str] = []
    for artifact_id in FACT_LAYER_IMPORT_REQUIRED_ARTIFACT_IDS:
        if artifact_id == "durable_source_evidence_or_source_pack":
            continue
        record = records.get(artifact_id) if isinstance(records.get(artifact_id), dict) else {}
        status = str(record.get("status") or "")
        validation_result = str(record.get("validation_result") or "")
        if status != ARTIFACT_VALID:
            reasons.append(
                f"Imported required artifact '{artifact_id}' is {status or '<missing>'} ({validation_result or 'not_checked'})."
            )
    return reasons


def _manifest_with_fast_rerun_freshness_at_finalize(
    manifest: dict[str, Any],
    freshness_at_finalize: dict[str, Any] | None,
) -> dict[str, Any]:
    record = manifest.get("fact_layer_import") if isinstance(manifest.get("fact_layer_import"), dict) else None
    if not record or not freshness_at_finalize:
        return manifest
    next_manifest = dict(manifest)
    next_record = dict(record)
    next_record["freshness_at_finalize"] = freshness_at_finalize
    next_manifest["fact_layer_import"] = next_record
    return next_manifest


def import_fact_layer_transaction(
    *,
    workspace: str | Path,
    archive: str | Path,
    runtime: str = "hermes",
    repo_workdir: str | Path | None = None,
    actor: str = "cli",
) -> dict[str, Any]:
    """Import a complete archived frozen fact layer into a new runtime run."""
    ws = _require_workspace(workspace)
    paths = runtime_state_paths(ws)
    if any(paths[key].exists() for key in ("runtime_manifest", "workflow_state", "event_log", "artifact_registry")):
        raise RuntimeStateError(
            "Fact layer import requires a workspace without existing runtime state. Use a fresh workspace for fast-rerun import.",
            details={"workspace": str(ws)},
            error_code=E_FACT_LAYER_IMPORT_INVALID,
        )

    import_plan = _read_fact_layer_import_plan(workspace=ws, archive=archive)
    _reject_existing_fact_layer_import_leftovers(ws, import_plan["import_files"])
    _reject_existing_fact_layer_import_targets(import_plan["import_files"])
    state_snapshots = _snapshot_state_files(paths, ("runtime_manifest", "workflow_state", "artifact_registry", "event_log"))
    target_snapshots = _snapshot_file_paths([record["target_path"] for record in import_plan["import_files"]])
    try:
        initialize_runtime_state(
            workspace=ws,
            runtime=runtime,
            repo_workdir=repo_workdir,
            actor=actor,
            recipe="fast-rerun",
        )
        ws, paths, manifest, workflow = _load_manifest_and_workflow(ws)
        repo = resolve_repo_workdir(repo_workdir, workspace=ws)
        stages = load_stage_specs(repo)
        artifacts = load_artifact_contracts(repo)
        _copy_import_files(import_plan["import_files"])

        now = utc_now()
        run_id = str(manifest["run_id"])
        satisfied_stage_ids = [
            stage_id
            for stage_id in ("doctor", "source-discovery", "input-governance", "scout", "screener", "claim-ledger")
            if stage_id in _stage_ids(stages)
        ]
        imported_file_records = [
            {
                "artifact_id": record["artifact_id"],
                "archive_path": record["archive_path"],
                "workspace_path": record["workspace_path"],
                "sha256": record["sha256"],
                "size_bytes": record["size_bytes"],
            }
            for record in import_plan["import_files"]
        ]
        source_run_id = _validate_runtime_run_id(str(import_plan["archive_manifest"].get("run_id") or ""))
        logical_archive_manifest = f"output/runs/{source_run_id}/manifest.json"
        import_record = {
            "schema_version": FACT_LAYER_IMPORT_SCHEMA,
            "imported_at": now,
            "source_run_id": source_run_id,
            "source_archive_manifest": logical_archive_manifest,
            "source_archive_manifest_sha256": import_plan["archive_manifest_sha256"],
            "fact_layer_status": import_plan["fact_layer"].get("status"),
            "fact_layer_sha256": import_plan["fact_layer_sha256"],
            "satisfied_stage_ids": satisfied_stage_ids,
            "required_artifact_ids": import_plan["required_artifact_ids"],
            "imported_file_count": len(imported_file_records),
            "imported_files": imported_file_records,
            "freshness_at_import": _fast_rerun_import_freshness_snapshot(ws, checked_at=now),
            "timing_comparability": "downstream_only",
        }

        manifest = dict(manifest)
        manifest["updated_at"] = now
        manifest["recipe"] = "fast-rerun"
        manifest["fact_layer_import"] = import_record

        statuses = dict(workflow.get("stage_statuses") or {})
        for stage_id in satisfied_stage_ids:
            statuses[stage_id] = _status_entry(
                STAGE_COMPLETE,
                "Satisfied by frozen fact layer import.",
                now,
                metadata={
                    "satisfied_by_import": True,
                    "fact_layer_import_sha256": import_record["fact_layer_sha256"],
                    "source_run_id": import_record["source_run_id"],
                },
            )
        current_stage = "analyst" if "analyst" in _stage_ids(stages) else _next_stage_id(stages, satisfied_stage_ids[-1])
        workflow = dict(workflow)
        workflow["updated_at"] = now
        workflow["current_stage"] = current_stage
        workflow["blocked"] = False
        workflow["blocking_reason"] = ""
        workflow["stage_statuses"] = statuses
        workflow["last_decision"] = {
            "stage_id": "claim-ledger",
            "decision": "continue",
            "reason": "Frozen fact layer imported for fast-rerun.",
            "recorded_at": now,
        }
        workflow["next_allowed_decisions"] = _allowed_decisions_for_stage(stages, current_stage)

        registry = _build_artifact_registry(
            workspace=ws,
            run_id=run_id,
            artifacts=artifacts,
            workflow=workflow,
            updated_at=now,
        )
        imported_artifact_reasons = _imported_required_artifact_reasons(registry)
        if imported_artifact_reasons:
            _raise_completion_reasons(
                message="Imported fact layer files do not satisfy current artifact contracts",
                reasons=imported_artifact_reasons,
                error_code=E_FACT_LAYER_IMPORT_INVALID,
                details={"source_run_id": import_record["source_run_id"]},
            )

        _write_json_atomic(paths["runtime_manifest"], manifest)
        _write_json_atomic(paths["artifact_registry"], registry)
        _write_json_atomic(paths["workflow_state"], workflow)
        append_event(
            workspace=ws,
            run_id=run_id,
            event_type="fact_layer_imported",
            actor=actor,
            stage_id="claim-ledger",
            decision="continue",
            reason="Frozen fact layer imported for fast-rerun.",
            metadata={
                "source_run_id": import_record["source_run_id"],
                "source_archive_manifest": import_record["source_archive_manifest"],
                "fact_layer_sha256": import_record["fact_layer_sha256"],
                "imported_file_count": import_record["imported_file_count"],
                "satisfied_stage_ids": satisfied_stage_ids,
            },
        )
    except Exception as exc:
        try:
            _restore_file_paths(target_snapshots)
            _restore_state_files(paths, state_snapshots)
        except RuntimeStateError as rollback_exc:
            raise RuntimeStateError(
                "Fact layer import partially wrote files and failed rollback.",
                details={
                    "import_error": str(exc),
                    "rollback_error": str(rollback_exc),
                },
                error_code=E_TRANSACTION_PARTIAL_WRITE,
            ) from rollback_exc
        if isinstance(exc, RuntimeStateError):
            raise
        raise RuntimeStateError(
            "Fact layer import failed; workspace files were restored.",
            details={"reason": str(exc)},
            error_code=E_TRANSACTION_PARTIAL_WRITE,
        ) from exc

    state = show_runtime_state(workspace=ws)
    state["fact_layer_import"] = import_record
    return state


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


def _preflight_transaction_files(paths: dict[str, Path]) -> list[dict[str, Any]]:
    paths["runtime_manifest"].parent.mkdir(parents=True, exist_ok=True)
    for key in ("runtime_manifest", "workflow_state"):
        if not paths[key].exists():
            raise RuntimeStateError(
                "Runtime state is not initialized. Run `multi-agent-brief state init --workspace <workspace>` first.",
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


def _contaminate_run_integrity(
    workflow: dict[str, Any],
    *,
    reason_code: str,
    message: str,
    created_at: str,
    event_type: str | None = None,
    stage_id: str | None = None,
    artifact_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    contaminated, _reason_added = _contaminate_run_integrity_with_event_flag(
        workflow,
        reason_code=reason_code,
        message=message,
        created_at=created_at,
        event_type=event_type,
        stage_id=stage_id,
        artifact_id=artifact_id,
        metadata=metadata,
    )
    return contaminated


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


def _older_stage_replay_message(
    *,
    stage_id: str,
    current_stage: str | None,
    stages: list[dict[str, Any]],
    workflow: dict[str, Any],
) -> str:
    if current_stage is None or stage_id == current_stage:
        return ""
    stage_ids = _stage_ids(stages)
    if stage_id not in stage_ids or current_stage not in stage_ids:
        return ""
    if stage_ids.index(stage_id) >= stage_ids.index(current_stage):
        return ""
    statuses = workflow.get("stage_statuses") if isinstance(workflow.get("stage_statuses"), dict) else {}
    downstream_ids = stage_ids[stage_ids.index(stage_id) + 1:]
    downstream_touched = [
        item
        for item in downstream_ids
        if ((statuses.get(item) or {}).get("status") or "") in {STAGE_COMPLETE, STAGE_READY, STAGE_BLOCKED, STAGE_SKIPPED}
    ]
    if not downstream_touched:
        return ""
    return (
        f"Stage-complete was attempted for older stage '{stage_id}' after downstream "
        f"stage '{downstream_touched[0]}' already existed."
    )


def initialize_runtime_state(
    *,
    workspace: str | Path,
    runtime: str = "hermes",
    repo_workdir: str | Path | None = None,
    reset_state: bool = False,
    actor: str = "cli",
    recipe: str | None = None,
) -> dict[str, Any]:
    """Initialize runtime control files for a workspace."""
    ws = _require_workspace(workspace)
    repo = resolve_repo_workdir(repo_workdir, workspace=ws)
    paths = runtime_state_paths(ws)
    paths["runtime_manifest"].parent.mkdir(parents=True, exist_ok=True)

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
    reset_archived_event_log_path: Path | None = None

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
        if paths["event_log"].exists():
            archive = paths["event_log"].with_name(f"event_log.{old_run_id}.jsonl")
            if archive.exists():
                archive = paths["event_log"].with_name(
                    f"event_log.{old_run_id}.{uuid.uuid4().hex[:8]}.jsonl"
                )
            os.replace(paths["event_log"], archive)
            reset_archived_event_log_path = archive
            archived_event_log = _workspace_relative(ws, archive)
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
        workflow = _checked_workflow_with_run_integrity(
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


def _load_manifest_and_workflow(workspace: str | Path) -> tuple[Path, dict[str, Path], dict[str, Any], dict[str, Any]]:
    ws = _require_workspace(workspace)
    paths = runtime_state_paths(ws)
    manifest = _read_json_if_exists(paths["runtime_manifest"])
    workflow = _read_json_if_exists(paths["workflow_state"])
    if manifest is None or workflow is None:
        raise RuntimeStateError(
            "Runtime state is not initialized. Run `multi-agent-brief state init --workspace <workspace>` first.",
            details={"workspace": str(ws)},
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
    workflow = _checked_workflow_with_run_integrity(
        workflow,
        path=paths["workflow_state"],
    )
    if workflow.get("run_id") is not None:
        workflow["run_id"] = _validate_runtime_run_id(
            workflow.get("run_id"),
            path=paths["workflow_state"],
        )
    return ws, paths, manifest, workflow


def show_runtime_state(*, workspace: str | Path) -> dict[str, Any]:
    ws, paths, manifest, workflow = _load_manifest_and_workflow(workspace)
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
            new_statuses[stage_id] = _status_entry(STAGE_PENDING, "", updated_at)
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
            new_statuses[stage_id] = _status_entry(STAGE_BLOCKED, blocking_reason, updated_at)
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
            new_statuses[stage_id] = _status_entry(STAGE_BLOCKED, blocking_reason, updated_at)
        else:
            current_stage = stage_id
            new_statuses[stage_id] = _status_entry(STAGE_READY, "", updated_at)

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
    paths = runtime_state_paths(ws)
    if not paths["runtime_manifest"].exists() or not paths["workflow_state"].exists():
        initialize_runtime_state(workspace=ws, repo_workdir=repo_workdir, actor=actor)

    ws, paths, manifest, workflow = _load_manifest_and_workflow(ws)
    _read_event_log_records(paths["event_log"])
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
    frozen_reasons = _frozen_artifact_integrity_reasons(
        old_registry=old_registry,
        registry=registry,
        workflow=workflow,
        artifacts=artifacts,
        stages=stages,
        mutating_stage=str(workflow.get("current_stage") or ""),
    )
    if frozen_reasons:
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
            message="Runtime state integrity check failed because a frozen artifact changed",
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


def _validate_completion_target(
    *,
    stage_id: str,
    workflow: dict[str, Any],
    stage_by_id: dict[str, dict[str, Any]],
    finalize: bool,
) -> dict[str, Any]:
    if stage_id not in stage_by_id:
        raise RuntimeStateError(
            f"Unknown stage: {stage_id}",
            details={"stage_id": stage_id, "known_stages": list(stage_by_id)},
            error_code=E_ILLEGAL_TRANSITION,
        )
    current_stage = workflow.get("current_stage")
    if current_stage is None and _stage_status(workflow, stage_id) == STAGE_COMPLETE:
        raise RuntimeStateError(
            f"Stage '{stage_id}' is already complete.",
            details={"stage_id": stage_id},
            error_code=E_STAGE_ALREADY_COMPLETED,
        )
    if stage_id != current_stage:
        if _stage_status(workflow, stage_id) == STAGE_COMPLETE:
            raise RuntimeStateError(
                f"Stage '{stage_id}' is already complete.",
                details={"stage_id": stage_id, "current_stage": current_stage},
                error_code=E_STAGE_ALREADY_COMPLETED,
            )
        raise RuntimeStateError(
            f"Completion stage '{stage_id}' does not match current stage '{current_stage}'.",
            details={"stage_id": stage_id, "current_stage": current_stage},
            error_code=E_STAGE_MISMATCH,
        )
    if finalize and stage_id != "finalize":
        raise RuntimeStateError(
            "finalize-complete can only complete the finalize stage.",
            details={"stage_id": stage_id},
            error_code=E_ILLEGAL_TRANSITION,
        )
    if not finalize and stage_id == "finalize":
        raise RuntimeStateError(
            "stage-complete cannot complete the finalize stage; use finalize-complete.",
            details={"stage_id": stage_id},
            error_code=E_ILLEGAL_TRANSITION,
        )
    stage = stage_by_id[stage_id]
    decision = "finalize" if finalize else "continue"
    allowed = [str(item) for item in (stage.get("allowed_decisions") or [])]
    if decision not in allowed:
        raise RuntimeStateError(
            f"Decision '{decision}' is not allowed for stage '{stage_id}'.",
            details={"stage_id": stage_id, "decision": decision, "stage_allowed_decisions": allowed},
            error_code=E_ILLEGAL_TRANSITION,
        )
    return stage


def _auditor_completion_metadata(
    *,
    workspace: Path,
    registry: dict[str, Any],
) -> dict[str, Any]:
    ledger_sha = _artifact_registry_sha(registry, "claim_ledger")
    audited_brief_sha = _artifact_registry_sha(registry, "audited_brief")
    audit_sha = _artifact_registry_sha(registry, "audit_report")
    ledger_path = workspace / _artifact_registry_path(
        registry,
        "claim_ledger",
        "output/intermediate/claim_ledger.json",
    )
    audited_brief_path = workspace / _artifact_registry_path(
        registry,
        "audited_brief",
        "output/intermediate/audited_brief.md",
    )
    audit_path = workspace / _artifact_registry_path(
        registry,
        "audit_report",
        "output/intermediate/audit_report.json",
    )
    if _sha256_file(ledger_path) != ledger_sha:
        raise RuntimeStateError(
            "Claim Ledger changed before auditor completion could bind it.",
            details={
                "artifact_id": "claim_ledger",
                "path": str(ledger_path),
                "registry_sha256": ledger_sha,
            },
            error_code=E_TRANSACTION_INTEGRITY,
        )
    if _sha256_file(audited_brief_path) != audited_brief_sha:
        raise RuntimeStateError(
            "Audited brief changed before auditor completion could bind it.",
            details={
                "artifact_id": "audited_brief",
                "path": str(audited_brief_path),
                "registry_sha256": audited_brief_sha,
            },
            error_code=E_TRANSACTION_INTEGRITY,
        )
    if _sha256_file(audit_path) != audit_sha:
        raise RuntimeStateError(
            "Audit report changed before auditor completion could bind it.",
            details={
                "artifact_id": "audit_report",
                "path": str(audit_path),
                "registry_sha256": audit_sha,
            },
            error_code=E_TRANSACTION_INTEGRITY,
        )
    return {
        "upstream_artifact_sha256": {
            "claim_ledger": ledger_sha,
            "audited_brief": audited_brief_sha,
        },
        "produced_artifact_sha256": {
            "audit_report": audit_sha,
        },
    }


def _append_transaction_events(
    *,
    workspace: Path,
    run_id: str,
    actor: str,
    transaction_id: str,
    stage_id: str,
    decision: str,
    reason: str,
    next_stage: str | None,
    artifact_events: list[dict[str, Any]],
) -> None:
    try:
        for event in artifact_events:
            metadata = dict(event.get("metadata") or {})
            metadata["transaction_id"] = transaction_id
            append_event(
                workspace=workspace,
                run_id=run_id,
                event_type=str(event["event_type"]),
                actor=actor,
                stage_id=event.get("stage_id"),
                artifact_id=event.get("artifact_id"),
                reason=str(event.get("reason") or ""),
                metadata=metadata,
            )
        append_event(
            workspace=workspace,
            run_id=run_id,
            event_type="decision_recorded",
            actor=actor,
            stage_id=stage_id,
            decision=decision,
            reason=reason,
            metadata={"next_stage": next_stage, "transaction_id": transaction_id},
        )
    except RuntimeStateError as exc:
        raise RuntimeStateError(
            "Completion transaction partially wrote state but failed to append event.",
            details={
                "transaction_id": transaction_id,
                "stage_id": stage_id,
                "decision": decision,
                "event_error": str(exc),
                "event_details": exc.details,
            },
            error_code=E_TRANSACTION_PARTIAL_WRITE,
        ) from exc


def _complete_stage_transaction(
    *,
    workspace: str | Path,
    stage_id: str,
    reason: str,
    repo_workdir: str | Path | None = None,
    actor: str = "orchestrator",
    finalize: bool = False,
) -> dict[str, Any]:
    ws = _require_workspace(workspace)
    paths = runtime_state_paths(ws)
    _preflight_transaction_files(paths)
    ws, paths, manifest, workflow = _load_manifest_and_workflow(ws)
    repo = resolve_repo_workdir(repo_workdir, workspace=ws)
    stages = load_stage_specs(repo)
    artifacts = load_artifact_contracts(repo)
    stage_by_id = {str(stage.get("stage_id")): stage for stage in stages}
    replay_message = _older_stage_replay_message(
        stage_id=stage_id,
        current_stage=workflow.get("current_stage"),
        stages=stages,
        workflow=workflow,
    )
    if replay_message:
        workflow = _persist_run_contamination(
            workspace=ws,
            paths=paths,
            run_id=str(manifest["run_id"]),
            workflow=workflow,
            reason_code="older_stage_replay",
            message=replay_message,
            actor=actor,
            stage_id=stage_id,
        )
    stage = _validate_completion_target(
        stage_id=stage_id,
        workflow=workflow,
        stage_by_id=stage_by_id,
        finalize=finalize,
    )

    artifact_reasons = _completion_artifact_gate_reasons(
        workspace=ws,
        stage=stage,
        artifacts_by_id=_artifact_map(artifacts),
    )
    if stage_id == "source-discovery":
        artifact_reasons.extend(_source_discovery_evidence_reasons(ws))
    if artifact_reasons:
        code = E_REQUIRED_ARTIFACT_MISSING
        if any("invalid" in item.lower() for item in artifact_reasons):
            code = E_ARTIFACT_INVALID
        _raise_completion_reasons(
            message=f"Cannot complete stage '{stage_id}'",
            reasons=artifact_reasons,
            error_code=code,
            details={"stage_id": stage_id},
        )

    feedback_reasons = current_stage_feedback_blocking_reasons(
        workspace=ws,
        current_stage=stage_id,
        stages=stages,
        artifacts=artifacts,
    )
    if feedback_reasons:
        _raise_completion_reasons(
            message=f"Cannot complete stage '{stage_id}'",
            reasons=feedback_reasons,
            error_code=E_ILLEGAL_TRANSITION,
            details={"stage_id": stage_id},
        )

    quality_reasons = current_stage_quality_gate_blocking_reasons(
        workspace=ws,
        current_stage=stage_id,
        stages=stages,
        artifacts=artifacts,
    )
    if stage_id == "auditor":
        quality_reasons.extend(_quality_gate_pass_reasons(workspace=ws, stages=stages, artifacts=artifacts))
    if quality_reasons:
        _raise_completion_reasons(
            message=f"Cannot complete stage '{stage_id}'",
            reasons=quality_reasons,
            error_code=E_QUALITY_GATE_REQUIRED,
            details={"stage_id": stage_id},
        )

    fast_rerun_freshness_at_finalize: dict[str, Any] | None = None
    manifest_for_completion = manifest
    if finalize:
        fast_rerun_freshness_at_finalize = _fast_rerun_finalize_freshness_snapshot(
            ws,
            manifest,
            checked_at=utc_now(),
        )
        manifest_for_completion = _manifest_with_fast_rerun_freshness_at_finalize(
            manifest,
            fast_rerun_freshness_at_finalize,
        )
        finalize_reasons = _finalize_completion_reasons(
            ws,
            stages=stages,
            artifacts=artifacts,
            runtime_manifest=manifest_for_completion,
            fast_rerun_freshness_at_finalize=fast_rerun_freshness_at_finalize,
        )
        if finalize_reasons:
            _raise_completion_reasons(
                message="Cannot complete finalize stage",
                reasons=finalize_reasons,
                error_code=E_READER_FINAL_GATE_FAILED,
                details={"stage_id": stage_id},
            )

    transaction_id = uuid.uuid4().hex
    now = utc_now()
    run_id = str(manifest["run_id"])
    preserved_extensions = _preserved_manifest_extensions(manifest_for_completion)
    next_workflow = _workflow_after_completion(
        workflow=workflow,
        stages=stages,
        stage_id=stage_id,
        reason=reason,
        now=now,
        transaction_id=transaction_id,
        finalize=finalize,
    )
    old_registry = _read_json_if_exists(paths["artifact_registry"])
    registry = _build_artifact_registry(
        workspace=ws,
        run_id=run_id,
        artifacts=artifacts,
        workflow=next_workflow,
        updated_at=now,
    )
    frozen_reasons = _frozen_artifact_integrity_reasons(
        old_registry=old_registry,
        registry=registry,
        workflow=workflow,
        artifacts=artifacts,
        stages=stages,
        mutating_stage=stage_id,
    )
    if frozen_reasons:
        workflow = _persist_run_contamination(
            workspace=ws,
            paths=paths,
            run_id=run_id,
            workflow=workflow,
            reason_code="frozen_artifact_changed",
            message=" ".join(frozen_reasons),
            actor=actor,
            stage_id=stage_id,
            metadata={"blocking_reasons": frozen_reasons},
        )
        _raise_completion_reasons(
            message="Completion transaction cannot proceed because a frozen upstream artifact changed",
            reasons=frozen_reasons,
            error_code=E_TRANSACTION_INTEGRITY,
            details={"stage_id": stage_id},
        )
    if stage_id == "auditor":
        statuses = dict(next_workflow.get("stage_statuses") or {})
        auditor_status = dict(statuses.get("auditor") or {})
        auditor_status["metadata"] = _auditor_completion_metadata(
            workspace=ws,
            registry=registry,
        )
        statuses["auditor"] = auditor_status
        next_workflow["stage_statuses"] = statuses
    finalize_report: dict[str, Any] | None = None
    if finalize:
        finalize_report = _read_json(paths["runtime_manifest"].parent / "finalize_report.json")
        try:
            preflight_finalized_run_archive(
                workspace=ws,
                run_id=run_id,
                manifest=manifest_for_completion,
                workflow=next_workflow,
                artifact_registry=registry,
                finalize_report=finalize_report,
                fast_rerun_freshness_at_finalize=fast_rerun_freshness_at_finalize,
            )
        except RunArchiveError as exc:
            raise _wrap_archive_error(exc) from exc
    artifact_events = _changed_artifact_events(old_registry=old_registry, registry=registry)

    state_written = False
    try:
        if manifest_for_completion != manifest:
            _write_json_atomic(paths["runtime_manifest"], manifest_for_completion)
            state_written = True
        _write_json_atomic(paths["artifact_registry"], registry)
        state_written = True
        _write_json_atomic(paths["workflow_state"], next_workflow)
    except RuntimeStateError as exc:
        code = E_TRANSACTION_PARTIAL_WRITE if state_written else exc.error_code
        raise RuntimeStateError(
            "Completion transaction failed while writing state files.",
            details={
                "transaction_id": transaction_id,
                "stage_id": stage_id,
                "state_error": str(exc),
                "state_details": exc.details,
            },
            error_code=code,
        ) from exc

    _append_transaction_events(
        workspace=ws,
        run_id=run_id,
        actor=actor,
        transaction_id=transaction_id,
        stage_id=stage_id,
        decision="finalize" if finalize else "continue",
        reason=reason,
        next_stage=next_workflow.get("current_stage"),
        artifact_events=artifact_events,
    )

    current_manifest = _read_json(paths["runtime_manifest"])
    _assert_manifest_extensions_preserved(before=preserved_extensions, after=current_manifest)
    archive_result: dict[str, Any] | None = None
    if finalize:
        archive_result = _archive_finalized_state_if_needed(
            workspace=ws,
            manifest=current_manifest,
            workflow=next_workflow,
            artifact_registry=registry,
            finalize_report=finalize_report or _read_json(paths["runtime_manifest"].parent / "finalize_report.json"),
            fast_rerun_freshness_at_finalize=fast_rerun_freshness_at_finalize,
        )
        try:
            append_event(
                workspace=ws,
                run_id=run_id,
                event_type="run_archived",
                actor=actor,
                stage_id=stage_id,
                reason="Finalized run archived.",
                metadata={
                    "archive_path": _workspace_relative(ws, Path(str(archive_result["archive_path"]))),
                    "archive_manifest": _workspace_relative(ws, Path(str(archive_result["archive_manifest"]))),
                    "archive_manifest_sha256": archive_result["archive_manifest_sha256"],
                    "file_count": archive_result["file_count"],
                    "event_log_includes_run_archived": False,
                    "transaction_id": transaction_id,
                },
            )
        except RuntimeStateError as exc:
            raise RuntimeStateError(
                "Completion transaction archived the run but failed to append archive event.",
                details={
                    "transaction_id": transaction_id,
                    "stage_id": stage_id,
                    "archive_path": archive_result.get("archive_path"),
                    "event_error": str(exc),
                    "event_details": exc.details,
                },
                error_code=E_TRANSACTION_PARTIAL_WRITE,
            ) from exc
    state = show_runtime_state(workspace=ws)
    state["transaction"] = {
        "transaction_id": transaction_id,
        "stage_id": stage_id,
        "decision": "finalize" if finalize else "continue",
    }
    if archive_result is not None:
        state["run_archive"] = archive_result
    return state


def complete_stage_transaction(
    *,
    workspace: str | Path,
    stage_id: str,
    reason: str,
    repo_workdir: str | Path | None = None,
    actor: str = "orchestrator",
) -> dict[str, Any]:
    return _complete_stage_transaction(
        workspace=workspace,
        stage_id=stage_id,
        reason=reason,
        repo_workdir=repo_workdir,
        actor=actor,
        finalize=False,
    )


def complete_finalize_transaction(
    *,
    workspace: str | Path,
    reason: str,
    repo_workdir: str | Path | None = None,
    actor: str = "orchestrator",
) -> dict[str, Any]:
    return _complete_stage_transaction(
        workspace=workspace,
        stage_id="finalize",
        reason=reason,
        repo_workdir=repo_workdir,
        actor=actor,
        finalize=True,
    )


def record_decision(
    *,
    workspace: str | Path,
    stage_id: str,
    decision: str,
    reason: str,
    repo_workdir: str | Path | None = None,
    actor: str = "orchestrator",
) -> dict[str, Any]:
    ws = _require_workspace(workspace)
    paths = runtime_state_paths(ws)
    if not paths["runtime_manifest"].exists() or not paths["workflow_state"].exists():
        initialize_runtime_state(workspace=ws, repo_workdir=repo_workdir, actor=actor)

    ws, paths, manifest, workflow = _load_manifest_and_workflow(ws)
    repo = resolve_repo_workdir(repo_workdir, workspace=ws)
    stages = load_stage_specs(repo)
    artifacts = load_artifact_contracts(repo)
    stage_by_id = {str(stage.get("stage_id")): stage for stage in stages}
    if stage_id not in stage_by_id:
        raise RuntimeStateError(
            f"Unknown stage: {stage_id}",
            details={"stage_id": stage_id, "known_stages": list(stage_by_id)},
        )
    if decision not in DECISION_VOCABULARY:
        raise RuntimeStateError(
            f"Unknown Orchestrator decision: {decision}",
            details={"decision": decision, "allowed_decisions": list(DECISION_VOCABULARY)},
        )
    stage_allowed = [str(item) for item in (stage_by_id[stage_id].get("allowed_decisions") or [])]
    if decision not in stage_allowed:
        raise RuntimeStateError(
            f"Decision '{decision}' is not allowed for stage '{stage_id}'.",
            details={"stage_id": stage_id, "decision": decision, "stage_allowed_decisions": stage_allowed},
        )
    current_stage_before = workflow.get("current_stage")
    if current_stage_before is None:
        raise RuntimeStateError(
            "Cannot record a decision because the workflow has no current stage.",
            details={"stage_id": stage_id, "decision": decision},
        )
    if stage_id != current_stage_before:
        raise RuntimeStateError(
            f"Decision stage '{stage_id}' does not match current stage '{current_stage_before}'.",
            details={
                "stage_id": stage_id,
                "current_stage": current_stage_before,
                "decision": decision,
            },
        )

    if decision in {"continue", "finalize"}:
        command = "finalize-complete" if decision == "finalize" else "stage-complete"
        raise RuntimeStateError(
            (
                f"Decision '{decision}' must be recorded with `multi-agent-brief state {command}`. "
                "`state decide` is reserved for retry_stage, delegate_repair, request_human_review, and block_run."
            ),
            details={
                "stage_id": stage_id,
                "decision": decision,
                "required_command": command,
            },
            error_code=E_COMPLETION_TRANSACTION_REQUIRED,
        )

    now = utc_now()
    statuses = dict(workflow.get("stage_statuses") or {})
    blocked = False
    blocking_reason = ""
    current_stage: str | None = stage_id

    if decision in {"continue", "finalize"}:
        statuses[stage_id] = _status_entry(STAGE_COMPLETE, reason, now)
        next_stage = _next_stage_id(stages, stage_id)
        if next_stage and decision != "finalize":
            statuses[next_stage] = _status_entry(STAGE_READY, "", now)
            current_stage = next_stage
        else:
            current_stage = None
    elif decision in {"retry_stage", "delegate_repair"}:
        statuses[stage_id] = _status_entry(STAGE_READY, reason, now)
    elif decision in {"request_human_review", "block_run"}:
        statuses[stage_id] = _status_entry(STAGE_BLOCKED, reason, now)
        blocked = True
        blocking_reason = reason

    workflow["updated_at"] = now
    workflow["current_stage"] = current_stage
    workflow["blocked"] = blocked
    workflow["blocking_reason"] = blocking_reason
    workflow["stage_statuses"] = statuses
    workflow["last_decision"] = {
        "stage_id": stage_id,
        "decision": decision,
        "reason": reason,
        "created_at": now,
    }
    workflow["next_allowed_decisions"] = _allowed_decisions_for_stage(stages, current_stage)

    append_event(
        workspace=ws,
        run_id=str(manifest["run_id"]),
        event_type="decision_recorded",
        actor=actor,
        stage_id=stage_id,
        decision=decision,
        reason=reason,
        metadata={"next_stage": current_stage},
    )
    _write_json_atomic(paths["workflow_state"], workflow)
    return show_runtime_state(workspace=ws)
