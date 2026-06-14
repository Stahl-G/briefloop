"""Read-only helpers for fast-rerun fact-layer import projections."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


FACT_LAYER_IMPORT_SCHEMA = "mabw.fact_layer_import.v1"
FAST_RERUN_IMPORT_REQUIRED_ERROR = "E_FAST_RERUN_IMPORT_REQUIRED"
FAST_RERUN_RECIPE = "fast-rerun"
FAST_RERUN_START_STAGE = "analyst"
IMPORT_SATISFIED_STAGE_IDS = (
    "doctor",
    "source-discovery",
    "input-governance",
    "scout",
    "screener",
    "claim-ledger",
)


def summarize_fact_layer_import(
    manifest: dict[str, Any] | None,
    workflow: dict[str, Any] | None = None,
    *,
    workspace: str | Path | None = None,
) -> dict[str, Any]:
    """Return a read-only fast-rerun import projection.

    The projection is intentionally diagnostic. It does not mutate runtime
    state and it does not prove downstream writing quality.
    """

    manifest_obj = manifest if isinstance(manifest, dict) else {}
    workflow_obj = workflow if isinstance(workflow, dict) else {}
    record = manifest_obj.get("fact_layer_import")
    if not isinstance(record, dict):
        return {
            "present": False,
            "status": "missing",
            "error_code": FAST_RERUN_IMPORT_REQUIRED_ERROR,
            "message": "runtime_manifest.fact_layer_import is missing.",
            "recipe": manifest_obj.get("recipe"),
            "next_stage": FAST_RERUN_START_STAGE,
            "satisfied_stage_ids": list(IMPORT_SATISFIED_STAGE_IDS),
            "imported_stages": _imported_stage_projection(record={}, workflow=workflow_obj),
            "errors": ["runtime_manifest.fact_layer_import is missing."],
        }

    errors: list[str] = []
    if record.get("schema_version") != FACT_LAYER_IMPORT_SCHEMA:
        errors.append(
            "runtime_manifest.fact_layer_import.schema_version must be "
            f"{FACT_LAYER_IMPORT_SCHEMA}."
        )
    if manifest_obj.get("recipe") != FAST_RERUN_RECIPE:
        errors.append("runtime_manifest.recipe must be fast-rerun for imported fact-layer runs.")

    source_run_id = str(record.get("source_run_id") or "")
    fact_layer_sha256 = str(record.get("fact_layer_sha256") or "")
    source_archive_manifest = str(record.get("source_archive_manifest") or "")
    source_archive_manifest_sha256 = str(record.get("source_archive_manifest_sha256") or "")
    imported_file_count = record.get("imported_file_count")
    satisfied_stage_ids = [str(item) for item in (record.get("satisfied_stage_ids") or [])]
    freshness_at_import = record.get("freshness_at_import") if isinstance(record.get("freshness_at_import"), dict) else {}

    if not source_run_id:
        errors.append("runtime_manifest.fact_layer_import.source_run_id is required.")
    if not fact_layer_sha256:
        errors.append("runtime_manifest.fact_layer_import.fact_layer_sha256 is required.")
    if not source_archive_manifest:
        errors.append("runtime_manifest.fact_layer_import.source_archive_manifest is required.")
    if not source_archive_manifest_sha256:
        errors.append("runtime_manifest.fact_layer_import.source_archive_manifest_sha256 is required.")
    if not isinstance(imported_file_count, int) or imported_file_count <= 0:
        errors.append("runtime_manifest.fact_layer_import.imported_file_count must be a positive integer.")
    errors.extend(_imported_file_record_errors(record, workspace=workspace))

    missing_satisfied = sorted(set(IMPORT_SATISFIED_STAGE_IDS) - set(satisfied_stage_ids))
    if missing_satisfied:
        errors.append(
            "runtime_manifest.fact_layer_import.satisfied_stage_ids is missing: "
            + ", ".join(missing_satisfied)
        )

    imported_stages = _imported_stage_projection(record=record, workflow=workflow_obj)
    for stage in imported_stages:
        if not stage.get("complete_via_import"):
            errors.append(f"workflow_state.stage_statuses.{stage['stage_id']} is not complete via import.")

    current_stage = workflow_obj.get("current_stage")
    status = "valid" if not errors else "invalid"
    return {
        "present": True,
        "status": status,
        "error_code": None if status == "valid" else FAST_RERUN_IMPORT_REQUIRED_ERROR,
        "message": "fast-rerun import is valid." if status == "valid" else "fast-rerun import is invalid.",
        "recipe": manifest_obj.get("recipe"),
        "source_run_id": source_run_id,
        "source_archive_manifest": source_archive_manifest,
        "source_archive_manifest_sha256": source_archive_manifest_sha256,
        "fact_layer_sha256": fact_layer_sha256,
        "imported_file_count": imported_file_count,
        "freshness_at_import": freshness_at_import,
        "satisfied_stage_ids": satisfied_stage_ids,
        "required_satisfied_stage_ids": list(IMPORT_SATISFIED_STAGE_IDS),
        "imported_stages": imported_stages,
        "next_stage": FAST_RERUN_START_STAGE,
        "current_stage": current_stage,
        "timing_comparability": "downstream_only",
        "errors": errors,
    }


def load_fact_layer_import_summary(workspace: str | Path) -> dict[str, Any]:
    """Load runtime files directly and summarize fast-rerun import readiness."""

    ws = Path(workspace).expanduser().resolve()
    intermediate = ws / "output" / "intermediate"
    manifest_result = _read_json(intermediate / "runtime_manifest.json")
    workflow_result = _read_json(intermediate / "workflow_state.json")
    manifest = manifest_result.get("payload") if manifest_result.get("status") == "present" else None
    workflow = workflow_result.get("payload") if workflow_result.get("status") == "present" else None
    summary = summarize_fact_layer_import(
        manifest if isinstance(manifest, dict) else None,
        workflow if isinstance(workflow, dict) else None,
        workspace=ws,
    )
    input_errors: list[str] = []
    for label, result in (("runtime_manifest", manifest_result), ("workflow_state", workflow_result)):
        if result.get("status") == "missing":
            input_errors.append(f"{label} missing")
        elif result.get("status") == "error":
            input_errors.append(f"{label} unreadable: {result.get('error')}")
    if input_errors:
        summary = dict(summary)
        summary["status"] = "invalid"
        summary["error_code"] = FAST_RERUN_IMPORT_REQUIRED_ERROR
        summary["input_errors"] = input_errors
        summary["errors"] = [*input_errors, *(summary.get("errors") or [])]
    return summary


def require_fast_rerun_handoff_ready(workspace: str | Path) -> dict[str, Any]:
    """Return import summary or raise ValueError with a typed message."""

    summary = load_fact_layer_import_summary(workspace)
    errors = list(summary.get("errors") or [])
    if summary.get("status") != "valid":
        raise ValueError(_format_import_required_message(summary))
    if summary.get("current_stage") != FAST_RERUN_START_STAGE:
        errors.append(
            "workflow_state.current_stage must be analyst before fast-rerun handoff starts."
        )
    if errors:
        failed = dict(summary)
        failed["errors"] = errors
        failed["status"] = "invalid"
        raise ValueError(_format_import_required_message(failed))
    return summary


def _imported_stage_projection(record: dict[str, Any], workflow: dict[str, Any]) -> list[dict[str, Any]]:
    statuses = workflow.get("stage_statuses") if isinstance(workflow.get("stage_statuses"), dict) else {}
    fact_layer_sha256 = str(record.get("fact_layer_sha256") or "")
    stages: list[dict[str, Any]] = []
    for stage_id in IMPORT_SATISFIED_STAGE_IDS:
        entry = statuses.get(stage_id) if isinstance(statuses.get(stage_id), dict) else {}
        metadata = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
        complete_via_import = (
            entry.get("status") == "complete"
            and metadata.get("satisfied_by_import") is True
            and (not fact_layer_sha256 or metadata.get("fact_layer_import_sha256") == fact_layer_sha256)
        )
        stages.append(
            {
                "stage_id": stage_id,
                "status": entry.get("status") or "missing",
                "satisfied_by_import": metadata.get("satisfied_by_import") is True,
                "complete_via_import": complete_via_import,
                "display_status": (
                    "complete via import"
                    if complete_via_import
                    else str(entry.get("status") or "missing")
                ),
            }
        )
    return stages


def _imported_file_record_errors(record: dict[str, Any], *, workspace: str | Path | None) -> list[str]:
    files = record.get("imported_files")
    expected_count = record.get("imported_file_count")
    errors: list[str] = []
    if not isinstance(files, list) or not files:
        return ["runtime_manifest.fact_layer_import.imported_files must be a non-empty list."]
    if isinstance(expected_count, int) and expected_count != len(files):
        errors.append(
            "runtime_manifest.fact_layer_import.imported_file_count does not match imported_files length."
        )

    ws = Path(workspace).expanduser().resolve() if workspace is not None else None
    seen_workspace_paths: set[str] = set()
    for index, item in enumerate(files):
        if not isinstance(item, dict):
            errors.append(f"runtime_manifest.fact_layer_import.imported_files[{index}] must be an object.")
            continue
        workspace_path = str(item.get("workspace_path") or "")
        sha256 = str(item.get("sha256") or "")
        size_bytes = item.get("size_bytes")
        if not workspace_path:
            errors.append(f"runtime_manifest.fact_layer_import.imported_files[{index}].workspace_path is required.")
            continue
        if _path_text_is_unsafe(workspace_path):
            errors.append(
                f"runtime_manifest.fact_layer_import.imported_files[{index}].workspace_path must be workspace-relative."
            )
            continue
        if workspace_path in seen_workspace_paths:
            errors.append(
                f"runtime_manifest.fact_layer_import.imported_files contains duplicate workspace_path: {workspace_path}."
            )
        seen_workspace_paths.add(workspace_path)
        if not sha256:
            errors.append(f"runtime_manifest.fact_layer_import.imported_files[{index}].sha256 is required.")
        if not isinstance(size_bytes, int) or size_bytes < 0:
            errors.append(
                f"runtime_manifest.fact_layer_import.imported_files[{index}].size_bytes must be a non-negative integer."
            )
        if ws is None:
            continue
        target = (ws / workspace_path).resolve()
        try:
            target.relative_to(ws)
        except ValueError:
            errors.append(
                f"runtime_manifest.fact_layer_import.imported_files[{index}].workspace_path escapes workspace."
            )
            continue
        if not target.exists() or not target.is_file():
            errors.append(f"Imported fact-layer file is missing: {workspace_path}.")
            continue
        if isinstance(size_bytes, int) and target.stat().st_size != size_bytes:
            errors.append(f"Imported fact-layer file size mismatch: {workspace_path}.")
        if sha256:
            actual_sha256 = _sha256_file(target)
            if actual_sha256 != sha256:
                errors.append(f"Imported fact-layer file hash mismatch: {workspace_path}.")
    return errors


def _path_text_is_unsafe(path_text: str) -> bool:
    path = Path(path_text)
    return (
        not path_text
        or path_text.startswith("/")
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or (len(path.parts) > 0 and ":" in path.parts[0])
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _format_import_required_message(summary: dict[str, Any]) -> str:
    reasons = (
        "; ".join(str(item) for item in (summary.get("errors") or []) if item)
        or "missing or invalid fact-layer import"
    )
    return (
        f"{FAST_RERUN_IMPORT_REQUIRED_ERROR}: run --recipe fast-rerun requires an existing valid "
        f"runtime_manifest.fact_layer_import. First run `multi-agent-brief state import-fact-layer "
        f"--workspace <workspace> --archive <output/runs/run_id>`; reason: {reasons}"
    )


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"status": "missing", "payload": None}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"status": "error", "payload": None, "error": str(exc)}
    if not isinstance(payload, dict):
        return {"status": "error", "payload": None, "error": "JSON root is not an object"}
    return {"status": "present", "payload": payload}
