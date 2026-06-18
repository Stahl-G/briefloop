"""Runtime state and artifact registry support for the Orchestrator."""

from __future__ import annotations

import hashlib
import fnmatch
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
from multi_agent_brief.contracts.schemas.claim_draft import ClaimDraftContract, claim_draft_diagnostics
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
    load_default_policy_pack,
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
    _role_topology_from_policy_pack,
    _source_discovery_evidence_reasons,
    _topology_satisfaction_artifact_reasons,
    _topology_satisfaction_rules,
)
from multi_agent_brief.orchestrator.runtime_state.errors import (
    E_ARTIFACT_INVALID,
    E_CLAIM_DRAFT_CONTRACT_INVALID,
    E_COMPLETION_TRANSACTION_REQUIRED,
    E_FACT_LAYER_IMPORT_INVALID,
    E_ILLEGAL_TRANSITION,
    E_QUALITY_GATE_REQUIRED,
    E_READER_FINAL_GATE_FAILED,
    E_REPAIR_TRANSACTION_REQUIRED,
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
    CLAIM_LEDGER_FROZEN_EDIT_GUIDANCE,
    _artifact_registry_path,
    _artifact_registry_sha,
    _build_artifact_registry,
    _changed_artifact_events,
    interpret_frozen_artifact_integrity,
    require_frozen_artifact_integrity_pass,
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
    workflow_with_persistable_stage_completions,
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
    contamination_event_metadata as _run_integrity_contamination_event_metadata,
    contaminate_run_integrity_with_event_flag as _contaminate_run_integrity_with_event_flag,
    finalize_run_integrity as _finalize_run_integrity,
    interpret_run_integrity as _interpret_run_integrity,
    project_for_read as _project_run_integrity_for_read,
    workflow_with_persistable_run_integrity as _workflow_with_persistable_run_integrity,
    workflow_with_sticky_contamination_events as _workflow_with_sticky_contamination_events,
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
    "freeze_claim_ledger_transaction",
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
ANALYST_DRAFT_SNAPSHOT_PATH = Path("output/intermediate/analyst_draft_snapshot.md")
CLAIM_DRAFTS_PATH = Path("output/intermediate/claim_drafts.json")
CLAIM_LEDGER_PATH = Path("output/intermediate/claim_ledger.json")
CLAIM_LEDGER_FREEZE_SCHEMA = "mabw.claim_ledger_freeze.v1"
CLAIM_LEDGER_FREEZE_ID_STRATEGY = "sorted_sequential_v1"
CLAIM_DRAFT_PROVENANCE_METADATA_FIELDS = (
    "published_at",
    "retrieved_at",
    "source_path",
    "source_title",
    "source_name",
    "publisher",
    "topic",
)
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
    integrity_verdict = _interpret_run_integrity(archive_manifest.get("run_integrity"), field_present=True)
    integrity = _project_run_integrity_for_read(integrity_verdict)
    if (
        integrity_verdict.kind != "canonical"
        or
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


def _normalize_claim_text(value: str) -> str:
    return " ".join(value.casefold().split())


def _claim_draft_sort_key(indexed_draft: tuple[int, dict[str, Any]]) -> tuple[str, str, str, int]:
    index, draft = indexed_draft
    return (
        _normalize_claim_text(str(draft.get("source_id") or "")),
        _normalize_claim_text(str(draft.get("statement") or "")),
        _normalize_claim_text(str(draft.get("evidence_text") or "")),
        index,
    )


def _claim_draft_warnings(drafts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, list[int]] = {}
    for idx, draft in enumerate(drafts):
        key = _normalize_claim_text(str(draft.get("statement") or ""))
        if key:
            buckets.setdefault(key, []).append(idx)
    return [
        {
            "warning_type": "lexical_duplicate_statement",
            "draft_indexes": indexes,
            "normalized_statement": statement,
        }
        for statement, indexes in sorted(buckets.items())
        if len(indexes) > 1
    ]


def _read_claim_drafts_for_freeze(workspace: Path) -> tuple[Path, dict[str, Any], list[dict[str, Any]]]:
    path = workspace / CLAIM_DRAFTS_PATH
    if not path.exists():
        raise RuntimeStateError(
            "Claim drafts are required before freezing the Claim Ledger.",
            details={"path": _workspace_relative(workspace, path)},
            error_code=E_REQUIRED_ARTIFACT_MISSING,
        )
    payload = _read_json(path)
    violations = ClaimDraftContract.validate(payload)
    errors = [violation for violation in violations if violation.severity == "error"]
    if errors:
        first = errors[0]
        diagnostics = claim_draft_diagnostics(errors)
        raise RuntimeStateError(
            "Claim drafts failed contract validation.",
            details={
                "path": _workspace_relative(workspace, path),
                "field": first.field,
                "error": first.error,
                "required_fields": ["statement", "source_id", "evidence_text"],
                "forbidden_fields": ["claim_id"],
                "diagnostics": diagnostics,
            },
            error_code=E_CLAIM_DRAFT_CONTRACT_INVALID,
        )
    drafts = payload.get("drafts") or []
    if not drafts:
        raise RuntimeStateError(
            "Claim drafts must contain at least one draft before freezing the Claim Ledger.",
            details={
                "path": _workspace_relative(workspace, path),
                "field": "drafts",
                "error": "must contain at least one draft",
                "required_fields": ["statement", "source_id", "evidence_text"],
                "forbidden_fields": ["claim_id"],
                "diagnostics": [
                    {
                        "field": "drafts",
                        "error": "must contain at least one draft",
                        "severity": "error",
                        "required_fields": ["statement", "source_id", "evidence_text"],
                    }
                ],
            },
            error_code=E_CLAIM_DRAFT_CONTRACT_INVALID,
        )
    return path, payload, [dict(draft) for draft in drafts]


def _canonical_claims_from_drafts(drafts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    claims: list[dict[str, Any]] = []
    for seq, (_original_index, draft) in enumerate(
        sorted(enumerate(drafts), key=_claim_draft_sort_key),
        start=1,
    ):
        metadata = dict(draft.get("metadata") or {})
        if draft.get("draft_id"):
            metadata["draft_id"] = str(draft["draft_id"])
        if draft.get("candidate_id"):
            metadata["candidate_id"] = str(draft["candidate_id"])
        for field in CLAIM_DRAFT_PROVENANCE_METADATA_FIELDS:
            if draft.get(field) is not None:
                metadata.setdefault(field, str(draft[field]).strip())
        claim = {
            "claim_id": f"CL-{seq:04d}",
            "statement": str(draft["statement"]).strip(),
            "source_id": str(draft["source_id"]).strip(),
            "evidence_text": str(draft["evidence_text"]).strip(),
            "source_url": str(draft.get("source_url") or ""),
            "source_type": str(draft.get("source_type") or "local_file"),
            "claim_type": str(draft.get("claim_type") or "fact"),
            "confidence": str(draft.get("confidence") or "medium"),
            "requires_audit": bool(draft.get("requires_audit", True)),
            "created_by": str(draft.get("created_by") or "claim-ledger"),
            "used_in_sections": list(draft.get("used_in_sections") or []),
            "metadata": metadata,
            "schema_version": "v2",
            "epistemic_type": str(draft.get("epistemic_type") or "observed"),
            "evidence_relation": str(draft.get("evidence_relation") or "direct"),
            "applicability_reason": str(draft.get("applicability_reason") or ""),
            "limitations": list(draft.get("limitations") or []),
        }
        claims.append(claim)
    return claims


def _claim_ledger_bytes(claims: list[dict[str, Any]]) -> bytes:
    text = json.dumps(claims, ensure_ascii=False, indent=2, sort_keys=True)
    return (text + "\n").encode("utf-8")


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


def _claim_ledger_freeze_manifest(
    *,
    workspace: Path,
    frozen_at: str,
    draft_path: Path,
    draft_payload: dict[str, Any],
    drafts: list[dict[str, Any]],
    ledger_path: Path,
    ledger_bytes: bytes,
    warnings: list[dict[str, Any]],
    transaction_id: str,
) -> dict[str, Any]:
    return {
        "schema_version": CLAIM_LEDGER_FREEZE_SCHEMA,
        "status": "frozen",
        "frozen_at": frozen_at,
        "transaction_id": transaction_id,
        "id_strategy": CLAIM_LEDGER_FREEZE_ID_STRATEGY,
        "id_stability_scope": "per_freeze_input",
        "id_strategy_description": (
            "Deterministic for identical claim_drafts.json content under sorted_sequential_v1; "
            "not a cross-incremental stability guarantee when drafts are added, removed, or changed."
        ),
        "source_artifact_id": "claim_drafts",
        "source_path": _workspace_relative(workspace, draft_path),
        "source_schema_version": draft_payload.get("schema_version"),
        "source_sha256": _sha256_file(draft_path),
        "claim_ledger_path": _workspace_relative(workspace, ledger_path),
        "claim_ledger_sha256": _sha256_bytes(ledger_bytes),
        "claim_count": len(drafts),
        "source_ids": sorted({str(draft.get("source_id") or "") for draft in drafts if draft.get("source_id")}),
        "warnings": warnings,
    }


def _claim_ledger_freeze_reasons(
    *,
    workspace: Path,
    manifest: dict[str, Any],
) -> list[str]:
    freeze = manifest.get("claim_ledger_freeze")
    if not isinstance(freeze, dict):
        return [
            "Claim Ledger has not been frozen. Run `multi-agent-brief state freeze-claim-ledger --workspace <workspace>`."
        ]
    reasons: list[str] = []
    if freeze.get("schema_version") != CLAIM_LEDGER_FREEZE_SCHEMA:
        reasons.append("Claim Ledger freeze metadata has an unsupported schema.")
    if freeze.get("status") != "frozen":
        reasons.append("Claim Ledger freeze metadata is not frozen.")
    draft_path = workspace / str(freeze.get("source_path") or CLAIM_DRAFTS_PATH)
    ledger_path = workspace / str(freeze.get("claim_ledger_path") or CLAIM_LEDGER_PATH)
    if not draft_path.exists() or not draft_path.is_file():
        reasons.append(f"Claim Ledger freeze source is missing: {_workspace_relative(workspace, draft_path)}.")
    elif _sha256_file(draft_path) != str(freeze.get("source_sha256") or ""):
        reasons.append("Claim Ledger freeze source hash does not match current claim_drafts.json.")
    if not ledger_path.exists() or not ledger_path.is_file():
        reasons.append(f"Frozen Claim Ledger is missing: {_workspace_relative(workspace, ledger_path)}.")
    elif _sha256_file(ledger_path) != str(freeze.get("claim_ledger_sha256") or ""):
        reasons.append(
            f"Frozen Claim Ledger hash does not match current claim_ledger.json. {CLAIM_LEDGER_FROZEN_EDIT_GUIDANCE}"
        )
    return reasons


def _current_run_start_event_exists(event_records: list[dict[str, Any]], run_id: str) -> bool:
    return any(
        event.get("run_id") == run_id and event.get("event_type") in {"run_initialized", "run_reset"}
        for event in event_records
    )


def freeze_claim_ledger_transaction(
    *,
    workspace: str | Path,
    repo_workdir: str | Path | None = None,
    actor: str = "cli",
) -> dict[str, Any]:
    ws = _require_workspace(workspace)
    paths = runtime_state_paths(ws)
    _preflight_transaction_files(paths)
    ws, paths, manifest, workflow = _load_manifest_and_workflow(ws)
    if not paths["event_log"].exists():
        raise RuntimeStateError(
            "Event log is required before freezing the Claim Ledger.",
            details={"missing": str(paths["event_log"])},
            error_code=E_RUNTIME_STATE_NOT_INITIALIZED,
        )
    event_records = read_event_log_records_strict(paths["event_log"])
    if workflow.get("current_stage") != "claim-ledger":
        raise RuntimeStateError(
            "Claim Ledger can only be frozen while claim-ledger is the current stage.",
            details={"current_stage": workflow.get("current_stage")},
            error_code=E_STAGE_MISMATCH,
        )
    repo = resolve_repo_workdir(repo_workdir, workspace=ws)
    stages = load_stage_specs(repo)
    artifacts = load_artifact_contracts(repo)
    run_id = str(manifest["run_id"])
    if not _current_run_start_event_exists(event_records, run_id):
        raise RuntimeStateError(
            "Event log does not contain a current-run start event; refusing Claim Ledger freeze.",
            details={"run_id": run_id, "event_log": str(paths["event_log"])},
            error_code=E_TRANSACTION_INTEGRITY,
        )
    transaction_id = uuid.uuid4().hex
    frozen_at = utc_now()
    draft_path, draft_payload, drafts = _read_claim_drafts_for_freeze(ws)
    warnings = _claim_draft_warnings(drafts)
    claims = _canonical_claims_from_drafts(drafts)
    ledger_bytes = _claim_ledger_bytes(claims)
    ledger_path = ws / CLAIM_LEDGER_PATH
    source_sha = _sha256_file(draft_path)
    ledger_sha = _sha256_bytes(ledger_bytes)

    if "claim_ledger_freeze" in manifest:
        existing_freeze = manifest.get("claim_ledger_freeze")
        if not isinstance(existing_freeze, dict):
            raise RuntimeStateError(
                "Claim Ledger freeze metadata is malformed; refusing to freeze again.",
                details={"field": "claim_ledger_freeze"},
                error_code=E_TRANSACTION_INTEGRITY,
            )
        freeze_reasons = _claim_ledger_freeze_reasons(workspace=ws, manifest=manifest)
        frozen_source_sha = str(existing_freeze.get("source_sha256") or "")
        frozen_ledger_sha = str(existing_freeze.get("claim_ledger_sha256") or "")
        if not freeze_reasons and frozen_source_sha == source_sha and frozen_ledger_sha == ledger_sha:
            state = show_runtime_state(workspace=ws)
            state["claim_ledger_freeze"] = existing_freeze
            state["transaction"] = {
                "transaction_id": existing_freeze.get("transaction_id"),
                "stage_id": "claim-ledger",
                "decision": "freeze_claim_ledger_idempotent",
            }
            return state
        message = (
            "Claim Ledger is already frozen; repeat freeze requires unchanged claim_drafts.json "
            "and claim_ledger.json. Route repair/reset before freezing changed drafts."
        )
        if any(CLAIM_LEDGER_FROZEN_EDIT_GUIDANCE in reason for reason in freeze_reasons):
            message = f"{message} {CLAIM_LEDGER_FROZEN_EDIT_GUIDANCE}"
        raise RuntimeStateError(
            message,
            details={
                "freeze_reasons": freeze_reasons,
                "frozen_source_sha256": frozen_source_sha,
                "current_source_sha256": source_sha,
                "frozen_claim_ledger_sha256": frozen_ledger_sha,
                "current_claim_ledger_sha256": ledger_sha,
            },
            error_code=E_TRANSACTION_INTEGRITY,
        )

    next_manifest = dict(manifest)
    next_manifest["updated_at"] = frozen_at
    next_manifest["claim_ledger_freeze"] = _claim_ledger_freeze_manifest(
        workspace=ws,
        frozen_at=frozen_at,
        draft_path=draft_path,
        draft_payload=draft_payload,
        drafts=drafts,
        ledger_path=ledger_path,
        ledger_bytes=ledger_bytes,
        warnings=warnings,
        transaction_id=transaction_id,
    )
    registry = _build_artifact_registry(
        workspace=ws,
        run_id=run_id,
        artifacts=artifacts,
        workflow=workflow,
        updated_at=frozen_at,
    )

    file_snapshots = _snapshot_file_paths([ledger_path])
    state_snapshots = _snapshot_state_files(paths, ("runtime_manifest", "artifact_registry"))
    try:
        _write_bytes_atomic(ledger_path, ledger_bytes)
        registry = _build_artifact_registry(
            workspace=ws,
            run_id=run_id,
            artifacts=artifacts,
            workflow=workflow,
            updated_at=frozen_at,
        )
        ledger_record = ((registry.get("artifacts") or {}).get("claim_ledger") or {})
        if ledger_record.get("status") != ARTIFACT_VALID:
            raise RuntimeStateError(
                "Frozen Claim Ledger failed artifact validation.",
                details={
                    "artifact_id": "claim_ledger",
                    "status": ledger_record.get("status"),
                    "validation_result": ledger_record.get("validation_result"),
                },
                error_code=E_ARTIFACT_INVALID,
            )
        _write_json_atomic(paths["runtime_manifest"], next_manifest)
        _write_json_atomic(paths["artifact_registry"], registry)
        append_event(
            workspace=ws,
            run_id=run_id,
            event_type="claim_ledger_frozen",
            actor=actor,
            stage_id="claim-ledger",
            artifact_id="claim_ledger",
            reason="Claim Ledger frozen from claim_drafts.json.",
            metadata={
                "transaction_id": transaction_id,
                "source_artifact_id": "claim_drafts",
                "source_path": _workspace_relative(ws, draft_path),
                "source_sha256": source_sha,
                "claim_ledger_path": _workspace_relative(ws, ledger_path),
                "claim_ledger_sha256": ledger_sha,
                "claim_count": len(claims),
                "id_strategy": CLAIM_LEDGER_FREEZE_ID_STRATEGY,
                "warning_count": len(warnings),
            },
        )
    except RuntimeStateError as exc:
        try:
            _restore_state_files(paths, state_snapshots)
            _restore_file_paths(
                file_snapshots,
                rollback_message="Claim Ledger freeze rollback failed after partial write.",
            )
        except RuntimeStateError as rollback_exc:
            raise RuntimeStateError(
                "Claim Ledger freeze partially wrote files and failed rollback.",
                details={
                    "transaction_id": transaction_id,
                    "freeze_error": str(exc),
                    "freeze_details": exc.details,
                    "rollback_error": str(rollback_exc),
                    "rollback_details": rollback_exc.details,
                },
                error_code=E_TRANSACTION_PARTIAL_WRITE,
            ) from rollback_exc
        raise RuntimeStateError(
            "Claim Ledger freeze failed; written files were restored.",
            details={
                "transaction_id": transaction_id,
                "freeze_error": str(exc),
                "freeze_details": exc.details,
            },
            error_code=exc.error_code,
        ) from exc

    state = show_runtime_state(workspace=ws)
    state["claim_ledger_freeze"] = next_manifest["claim_ledger_freeze"]
    state["transaction"] = {
        "transaction_id": transaction_id,
        "stage_id": "claim-ledger",
        "decision": "freeze_claim_ledger",
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


def _stage_runtime_provenance(
    *,
    runtime: str | None,
    model: str | None,
    actor: str,
) -> dict[str, Any] | None:
    data: dict[str, Any] = {
        "schema_version": "mabw.stage_runtime_provenance.v1",
        "source": "stage_completion_args",
        "recorded_by_actor": actor,
        "provenance_only": True,
        "quality_claim": False,
    }
    if runtime is not None and str(runtime).strip():
        data["runtime"] = str(runtime).strip()
    if model is not None and str(model).strip():
        data["model"] = str(model).strip()
    return data if "runtime" in data or "model" in data else None


def _topology_satisfier_aliases(*, stage_id: str, topology: str) -> set[str]:
    aliases = {stage_id}
    if topology == "human_assisted" and stage_id in {"analyst", "editor", "writer"}:
        aliases.add("writer")
    return aliases


def _topology_satisfaction_targets_for_completion(
    *,
    stages: list[dict[str, Any]],
    policy_pack: dict[str, Any],
    stage_id: str,
) -> list[tuple[str, dict[str, Any]]]:
    try:
        topology = _role_topology_from_policy_pack(policy_pack)
        rules = _topology_satisfaction_rules(stages=stages, policy_pack=policy_pack)
    except ValueError as exc:
        raise RuntimeStateError(
            "policy.role_topology is invalid for stage satisfaction.",
            details={"reason": str(exc)},
            error_code=E_TRANSACTION_INTEGRITY,
        ) from exc

    satisfiers = _topology_satisfier_aliases(stage_id=stage_id, topology=topology)
    targets: list[tuple[str, dict[str, Any]]] = []
    current = _next_stage_id(stages, stage_id)
    while current:
        rule = rules.get(current)
        if not rule:
            break
        if str(rule.get("satisfied_by") or "") not in satisfiers:
            break
        targets.append((current, rule))
        current = _next_stage_id(stages, current)
    return targets


def _topology_satisfaction_required_reasons(
    *,
    workspace: Path,
    targets: list[tuple[str, dict[str, Any]]],
    artifacts_by_id: dict[str, dict[str, Any]],
) -> list[str]:
    reasons: list[str] = []
    for target_stage_id, rule in targets:
        reasons.extend(
            _topology_satisfaction_artifact_reasons(
                workspace=workspace,
                stage_id=target_stage_id,
                rule=rule,
                artifacts_by_id=artifacts_by_id,
            )
        )
    return reasons


def _topology_satisfaction_target_blocking_reasons(
    *,
    workspace: Path,
    targets: list[tuple[str, dict[str, Any]]],
    stages: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
) -> list[str]:
    reasons: list[str] = []
    for target_stage_id, _rule in targets:
        reasons.extend(
            current_stage_feedback_blocking_reasons(
                workspace=workspace,
                current_stage=target_stage_id,
                stages=stages,
                artifacts=artifacts,
            )
        )
        reasons.extend(
            current_stage_quality_gate_blocking_reasons(
                workspace=workspace,
                current_stage=target_stage_id,
                stages=stages,
                artifacts=artifacts,
            )
        )
    return reasons


def _workflow_with_topology_satisfaction(
    *,
    workflow: dict[str, Any],
    stages: list[dict[str, Any]],
    targets: list[tuple[str, dict[str, Any]]],
    trigger_stage_id: str,
    now: str,
    transaction_id: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not targets:
        return workflow, []

    updated = dict(workflow)
    statuses = dict(updated.get("stage_statuses") or {})
    topology_events: list[dict[str, Any]] = []
    current_stage = updated.get("current_stage")

    for target_stage_id, rule in targets:
        if current_stage != target_stage_id:
            raise RuntimeStateError(
                "Topology satisfaction target does not match the current workflow stage.",
                details={
                    "target_stage_id": target_stage_id,
                    "current_stage": current_stage,
                    "trigger_stage_id": trigger_stage_id,
                },
                error_code=E_TRANSACTION_INTEGRITY,
            )
        topology = str(rule.get("topology") or "")
        satisfied_by = str(rule.get("satisfied_by") or "")
        required_artifacts = [
            str(item)
            for item in (rule.get("required_artifacts") or [])
            if item
        ]
        reason = f"Stage satisfied by {satisfied_by} under {topology} role topology."
        metadata = {
            "satisfied_by_topology": True,
            "topology": topology,
            "satisfied_by": satisfied_by,
            "satisfied_by_stage": trigger_stage_id,
            "required_artifacts": required_artifacts,
            "transaction_id": transaction_id,
        }
        statuses[target_stage_id] = _status_entry(
            STAGE_COMPLETE,
            reason,
            now,
            metadata=metadata,
        )
        topology_events.append({
            "event_type": "stage_satisfied_by_topology",
            "stage_id": target_stage_id,
            "reason": reason,
            "metadata": metadata,
        })
        current_stage = _next_stage_id(stages, target_stage_id)
        if current_stage:
            statuses[current_stage] = _status_entry(STAGE_READY, "", now)

    updated["current_stage"] = current_stage
    updated["blocked"] = False
    updated["blocking_reason"] = ""
    updated["stage_statuses"] = statuses
    updated["next_allowed_decisions"] = _allowed_decisions_for_stage(stages, current_stage)
    return updated, topology_events


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
    topology_events: list[dict[str, Any]] | None = None,
    runtime_provenance: dict[str, Any] | None = None,
) -> None:
    try:
        for event in [*artifact_events, *(topology_events or [])]:
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
        decision_metadata = {"next_stage": next_stage, "transaction_id": transaction_id}
        if runtime_provenance:
            decision_metadata["runtime_provenance"] = runtime_provenance
        append_event(
            workspace=workspace,
            run_id=run_id,
            event_type="decision_recorded",
            actor=actor,
            stage_id=stage_id,
            decision=decision,
            reason=reason,
            metadata=decision_metadata,
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
    stage_runtime: str | None = None,
    stage_model: str | None = None,
) -> dict[str, Any]:
    ws = _require_workspace(workspace)
    paths = runtime_state_paths(ws)
    _preflight_transaction_files(paths)
    ws, paths, manifest, workflow = _load_manifest_and_workflow(ws)
    repo = resolve_repo_workdir(repo_workdir, workspace=ws)
    stages = load_stage_specs(repo)
    artifacts = load_artifact_contracts(repo)
    policy_pack = load_default_policy_pack(repo)
    artifacts_by_id = _artifact_map(artifacts)
    stage_by_id = {str(stage.get("stage_id")): stage for stage in stages}
    stage = _validate_completion_target(
        stage_id=stage_id,
        workflow=workflow,
        stage_by_id=stage_by_id,
        finalize=finalize,
    )
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

    transaction_id = uuid.uuid4().hex
    now = utc_now()
    runtime_provenance = _stage_runtime_provenance(
        runtime=stage_runtime,
        model=stage_model,
        actor=actor,
    )
    run_id = str(manifest["run_id"])
    analyst_snapshot_before: dict[Path, bytes | None] | None = None
    if stage_id == "analyst":
        analyst_snapshot_before = _snapshot_file_paths([ws / ANALYST_DRAFT_SNAPSHOT_PATH])
        _snapshot_analyst_draft(ws)
    try:
        artifact_reasons = _completion_artifact_gate_reasons(
            workspace=ws,
            stage=stage,
            artifacts_by_id=artifacts_by_id,
        )
        topology_targets = _topology_satisfaction_targets_for_completion(
            stages=stages,
            policy_pack=policy_pack,
            stage_id=stage_id,
        )
        artifact_reasons.extend(
            _topology_satisfaction_required_reasons(
                workspace=ws,
                targets=topology_targets,
                artifacts_by_id=artifacts_by_id,
            )
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
        if stage_id == "claim-ledger":
            freeze_reasons = _claim_ledger_freeze_reasons(workspace=ws, manifest=manifest)
            if freeze_reasons:
                _raise_completion_reasons(
                    message="Cannot complete stage 'claim-ledger' before Claim Ledger freeze",
                    reasons=freeze_reasons,
                    error_code=E_COMPLETION_TRANSACTION_REQUIRED,
                    details={"stage_id": stage_id},
                )

        topology_target_reasons = _topology_satisfaction_target_blocking_reasons(
            workspace=ws,
            targets=topology_targets,
            stages=stages,
            artifacts=artifacts,
        )
        if topology_target_reasons:
            _raise_completion_reasons(
                message=f"Cannot complete stage '{stage_id}' because a topology-satisfied downstream stage is blocked",
                reasons=topology_target_reasons,
                error_code=E_ILLEGAL_TRANSITION,
                details={
                    "stage_id": stage_id,
                    "topology_target_stages": [target for target, _rule in topology_targets],
                },
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

        preserved_extensions = _preserved_manifest_extensions(manifest_for_completion)
        next_workflow = _workflow_after_completion(
            workflow=workflow,
            stages=stages,
            stage_id=stage_id,
            reason=reason,
            now=now,
            transaction_id=transaction_id,
            finalize=finalize,
            runtime_provenance=runtime_provenance,
        )
        if finalize:
            next_workflow = _finalize_run_integrity(next_workflow)
        next_workflow, topology_events = _workflow_with_topology_satisfaction(
            workflow=next_workflow,
            stages=stages,
            targets=topology_targets,
            trigger_stage_id=stage_id,
            now=now,
            transaction_id=transaction_id,
        )
        old_registry = _read_json_if_exists(paths["artifact_registry"])
        registry = _build_artifact_registry(
            workspace=ws,
            run_id=run_id,
            artifacts=artifacts,
            workflow=next_workflow,
            updated_at=now,
        )
        frozen_verdict = interpret_frozen_artifact_integrity(
            old_registry=old_registry,
            registry=registry,
            workflow=workflow,
            artifacts=artifacts,
            stages=stages,
            mutating_stage=stage_id,
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
                    stage_id=stage_id,
                    metadata={"blocking_reasons": frozen_reasons},
                )
            _raise_completion_reasons(
                message=(
                    "Completion transaction cannot proceed because a frozen upstream artifact changed"
                    if frozen_verdict.contaminates_run
                    else "Completion transaction cannot proceed because frozen artifact integrity could not be verified"
                ),
                reasons=frozen_reasons,
                error_code=E_TRANSACTION_INTEGRITY,
                details={"stage_id": stage_id},
            )
        if stage_id == "auditor":
            statuses = dict(next_workflow.get("stage_statuses") or {})
            auditor_status = dict(statuses.get("auditor") or {})
            auditor_metadata = dict(auditor_status.get("metadata") or {})
            auditor_metadata.update(_auditor_completion_metadata(
                workspace=ws,
                registry=registry,
            ))
            auditor_status["metadata"] = auditor_metadata
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
    except RuntimeStateError:
        if analyst_snapshot_before is not None:
            _restore_file_paths(
                analyst_snapshot_before,
                rollback_message="Stage completion rollback failed after Analyst snapshot write.",
            )
        raise

    state_written = False
    state_snapshots = _snapshot_state_files(paths, ("runtime_manifest", "artifact_registry", "workflow_state"))
    try:
        if manifest_for_completion != manifest:
            _write_json_atomic(paths["runtime_manifest"], manifest_for_completion)
            state_written = True
        _write_json_atomic(paths["artifact_registry"], registry)
        state_written = True
        _write_json_atomic(paths["workflow_state"], next_workflow)
    except RuntimeStateError as exc:
        try:
            _restore_state_files(paths, state_snapshots)
            if analyst_snapshot_before is not None:
                _restore_file_paths(
                    analyst_snapshot_before,
                    rollback_message="Stage completion rollback failed after state write failure.",
                )
        except RuntimeStateError as rollback_exc:
            raise RuntimeStateError(
                "Completion transaction partially wrote files and failed rollback.",
                details={
                    "transaction_id": transaction_id,
                    "stage_id": stage_id,
                    "state_error": str(exc),
                    "state_details": exc.details,
                    "rollback_error": str(rollback_exc),
                    "rollback_details": rollback_exc.details,
                },
                error_code=E_TRANSACTION_PARTIAL_WRITE,
            ) from rollback_exc
        code = E_TRANSACTION_PARTIAL_WRITE if state_written else exc.error_code
        raise RuntimeStateError(
            "Completion transaction failed while writing state files; control files were restored.",
            details={
                "transaction_id": transaction_id,
                "stage_id": stage_id,
                "state_error": str(exc),
                "state_details": exc.details,
                "restored": True,
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
        topology_events=topology_events,
        runtime_provenance=runtime_provenance,
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
    if runtime_provenance:
        state["transaction"]["runtime_provenance"] = runtime_provenance
    if archive_result is not None:
        state["run_archive"] = archive_result
    return state


def _repair_route_error(payload: dict[str, Any]) -> RuntimeStateError:
    return RuntimeStateError(
        str(payload.get("message") or payload.get("reason") or payload.get("error") or "No deterministic repair route found."),
        details=payload,
        error_code=str(payload.get("error_code") or E_ILLEGAL_TRANSITION),
    )


def _delegate_repair_transaction_required_error(*, workspace: Path, stage_id: str, decision: str) -> RuntimeStateError:
    try:
        from multi_agent_brief.repair.router import route_repair

        repair_route = route_repair(workspace=workspace)
    except Exception as exc:  # pragma: no cover - defensive best-effort diagnostics
        repair_route = {"ok": False, "error": str(exc)}
    return RuntimeStateError(
        (
            "Decision 'delegate_repair' requires `multi-agent-brief repair start`; "
            "`state decide` cannot authorize owner-stage artifact edits."
        ),
        details={
            "stage_id": stage_id,
            "decision": decision,
            "required_commands": [
                f"multi-agent-brief repair route --workspace {workspace}",
                f"multi-agent-brief repair start --workspace {workspace}",
            ],
            "fallback_decisions": ["request_human_review", "block_run"],
            "repair_route": repair_route,
        },
        error_code=E_REPAIR_TRANSACTION_REQUIRED,
    )


def _repair_event_metadata(active_repair: dict[str, Any]) -> dict[str, Any]:
    return {
        "transaction_id": active_repair.get("transaction_id"),
        "repair_owner": active_repair.get("repair_owner"),
        "allowed_artifacts": list(active_repair.get("allowed_artifacts") or []),
        "blocked_direct_edits": list(active_repair.get("blocked_direct_edits") or []),
        "source": active_repair.get("source") or {},
        "must_rerun_from": active_repair.get("must_rerun_from"),
        "recommended_action": active_repair.get("recommended_action"),
        "run_integrity_effect": active_repair.get("run_integrity_effect"),
    }


def _workflow_with_repair_run_integrity_effect(
    *,
    workflow: dict[str, Any],
    active_repair: dict[str, Any],
    now: str,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    effect = active_repair.get("run_integrity_effect")
    if not isinstance(effect, dict) or effect.get("reference_eligible") is not False:
        return workflow, None
    current_integrity = workflow.get("run_integrity") if isinstance(workflow.get("run_integrity"), dict) else {}
    if (
        current_integrity.get("status") != RUN_INTEGRITY_CLEAN
        or current_integrity.get("reference_eligible", True) is not True
    ):
        return workflow, None

    source = active_repair.get("source") if isinstance(active_repair.get("source"), dict) else {}
    reason_code = str(source.get("finding_type") or effect.get("reason_code") or "repair_non_reference")
    message = str(
        effect.get("reason")
        or active_repair.get("reason")
        or "Repair route marked this run non-reference-eligible."
    )
    stage_id = source.get("stage_id") or active_repair.get("repair_owner")
    artifact_id = source.get("artifact_id")
    metadata = {
        "repair_transaction_id": active_repair.get("transaction_id"),
        "repair_owner": active_repair.get("repair_owner"),
        "source": source,
        "recommended_action": active_repair.get("recommended_action"),
        "run_integrity_effect": effect,
    }
    contaminated, reason_added = _contaminate_run_integrity_with_event_flag(
        workflow,
        reason_code=reason_code,
        message=message,
        created_at=now,
        event_type="repair_started",
        stage_id=str(stage_id) if stage_id else None,
        artifact_id=str(artifact_id) if artifact_id else None,
        metadata=metadata,
    )
    if not reason_added:
        return contaminated, None
    reasons = (contaminated.get("run_integrity") or {}).get("reasons")
    reason = reasons[-1] if isinstance(reasons, list) and reasons and isinstance(reasons[-1], dict) else {}
    return contaminated, reason


def _source_stage_for_repair_route(route: dict[str, Any]) -> str:
    source = route.get("source") if isinstance(route.get("source"), dict) else {}
    stage_id = str(source.get("stage_id") or "")
    if stage_id:
        return stage_id
    kind = str(source.get("kind") or "")
    if kind == "auditor_quality_gate_report":
        return "auditor"
    if kind == "finalize_quality_gate_report":
        return "finalize"
    if kind == "audit_report":
        return "auditor"
    return ""


def _repair_artifact_baseline(registry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    records = registry.get("artifacts")
    if not isinstance(records, dict):
        return {}
    baseline: dict[str, dict[str, Any]] = {}
    for artifact_id, record in records.items():
        if not isinstance(record, dict):
            continue
        baseline[str(artifact_id)] = {
            "path": record.get("path"),
            "status": record.get("status"),
            "validation_result": record.get("validation_result"),
            "sha256": record.get("sha256"),
        }
    return baseline


def _workflow_with_active_repair(
    *,
    workflow: dict[str, Any],
    stages: list[dict[str, Any]],
    active_repair: dict[str, Any],
    now: str,
) -> dict[str, Any]:
    owner = str(active_repair.get("repair_owner") or "")
    if owner not in _stage_ids(stages):
        raise RuntimeStateError(
            f"Repair owner '{owner}' is not a workflow stage.",
            details={"repair_owner": owner, "known_stages": _stage_ids(stages)},
            error_code=E_ILLEGAL_TRANSITION,
        )
    updated = dict(workflow)
    statuses = dict(updated.get("stage_statuses") or {})
    statuses[owner] = _status_entry(
        STAGE_READY,
        f"Repair started: {active_repair.get('reason') or ''}".strip(),
        now,
        metadata={
            "active_repair": True,
            "repair_transaction_id": active_repair.get("transaction_id"),
            "allowed_artifacts": list(active_repair.get("allowed_artifacts") or []),
            "must_rerun_from": active_repair.get("must_rerun_from"),
        },
    )
    updated["updated_at"] = now
    updated["current_stage"] = owner
    updated["blocked"] = False
    updated["blocking_reason"] = ""
    updated["stage_statuses"] = statuses
    updated["active_repair"] = active_repair
    updated["next_allowed_decisions"] = _allowed_decisions_for_stage(stages, owner)
    return updated


def start_repair_transaction(
    *,
    workspace: str | Path,
    repo_workdir: str | Path | None = None,
    actor: str = "orchestrator",
) -> dict[str, Any]:
    """Start an explicit owner-stage repair transaction from the deterministic route."""

    ws = _require_workspace(workspace)
    paths = runtime_state_paths(ws)
    _preflight_transaction_files(paths)
    if not paths["event_log"].exists():
        raise RuntimeStateError(
            "Repair start requires an existing event_log.jsonl control trace.",
            details={"path": str(paths["event_log"])},
            error_code=E_TRANSACTION_INTEGRITY,
        )
    ws, paths, manifest, workflow = _load_manifest_and_workflow(ws)
    if _workflow_is_finalized(workflow) or workflow.get("current_stage") is None:
        raise RuntimeStateError(
            "Cannot start repair for a finalized workflow; create a new run or use an explicit supersede/revision path.",
            details={"current_stage": workflow.get("current_stage")},
            error_code=E_ILLEGAL_TRANSITION,
        )
    if isinstance(workflow.get("active_repair"), dict):
        raise RuntimeStateError(
            "A repair transaction is already active.",
            details={"active_repair": workflow.get("active_repair")},
            error_code=E_ILLEGAL_TRANSITION,
        )

    from multi_agent_brief.repair.router import route_repair

    route = route_repair(workspace=ws)
    if not route.get("ok"):
        raise _repair_route_error(route)
    if route.get("repair_owner") in {None, "", "none"}:
        raise RuntimeStateError(
            "No deterministic repair route found.",
            details=route,
            error_code=E_ILLEGAL_TRANSITION,
        )
    if not route.get("allowed_artifacts"):
        raise RuntimeStateError(
            "Deterministic repair route has no allowed artifacts.",
            details=route,
            error_code=E_ILLEGAL_TRANSITION,
        )

    repo = resolve_repo_workdir(repo_workdir, workspace=ws)
    stages = load_stage_specs(repo)
    artifacts = load_artifact_contracts(repo)
    transaction_id = uuid.uuid4().hex
    now = utc_now()
    route_stage = _source_stage_for_repair_route(route)
    current_stage = str(workflow.get("current_stage") or "")
    if route_stage and route_stage != current_stage:
        raise RuntimeStateError(
            "Repair route source stage does not match the current workflow stage.",
            details={
                "route_stage_id": route_stage,
                "current_stage": current_stage,
                "source": route.get("source") or {},
            },
            error_code=E_ILLEGAL_TRANSITION,
        )
    baseline_registry = _build_artifact_registry(
        workspace=ws,
        run_id=str(manifest["run_id"]),
        artifacts=artifacts,
        workflow=workflow,
        updated_at=now,
    )
    active_repair = {
        "schema_version": "mabw.active_repair.v1",
        "transaction_id": transaction_id,
        "repair_owner": route.get("repair_owner"),
        "allowed_artifacts": list(route.get("allowed_artifacts") or []),
        "blocked_direct_edits": list(route.get("blocked_direct_edits") or []),
        "source": route.get("source") or {},
        "source_report_path": (route.get("source") or {}).get("file"),
        "must_rerun_from": route.get("must_rerun_from") or "",
        "reason": route.get("reason") or "",
        "recommended_action": route.get("recommended_action"),
        "run_integrity_effect": route.get("run_integrity_effect"),
        "started_at": now,
        "artifact_baseline": _repair_artifact_baseline(baseline_registry),
    }
    next_workflow = _workflow_with_active_repair(
        workflow=workflow,
        stages=stages,
        active_repair=active_repair,
        now=now,
    )
    next_workflow, contamination_reason = _workflow_with_repair_run_integrity_effect(
        workflow=next_workflow,
        active_repair=active_repair,
        now=now,
    )

    state_snapshots = _snapshot_state_files(paths, ("workflow_state", "event_log"))
    _write_json_atomic(paths["workflow_state"], next_workflow)
    try:
        append_event(
            workspace=ws,
            run_id=str(manifest["run_id"]),
            event_type="repair_started",
            actor=actor,
            stage_id=str(active_repair["repair_owner"]),
            reason=str(active_repair.get("reason") or "Repair transaction started."),
            metadata=_repair_event_metadata(active_repair),
        )
        if contamination_reason is not None:
            append_event(
                workspace=ws,
                run_id=str(manifest["run_id"]),
                event_type="run_integrity_contaminated",
                actor=actor,
                stage_id=contamination_reason.get("stage_id"),
                artifact_id=contamination_reason.get("artifact_id"),
                reason=str(contamination_reason.get("message") or "Repair start contaminated run integrity."),
                metadata=_run_integrity_contamination_event_metadata(contamination_reason),
            )
    except RuntimeStateError as exc:
        try:
            _restore_state_files(paths, state_snapshots)
        except RuntimeStateError as rollback_exc:
            raise RuntimeStateError(
                "Repair start partially wrote control files and failed rollback.",
                details={
                    "transaction_id": transaction_id,
                    "event_error": str(exc),
                    "rollback_error": str(rollback_exc),
                },
                error_code=E_TRANSACTION_PARTIAL_WRITE,
            ) from rollback_exc
        raise RuntimeStateError(
            "Repair start event append failed; control files were restored.",
            details={"transaction_id": transaction_id, "event_error": str(exc), "event_details": exc.details},
            error_code=E_TRANSACTION_PARTIAL_WRITE,
        ) from exc

    state = show_runtime_state(workspace=ws)
    state["repair"] = active_repair
    state["transaction"] = {
        "transaction_id": transaction_id,
        "stage_id": active_repair["repair_owner"],
        "decision": "repair_start",
    }
    return state


def _artifact_path_matches(pattern: str, path: str) -> bool:
    normalized_pattern = pattern.strip()
    normalized_path = path.strip()
    return bool(
        normalized_pattern
        and (
            normalized_path == normalized_pattern
            or fnmatch.fnmatch(normalized_path, normalized_pattern)
        )
    )


def _artifact_allowed(path: str, patterns: list[str]) -> bool:
    return any(_artifact_path_matches(pattern, path) for pattern in patterns)


def _repair_changed_artifact_reasons(
    *,
    baseline_records: dict[str, Any],
    registry: dict[str, Any],
    allowed_artifacts: list[str],
    blocked_direct_edits: list[str],
) -> tuple[list[str], bool]:
    new_records = registry.get("artifacts")
    if not isinstance(baseline_records, dict) or not isinstance(new_records, dict):
        return ["Repair completion requires a valid artifact baseline and artifact_registry.json."], False

    reasons: list[str] = []
    allowed_changed = False
    for artifact_id in sorted({*baseline_records.keys(), *new_records.keys()}):
        old_record_raw = baseline_records.get(artifact_id) or {}
        new_record = new_records.get(artifact_id) or {}
        if not isinstance(old_record_raw, dict):
            old_record_raw = {}
        if not isinstance(new_record, dict):
            new_record = {}
        path = str(new_record.get("path") or old_record_raw.get("path") or artifact_id)
        old_state = (
            old_record_raw.get("status"),
            old_record_raw.get("validation_result"),
            old_record_raw.get("sha256"),
        )
        new_state = (
            new_record.get("status"),
            new_record.get("validation_result"),
            new_record.get("sha256"),
        )
        if old_state == new_state:
            continue
        if _artifact_allowed(path, allowed_artifacts):
            allowed_changed = True
            continue
        if _artifact_allowed(path, blocked_direct_edits):
            reasons.append(
                f"Blocked repair artifact changed without ownership: {path}."
            )
        else:
            reasons.append(
                f"Repair changed non-allowed frozen artifact: {path}."
            )
    return reasons, allowed_changed


def _workflow_after_repair_completion(
    *,
    workflow: dict[str, Any],
    stages: list[dict[str, Any]],
    active_repair: dict[str, Any],
    reason: str,
    now: str,
    transaction_id: str,
) -> dict[str, Any]:
    owner = str(active_repair.get("repair_owner") or "")
    stage_ids = _stage_ids(stages)
    if owner not in stage_ids:
        raise RuntimeStateError(
            f"Repair owner '{owner}' is not a workflow stage.",
            details={"repair_owner": owner, "known_stages": stage_ids},
            error_code=E_ILLEGAL_TRANSITION,
        )
    owner_index = stage_ids.index(owner)
    requested_rerun = str(active_repair.get("must_rerun_from") or "")
    rerun_stage = requested_rerun if requested_rerun in stage_ids else _next_stage_id(stages, owner)
    statuses = dict(workflow.get("stage_statuses") or {})
    statuses[owner] = _status_entry(
        STAGE_COMPLETE,
        reason,
        now,
        metadata={
            "repaired": True,
            "repair_transaction_id": transaction_id,
            "allowed_artifacts": list(active_repair.get("allowed_artifacts") or []),
        },
    )
    for stage_id in stage_ids[owner_index + 1:]:
        if stage_id == rerun_stage:
            statuses[stage_id] = _status_entry(
                STAGE_READY,
                "Ready after owner-stage repair completion.",
                now,
                metadata={
                    "stale_after_repair": True,
                    "repair_transaction_id": transaction_id,
                    "repair_owner": owner,
                },
            )
        else:
            statuses[stage_id] = _status_entry(
                STAGE_PENDING,
                "Pending rerun after owner-stage repair completion.",
                now,
                metadata={
                    "stale_after_repair": True,
                    "repair_transaction_id": transaction_id,
                    "repair_owner": owner,
                },
            )
    updated = dict(workflow)
    updated.pop("active_repair", None)
    updated["updated_at"] = now
    updated["current_stage"] = rerun_stage
    updated["blocked"] = False
    updated["blocking_reason"] = ""
    updated["stage_statuses"] = statuses
    updated["last_decision"] = {
        "stage_id": owner,
        "decision": "repair_complete",
        "reason": reason,
        "created_at": now,
    }
    updated["last_repair_transaction"] = {
        "transaction_id": transaction_id,
        "stage_id": owner,
        "decision": "repair_complete",
        "reason": reason,
        "created_at": now,
    }
    updated["next_allowed_decisions"] = _allowed_decisions_for_stage(stages, rerun_stage)
    return updated


def complete_repair_transaction(
    *,
    workspace: str | Path,
    reason: str,
    repo_workdir: str | Path | None = None,
    actor: str = "orchestrator",
) -> dict[str, Any]:
    """Complete the active owner-stage repair transaction."""

    ws = _require_workspace(workspace)
    paths = runtime_state_paths(ws)
    _preflight_transaction_files(paths)
    if not paths["event_log"].exists():
        raise RuntimeStateError(
            "Repair completion requires an existing event_log.jsonl control trace.",
            details={"path": str(paths["event_log"])},
            error_code=E_TRANSACTION_INTEGRITY,
        )
    ws, paths, manifest, workflow = _load_manifest_and_workflow(ws)
    if _workflow_is_finalized(workflow) or workflow.get("current_stage") is None:
        raise RuntimeStateError(
            "Cannot complete repair for a finalized workflow; create a new run or use an explicit supersede/revision path.",
            details={"current_stage": workflow.get("current_stage")},
            error_code=E_ILLEGAL_TRANSITION,
        )
    active_repair = workflow.get("active_repair")
    if not isinstance(active_repair, dict):
        raise RuntimeStateError(
            "No active repair transaction exists.",
            details={"workspace": str(ws)},
            error_code=E_ILLEGAL_TRANSITION,
        )
    owner = str(active_repair.get("repair_owner") or "")
    if workflow.get("current_stage") != owner:
        raise RuntimeStateError(
            "Active repair owner does not match current workflow stage.",
            details={"repair_owner": owner, "current_stage": workflow.get("current_stage")},
            error_code=E_STAGE_MISMATCH,
        )

    allowed_artifacts = [str(item) for item in active_repair.get("allowed_artifacts") or []]
    blocked_direct_edits = [str(item) for item in active_repair.get("blocked_direct_edits") or []]
    if not allowed_artifacts:
        raise RuntimeStateError(
            "Active repair has no allowed artifacts.",
            details={"active_repair": active_repair},
            error_code=E_TRANSACTION_INTEGRITY,
        )

    repo = resolve_repo_workdir(repo_workdir, workspace=ws)
    stages = load_stage_specs(repo)
    artifacts = load_artifact_contracts(repo)
    stage_by_id = {str(stage.get("stage_id")): stage for stage in stages}
    stage = stage_by_id.get(owner)
    if stage is None:
        raise RuntimeStateError(
            f"Unknown repair owner stage: {owner}",
            details={"repair_owner": owner, "known_stages": list(stage_by_id)},
            error_code=E_ILLEGAL_TRANSITION,
        )
    artifacts_by_id = _artifact_map(artifacts)
    artifact_reasons = _completion_artifact_gate_reasons(
        workspace=ws,
        stage=stage,
        artifacts_by_id=artifacts_by_id,
    )
    if artifact_reasons:
        code = E_REQUIRED_ARTIFACT_MISSING
        if any("invalid" in item.lower() for item in artifact_reasons):
            code = E_ARTIFACT_INVALID
        _raise_completion_reasons(
            message=f"Cannot complete repair for stage '{owner}'",
            reasons=artifact_reasons,
            error_code=code,
            details={"stage_id": owner},
        )
    feedback_reasons = current_stage_feedback_blocking_reasons(
        workspace=ws,
        current_stage=owner,
        stages=stages,
        artifacts=artifacts,
    )
    if feedback_reasons:
        _raise_completion_reasons(
            message=f"Cannot complete repair for stage '{owner}'",
            reasons=feedback_reasons,
            error_code=E_ILLEGAL_TRANSITION,
            details={"stage_id": owner},
        )
    transaction_id = uuid.uuid4().hex
    now = utc_now()
    run_id = str(manifest["run_id"])
    old_registry = _read_json_if_exists(paths["artifact_registry"])
    registry_for_change_check = _build_artifact_registry(
        workspace=ws,
        run_id=run_id,
        artifacts=artifacts,
        workflow=workflow,
        updated_at=now,
    )
    baseline_records = active_repair.get("artifact_baseline")
    if not isinstance(baseline_records, dict):
        raise RuntimeStateError(
            "Active repair is missing its artifact baseline.",
            details={"active_repair": active_repair},
            error_code=E_TRANSACTION_INTEGRITY,
        )
    changed_reasons, allowed_changed = _repair_changed_artifact_reasons(
        baseline_records=baseline_records,
        registry=registry_for_change_check,
        allowed_artifacts=allowed_artifacts,
        blocked_direct_edits=blocked_direct_edits,
    )
    if changed_reasons:
        _raise_completion_reasons(
            message="Repair completion changed artifacts outside the deterministic repair route",
            reasons=changed_reasons,
            error_code=E_TRANSACTION_INTEGRITY,
            details={"stage_id": owner, "allowed_artifacts": allowed_artifacts},
        )
    if not allowed_changed:
        raise RuntimeStateError(
            "Repair completion did not modify any allowed artifact.",
            details={"stage_id": owner, "allowed_artifacts": allowed_artifacts},
            error_code=E_TRANSACTION_INTEGRITY,
        )

    next_workflow = _workflow_after_repair_completion(
        workflow=workflow,
        stages=stages,
        active_repair=active_repair,
        reason=reason,
        now=now,
        transaction_id=transaction_id,
    )
    registry = _build_artifact_registry(
        workspace=ws,
        run_id=run_id,
        artifacts=artifacts,
        workflow=next_workflow,
        updated_at=now,
    )
    frozen_verdict = interpret_frozen_artifact_integrity(
        old_registry=old_registry,
        registry=registry,
        workflow=workflow,
        artifacts=artifacts,
        stages=stages,
        mutating_stage=owner,
    )
    frozen_reasons = require_frozen_artifact_integrity_pass(frozen_verdict)
    if frozen_reasons:
        _raise_completion_reasons(
            message="Repair completion cannot proceed because frozen artifact integrity could not be verified",
            reasons=frozen_reasons,
            error_code=E_TRANSACTION_INTEGRITY,
            details={"stage_id": owner},
        )
    artifact_events = _changed_artifact_events(old_registry=old_registry, registry=registry)

    state_snapshots = _snapshot_state_files(paths, ("artifact_registry", "workflow_state", "event_log"))
    state_written = False
    try:
        _write_json_atomic(paths["artifact_registry"], registry)
        state_written = True
        _write_json_atomic(paths["workflow_state"], next_workflow)
    except RuntimeStateError as exc:
        try:
            _restore_state_files(paths, state_snapshots)
        except RuntimeStateError as rollback_exc:
            raise RuntimeStateError(
                "Repair completion partially wrote files and failed rollback.",
                details={
                    "transaction_id": transaction_id,
                    "stage_id": owner,
                    "state_error": str(exc),
                    "rollback_error": str(rollback_exc),
                },
                error_code=E_TRANSACTION_PARTIAL_WRITE,
            ) from rollback_exc
        code = E_TRANSACTION_PARTIAL_WRITE if state_written else exc.error_code
        raise RuntimeStateError(
            "Repair completion failed while writing state files; control files were restored.",
            details={
                "transaction_id": transaction_id,
                "stage_id": owner,
                "state_error": str(exc),
                "state_details": exc.details,
                "restored": True,
            },
            error_code=code,
        ) from exc

    try:
        for event in artifact_events:
            append_event(
                workspace=ws,
                run_id=run_id,
                event_type=str(event["event_type"]),
                actor=actor,
                artifact_id=event.get("artifact_id"),
                reason=str(event.get("reason") or ""),
                metadata={**(event.get("metadata") or {}), "transaction_id": transaction_id},
            )
        append_event(
            workspace=ws,
            run_id=run_id,
            event_type="repair_completed",
            actor=actor,
            stage_id=owner,
            decision="repair_complete",
            reason=reason,
            metadata={
                **_repair_event_metadata({**active_repair, "transaction_id": transaction_id}),
                "next_stage": next_workflow.get("current_stage"),
            },
        )
    except RuntimeStateError as exc:
        try:
            _restore_state_files(paths, state_snapshots)
        except RuntimeStateError as rollback_exc:
            raise RuntimeStateError(
                "Repair completion partially wrote files and failed rollback after event append failure.",
                details={
                    "transaction_id": transaction_id,
                    "stage_id": owner,
                    "event_error": str(exc),
                    "rollback_error": str(rollback_exc),
                },
                error_code=E_TRANSACTION_PARTIAL_WRITE,
            ) from rollback_exc
        raise RuntimeStateError(
            "Repair completion event append failed; control files were restored.",
            details={"transaction_id": transaction_id, "event_error": str(exc), "event_details": exc.details},
            error_code=E_TRANSACTION_PARTIAL_WRITE,
        ) from exc

    state = show_runtime_state(workspace=ws)
    state["repair"] = {
        "completed": True,
        "repair_owner": owner,
        "allowed_artifacts": allowed_artifacts,
        "must_rerun_from": active_repair.get("must_rerun_from"),
        "next_stage": next_workflow.get("current_stage"),
    }
    state["transaction"] = {
        "transaction_id": transaction_id,
        "stage_id": owner,
        "decision": "repair_complete",
    }
    return state


def _snapshot_analyst_draft(workspace: Path) -> None:
    source = workspace / "output/intermediate/audited_brief.md"
    target = workspace / ANALYST_DRAFT_SNAPSHOT_PATH
    if not source.exists():
        raise RuntimeStateError(
            "Cannot snapshot Analyst draft because audited_brief.md is missing.",
            details={"path": _workspace_relative(workspace, source)},
            error_code=E_REQUIRED_ARTIFACT_MISSING,
        )
    try:
        data = source.read_bytes()
    except OSError as exc:
        raise RuntimeStateError(
            "Cannot read Analyst draft for snapshot.",
            details={"path": _workspace_relative(workspace, source), "reason": str(exc)},
        ) from exc
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, target)
    except OSError as exc:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise RuntimeStateError(
            "Cannot write Analyst draft snapshot.",
            details={"path": _workspace_relative(workspace, target), "reason": str(exc)},
        ) from exc


def complete_stage_transaction(
    *,
    workspace: str | Path,
    stage_id: str,
    reason: str,
    repo_workdir: str | Path | None = None,
    actor: str = "orchestrator",
    runtime: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    return _complete_stage_transaction(
        workspace=workspace,
        stage_id=stage_id,
        reason=reason,
        repo_workdir=repo_workdir,
        actor=actor,
        finalize=False,
        stage_runtime=runtime,
        stage_model=model,
    )


def complete_finalize_transaction(
    *,
    workspace: str | Path,
    reason: str,
    repo_workdir: str | Path | None = None,
    actor: str = "orchestrator",
    runtime: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    return _complete_stage_transaction(
        workspace=workspace,
        stage_id="finalize",
        reason=reason,
        repo_workdir=repo_workdir,
        actor=actor,
        finalize=True,
        stage_runtime=runtime,
        stage_model=model,
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

    if decision == "delegate_repair":
        raise _delegate_repair_transaction_required_error(
            workspace=ws,
            stage_id=stage_id,
            decision=decision,
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
    elif decision == "retry_stage":
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
