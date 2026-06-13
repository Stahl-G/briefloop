"""Immutable finalized run archive support."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from pathlib import Path
from typing import Any


RUN_ARCHIVE_SCHEMA = "mabw.run_archive.v1"
E_RUN_ARCHIVE_CONFLICT = "E_RUN_ARCHIVE_CONFLICT"
E_RUN_ARCHIVE_FAILED = "E_RUN_ARCHIVE_FAILED"

_KNOWN_INTERMEDIATE_FILES = (
    "candidate_claims.json",
    "screened_candidates.json",
    "claim_ledger.json",
    "audited_brief.md",
    "audit_report.json",
    "quality_gate_report.json",
    "finalize_report.json",
)
_CONTROL_FILES = (
    "runtime_manifest.json",
    "workflow_state.json",
    "artifact_registry.json",
    "event_log.jsonl",
)


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
) -> dict[str, Any]:
    """Create or verify the immutable archive for a finalized run."""
    ws = workspace.expanduser().resolve()
    archive_root = ws / "output" / "runs" / run_id
    files = _archive_file_plan(
        workspace=ws,
        finalize_report=finalize_report,
        artifact_registry=artifact_registry,
    )
    archive_manifest = {
        "schema_version": RUN_ARCHIVE_SCHEMA,
        "run_id": run_id,
        "archived_at": _utc_now(),
        "source": "finalize-complete",
        "runtime_manifest_run_id": manifest.get("run_id"),
        "workflow_current_stage": workflow.get("current_stage"),
        "event_log_semantics": "copied_before_current_archive_event",
        "files": files,
    }
    if archive_root.exists():
        return _verify_existing_archive(archive_root=archive_root)

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


def _archive_file_plan(
    *,
    workspace: Path,
    finalize_report: dict[str, Any],
    artifact_registry: dict[str, Any],
) -> list[dict[str, Any]]:
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
        source = intermediate_dir / name
        if source.exists() and source.is_file():
            _add_file_record(
                records,
                seen_archive_paths,
                role="intermediate",
                source=source,
                original_path=_workspace_relative(workspace, source),
                archive_path=Path("intermediate") / name,
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
        archive_path = Path("intermediate") / source.name
        _add_file_record(
            records,
            seen_archive_paths,
            role="intermediate",
            source=source,
            original_path=rel_path,
            archive_path=archive_path,
            artifact_id=str(artifact_id),
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
    return records


def _add_file_record(
    records: list[dict[str, Any]],
    seen_archive_paths: set[str],
    *,
    role: str,
    source: Path,
    original_path: str,
    archive_path: Path,
    artifact_id: str | None = None,
) -> None:
    archive_rel = archive_path.as_posix()
    if archive_rel in seen_archive_paths:
        return
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
