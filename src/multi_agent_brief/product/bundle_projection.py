"""Product-layer delivery/audit bundle projection.

This module classifies already-finalized workspace artifacts. It does not move
files, render templates, deliver reports, or approve publication.
"""

from __future__ import annotations

import hashlib
import json
import re
import zipfile
from pathlib import Path
from typing import Any

from multi_agent_brief.outputs.finalize import (
    interpret_finalize_audit_binding,
    require_finalize_audit_binding_pass,
)
from multi_agent_brief.product.report_spec import ReportSpecLoadError, load_report_spec
from multi_agent_brief.product.template_registry import ReportTemplateRegistry

REPORT_BUNDLE_MANIFEST_SCHEMA_VERSION = "briefloop.report_bundle_manifest.v1"
_ASCII_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")
_JUNK_SUFFIXES = {".tmp", ".temp", ".swp", ".swo"}


class ReportBundleProjectionError(Exception):
    """Raised when a bundle projection cannot be built safely."""


def build_report_bundle_manifest(
    *,
    workspace: str | Path,
    template_registry: ReportTemplateRegistry | None = None,
) -> dict[str, Any]:
    ws = Path(workspace).expanduser().resolve()
    finalize_report = _load_finalize_report(ws)
    hygiene: dict[str, Any] = {"status": "clean", "excluded_artifacts": []}
    delivery_records = _delivery_records(ws, finalize_report, hygiene=hygiene)
    audit_records = _audit_records(ws, finalize_report, hygiene=hygiene)
    if hygiene["excluded_artifacts"]:
        hygiene["status"] = "excluded_packaging_junk"
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
        "packaging_hygiene": hygiene,
        "bundle_archives": {"status": "not_requested"},
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
    write_archives: bool = False,
) -> dict[str, Any]:
    ws = Path(workspace).expanduser().resolve()
    target = _manifest_output_path(ws, output_path)
    _raise_if_reserved_archive_output(ws, target)
    manifest = build_report_bundle_manifest(workspace=ws, template_registry=template_registry)
    if write_archives:
        manifest["bundle_archives"] = _write_bundle_archives(ws, manifest)
    manifest["manifest_path"] = _workspace_relative(ws, target)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def _manifest_output_path(workspace: Path, output_path: str | Path | None) -> Path:
    target = Path(output_path).expanduser() if output_path else workspace / "output" / "report_bundle_manifest.json"
    if not target.is_absolute():
        target = workspace / target
    target = target.resolve()
    try:
        _workspace_relative(workspace, target)
    except ValueError as exc:
        raise ReportBundleProjectionError("bundle manifest output must stay inside the workspace.") from exc
    return target


def _raise_if_reserved_archive_output(workspace: Path, target: Path) -> None:
    reserved = {
        (workspace / "output" / "delivery_bundle.zip").resolve(),
        (workspace / "output" / "audit_bundle.zip").resolve(),
    }
    if target in reserved:
        rel = _workspace_relative(workspace, target)
        raise ReportBundleProjectionError(
            f"bundle manifest output path is reserved for clean bundle archives: {rel}"
        )


def _write_bundle_archives(workspace: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    output_dir = workspace / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    delivery_path = output_dir / "delivery_bundle.zip"
    audit_path = output_dir / "audit_bundle.zip"
    delivery_records = _records_from_bundle(manifest, "delivery_bundle")
    audit_records = _records_from_bundle(manifest, "audit_bundle")
    _write_zip_from_records(
        workspace=workspace,
        archive_path=delivery_path,
        records=delivery_records,
        surface="delivery",
    )
    _write_zip_from_records(
        workspace=workspace,
        archive_path=audit_path,
        records=audit_records,
        surface="audit",
    )
    return {
        "status": "generated",
        "semantics": "clean_archives_from_report_bundle_manifest",
        "delivery": _archive_record(workspace, delivery_path, artifact_count=len(delivery_records)),
        "audit": _archive_record(workspace, audit_path, artifact_count=len(audit_records)),
    }


def _records_from_bundle(manifest: dict[str, Any], key: str) -> list[dict[str, Any]]:
    bundle = manifest.get(key)
    artifacts = bundle.get("artifacts") if isinstance(bundle, dict) else None
    if not isinstance(artifacts, list):
        return []
    return [item for item in artifacts if isinstance(item, dict)]


def _write_zip_from_records(
    *,
    workspace: Path,
    archive_path: Path,
    records: list[dict[str, Any]],
    surface: str,
) -> None:
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for record in sorted(records, key=lambda item: str(item.get("path") or "")):
            rel = str(record.get("path") or "").strip()
            if not rel:
                continue
            source = _resolve_workspace_path(workspace, rel)
            arcname = _archive_member_name(rel, surface=surface)
            info = zipfile.ZipInfo(arcname)
            info.date_time = (1980, 1, 1, 0, 0, 0)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, source.read_bytes())


