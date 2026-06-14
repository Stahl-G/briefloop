"""Runtime state and artifact registry support for the Orchestrator.

This module deliberately does not reuse ``core.manifest``.  The core manifest
tracks historical Python pipeline output, while this module tracks the
external-runtime handoff state introduced in v0.6.1.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from multi_agent_brief.feedback.feedback_contract import (
    current_stage_feedback_blocking_reasons,
    optional_feedback_artifact_activated,
)
from multi_agent_brief.contracts.schemas.audit_report import AuditReportContract
from multi_agent_brief.contracts.schemas.claim import ClaimContract
from multi_agent_brief.core.claim_ledger import ClaimLedger
from multi_agent_brief.core.schemas import Claim
from multi_agent_brief.quality_gates.contract import (
    QualityGateContractError,
    current_stage_quality_gate_blocking_reasons,
    load_quality_gate_report_for_stage,
    quality_gate_artifact_activated,
    quality_gate_report_path_for_stage,
    validate_quality_gate_report_payload,
)
from multi_agent_brief.provenance.contract import provenance_artifact_activated
from multi_agent_brief import __version__
from multi_agent_brief.orchestrator_contract import (
    CONTRACT_REFERENCES,
    DECISION_VOCABULARY,
    resolve_repo_workdir,
)
from multi_agent_brief.orchestrator.run_archive import (
    E_RUN_ARCHIVE_CONFLICT,
    RUN_ARCHIVE_FACT_LAYER_SCHEMA,
    RUN_ARCHIVE_SCHEMA,
    RunArchiveError,
    archive_finalized_run,
    preflight_finalized_run_archive,
)
from multi_agent_brief.orchestrator.run_integrity import (
    RUN_INTEGRITY_CLEAN,
    RUN_INTEGRITY_CONTAMINATED,
    clean_run_integrity as _clean_run_integrity,
    contamination_event_metadata as _run_integrity_contamination_event_metadata,
    contaminate_run_integrity_with_event_flag as _contaminate_run_integrity_with_event_flag,
    normalize_run_integrity as _normalize_run_integrity,
    workflow_with_run_integrity as _workflow_with_run_integrity,
)
from multi_agent_brief.orchestrator.source_evidence import is_evidence_input_path
from multi_agent_brief.outputs.reader_final_gate import (
    combine_reader_final_gate_results,
    detect_reader_residue,
    detect_reader_residue_in_docx,
)


RUNTIME_MANIFEST_SCHEMA = "multi-agent-brief-runtime-manifest/v1"
WORKFLOW_STATE_SCHEMA = "multi-agent-brief-workflow-state/v1"
ARTIFACT_REGISTRY_SCHEMA = "multi-agent-brief-artifact-registry/v1"
EVENT_LOG_SCHEMA = "multi-agent-brief-event-log/v1"

RUNTIME_STATE_FILES = {
    "runtime_manifest": "output/intermediate/runtime_manifest.json",
    "workflow_state": "output/intermediate/workflow_state.json",
    "artifact_registry": "output/intermediate/artifact_registry.json",
    "event_log": "output/intermediate/event_log.jsonl",
}
PRESERVED_RUNTIME_MANIFEST_EXTENSION_KEYS = ("improvement", "recipe", "fact_layer_import")
FACT_LAYER_IMPORT_SCHEMA = "mabw.fact_layer_import.v1"
FACT_LAYER_IMPORT_REQUIRED_ARTIFACT_IDS = (
    "durable_source_evidence_or_source_pack",
    "input_classification",
    "candidate_claims",
    "screened_candidates",
    "claim_ledger",
)
FACT_LAYER_IMPORT_FORBIDDEN_ARTIFACT_IDS = {"source_candidates", "source_plan"}

EVENT_TYPES = {
    "run_initialized",
    "handoff_written",
    "artifact_observed",
    "artifact_validated",
    "stage_status_changed",
    "decision_recorded",
    "feedback_issue_created",
    "feedback_issue_planned",
    "feedback_issue_resolved",
    "repair_plan_created",
    "repair_plan_completed",
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
    "fact_layer_imported",
    "run_archived",
    "run_blocked",
    "run_integrity_contaminated",
    "run_reset",
}

ACTORS = {"cli", "orchestrator", "runtime", "system"}

STAGE_PENDING = "pending"
STAGE_READY = "ready"
STAGE_COMPLETE = "complete"
STAGE_BLOCKED = "blocked"
STAGE_SKIPPED = "skipped"

ARTIFACT_EXPECTED = "expected"
ARTIFACT_MISSING = "missing"
ARTIFACT_PRESENT = "present"
ARTIFACT_VALID = "valid"
ARTIFACT_INVALID = "invalid"

E_STAGE_ALREADY_COMPLETED = "E_STAGE_ALREADY_COMPLETED"
E_STAGE_MISMATCH = "E_STAGE_MISMATCH"
E_REQUIRED_ARTIFACT_MISSING = "E_REQUIRED_ARTIFACT_MISSING"
E_ARTIFACT_INVALID = "E_ARTIFACT_INVALID"
E_ILLEGAL_TRANSITION = "E_ILLEGAL_TRANSITION"
E_MANIFEST_EXTENSION_LOST = "E_MANIFEST_EXTENSION_LOST"
E_TRANSACTION_PARTIAL_WRITE = "E_TRANSACTION_PARTIAL_WRITE"
E_TRANSACTION_INTEGRITY = "E_TRANSACTION_INTEGRITY"
E_RUNTIME_STATE_NOT_INITIALIZED = "E_RUNTIME_STATE_NOT_INITIALIZED"
E_RUN_ARCHIVE_FAILED = "E_RUN_ARCHIVE_FAILED"
E_FACT_LAYER_IMPORT_INVALID = "E_FACT_LAYER_IMPORT_INVALID"
E_QUALITY_GATE_REQUIRED = "E_QUALITY_GATE_REQUIRED"
E_READER_FINAL_GATE_FAILED = "E_READER_FINAL_GATE_FAILED"
E_COMPLETION_TRANSACTION_REQUIRED = "E_COMPLETION_TRANSACTION_REQUIRED"

MAX_RUN_ID_LENGTH = 200
_RUN_ID_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_RUN_ID_WINDOWS_ABSOLUTE_RE = re.compile(r"\b[A-Za-z]:[\\/]")
_RUN_ID_TOKEN_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"ghp_[A-Za-z0-9_]{16,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"\b[A-Za-z0-9_-]{40,}\b"),
]
_RUN_ID_FORBIDDEN_PATH_FRAGMENTS = ("/Users/", "/home/", "/var/", "file://")
_RUN_ID_INJECTION_PHRASES = ("system:", "developer:", "assistant:", "ignore previous", "ignore all previous")


class RuntimeStateError(Exception):
    """Raised when runtime state cannot be read or written safely."""

    def __init__(
        self,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        error_code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.details = details or {}
        self.error_code = error_code

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "ok": False,
            "error": str(self),
            "details": self.details,
        }
        if self.error_code:
            payload["error_code"] = self.error_code
        return payload


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def new_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"mabw-{stamp}-{uuid.uuid4().hex[:8]}"


def _validate_runtime_run_id(value: Any, *, path: Path | None = None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RuntimeStateError(
            "runtime run_id is required.",
            details={"path": str(path) if path is not None else None},
        )
    text = value.strip()
    if _unsafe_runtime_run_id(text):
        raise RuntimeStateError(
            "runtime run_id is unsafe.",
            details={"path": str(path) if path is not None else None},
        )
    return text


def _safe_previous_run_id(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if _unsafe_runtime_run_id(text):
        return "unsafe-run-id"
    return text


def _unsafe_runtime_run_id(text: str) -> bool:
    lower = text.lower()
    return (
        len(text) > MAX_RUN_ID_LENGTH
        or "\n" in text
        or "\r" in text
        or "/" in text
        or "\\" in text
        or text.lstrip().startswith("#")
        or "```" in text
        or "~~~" in text
        or "<!--" in text
        or "-->" in text
        or bool(_RUN_ID_CONTROL_CHAR_RE.search(text))
        or bool(_RUN_ID_WINDOWS_ABSOLUTE_RE.search(text))
        or any(fragment.lower() in lower for fragment in _RUN_ID_FORBIDDEN_PATH_FRAGMENTS)
        or any(pattern.search(text) for pattern in _RUN_ID_TOKEN_PATTERNS)
        or any(phrase in lower for phrase in _RUN_ID_INJECTION_PHRASES)
    )


def _source_or_package_version() -> str:
    for parent in Path(__file__).resolve().parents:
        version_file = parent / "VERSION"
        if version_file.exists():
            text = version_file.read_text(encoding="utf-8").strip()
            if text:
                return text
    return __version__


def runtime_state_paths(workspace: str | Path) -> dict[str, Path]:
    ws = Path(workspace).expanduser().resolve()
    return {key: ws / rel_path for key, rel_path in RUNTIME_STATE_FILES.items()}


def _require_workspace(workspace: str | Path) -> Path:
    ws = Path(workspace).expanduser().resolve()
    if not (ws / "config.yaml").exists():
        raise RuntimeStateError(
            f"Workspace config.yaml not found: {ws / 'config.yaml'}",
            details={"workspace": str(ws)},
        )
    return ws


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeStateError(
            f"Invalid JSON state file: {path}",
            details={"path": str(path), "reason": str(exc)},
        ) from exc
    except OSError as exc:
        raise RuntimeStateError(
            f"Failed to read state file: {path}",
            details={"path": str(path), "reason": str(exc)},
        ) from exc
    if not isinstance(data, dict):
        raise RuntimeStateError(
            f"State file must contain a JSON object: {path}",
            details={"path": str(path)},
        )
    return data


def _read_json_if_exists(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return _read_json(path)


def _wrap_archive_error(exc: RunArchiveError) -> RuntimeStateError:
    return RuntimeStateError(
        str(exc),
        details=exc.details,
        error_code=exc.error_code or E_RUN_ARCHIVE_FAILED,
    )


def _checked_workflow_with_run_integrity(workflow: dict[str, Any], *, path: Path) -> dict[str, Any]:
    try:
        return _workflow_with_run_integrity(workflow)
    except ValueError as exc:
        raise RuntimeStateError(
            "workflow_state.run_integrity is malformed.",
            details={"path": str(path), "reason": str(exc)},
            error_code=E_TRANSACTION_INTEGRITY,
        ) from exc


def _workflow_is_finalized(workflow: dict[str, Any] | None) -> bool:
    if not workflow:
        return False
    statuses = workflow.get("stage_statuses") if isinstance(workflow.get("stage_statuses"), dict) else {}
    finalize_status = statuses.get("finalize") if isinstance(statuses.get("finalize"), dict) else {}
    return workflow.get("current_stage") is None and finalize_status.get("status") == STAGE_COMPLETE


def _archive_finalized_state_if_needed(
    *,
    workspace: Path,
    manifest: dict[str, Any],
    workflow: dict[str, Any],
    artifact_registry: dict[str, Any],
    finalize_report: dict[str, Any],
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


def _require_fact_layer_file_record(
    *,
    workspace: Path,
    archive_root: Path,
    record: dict[str, Any],
    artifact_id: str,
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
                    )
                )
        else:
            import_files.append(
                _require_fact_layer_file_record(
                    workspace=workspace,
                    archive_root=archive_root,
                    record=artifact,
                    artifact_id=artifact_id,
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


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    text += "\n"
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
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


def _read_state_bytes(path: Path) -> bytes | None:
    try:
        if not path.exists():
            return None
        return path.read_bytes()
    except OSError as exc:
        raise RuntimeStateError(
            f"Failed to snapshot state file: {path}",
            details={"path": str(path), "reason": str(exc)},
        ) from exc


def _restore_state_bytes(path: Path, data: bytes | None) -> None:
    try:
        if data is None:
            path.unlink(missing_ok=True)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.rollback.tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)
    except OSError as exc:
        raise RuntimeStateError(
            f"Failed to restore state file after partial write: {path}",
            details={"path": str(path), "reason": str(exc)},
            error_code=E_TRANSACTION_PARTIAL_WRITE,
        ) from exc


def _snapshot_state_files(paths: dict[str, Path], keys: tuple[str, ...]) -> dict[str, bytes | None]:
    return {key: _read_state_bytes(paths[key]) for key in keys}


def _restore_state_files(paths: dict[str, Path], snapshots: dict[str, bytes | None]) -> None:
    rollback_errors: list[dict[str, str]] = []
    for key, data in snapshots.items():
        try:
            _restore_state_bytes(paths[key], data)
        except RuntimeStateError as exc:
            rollback_errors.append({
                "key": key,
                "path": str(paths[key]),
                "reason": str(exc),
            })
    if rollback_errors:
        raise RuntimeStateError(
            "Runtime state rollback failed after partial write.",
            details={"rollback_errors": rollback_errors},
            error_code=E_TRANSACTION_PARTIAL_WRITE,
        )


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


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n"
    try:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line)
    except OSError as exc:
        raise RuntimeStateError(
            f"Failed to append event log: {path}",
            details={"path": str(path), "reason": str(exc)},
        ) from exc


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

    records: list[dict[str, Any]] = []
    for lineno, line in enumerate(raw.decode("utf-8").splitlines(), start=1):
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


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise RuntimeStateError(
            f"Invalid YAML contract file: {path}",
            details={"path": str(path), "reason": str(exc)},
        ) from exc
    except OSError as exc:
        raise RuntimeStateError(
            f"Failed to read contract file: {path}",
            details={"path": str(path), "reason": str(exc)},
        ) from exc
    if not isinstance(data, dict):
        raise RuntimeStateError(
            f"Contract file must contain a mapping: {path}",
            details={"path": str(path)},
        )
    return data


def _contract_file(repo_workdir: Path, rel_path: str) -> Path:
    path = repo_workdir / rel_path
    if not path.exists():
        raise RuntimeStateError(
            f"Contract file not found: {path}",
            details={"contract": rel_path, "repo_workdir": str(repo_workdir)},
        )
    return path


def load_stage_specs(repo_workdir: str | Path) -> list[dict[str, Any]]:
    repo = Path(repo_workdir).expanduser().resolve()
    data = _load_yaml(_contract_file(repo, CONTRACT_REFERENCES["stage_specs"]))
    stages = ((data.get("workflow") or {}).get("stages") or [])
    if not isinstance(stages, list):
        raise RuntimeStateError("stage_specs.yaml workflow.stages must be a list")
    return [stage for stage in stages if isinstance(stage, dict)]


def load_artifact_contracts(repo_workdir: str | Path) -> list[dict[str, Any]]:
    repo = Path(repo_workdir).expanduser().resolve()
    data = _load_yaml(_contract_file(repo, CONTRACT_REFERENCES["artifact_contracts"]))
    artifacts = data.get("artifacts") or []
    if not isinstance(artifacts, list):
        raise RuntimeStateError("artifact_contracts.yaml artifacts must be a list")
    return [artifact for artifact in artifacts if isinstance(artifact, dict)]


def _stage_ids(stages: list[dict[str, Any]]) -> list[str]:
    return [str(stage["stage_id"]) for stage in stages if stage.get("stage_id")]


def _artifact_ids(artifacts: list[dict[str, Any]]) -> set[str]:
    return {
        str(artifact["artifact_id"])
        for artifact in artifacts
        if artifact.get("artifact_id")
    }


def _artifact_map(artifacts: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        str(artifact["artifact_id"]): artifact
        for artifact in artifacts
        if artifact.get("artifact_id")
    }


def _initial_stage_statuses(stages: list[dict[str, Any]], *, now: str) -> dict[str, dict[str, Any]]:
    statuses: dict[str, dict[str, Any]] = {}
    first = True
    for stage_id in _stage_ids(stages):
        statuses[stage_id] = {
            "status": STAGE_READY if first else STAGE_PENDING,
            "reason": "",
            "updated_at": now,
        }
        first = False
    return statuses


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


def _initial_workflow_state(
    *,
    run_id: str,
    stages: list[dict[str, Any]],
    created_at: str,
    updated_at: str,
) -> dict[str, Any]:
    stage_statuses = _initial_stage_statuses(stages, now=updated_at)
    current_stage = _stage_ids(stages)[0] if stages else None
    return {
        "schema_version": WORKFLOW_STATE_SCHEMA,
        "run_id": run_id,
        "created_at": created_at,
        "updated_at": updated_at,
        "current_stage": current_stage,
        "blocked": False,
        "blocking_reason": "",
        "stage_statuses": stage_statuses,
        "last_decision": None,
        "next_allowed_decisions": _allowed_decisions_for_stage(stages, current_stage),
        "run_integrity": _clean_run_integrity(),
    }


def _allowed_decisions_for_stage(
    stages: list[dict[str, Any]],
    stage_id: str | None,
) -> list[str]:
    if stage_id is None:
        return []
    for stage in stages:
        if stage.get("stage_id") == stage_id:
            decisions = stage.get("allowed_decisions") or []
            return [str(decision) for decision in decisions]
    return []


def _runtime_manifest(
    *,
    run_id: str,
    created_at: str,
    updated_at: str,
    runtime: str,
    stages: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
) -> dict[str, Any]:
    manifest = {
        "schema_version": RUNTIME_MANIFEST_SCHEMA,
        "run_id": run_id,
        "created_at": created_at,
        "updated_at": updated_at,
        "workspace": ".",
        "runtime": runtime,
        "mabw_version": _source_or_package_version(),
        "contract_references": dict(CONTRACT_REFERENCES),
        "runtime_state_files": dict(RUNTIME_STATE_FILES),
        "stage_order": _stage_ids(stages),
        "expected_artifacts": [
            {
                "artifact_id": artifact.get("artifact_id", ""),
                "path": artifact.get("path", ""),
                "required": bool(artifact.get("required", False)),
                "producer_stage": artifact.get("producer_stage", ""),
                "consumer_stages": artifact.get("consumer_stages", []),
            }
            for artifact in artifacts
        ],
    }
    return manifest


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
    return {
        "ok": True,
        "workspace": str(ws),
        "runtime_state_files": dict(RUNTIME_STATE_FILES),
        "manifest": manifest,
        "workflow_state": workflow,
        "artifact_registry": registry,
        "event_count": event_count,
    }


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


def record_handoff_written(
    *,
    workspace: str | Path,
    handoff_markdown: str | Path,
    handoff_json: str | Path,
    actor: str = "cli",
) -> dict[str, Any]:
    ws, _paths, manifest, _workflow = _load_manifest_and_workflow(workspace)
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


def _workspace_relative(workspace: Path, path: Path) -> str:
    resolved = path.expanduser().resolve()
    try:
        return resolved.relative_to(workspace).as_posix()
    except ValueError:
        return path.as_posix()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_artifact(path: Path, fmt: str, artifact_id: str = "") -> tuple[str, str]:
    if not path.exists():
        return ARTIFACT_EXPECTED, "not_checked"
    if not path.is_file():
        return ARTIFACT_INVALID, "not_a_file"
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return ARTIFACT_INVALID, "decode_error"
    except OSError:
        return ARTIFACT_INVALID, "read_error"
    if not text.strip():
        return ARTIFACT_INVALID, "empty"

    try:
        if fmt == "json":
            payload = json.loads(text)
            if artifact_id == "claim_ledger":
                return _validate_claim_ledger_payload(payload)
            if artifact_id == "audit_report":
                return _validate_audit_report_payload(payload)
        elif fmt in {"yaml", "yml"}:
            yaml.safe_load(text)
        elif fmt == "markdown":
            pass
    except json.JSONDecodeError:
        return ARTIFACT_INVALID, "parse_error"
    except yaml.YAMLError:
        return ARTIFACT_INVALID, "parse_error"

    return ARTIFACT_VALID, "valid_minimum"


def _validate_claim_ledger_payload(payload: Any) -> tuple[str, str]:
    try:
        claims = ClaimLedger._claim_items_from_json(payload)
    except ValueError as exc:
        return ARTIFACT_INVALID, f"claim_ledger_schema_error:{exc}"

    seen_ids: set[str] = set()
    for idx, claim in enumerate(claims):
        for field in ("claim_id", "statement", "source_id", "evidence_text"):
            value = claim.get(field)
            if not isinstance(value, str) or not value.strip():
                return ARTIFACT_INVALID, f"claim_ledger_schema_error:claim[{idx}].{field}"
        claim_id = str(claim["claim_id"]).strip()
        if claim_id in seen_ids:
            return ARTIFACT_INVALID, f"claim_ledger_schema_error:duplicate_claim_id:{claim_id}"
        seen_ids.add(claim_id)
        violations = ClaimContract.validate(claim)
        errors = [violation for violation in violations if violation.severity == "error"]
        if errors:
            first = errors[0]
            return ARTIFACT_INVALID, f"claim_ledger_schema_error:claim[{idx}].{first.field}"

    try:
        ledger = ClaimLedger([Claim.from_dict(item) for item in claims])
    except (TypeError, ValueError) as exc:
        return ARTIFACT_INVALID, f"claim_ledger_schema_error:{exc}"
    errors = ledger.validate_claims()
    if errors:
        return ARTIFACT_INVALID, f"claim_ledger_schema_error:{errors[0]}"
    return ARTIFACT_VALID, "valid_claim_ledger_schema"


def _validate_audit_report_payload(payload: Any) -> tuple[str, str]:
    if not isinstance(payload, dict):
        return ARTIFACT_INVALID, "audit_report_schema_error:not_object"
    violations = AuditReportContract.validate(payload)
    errors = [violation for violation in violations if violation.severity == "error"]
    if errors:
        first = errors[0]
        return ARTIFACT_INVALID, f"audit_report_schema_error:{first.field}"
    findings = payload.get("findings")
    if findings is not None and not isinstance(findings, list):
        return ARTIFACT_INVALID, "audit_report_schema_error:findings"
    for idx, finding in enumerate(findings or []):
        if not isinstance(finding, dict):
            return ARTIFACT_INVALID, f"audit_report_schema_error:findings[{idx}]"
        for field in ("finding_id", "severity", "finding_type", "description"):
            value = finding.get(field)
            if not isinstance(value, str) or not value.strip():
                return ARTIFACT_INVALID, f"audit_report_schema_error:findings[{idx}].{field}"
        if finding.get("severity") not in {"low", "medium", "high"}:
            return ARTIFACT_INVALID, f"audit_report_schema_error:findings[{idx}].severity"
    return ARTIFACT_VALID, "valid_audit_report_schema"


def _current_stage_index(stages: list[dict[str, Any]], stage_id: str | None) -> int | None:
    ids = _stage_ids(stages)
    if stage_id in ids:
        return ids.index(str(stage_id))
    return None


def _next_stage_id(stages: list[dict[str, Any]], stage_id: str) -> str | None:
    ids = _stage_ids(stages)
    if stage_id not in ids:
        return None
    idx = ids.index(stage_id)
    if idx + 1 >= len(ids):
        return None
    return ids[idx + 1]


def _stage_status(workflow: dict[str, Any], stage_id: str) -> str:
    stage = (workflow.get("stage_statuses") or {}).get(stage_id) or {}
    return str(stage.get("status") or STAGE_PENDING)


def _stage_is_complete_or_skipped(workflow: dict[str, Any], stage_id: str) -> bool:
    return _stage_status(workflow, stage_id) in {STAGE_COMPLETE, STAGE_SKIPPED}


def _artifact_record(
    *,
    workspace: Path,
    artifact: dict[str, Any],
    workflow: dict[str, Any],
) -> dict[str, Any]:
    artifact_id = str(artifact.get("artifact_id") or "")
    rel_path = str(artifact.get("path") or "")
    fmt = str(artifact.get("format") or "")
    producer_stage = str(artifact.get("producer_stage") or "")
    status, validation_result = _validate_artifact(workspace / rel_path, fmt, artifact_id)

    activated_optional = optional_feedback_artifact_activated(
        workspace=workspace,
        artifact_id=artifact_id,
    ) or quality_gate_artifact_activated(
        workspace=workspace,
        artifact_id=artifact_id,
    ) or provenance_artifact_activated(
        workspace=workspace,
        artifact_id=artifact_id,
    )
    if (
        status == ARTIFACT_EXPECTED
        and _stage_is_complete_or_skipped(workflow, producer_stage)
        and (bool(artifact.get("required", False)) or activated_optional)
    ):
        status = ARTIFACT_MISSING
        validation_result = "missing"

    blocking_reason = ""
    if status == ARTIFACT_MISSING:
        blocking_reason = f"Producer stage '{producer_stage}' completed but '{rel_path}' is missing."
    elif status == ARTIFACT_INVALID:
        blocking_reason = f"Artifact '{rel_path}' failed minimum {fmt} validation."

    path = workspace / rel_path
    size_bytes = path.stat().st_size if path.exists() and path.is_file() else None
    mtime = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).replace(microsecond=0).isoformat() if path.exists() else None
    sha256 = _sha256_file(path) if path.exists() and path.is_file() else None

    return {
        "artifact_id": artifact_id,
        "path": rel_path,
        "format": fmt,
        "required": bool(artifact.get("required", False)),
        "producer_stage": producer_stage,
        "producer_role": artifact.get("producer_role", ""),
        "consumer_stages": artifact.get("consumer_stages", []),
        "status": status,
        "validation_result": validation_result,
        "blocking_reason": blocking_reason,
        "allowed_decisions": artifact.get("allowed_decisions", []),
        "retry_or_human_review_decision": artifact.get("retry_or_human_review_decision", ""),
        "size_bytes": size_bytes,
        "mtime": mtime,
        "sha256": sha256,
    }


def _build_artifact_registry(
    *,
    workspace: Path,
    run_id: str,
    artifacts: list[dict[str, Any]],
    workflow: dict[str, Any],
    updated_at: str,
) -> dict[str, Any]:
    records = {
        str(artifact.get("artifact_id")): _artifact_record(
            workspace=workspace,
            artifact=artifact,
            workflow=workflow,
        )
        for artifact in artifacts
        if artifact.get("artifact_id")
    }
    return {
        "schema_version": ARTIFACT_REGISTRY_SCHEMA,
        "run_id": run_id,
        "updated_at": updated_at,
        "artifacts": records,
    }


def _frozen_artifact_integrity_reasons(
    *,
    old_registry: dict[str, Any] | None,
    registry: dict[str, Any],
    workflow: dict[str, Any],
    artifacts: list[dict[str, Any]],
    stages: list[dict[str, Any]],
    mutating_stage: str | None = None,
) -> list[str]:
    old_records = ((old_registry or {}).get("artifacts") or {})
    new_records = registry.get("artifacts") or {}
    reasons: list[str] = []
    statuses = workflow.get("stage_statuses") or {}
    mutating_stage_produces = {
        str(item)
        for stage in stages
        if str(stage.get("stage_id") or "") == str(mutating_stage or "")
        for item in (stage.get("produces") or [])
    }
    for artifact in artifacts:
        artifact_id = str(artifact.get("artifact_id") or "")
        if not artifact_id:
            continue
        if artifact_id in mutating_stage_produces:
            continue
        producer_stage = str(artifact.get("producer_stage") or "")
        producer_status = ((statuses.get(producer_stage) or {}).get("status") or "")
        if producer_status not in {STAGE_COMPLETE, STAGE_SKIPPED}:
            continue
        old_record = old_records.get(artifact_id) or {}
        old_sha = old_record.get("sha256")
        if not old_sha:
            continue
        new_record = new_records.get(artifact_id) or {}
        new_sha = new_record.get("sha256")
        path = str(new_record.get("path") or old_record.get("path") or artifact.get("path") or artifact_id)
        if new_record.get("status") == ARTIFACT_MISSING or not new_sha:
            reasons.append(
                f"Frozen artifact '{path}' from owner stage '{producer_stage}' is missing after stage-complete; route repair back to the owner stage."
            )
        elif new_sha != old_sha:
            reasons.append(
                f"Frozen artifact '{path}' from owner stage '{producer_stage}' changed after stage-complete; route repair back to the owner stage instead of downstream in-place conversion."
            )
    return reasons


def _changed_artifact_events(
    *,
    old_registry: dict[str, Any] | None,
    registry: dict[str, Any],
) -> list[dict[str, Any]]:
    old_records = ((old_registry or {}).get("artifacts") or {})
    events: list[dict[str, Any]] = []
    for artifact_id, record in (registry.get("artifacts") or {}).items():
        old_record = old_records.get(artifact_id) or {}
        observed_changed = (
            record.get("status") in {ARTIFACT_VALID, ARTIFACT_INVALID}
            and (
                old_record.get("status") != record.get("status")
                or old_record.get("size_bytes") != record.get("size_bytes")
                or old_record.get("mtime") != record.get("mtime")
            )
        )
        if observed_changed:
            events.append({
                "event_type": "artifact_observed",
                "artifact_id": str(artifact_id),
                "metadata": {
                    "path": record.get("path"),
                    "size_bytes": record.get("size_bytes"),
                    "mtime": record.get("mtime"),
                },
            })

        validated_changed = (
            record.get("status") in {ARTIFACT_PRESENT, ARTIFACT_VALID, ARTIFACT_INVALID, ARTIFACT_MISSING}
            and (
                old_record.get("status") != record.get("status")
                or old_record.get("validation_result") != record.get("validation_result")
                or old_record.get("blocking_reason") != record.get("blocking_reason")
            )
        )
        if validated_changed:
            events.append({
                "event_type": "artifact_validated",
                "artifact_id": str(artifact_id),
                "reason": str(record.get("blocking_reason") or ""),
                "metadata": {
                    "path": record.get("path"),
                    "status": record.get("status"),
                    "validation_result": record.get("validation_result"),
                },
            })
    return events


def _stage_entry(workflow: dict[str, Any], stage_id: str | None) -> dict[str, Any]:
    if stage_id is None:
        return {}
    return ((workflow.get("stage_statuses") or {}).get(stage_id) or {})


def _changed_workflow_events(
    *,
    old_workflow: dict[str, Any],
    workflow: dict[str, Any],
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    current_stage = workflow.get("current_stage")
    old_current_stage = old_workflow.get("current_stage")
    old_entry = _stage_entry(old_workflow, str(current_stage) if current_stage else None)
    new_entry = _stage_entry(workflow, str(current_stage) if current_stage else None)
    stage_changed = (
        current_stage != old_current_stage
        or old_entry.get("status") != new_entry.get("status")
        or old_entry.get("reason") != new_entry.get("reason")
    )
    if current_stage and stage_changed:
        events.append({
            "event_type": "stage_status_changed",
            "stage_id": str(current_stage),
            "reason": str(workflow.get("blocking_reason") or ""),
            "metadata": {"status": new_entry.get("status")},
        })

    run_block_changed = (
        bool(workflow.get("blocked")) is True
        and (
            bool(old_workflow.get("blocked")) is not True
            or old_workflow.get("blocking_reason") != workflow.get("blocking_reason")
            or old_current_stage != current_stage
        )
    )
    if run_block_changed:
        events.append({
            "event_type": "run_blocked",
            "stage_id": str(current_stage) if current_stage else None,
            "reason": str(workflow.get("blocking_reason") or ""),
            "metadata": {},
        })
    return events


def _required_consumed_artifacts(
    *,
    stage: dict[str, Any],
    artifacts_by_id: dict[str, dict[str, Any]],
) -> list[str]:
    consumed = stage.get("consumes") or []
    required: list[str] = []
    for item in consumed:
        artifact_id = str(item)
        contract = artifacts_by_id.get(artifact_id)
        if contract and bool(contract.get("required", False)):
            required.append(artifact_id)
    return required


def _status_entry(
    status: str,
    reason: str,
    updated_at: str,
    *,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    entry = {
        "status": status,
        "reason": reason,
        "updated_at": updated_at,
    }
    if metadata:
        entry["metadata"] = metadata
    return entry


def _completion_artifact_gate_reasons(
    *,
    workspace: Path,
    stage: dict[str, Any],
    artifacts_by_id: dict[str, dict[str, Any]],
) -> list[str]:
    reasons: list[str] = []
    for artifact_id in stage.get("expected_artifacts") or []:
        contract = artifacts_by_id.get(str(artifact_id))
        if not contract:
            continue
        rel_path = str(contract.get("path") or "")
        fmt = str(contract.get("format") or "")
        status, validation_result = _validate_artifact(workspace / rel_path, fmt, str(artifact_id))
        required = bool(contract.get("required", False))
        if required and status != ARTIFACT_VALID:
            reasons.append(
                f"Required expected artifact '{artifact_id}' at '{rel_path}' is {status} ({validation_result})."
            )
        elif not required and status == ARTIFACT_INVALID:
            reasons.append(
                f"Optional expected artifact '{artifact_id}' at '{rel_path}' is invalid ({validation_result})."
            )
    return reasons


def _load_workspace_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def _count_evidence_files(path: Path, workspace: Path) -> int:
    if not path.exists() or not is_evidence_input_path(path, workspace):
        return 0
    if path.is_file():
        return 1
    if path.is_dir():
        return sum(
            1
            for item in path.rglob("*")
            if item.is_file() and is_evidence_input_path(item, workspace)
        )
    return 0


def _configured_evidence_source_count(sources: dict[str, Any], workspace: Path) -> int:
    count = 0
    manual = sources.get("manual") if isinstance(sources.get("manual"), dict) else {}
    manual_sources = manual.get("sources") if isinstance(manual.get("sources"), list) else []
    for item in manual_sources:
        if not isinstance(item, dict):
            continue
        if item.get("enabled") is False:
            continue
        if item.get("url"):
            count += 1
            continue
        raw_path = item.get("path")
        if raw_path:
            source_path = Path(str(raw_path))
            if not source_path.is_absolute():
                source_path = workspace / source_path
            count += _count_evidence_files(source_path, workspace)

    rss = sources.get("rss") if isinstance(sources.get("rss"), dict) else {}
    feeds = rss.get("feeds") if isinstance(rss.get("feeds"), list) else []
    count += len([
        item
        for item in feeds
        if isinstance(item, dict) and item.get("enabled", True) and item.get("url")
    ])

    cached = sources.get("cached_package") if isinstance(sources.get("cached_package"), dict) else {}
    if cached.get("enabled"):
        paths = cached.get("paths") if isinstance(cached.get("paths"), list) else []
        for raw_path in paths:
            source_path = Path(str(raw_path))
            if not source_path.is_absolute():
                source_path = workspace / source_path
            count += _count_evidence_files(source_path, workspace)

    filing_resolver = (
        sources.get("filing_resolver")
        if isinstance(sources.get("filing_resolver"), dict)
        else {}
    )
    if filing_resolver.get("enabled"):
        tickers = filing_resolver.get("tickers")
        if isinstance(tickers, list):
            count += len([item for item in tickers if item])

    feishu = sources.get("feishu") if isinstance(sources.get("feishu"), dict) else {}
    if feishu.get("enabled"):
        feishu_sources = feishu.get("sources")
        if isinstance(feishu_sources, list):
            count += len([item for item in feishu_sources if item])

    mcp = sources.get("mcp") if isinstance(sources.get("mcp"), dict) else {}
    if mcp.get("enabled"):
        servers = mcp.get("servers")
        if isinstance(servers, list):
            count += len([item for item in servers if item])

    input_dir = workspace / "input"
    if input_dir.exists():
        count += sum(
            1
            for item in input_dir.rglob("*")
            if item.is_file() and is_evidence_input_path(item, workspace)
        )
    return count


def _runtime_search_observation_counts(path: Path) -> tuple[bool, list[int]]:
    if not path.exists():
        return False, []
    try:
        text = path.read_text(encoding="utf-8")
        data = yaml.safe_load(text) or {}
    except (OSError, yaml.YAMLError):
        return False, []
    if "Did 0 searches" in text:
        return True, [0]
    if not isinstance(data, dict):
        return False, []

    counts: list[int] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            lowered = {str(key).lower(): val for key, val in value.items()}
            for key in ("result_count", "results_count", "search_count", "observation_count"):
                if key in lowered:
                    try:
                        counts.append(int(lowered[key]))
                    except (TypeError, ValueError):
                        pass
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(data)
    return bool(counts), counts


def _contains_zero_runtime_search_observation(path: Path) -> bool:
    has_observation, counts = _runtime_search_observation_counts(path)
    return has_observation and counts and all(count == 0 for count in counts)


def _source_candidates_is_plan_only(path: Path) -> bool:
    if not path.exists():
        return False
    data = _load_workspace_yaml(path)
    artifact_type = str(data.get("artifact_type") or "")
    evidence_status = str(data.get("evidence_status") or "")
    return artifact_type == "source_plan_only" or evidence_status == "not_evidence"


def _source_discovery_evidence_reasons(workspace: Path) -> list[str]:
    sources = _load_workspace_yaml(workspace / "sources.yaml")
    web_search = sources.get("web_search") if isinstance(sources.get("web_search"), dict) else {}
    web_search_enabled = web_search.get("enabled") is True
    web_search_mode = str(web_search.get("mode") or "")
    candidates_path = workspace / "source_candidates.yaml"
    evidence_count = _configured_evidence_source_count(sources, workspace)
    has_evidence = evidence_count > 0

    reasons: list[str] = []
    if (
        web_search_enabled
        and web_search_mode == "runtime_tool"
        and _contains_zero_runtime_search_observation(candidates_path)
    ):
        reasons.append(
            "Runtime WebSearch source discovery reported zero searches or zero observations; request human review instead of completing source-discovery."
        )

    if has_evidence:
        return reasons

    if _source_candidates_is_plan_only(candidates_path):
        reasons.append(
            "source_candidates.yaml is a source plan, not evidence; materialize approved sources into input/sources/ or supported source configuration before completing source-discovery."
        )
    if web_search_enabled and web_search_mode == "configure_later":
        reasons.append(
            "Cannot complete source-discovery: web_search.mode is configure_later, and no durable evidence source is available."
        )
    if web_search_enabled and web_search_mode == "runtime_tool":
        reasons.append(
            "Cannot complete source-discovery: runtime_tool web search is enabled, but no evidence source is available. Runtime WebSearch results must be written as durable source files under input/sources/ or into supported source configuration. source_candidates.yaml is a source plan, not evidence."
        )
    return reasons


def _completion_decision_gate_reasons(
    *,
    workspace: Path,
    stage: dict[str, Any],
    stages: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
) -> list[str]:
    stage_id = str(stage.get("stage_id") or "")
    reasons = _completion_artifact_gate_reasons(
        workspace=workspace,
        stage=stage,
        artifacts_by_id=_artifact_map(artifacts),
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
    return reasons


def _stage_quality_gate_pass_reasons(
    *,
    workspace: Path,
    stage_id: str,
    expected_brief: str,
    expected_ledger: str,
    stages: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
) -> list[str]:
    try:
        payload = load_quality_gate_report_for_stage(workspace, stage_id, allow_legacy=False)
    except QualityGateContractError as exc:
        return [f"Quality gate report is invalid: {exc}"]
    if payload is None:
        report_path = quality_gate_report_path_for_stage(workspace, stage_id)
        try:
            rel_path = report_path.relative_to(workspace).as_posix()
        except ValueError:
            rel_path = str(report_path)
        return [f"{rel_path} is required before completing stage '{stage_id}'."]

    errors = validate_quality_gate_report_payload(payload, stages=stages, artifacts=artifacts)
    if errors:
        return [f"Quality gate report is invalid: {' '.join(errors)}"]
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    gate_stage_id = str(metadata.get("gate_stage_id") or metadata.get("stage_id") or "")
    if gate_stage_id != stage_id:
        return [
            f"Quality gate report must be generated for {stage_id} completion "
            f"(metadata.gate_stage_id='{stage_id}'); got {gate_stage_id or '<missing>'}."
        ]
    brief_ref = str(metadata.get("brief") or metadata.get("audited_brief") or "")
    ledger_ref = str(metadata.get("ledger") or metadata.get("claim_ledger") or "")
    if brief_ref != expected_brief:
        return [f"Quality gate report brief metadata must be {expected_brief}; got {brief_ref}."]
    if ledger_ref != expected_ledger:
        return [f"Quality gate report ledger metadata must be {expected_ledger}; got {ledger_ref}."]
    gate_ids = {
        str(result.get("gate_id") or "")
        for result in payload.get("gate_results") or []
        if isinstance(result, dict)
    }
    required_gate_ids = {"material_fact", "freshness", "target_relevance"}
    missing_gate_ids = sorted(required_gate_ids - gate_ids)
    if missing_gate_ids:
        return [
            "Quality gate report must include material_fact, freshness, and target_relevance gate_results; "
            f"missing: {', '.join(missing_gate_ids)}."
        ]
    if payload.get("status") == "fail":
        return ["Quality gate report status is fail."]
    failed_gate_ids = sorted(
        str(result.get("gate_id") or "")
        for result in payload.get("gate_results") or []
        if isinstance(result, dict) and result.get("status") == "fail"
    )
    if failed_gate_ids:
        return [f"Quality gate report has failing gate_results: {', '.join(failed_gate_ids)}."]
    blocking_findings = [
        str(finding.get("finding_id") or "")
        for finding in payload.get("findings") or []
        if isinstance(finding, dict) and finding.get("blocking_level") == "blocking"
    ]
    if blocking_findings:
        return [
            "Quality gate report has blocking findings: "
            + ", ".join(finding for finding in blocking_findings if finding)
        ]
    return []


def _quality_gate_pass_reasons(
    *,
    workspace: Path,
    stages: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
) -> list[str]:
    return _stage_quality_gate_pass_reasons(
        workspace=workspace,
        stage_id="auditor",
        expected_brief="output/intermediate/audited_brief.md",
        expected_ledger="output/intermediate/claim_ledger.json",
        stages=stages,
        artifacts=artifacts,
    )


def _finalize_quality_gate_pass_reasons(
    *,
    workspace: Path,
    stages: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
) -> list[str]:
    return _stage_quality_gate_pass_reasons(
        workspace=workspace,
        stage_id="finalize",
        expected_brief="output/brief.md",
        expected_ledger="output/intermediate/claim_ledger.json",
        stages=stages,
        artifacts=artifacts,
    )


def _resolve_report_artifact_path(workspace: Path, value: Any) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = workspace / path
    return path.resolve()


def _finalize_report_reader_artifact_paths(workspace: Path, report: dict[str, Any]) -> list[Path]:
    paths: list[Path] = []
    required_brief = workspace / "output" / "brief.md"
    paths.append(required_brief.resolve())
    for key in ("reader_brief", "named_reader_brief", "reader_docx", "named_reader_docx", "source_appendix"):
        path = _resolve_report_artifact_path(workspace, report.get(key))
        if path is not None:
            paths.append(path)
    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        marker = str(path)
        if marker not in seen:
            seen.add(marker)
            unique.append(path)
    return unique


def _finalize_report_delivery_artifact_reasons(workspace: Path, report: dict[str, Any]) -> list[str]:
    artifacts = report.get("delivery_artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        return ["finalize_report.json delivery_artifacts must list the reader delivery bundle."]
    reasons: list[str] = []
    delivery_root = (workspace / "output" / "delivery").resolve()
    for item in artifacts:
        path = _resolve_report_artifact_path(workspace, item)
        if path is None:
            reasons.append("finalize_report.json contains an invalid delivery_artifacts entry.")
            continue
        if not path.exists():
            reasons.append(f"finalize_report.json references missing delivery artifact: {path}.")
            continue
        try:
            path.relative_to(delivery_root)
        except ValueError:
            reasons.append(
                "finalize_report.json delivery_artifacts may only reference files under output/delivery."
            )
    return reasons


def _finalize_completion_reasons(
    workspace: Path,
    *,
    stages: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
) -> list[str]:
    reasons: list[str] = []
    reasons.extend(
        _finalize_quality_gate_pass_reasons(
            workspace=workspace,
            stages=stages,
            artifacts=artifacts,
        )
    )
    report_path = workspace / "output" / "intermediate" / "finalize_report.json"
    if not report_path.exists():
        reasons.append("finalize_report.json is required before finalize-complete.")
        return reasons
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"finalize_report.json is invalid JSON: {exc}"]
    except OSError as exc:
        return [f"finalize_report.json could not be read: {exc}"]
    if not isinstance(report, dict):
        return ["finalize_report.json must contain an object."]
    if report.get("status") != "pass":
        reasons.append("finalize_report.json status must be pass.")
    reader_clean = report.get("reader_clean")
    if not isinstance(reader_clean, dict) or reader_clean.get("status") != "pass":
        reasons.append("finalize_report.json reader_clean.status must be pass.")
    audit_binding = report.get("audit_binding")
    if not isinstance(audit_binding, dict) or audit_binding.get("status") != "pass":
        reasons.append("finalize_report.json audit_binding.status must be pass.")
    else:
        audit_binding_paths = {
            "claim_ledger_sha256": workspace / "output" / "intermediate" / "claim_ledger.json",
            "audited_brief_sha256": workspace / "output" / "intermediate" / "audited_brief.md",
            "audit_report_sha256": workspace / "output" / "intermediate" / "audit_report.json",
        }
        for field, path in audit_binding_paths.items():
            value = audit_binding.get(field)
            if not isinstance(value, str) or not value.strip():
                reasons.append(f"finalize_report.json audit_binding.{field} is required.")
                continue
            if not path.exists():
                reasons.append(f"finalize_report.json audit_binding.{field} target is missing: {path}.")
                continue
            try:
                current_sha256 = _sha256_file(path)
            except OSError as exc:
                reasons.append(
                    f"finalize_report.json audit_binding.{field} target could not be read: {exc}"
                )
                continue
            if value != current_sha256:
                reasons.append(
                    f"finalize_report.json audit_binding.{field} does not match current artifact bytes."
                )
    reasons.extend(_finalize_report_delivery_artifact_reasons(workspace, report))

    artifact_paths = _finalize_report_reader_artifact_paths(workspace, report)
    missing = [path for path in artifact_paths if not path.exists()]
    if missing:
        reasons.append(
            "finalize_report.json references missing reader artifacts: "
            + ", ".join(str(path) for path in missing)
        )
        return reasons

    gate_results = []
    for path in artifact_paths:
        suffix = path.suffix.lower()
        if suffix in {".md", ".markdown"}:
            try:
                gate_results.append(
                    detect_reader_residue(path.read_text(encoding="utf-8"), artifact=str(path))
                )
            except OSError as exc:
                reasons.append(f"Reader artifact could not be read: {path}: {exc}")
        elif suffix == ".docx":
            gate_results.append(detect_reader_residue_in_docx(path, artifact=str(path)))
    if gate_results:
        reader_gate = combine_reader_final_gate_results(gate_results)
        if reader_gate.status == "fail":
            reasons.append(
                "Current reader artifacts fail reader final gate: "
                f"{sum(reader_gate.counts.values())} residue findings."
            )
    return reasons


def _raise_completion_reasons(
    *,
    message: str,
    reasons: list[str],
    error_code: str,
    details: dict[str, Any] | None = None,
) -> None:
    payload = dict(details or {})
    payload["blocking_reasons"] = reasons
    raise RuntimeStateError(
        f"{message}: {' '.join(reasons)}",
        details=payload,
        error_code=error_code,
    )


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


def _workflow_after_completion(
    *,
    workflow: dict[str, Any],
    stages: list[dict[str, Any]],
    stage_id: str,
    reason: str,
    now: str,
    transaction_id: str,
    finalize: bool,
) -> dict[str, Any]:
    decision = "finalize" if finalize else "continue"
    next_stage = _next_stage_id(stages, stage_id)
    current_stage = None if finalize else next_stage
    statuses = dict(workflow.get("stage_statuses") or {})
    statuses[stage_id] = _status_entry(STAGE_COMPLETE, reason, now)
    if current_stage:
        statuses[current_stage] = _status_entry(STAGE_READY, "", now)
    updated = dict(workflow)
    updated["updated_at"] = now
    updated["current_stage"] = current_stage
    updated["blocked"] = False
    updated["blocking_reason"] = ""
    updated["stage_statuses"] = statuses
    updated["last_decision"] = {
        "stage_id": stage_id,
        "decision": decision,
        "reason": reason,
        "created_at": now,
    }
    updated["last_completion_transaction"] = {
        "transaction_id": transaction_id,
        "stage_id": stage_id,
        "decision": decision,
        "reason": reason,
        "created_at": now,
    }
    updated["next_allowed_decisions"] = _allowed_decisions_for_stage(stages, current_stage)
    return updated


def _artifact_registry_sha(
    registry: dict[str, Any],
    artifact_id: str,
) -> str:
    record = ((registry.get("artifacts") or {}).get(artifact_id) or {})
    sha256 = str(record.get("sha256") or "")
    if not sha256:
        path = str(record.get("path") or artifact_id)
        raise RuntimeStateError(
            f"Artifact '{artifact_id}' has no frozen sha256 in artifact_registry.json.",
            details={"artifact_id": artifact_id, "path": path},
            error_code=E_TRANSACTION_INTEGRITY,
        )
    return sha256


def _artifact_registry_path(
    registry: dict[str, Any],
    artifact_id: str,
    default: str,
) -> str:
    record = ((registry.get("artifacts") or {}).get(artifact_id) or {})
    return str(record.get("path") or default)


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


def _preserved_manifest_extensions(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        key: manifest[key]
        for key in PRESERVED_RUNTIME_MANIFEST_EXTENSION_KEYS
        if key in manifest
    }


def _assert_manifest_extensions_preserved(
    *,
    before: dict[str, Any],
    after: dict[str, Any],
) -> None:
    missing = [
        key
        for key, value in before.items()
        if key not in after or after.get(key) != value
    ]
    if missing:
        raise RuntimeStateError(
            "Registered runtime_manifest extension keys were lost.",
            details={"missing_extensions": missing},
            error_code=E_MANIFEST_EXTENSION_LOST,
        )


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

    if finalize:
        finalize_reasons = _finalize_completion_reasons(ws, stages=stages, artifacts=artifacts)
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
    preserved_extensions = _preserved_manifest_extensions(manifest)
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
                manifest=manifest,
                workflow=next_workflow,
                artifact_registry=registry,
                finalize_report=finalize_report,
            )
        except RunArchiveError as exc:
            raise _wrap_archive_error(exc) from exc
    artifact_events = _changed_artifact_events(old_registry=old_registry, registry=registry)

    state_written = False
    try:
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
