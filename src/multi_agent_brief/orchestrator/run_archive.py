"""Immutable finalized run archive support."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from pathlib import Path
from typing import Any

from multi_agent_brief.orchestrator.run_integrity import interpret_run_integrity, project_for_read
from multi_agent_brief.orchestrator.source_evidence import is_evidence_input_path
from multi_agent_brief.orchestrator.timing import derive_control_timing_from_path


RUN_ARCHIVE_SCHEMA = "mabw.run_archive.v1"
RUN_ARCHIVE_FACT_LAYER_SCHEMA = "mabw.run_archive.fact_layer.v1"
E_RUN_ARCHIVE_CONFLICT = "E_RUN_ARCHIVE_CONFLICT"
E_RUN_ARCHIVE_FAILED = "E_RUN_ARCHIVE_FAILED"

_KNOWN_INTERMEDIATE_FILES = (
    "candidate_claims.json",
    "screened_candidates.json",
    "claim_ledger.json",
    "audited_brief.md",
    "audit_report.json",
    "gates/auditor_quality_gate_report.json",
    "gates/finalize_quality_gate_report.json",
    "quality_gate_report.json",
    "finalize_report.json",
)
_CONTROL_FILES = (
    "runtime_manifest.json",
    "workflow_state.json",
    "artifact_registry.json",
    "event_log.jsonl",
)
_FACT_LAYER_REQUIRED_ARTIFACTS: dict[str, str] = {
    "input_classification": "output/input_classification.json",
    "candidate_claims": "output/intermediate/candidate_claims.json",
    "screened_candidates": "output/intermediate/screened_candidates.json",
    "claim_ledger": "output/intermediate/claim_ledger.json",
}
_FACT_LAYER_SOURCE_ARTIFACT_ID = "durable_source_evidence_or_source_pack"
_SOURCE_PLAN_EXCLUDED_ARTIFACT_ID = "source_candidates"


class RunArchiveError(Exception):
    """Raised when a finalized run cannot be archived safely."""

    def __init__(self, message: str, *, details: dict[str, Any] | None = None, error_code: str = E_RUN_ARCHIVE_FAILED) -> None:
        super().__init__(message)
        self.details = details or {}
        self.error_code = error_code


def archive_finalized_run(
    *,
    workspace: Path,
    run_id: str,
    manifest: dict[str, Any],
    workflow: dict[str, Any],
    artifact_registry: dict[str, Any],
    finalize_report: dict[str, Any],
    fast_rerun_freshness_at_finalize: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create or verify the immutable archive for a finalized run."""
    ws = workspace.expanduser().resolve()
    archive_root = ws / "output" / "runs" / run_id
    archive_plan = _archive_plan(
        workspace=ws,
        finalize_report=finalize_report,
        artifact_registry=artifact_registry,
    )
    files = archive_plan["files"]
    fast_rerun = _fast_rerun_for_manifest(
        manifest,
        freshness_at_finalize=fast_rerun_freshness_at_finalize,
    )
    archive_manifest = {
        "schema_version": RUN_ARCHIVE_SCHEMA,
        "run_id": run_id,
        "archived_at": _utc_now(),
        "source": "finalize-complete",
        "runtime_manifest_run_id": manifest.get("run_id"),
        "workflow_current_stage": workflow.get("current_stage"),
        "run_integrity": _run_integrity_for_manifest(workflow),
        "timing": _timing_for_manifest(ws, workflow),
        "fast_rerun": fast_rerun,
        "fact_layer": archive_plan["fact_layer"],
        "event_log_semantics": "copied_before_current_archive_event",
        "files": files,
    }
    if archive_root.exists():
        _verify_existing_archive_matches_plan(
            archive_root=archive_root,
            planned_files=files,
            planned_fact_layer=archive_plan["fact_layer"],
            planned_fast_rerun=fast_rerun,
        )
        return _archive_result(archive_root)

    tmp_root = ws / "output" / "runs" / f".tmp-{run_id}-{uuid.uuid4().hex}"
    try:
        tmp_root.mkdir(parents=True, exist_ok=False)
        for record in files:
            src = ws / str(record["original_path"])
            dst = tmp_root / str(record["archive_path"])
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            copied_sha = _sha256_file(dst)
            if copied_sha != record["sha256"]:
                raise RunArchiveError(
                    "Copied archive file hash does not match source hash.",
                    details={
                        "archive_path": record["archive_path"],
                        "expected_sha256": record["sha256"],
                        "actual_sha256": copied_sha,
                    },
                )
        _write_manifest(tmp_root / "manifest.json", archive_manifest)
        os.replace(tmp_root, archive_root)
    except Exception:
        if tmp_root.exists():
            shutil.rmtree(tmp_root, ignore_errors=True)
        raise
    return _archive_result(archive_root)