def _archive_member_name(rel_path: str, *, surface: str) -> str:
    rel = Path(rel_path).as_posix()
    if surface == "delivery" and rel.startswith("output/delivery/"):
        rel = rel.removeprefix("output/delivery/")
    return f"{surface}/{rel}".replace("//", "/")


def _archive_record(workspace: Path, path: Path, *, artifact_count: int) -> dict[str, Any]:
    return {
        "path": _workspace_relative(workspace, path),
        "sha256": _sha256_file(path),
        "size_bytes": path.stat().st_size,
        "artifact_count": artifact_count,
    }


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
    audit_binding_reasons = require_finalize_audit_binding_pass(
        interpret_finalize_audit_binding(
            workspace=workspace,
            finalize_report=payload,
        )
    )
    if audit_binding_reasons:
        raise ReportBundleProjectionError(
            "finalize_report.json audit_binding must pass before building report bundles: "
            + "; ".join(audit_binding_reasons)
        )
    return payload


def _delivery_records(
    workspace: Path,
    finalize_report: dict[str, Any],
    *,
    hygiene: dict[str, Any],
) -> list[dict[str, Any]]:
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
        if _is_packaging_junk(path):
            _record_hygiene_exclusion(workspace, path, hygiene=hygiene, surface="delivery")
            continue
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
    if not records:
        raise ReportBundleProjectionError(
            "finalize_report.json delivery_artifacts did not include packageable reader artifacts."
        )
    return records


def _audit_records(
    workspace: Path,
    finalize_report: dict[str, Any],
    *,
    hygiene: dict[str, Any],
) -> list[dict[str, Any]]:
    candidates = [
        ("finalize_report", workspace / "output" / "intermediate" / "finalize_report.json"),
        ("claim_ledger", workspace / "output" / "intermediate" / "claim_ledger.json"),
        ("audited_brief", workspace / "output" / "intermediate" / "audited_brief.md"),
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
        if _is_packaging_junk(resolved):
            _record_hygiene_exclusion(workspace, resolved, hygiene=hygiene, surface="audit")
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
    record = {
        "path": _workspace_relative(workspace, path),
        "role": role,
        "sha256": _sha256_file(path),
        "size_bytes": path.stat().st_size,
    }
    fallback = _ascii_fallback_name(path.name)
    if fallback != path.name:
        record["ascii_fallback_name"] = fallback
    return record


def _is_packaging_junk(path: Path) -> bool:
    parts = set(path.parts)
    name = path.name
    lower = name.lower()
    return (
        "__MACOSX" in parts
        or name == ".DS_Store"
        or name.startswith("~$")
        or name.startswith(".~lock.")
        or name.endswith("~")
        or name.endswith("#")
        or lower in {"thumbs.db", "desktop.ini"}
        or lower.endswith(tuple(_JUNK_SUFFIXES))
    )


def _record_hygiene_exclusion(
    workspace: Path,
    path: Path,
    *,
    hygiene: dict[str, Any],
    surface: str,
) -> None:
    exclusions = hygiene.setdefault("excluded_artifacts", [])
    exclusions.append({
        "path": _workspace_relative(workspace, path),
        "surface": surface,
        "reason": "packaging_junk",
    })


def _ascii_fallback_name(filename: str) -> str:
    path = Path(filename)
    suffix = path.suffix
    raw_stem = path.stem or filename
    encoded_stem = raw_stem.encode("ascii", "ignore").decode("ascii")
    fallback_stem = _ASCII_SAFE_RE.sub("-", encoded_stem).strip(".-")
    safe_suffix = suffix if suffix and suffix.encode("ascii", "ignore").decode("ascii") == suffix else ""
    digest = hashlib.sha256(filename.encode("utf-8")).hexdigest()[:12]
    if fallback_stem:
        return f"{fallback_stem}-{digest}{safe_suffix}"
    return f"artifact-{digest}{safe_suffix}"


def _workspace_relative(workspace: Path, path: Path) -> str:
    return path.resolve().relative_to(workspace).as_posix()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()
