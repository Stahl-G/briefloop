"""Product-layer delivery/audit bundle projection.

This module classifies already-finalized workspace artifacts. It does not move
files, render templates, deliver reports, or approve publication.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from multi_agent_brief.product.report_spec import ReportSpecLoadError, load_report_spec
from multi_agent_brief.product.template_registry import ReportTemplateRegistry

REPORT_BUNDLE_MANIFEST_SCHEMA_VERSION = "briefloop.report_bundle_manifest.v1"


class ReportBundleProjectionError(Exception):
    """Raised when a bundle projection cannot be built safely."""


def build_report_bundle_manifest(
    *,
    workspace: str | Path,
    template_registry: ReportTemplateRegistry | None = None,
) -> dict[str, Any]:
    ws = Path(workspace).expanduser().resolve()
    finalize_report = _load_finalize_report(ws)
    delivery_records = _delivery_records(ws, finalize_report)
    audit_records = _audit_records(ws, finalize_report)
    template = _template_projection(
        ws,
        template_registry=template_registry or ReportTemplateRegistry.from_package(),
    )
    return {
        "schema_version": REPORT_BUNDLE_MANIFEST_SCHEMA_VERSION,
        "workspace": ".",
        "source": "finalize_report_projection",
        "semantics": "delivery_and_audit_bundle_projection_only",
        "template": template,
        "delivery_bundle": {
            "status": "available",
            "semantics": "reader_facing_artifacts_only",
            "artifact_count": len(delivery_records),
            "artifacts": delivery_records,
        },
        "audit_bundle": {
            "status": "available",
            "semantics": "audit_control_artifacts_only_not_reader_delivery",
            "artifact_count": len(audit_records),
            "artifacts": audit_records,
        },
        "non_goals": [
            "template_rendering",
            "delivery_approval",
            "gate_bypass",
            "publication_authorization",
        ],
    }


def write_report_bundle_manifest(
    *,
    workspace: str | Path,
    output_path: str | Path | None = None,
    template_registry: ReportTemplateRegistry | None = None,
) -> dict[str, Any]:
    ws = Path(workspace).expanduser().resolve()
    manifest = build_report_bundle_manifest(workspace=ws, template_registry=template_registry)
    target = Path(output_path).expanduser() if output_path else ws / "output" / "report_bundle_manifest.json"
    if not target.is_absolute():
        target = ws / target
    target = target.resolve()
    try:
        manifest["manifest_path"] = _workspace_relative(ws, target)
    except ValueError as exc:
        raise ReportBundleProjectionError("bundle manifest output must stay inside the workspace.") from exc
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def _load_finalize_report(workspace: Path) -> dict[str, Any]:
    path = workspace / "output" / "intermediate" / "finalize_report.json"
    if not path.exists():
        raise ReportBundleProjectionError(
            "finalize_report.json is required before building report bundles."
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ReportBundleProjectionError(f"finalize_report.json is invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ReportBundleProjectionError("finalize_report.json must contain an object.")
    if payload.get("status") != "pass":
        raise ReportBundleProjectionError("finalize_report.json status must be pass.")
    return payload


def _delivery_records(workspace: Path, finalize_report: dict[str, Any]) -> list[dict[str, Any]]:
    raw_artifacts = finalize_report.get("delivery_artifacts")
    if not isinstance(raw_artifacts, list) or not raw_artifacts:
        raise ReportBundleProjectionError("finalize_report.json delivery_artifacts must be non-empty.")
    raw_hashes = finalize_report.get("delivery_artifact_sha256")
    if not isinstance(raw_hashes, dict) or not raw_hashes:
        raise ReportBundleProjectionError(
            "finalize_report.json delivery_artifact_sha256 must be a non-empty object."
        )
    hashes = raw_hashes
    records: list[dict[str, Any]] = []
    delivery_root = (workspace / "output" / "delivery").resolve()
    for raw in raw_artifacts:
        if not isinstance(raw, str) or not raw.strip():
            raise ReportBundleProjectionError("finalize_report.json contains an invalid delivery artifact path.")
        path = _resolve_workspace_path(workspace, raw)
        try:
            path.relative_to(delivery_root)
        except ValueError as exc:
            raise ReportBundleProjectionError(
                "delivery artifacts must be under output/delivery/."
            ) from exc
        expected_sha = _hash_for_path(hashes, raw=raw, workspace=workspace, path=path)
        if not expected_sha:
            raise ReportBundleProjectionError(
                f"delivery artifact hash missing: {_workspace_relative(workspace, path)}"
            )
        actual_sha = _sha256_file(path)
        if expected_sha != actual_sha:
            raise ReportBundleProjectionError(
                f"delivery artifact hash mismatch: {_workspace_relative(workspace, path)}"
            )
        records.append(_artifact_record(workspace, path, role="reader_delivery"))
    return records


def _audit_records(workspace: Path, finalize_report: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = [
        ("finalize_report", workspace / "output" / "intermediate" / "finalize_report.json"),
        ("claim_ledger", workspace / "output" / "intermediate" / "claim_ledger.json"),
        ("audit_report", workspace / "output" / "intermediate" / "audit_report.json"),
        ("artifact_registry", workspace / "output" / "intermediate" / "artifact_registry.json"),
        ("runtime_manifest", workspace / "output" / "intermediate" / "runtime_manifest.json"),
        ("workflow_state", workspace / "output" / "intermediate" / "workflow_state.json"),
        ("event_log", workspace / "output" / "intermediate" / "event_log.jsonl"),
        ("auditor_gate_report", workspace / "output" / "intermediate" / "gates" / "auditor_quality_gate_report.json"),
        (
            "finalize_gate_report",
            workspace / "output" / "intermediate" / "gates" / "finalize_quality_gate_report.json",
        ),
        ("source_appendix", workspace / "output" / "source_appendix.md"),
        ("source_appendix_trace", _optional_report_path(workspace, finalize_report, "source_appendix_trace")),
        ("atomic_claim_graph", workspace / "output" / "intermediate" / "atomic_claim_graph.json"),
        ("evidence_span_registry", workspace / "output" / "intermediate" / "evidence_span_registry.json"),
        ("claim_support_matrix", workspace / "output" / "intermediate" / "claim_support_matrix.json"),
        ("semantic_assessment_report", workspace / "output" / "intermediate" / "semantic_assessment_report.json"),
    ]
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    delivery_root = (workspace / "output" / "delivery").resolve()
    for role, path in candidates:
        if path is None or not path.exists() or not path.is_file():
            continue
        resolved = path.resolve()
        try:
            resolved.relative_to(delivery_root)
            continue
        except ValueError:
            pass
        rel = _workspace_relative(workspace, resolved)
        if rel in seen:
            continue
        seen.add(rel)
        records.append(_artifact_record(workspace, resolved, role=role))
    return records


def _template_projection(
    workspace: Path,
    *,
    template_registry: ReportTemplateRegistry,
) -> dict[str, Any]:
    spec_path = workspace / "report_spec.yaml"
    if not spec_path.exists():
        return {"status": "not_available", "reason": "report_spec_missing"}
    try:
        spec = load_report_spec(spec_path)
    except (OSError, ReportSpecLoadError) as exc:
        return {"status": "invalid_report_spec", "reason": str(exc)}
    report_type = str(spec.get("report_type") or "").strip()
    template = template_registry.get_by_report_type(report_type)
    if template is None:
        return {"status": "not_available", "report_type": report_type, "reason": "template_missing"}
    return {
        "status": "available",
        "template_id": template.template_id,
        "report_type": template.report_type,
        "section_order": list(template.section_order),
        "semantics": "stable_section_order_only_not_renderer",
    }


def _optional_report_path(workspace: Path, report: dict[str, Any], field: str) -> Path | None:
    raw = report.get(field)
    if not isinstance(raw, str) or not raw.strip():
        return None
    return _resolve_workspace_path(workspace, raw)


def _resolve_workspace_path(workspace: Path, raw: str) -> Path:
    path = Path(raw).expanduser()
    resolved = path.resolve() if path.is_absolute() else (workspace / path).resolve()
    try:
        resolved.relative_to(workspace)
    except ValueError as exc:
        raise ReportBundleProjectionError(f"artifact path escapes workspace: {raw}") from exc
    if not resolved.exists() or not resolved.is_file():
        raise ReportBundleProjectionError(f"artifact path is missing: {raw}")
    return resolved


def _hash_for_path(
    hashes: dict[str, Any],
    *,
    raw: str,
    workspace: Path,
    path: Path,
) -> str:
    rel = _workspace_relative(workspace, path)
    for key in (raw, rel, path.as_posix(), str(path)):
        value = hashes.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _artifact_record(workspace: Path, path: Path, *, role: str) -> dict[str, Any]:
    return {
        "path": _workspace_relative(workspace, path),
        "role": role,
        "sha256": _sha256_file(path),
        "size_bytes": path.stat().st_size,
    }


def _workspace_relative(workspace: Path, path: Path) -> str:
    return path.resolve().relative_to(workspace).as_posix()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()