def preflight_finalized_run_archive(
    *,
    workspace: Path,
    run_id: str,
    manifest: dict[str, Any],
    workflow: dict[str, Any],
    artifact_registry: dict[str, Any],
    finalize_report: dict[str, Any],
    fast_rerun_freshness_at_finalize: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate whether the finalized run archive can be created without mutating state."""
    ws = workspace.expanduser().resolve()
    archive_root = ws / "output" / "runs" / run_id
    archive_plan = _archive_plan(
        workspace=ws,
        finalize_report=finalize_report,
        artifact_registry=artifact_registry,
    )
    files = archive_plan["files"]
    fast_rerun = _fast_rerun_for_manifest(
        manifest,
        freshness_at_finalize=fast_rerun_freshness_at_finalize,
    )
    if archive_root.exists():
        return _verify_existing_archive_matches_plan(
            archive_root=archive_root,
            planned_files=files,
            planned_fact_layer=archive_plan["fact_layer"],
            planned_fast_rerun=fast_rerun,
        )
    return {
        "archive_path": str(archive_root),
        "file_count": len(files),
        "would_create": True,
        "run_id": run_id,
        "runtime_manifest_run_id": manifest.get("run_id"),
        "workflow_current_stage": workflow.get("current_stage"),
        "run_integrity": _run_integrity_for_manifest(workflow),
        "timing": _timing_for_manifest(ws, workflow),
        "fast_rerun": fast_rerun,
        "fact_layer": archive_plan["fact_layer"],
    }


def _archive_plan(
    *,
    workspace: Path,
    finalize_report: dict[str, Any],
    artifact_registry: dict[str, Any],
) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    seen_archive_paths: set[str] = set()
    delivery_count = 0
    for raw_path in finalize_report.get("delivery_artifacts") or []:
        source = _resolve_workspace_file(workspace, raw_path)
        try:
            rel_from_delivery = source.relative_to(workspace / "output" / "delivery")
        except ValueError as exc:
            raise RunArchiveError(
                "Finalize report contains a delivery artifact outside output/delivery.",
                details={"artifact": str(raw_path)},
            ) from exc
        _add_file_record(
            records,
            seen_archive_paths,
            role="delivery",
            source=source,
            original_path=_workspace_relative(workspace, source),
            archive_path=Path("delivery") / rel_from_delivery,
        )
        delivery_count += 1
    if delivery_count == 0:
        raise RunArchiveError(
            "finalize_report.json must list at least one delivery artifact for run archive.",
            details={"field": "delivery_artifacts"},
        )

    intermediate_dir = workspace / "output" / "intermediate"
    for name in _KNOWN_INTERMEDIATE_FILES:
        rel_from_intermediate = Path(name)
        source = intermediate_dir / rel_from_intermediate
        if source.exists() and source.is_file():
            _add_file_record(
                records,
                seen_archive_paths,
                role="intermediate",
                source=source,
                original_path=_workspace_relative(workspace, source),
                archive_path=Path("intermediate") / rel_from_intermediate,
            )

    artifacts = artifact_registry.get("artifacts") if isinstance(artifact_registry.get("artifacts"), dict) else {}
    for artifact_id, artifact in sorted(artifacts.items()):
        if not isinstance(artifact, dict):
            continue
        rel_path = str(artifact.get("path") or "")
        if not rel_path.startswith("output/intermediate/"):
            continue
        source = workspace / rel_path
        if not source.exists() or not source.is_file():
            continue
        try:
            rel_from_intermediate = source.relative_to(intermediate_dir)
        except ValueError:
            rel_from_intermediate = Path(source.name)
        archive_path = Path("intermediate") / rel_from_intermediate
        _add_file_record(
            records,
            seen_archive_paths,
            role="intermediate",
            source=source,
            original_path=rel_path,
            archive_path=archive_path,
            artifact_id=str(artifact_id),
        )

    fact_layer = _add_fact_layer_records(
        records=records,
        seen_archive_paths=seen_archive_paths,
        workspace=workspace,
    )

    for name in _CONTROL_FILES:
        source = intermediate_dir / name
        if source.exists() and source.is_file():
            _add_file_record(
                records,
                seen_archive_paths,
                role="control",
                source=source,
                original_path=_workspace_relative(workspace, source),
                archive_path=Path("control") / name,
            )
    return {"files": records, "fact_layer": fact_layer}


def _add_fact_layer_records(
    *,
    records: list[dict[str, Any]],
    seen_archive_paths: set[str],
    workspace: Path,
) -> dict[str, Any]:
    artifacts: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    missing: list[str] = []

    source_files = _iter_durable_source_files(workspace)
    if not source_files:
        missing.append(_FACT_LAYER_SOURCE_ARTIFACT_ID)
    source_pack_files: list[dict[str, Any]] = []
    for source in source_files:
        original_path = _workspace_relative(workspace, source)
        record = _add_file_record(
            records,
            seen_archive_paths,
            role="fact_layer",
            source=source,
            original_path=original_path,
            archive_path=Path("fact_layer") / original_path,
            artifact_id=_FACT_LAYER_SOURCE_ARTIFACT_ID,
        )
        if record is not None:
            source_pack_files.append(_fact_layer_file_record(record))
    if source_pack_files:
        artifacts.append({
            "artifact_id": _FACT_LAYER_SOURCE_ARTIFACT_ID,
            "fact_role": "durable_source_evidence_pack",
            "file_count": len(source_pack_files),
            "files": source_pack_files,
            "pack_sha256": _sha256_json(source_pack_files),
        })

    for artifact_id, rel_path in _FACT_LAYER_REQUIRED_ARTIFACTS.items():
        source = workspace / rel_path
        if not source.exists() or not source.is_file():
            missing.append(artifact_id)
            continue
        record = _add_file_record(
            records,
            seen_archive_paths,
            role="fact_layer",
            source=source,
            original_path=rel_path,
            archive_path=Path("fact_layer") / rel_path,
            artifact_id=artifact_id,
        )
        if record is not None:
            artifacts.append(_fact_layer_artifact_record(
                record,
                artifact_id=artifact_id,
                fact_role="fact_layer_artifact",
            ))

    source_candidates = workspace / "source_candidates.yaml"
    if source_candidates.exists() and source_candidates.is_file():
        excluded.append({
            "artifact_id": _SOURCE_PLAN_EXCLUDED_ARTIFACT_ID,
            "original_path": "source_candidates.yaml",
            "reason": "source_plan_not_evidence",
            "sha256": _sha256_file(source_candidates),
            "size_bytes": source_candidates.stat().st_size,
        })

    return {
        "schema_version": RUN_ARCHIVE_FACT_LAYER_SCHEMA,
        "status": "complete" if not missing else "incomplete",
        "completion_semantics": "required_fact_files_present_and_source_paths_pass_evidence_filter",
        "required_artifact_ids": [
            _FACT_LAYER_SOURCE_ARTIFACT_ID,
            *_FACT_LAYER_REQUIRED_ARTIFACTS.keys(),
        ],
        "missing_artifact_ids": missing,
        "artifact_count": len(artifacts),
        "source_evidence_count": len(source_files),
        "artifacts": artifacts,
        "excluded": excluded,
    }


def _iter_durable_source_files(workspace: Path) -> list[Path]:
    source_dir = workspace / "input" / "sources"
    if not source_dir.exists() or not source_dir.is_dir():
        return []
    return [
        path
        for path in sorted(source_dir.rglob("*"))
        if path.is_file() and is_evidence_input_path(path, workspace)
    ]


def _fact_layer_artifact_record(
    record: dict[str, Any],
    *,
    artifact_id: str,
    fact_role: str,
) -> dict[str, Any]:
    return {
        "artifact_id": artifact_id,
        "fact_role": fact_role,
        "archive_path": record["archive_path"],
        "original_path": record["original_path"],
        "sha256": record["sha256"],
        "size_bytes": record["size_bytes"],
    }


def _fact_layer_file_record(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "archive_path": record["archive_path"],
        "original_path": record["original_path"],
        "sha256": record["sha256"],
        "size_bytes": record["size_bytes"],
    }


def _add_file_record(
    records: list[dict[str, Any]],
    seen_archive_paths: set[str],
    *,
    role: str,
    source: Path,
    original_path: str,
    archive_path: Path,
    artifact_id: str | None = None,
) -> dict[str, Any] | None:
    archive_rel = archive_path.as_posix()
    if archive_rel in seen_archive_paths:
        return None
    seen_archive_paths.add(archive_rel)
    record: dict[str, Any] = {
        "role": role,
        "archive_path": archive_rel,
        "original_path": original_path,
        "sha256": _sha256_file(source),
        "size_bytes": source.stat().st_size,
    }
    if artifact_id:
        record["artifact_id"] = artifact_id
    records.append(record)
    return record


def _resolve_workspace_file(workspace: Path, raw_path: Any) -> Path:
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise RunArchiveError(
            "Finalize report contains an invalid delivery artifact path.",
            details={"artifact": raw_path},
        )
    path = Path(raw_path)
    if not path.is_absolute():
        path = workspace / path
    resolved = path.expanduser().resolve()
    try:
        resolved.relative_to(workspace)
    except ValueError as exc:
        raise RunArchiveError(
            "Finalize report delivery artifact is outside the workspace.",
            details={"artifact": raw_path},
        ) from exc
    if not resolved.exists() or not resolved.is_file():
        raise RunArchiveError(
            "Finalize report delivery artifact does not exist.",
            details={"artifact": raw_path},
        )
    return resolved


def _verify_existing_archive(
    *,
    archive_root: Path,
) -> dict[str, Any]:
    manifest_path = archive_root / "manifest.json"
    try:
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RunArchiveError(
            "Existing run archive manifest is unreadable.",
            details={"archive_path": str(archive_root), "reason": str(exc)},
            error_code=E_RUN_ARCHIVE_CONFLICT,
        ) from exc
    if not isinstance(existing, dict):
        raise RunArchiveError(
            "Existing run archive manifest must be an object.",
            details={"archive_path": str(archive_root)},
            error_code=E_RUN_ARCHIVE_CONFLICT,
        )
    existing_files = existing.get("files")
    if existing.get("schema_version") != RUN_ARCHIVE_SCHEMA or not isinstance(existing_files, list):
        raise RunArchiveError(
            "Existing run archive manifest is invalid.",
            details={"archive_path": _workspaceish(archive_root)},
            error_code=E_RUN_ARCHIVE_CONFLICT,
        )
    for record in existing_files:
        if not isinstance(record, dict):
            raise RunArchiveError(
                "Existing run archive manifest contains an invalid file record.",
                details={"archive_path": _workspaceish(archive_root)},
                error_code=E_RUN_ARCHIVE_CONFLICT,
            )
        path = archive_root / str(record["archive_path"])
        if not path.exists() or _sha256_file(path) != record["sha256"]:
            raise RunArchiveError(
                "Existing run archive file differs from manifest.",
                details={"archive_path": str(record["archive_path"])},
                error_code=E_RUN_ARCHIVE_CONFLICT,
            )
    return _archive_result(archive_root)


def _verify_existing_archive_matches_plan(
    *,
    archive_root: Path,
    planned_files: list[dict[str, Any]],
    planned_fact_layer: dict[str, Any],
    planned_fast_rerun: dict[str, Any],
) -> dict[str, Any]:
    result = _verify_existing_archive(archive_root=archive_root)
    existing_files = result["manifest"].get("files")
    if not isinstance(existing_files, list):
        raise RunArchiveError(
            "Existing run archive manifest is invalid.",
            details={"archive_path": _workspaceish(archive_root)},
            error_code=E_RUN_ARCHIVE_CONFLICT,
        )
    planned_by_archive_path = {
        str(record.get("archive_path")): record
        for record in planned_files
        if isinstance(record, dict) and record.get("archive_path")
    }
    existing_by_archive_path = {
        str(record.get("archive_path")): record
        for record in existing_files
        if isinstance(record, dict) and record.get("archive_path")
    }
    if set(planned_by_archive_path) != set(existing_by_archive_path):
        raise RunArchiveError(
            "Existing run archive file set differs from the current finalized run.",
            details={
                "archive_path": _workspaceish(archive_root),
                "missing_from_existing": sorted(
                    set(planned_by_archive_path) - set(existing_by_archive_path)
                ),
                "extra_in_existing": sorted(
                    set(existing_by_archive_path) - set(planned_by_archive_path)
                ),
            },
            error_code=E_RUN_ARCHIVE_CONFLICT,
        )
    for archive_path, planned in planned_by_archive_path.items():
        existing = existing_by_archive_path[archive_path]
        for field in ("original_path", "sha256", "size_bytes"):
            if (
                archive_path == "control/event_log.jsonl"
                and field in {"sha256", "size_bytes"}
                and result["manifest"].get("event_log_semantics")
                == "copied_before_current_archive_event"
            ):
                continue
            if existing.get(field) != planned.get(field):
                raise RunArchiveError(
                    "Existing run archive differs from the current finalized run.",
                    details={
                        "archive_path": archive_path,
                        "field": field,
                        "existing": existing.get(field),
                        "planned": planned.get(field),
                    },
                    error_code=E_RUN_ARCHIVE_CONFLICT,
                )
    existing_fact_layer = result["manifest"].get("fact_layer")
    if existing_fact_layer != planned_fact_layer:
        raise RunArchiveError(
            "Existing run archive fact_layer projection differs from the current finalized run.",
            details={
                "archive_path": _workspaceish(archive_root),
                "field": "fact_layer",
            },
            error_code=E_RUN_ARCHIVE_CONFLICT,
        )
    existing_fast_rerun = result["manifest"].get("fast_rerun")
    if existing_fast_rerun != planned_fast_rerun:
        raise RunArchiveError(
            "Existing run archive fast_rerun projection differs from the current finalized run.",
            details={
                "archive_path": _workspaceish(archive_root),
                "field": "fast_rerun",
            },
            error_code=E_RUN_ARCHIVE_CONFLICT,
        )
    return result


def _archive_result(archive_root: Path) -> dict[str, Any]:
    manifest = json.loads((archive_root / "manifest.json").read_text(encoding="utf-8"))
    return {
        "archive_path": str(archive_root),
        "archive_manifest": str(archive_root / "manifest.json"),
        "archive_manifest_sha256": _sha256_file(archive_root / "manifest.json"),
        "file_count": len(manifest.get("files") or []),
        "manifest": manifest,
    }


def _write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    text = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    path.write_text(text, encoding="utf-8")


def _sha256_json(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _run_integrity_for_manifest(workflow: dict[str, Any]) -> dict[str, Any]:
    return project_for_read(
        interpret_run_integrity(
            workflow.get("run_integrity"),
            field_present="run_integrity" in workflow,
        )
    )


def _timing_for_manifest(workspace: Path, workflow: dict[str, Any]) -> dict[str, Any]:
    timing = derive_control_timing_from_path(
        workspace / "output" / "intermediate" / "event_log.jsonl",
        workflow_state=workflow,
        expected_run_id=workflow.get("run_id") if isinstance(workflow.get("run_id"), str) else None,
    )
    return {
        "schema_version": timing.get("schema_version"),
        "kind": timing.get("kind"),
        "source": timing.get("source"),
        "precision": timing.get("precision"),
        "status": timing.get("status"),
        "total_elapsed_seconds": timing.get("total_elapsed_seconds"),
        "warnings": timing.get("warnings") or [],
    }


def _fast_rerun_for_manifest(
    manifest: dict[str, Any],
    *,
    freshness_at_finalize: dict[str, Any] | None = None,
) -> dict[str, Any]:
    record = manifest.get("fact_layer_import")
    if not isinstance(record, dict):
        return {}
    projected = {
        "schema_version": "mabw.run_archive.fast_rerun.v1",
        "source_run_id": record.get("source_run_id", ""),
        "source_archive_manifest": record.get("source_archive_manifest", ""),
        "source_archive_manifest_sha256": record.get("source_archive_manifest_sha256", ""),
        "fact_layer_sha256": record.get("fact_layer_sha256", ""),
        "freshness_at_import": (
            record.get("freshness_at_import") if isinstance(record.get("freshness_at_import"), dict) else {}
        ),
        "satisfied_stage_ids": (
            record.get("satisfied_stage_ids") if isinstance(record.get("satisfied_stage_ids"), list) else []
        ),
        "timing_comparability": record.get("timing_comparability") or "downstream_only",
    }
    if isinstance(freshness_at_finalize, dict):
        projected["freshness_at_finalize"] = freshness_at_finalize
    elif isinstance(record.get("freshness_at_finalize"), dict):
        projected["freshness_at_finalize"] = record["freshness_at_finalize"]
    return projected


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _workspace_relative(workspace: Path, path: Path) -> str:
    return path.resolve().relative_to(workspace).as_posix()


def _workspaceish(path: Path) -> str:
    return path.as_posix()


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
