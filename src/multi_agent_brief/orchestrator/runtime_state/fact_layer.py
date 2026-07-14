"""Fact-layer import transaction for fast-rerun workspaces.

Validates, archives, and atomically imports prior-run fact-layer artifacts
into a new run. Owns the mabw.fact_layer_import.v1 plan schema checks.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any

from multi_agent_brief.orchestrator_contract import resolve_repo_workdir
from multi_agent_brief.orchestrator.runtime_state._io import (
    _read_json,
    _restore_state_files,
    _sha256_file,
    _snapshot_state_files,
    _write_json_atomic,
)
from multi_agent_brief.orchestrator.runtime_state._transactions import (
    _load_manifest_and_workflow,
    _restore_file_paths,
    _snapshot_file_paths,
)
from multi_agent_brief.orchestrator.runtime_state.artifact_registry import (
    ARTIFACT_VALID,
    _build_artifact_registry,
)
from multi_agent_brief.orchestrator.runtime_state.completion_gates import (
    _fast_rerun_import_freshness_snapshot,
    _raise_completion_reasons,
)
from multi_agent_brief.orchestrator.runtime_state.contracts_loader import (
    _stage_ids,
    load_artifact_contracts,
    load_stage_specs,
)
from multi_agent_brief.orchestrator.runtime_state.errors import (
    E_FACT_LAYER_IMPORT_INVALID,
    E_TRANSACTION_PARTIAL_WRITE,
    RuntimeStateError,
)
from multi_agent_brief.orchestrator.runtime_state.event_log import append_event
from multi_agent_brief.orchestrator.runtime_state.identity import (
    _validate_runtime_run_id,
    utc_now,
)
from multi_agent_brief.orchestrator.runtime_state.lifecycle import (
    initialize_runtime_state,
    show_runtime_state,
)
from multi_agent_brief.orchestrator.runtime_state.paths import (
    _require_workspace,
    _workspace_relative,
    runtime_state_paths,
)
from multi_agent_brief.orchestrator.runtime_state.workflow import (
    STAGE_COMPLETE,
    _allowed_decisions_for_stage,
    _next_stage_id,
    _status_entry,
)
from multi_agent_brief.orchestrator.run_archive import (
    RUN_ARCHIVE_FACT_LAYER_SCHEMA,
    RUN_ARCHIVE_SCHEMA,
)
from multi_agent_brief.orchestrator.run_integrity import (
    RUN_INTEGRITY_CLEAN,
    interpret_run_integrity as _interpret_run_integrity,
    project_for_read as _project_run_integrity_for_read,
)


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


def import_fact_layer_transaction(
    *,
    workspace: str | Path,
    archive: str | Path,
    runtime: str,
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
