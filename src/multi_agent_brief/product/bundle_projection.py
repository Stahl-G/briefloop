"""Product-layer delivery/audit bundle projection.

This module classifies already-finalized workspace artifacts. It does not move
files, render templates, deliver reports, or approve publication.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import uuid
import zipfile
from pathlib import Path
from typing import Any

from multi_agent_brief.outputs.reader_final_gate import (
    detect_reader_residue,
    detect_reader_residue_in_docx,
)
from multi_agent_brief.outputs.finalize import (
    interpret_finalize_audit_binding,
    require_finalize_audit_binding_pass,
)
from multi_agent_brief.product.citation_profile import (
    DEFAULT_CITATION_PROFILE,
    citation_profile_report,
    normalize_citation_profile,
    validate_citation_profile_report,
)
from multi_agent_brief.product.quality_closeout import (
    CanonicalQualityPanelView,
    QualityPanelDegradation,
    QualityPanelNotMaterialized,
    interpret_quality_panel_closeout,
)
from multi_agent_brief.product.report_spec import ReportSpecLoadError, load_report_spec
from multi_agent_brief.product.template_registry import ReportTemplateRegistry

REPORT_BUNDLE_MANIFEST_SCHEMA_VERSION = "briefloop.report_bundle_manifest.v1"
_ASCII_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")
_JUNK_SUFFIXES = {".tmp", ".temp", ".swp", ".swo"}
_DELIVERY_BUNDLE_README_MEMBER = "delivery/_BUNDLE_README.md"
_AUDIT_BUNDLE_README_MEMBER = "audit/_BUNDLE_README.md"
_DELIVERY_BUNDLE_README = """# BriefLoop Delivery Bundle

Open the files in this bundle for the reader-facing report.

- `brief.md` is the local Markdown delivery when present.
- DOCX or other configured delivery files are reader-facing copies of the same finalized report surface.
- Audit/control artifacts are intentionally excluded from this bundle.

This bundle does not prove semantic truth, approve publication, or replace human review before sending.
For claim, source, gate, event, and quality traces, open the separate audit bundle.
"""
_AUDIT_BUNDLE_README = """# BriefLoop Audit Bundle

Open this bundle when a reviewer asks where a claim, warning, gate result, or delivery decision came from.

Useful starting points when present:

- `output/intermediate/quality_summary.md` for a compact quality summary.
- `output/intermediate/quality_panel.html` for a static inspection panel.
- `output/intermediate/claim_ledger.json` for recorded claims.
- `output/source_appendix.md` and `output/source_appendix_trace.md` for source trail review.
- `output/intermediate/audit_report.json`, gate reports, workflow state, runtime manifest, and event log for control records.

This bundle is not reader delivery, semantic proof, delivery approval, or release authority.
Do not edit these control files in place to change a run outcome.
"""


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
    citation_profile = _citation_profile_projection(finalize_report)
    return {
        "schema_version": REPORT_BUNDLE_MANIFEST_SCHEMA_VERSION,
        "workspace": ".",
        "source": "finalize_report_projection",
        "semantics": "delivery_and_audit_bundle_projection_only",
        "template": template,
        "citation_profile": citation_profile,
        "packaging_hygiene": hygiene,
        "supplemental_guidance": {
            "status": "available_when_archives_are_written",
            "semantics": "supplemental_guidance_non_authoritative_not_counted_as_artifacts",
            "artifact_count_policy": "excluded_from_delivery_bundle_and_audit_bundle_artifact_count",
            "delivery_archive_member": _DELIVERY_BUNDLE_README_MEMBER,
            "audit_archive_member": _AUDIT_BUNDLE_README_MEMBER,
        },
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
            "delivery_approval",
            "gate_bypass",
            "publication_authorization",
            "semantic_support_assessment",
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
    manifest["manifest_path"] = _workspace_relative(ws, target)
    if write_archives:
        cleanup_warning = _write_bundle_publication(ws, target, manifest)
    else:
        cleanup_warning = _write_manifest_publication(target, manifest)
    if cleanup_warning is not None:
        manifest["publication_cleanup_warning"] = cleanup_warning
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


def _write_bundle_publication(
    workspace: Path,
    manifest_path: Path,
    manifest: dict[str, Any],
) -> dict[str, str] | None:
    output_dir = workspace / "output"
    delivery_path = output_dir / "delivery_bundle.zip"
    audit_path = output_dir / "audit_bundle.zip"
    delivery_staged: Path | None = None
    audit_staged: Path | None = None
    manifest_staged: Path | None = None
    delivery_records = _records_from_bundle(manifest, "delivery_bundle")
    audit_records = _records_from_bundle(manifest, "audit_bundle")
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        delivery_staged = _staged_path(delivery_path)
        audit_staged = _staged_path(audit_path)
        manifest_staged = _staged_path(manifest_path)
        _write_zip_from_records(
            workspace=workspace,
            archive_path=delivery_staged,
            records=delivery_records,
            surface="delivery",
        )
        _write_zip_from_records(
            workspace=workspace,
            archive_path=audit_staged,
            records=audit_records,
            surface="audit",
        )
        manifest["bundle_archives"] = {
            "status": "generated",
            "semantics": "clean_archives_from_report_bundle_manifest",
            "delivery": _archive_record(
                workspace,
                delivery_path,
                artifact_count=len(delivery_records),
                content_path=delivery_staged,
            ),
            "audit": _archive_record(
                workspace,
                audit_path,
                artifact_count=len(audit_records),
                content_path=audit_staged,
            ),
        }
        manifest_bytes = _manifest_bytes(manifest)
        _write_staged_bytes(manifest_staged, manifest_bytes)
        _verify_staged_bundle_publication(
            manifest=manifest,
            manifest_path=manifest_staged,
            expected_manifest_bytes=manifest_bytes,
            delivery_path=delivery_staged,
            audit_path=audit_staged,
        )
        return _publish_bundle_generation(
            manifest=manifest,
            delivery_staged=delivery_staged,
            delivery_path=delivery_path,
            audit_staged=audit_staged,
            audit_path=audit_path,
            manifest_staged=manifest_staged,
            manifest_path=manifest_path,
        )
    except ReportBundleProjectionError:
        _cleanup_paths(delivery_staged, audit_staged, manifest_staged)
        raise
    except Exception as exc:
        _cleanup_paths(delivery_staged, audit_staged, manifest_staged)
        raise ReportBundleProjectionError(
            "report bundle publication staging failed."
        ) from exc


def _write_manifest_publication(
    target: Path,
    manifest: dict[str, Any],
) -> dict[str, str] | None:
    manifest_bytes = _manifest_bytes(manifest)
    manifest_staged: Path | None = None
    try:
        manifest_staged = _staged_path(target)
        _write_staged_bytes(manifest_staged, manifest_bytes)
        _verify_staged_manifest(
            target=manifest_staged,
            expected_bytes=manifest_bytes,
        )
    except Exception as exc:
        _cleanup_paths(manifest_staged)
        raise ReportBundleProjectionError(
            "report bundle manifest staging failed."
        ) from exc

    try:
        os.replace(manifest_staged, target)
    except Exception as exc:
        _cleanup_paths(manifest_staged)
        raise ReportBundleProjectionError(
            "report_bundle_publication_failed"
        ) from exc
    if _cleanup_paths(manifest_staged):
        return _publication_cleanup_warning()
    return None


def _manifest_bytes(manifest: dict[str, Any]) -> bytes:
    return (
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _staged_path(target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    return target.parent / f".{target.name}.{uuid.uuid4().hex}.tmp"


def _backup_path(target: Path) -> Path:
    return target.parent / f".{target.name}.{uuid.uuid4().hex}.backup"


def _write_staged_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _publish_bundle_generation(
    *,
    manifest: dict[str, Any],
    delivery_staged: Path,
    delivery_path: Path,
    audit_staged: Path,
    audit_path: Path,
    manifest_staged: Path,
    manifest_path: Path,
) -> dict[str, str] | None:
    states: list[dict[str, Any]] = [
        {
            "staged": staged,
            "target": target,
            "existed": target.exists(),
            "backup": _backup_path(target) if target.exists() else None,
        }
        for staged, target in (
            (delivery_staged, delivery_path),
            (audit_staged, audit_path),
        )
    ]
    attempted: list[dict[str, Any]] = []
    try:
        for state in states:
            target = state["target"]
            backup = state["backup"]
            if backup is not None:
                shutil.copyfile(target, backup)
        for state in states:
            attempted.append(state)
            os.replace(state["staged"], state["target"])
        _verify_bundle_archives(
            manifest=manifest,
            delivery_path=delivery_path,
            audit_path=audit_path,
        )
        # This atomic replacement is the only publication commit point.
        os.replace(manifest_staged, manifest_path)
    except Exception as exc:
        rollback_failures = _rollback_zip_targets(attempted)
        cleanup_paths = [delivery_staged, audit_staged, manifest_staged]
        if not rollback_failures:
            cleanup_paths.extend(
                state["backup"]
                for state in states
                if isinstance(state["backup"], Path)
            )
        _cleanup_paths(*cleanup_paths)
        if rollback_failures:
            raise ReportBundleProjectionError(
                "report_bundle_publication_rollback_incomplete"
            ) from exc
        raise ReportBundleProjectionError(
            "report_bundle_publication_failed"
        ) from exc
    cleanup_failed = _cleanup_paths(
        delivery_staged,
        audit_staged,
        manifest_staged,
        *(state["backup"] for state in states),
    )
    if cleanup_failed:
        return _publication_cleanup_warning()
    return None


def _rollback_zip_targets(
    states: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    for state in reversed(states):
        target = state["target"]
        try:
            if state["existed"]:
                os.replace(state["backup"], target)
            else:
                target.unlink(missing_ok=True)
        except Exception as exc:
            failed_state = dict(state)
            failed_state["rollback_error_type"] = type(exc).__name__
            failures.append(failed_state)
    return failures


def _cleanup_paths(*paths: Path | None) -> bool:
    cleanup_failed = False
    for path in paths:
        if path is None:
            continue
        try:
            path.unlink(missing_ok=True)
        except Exception:
            cleanup_failed = True
    return cleanup_failed


def _publication_cleanup_warning() -> dict[str, str]:
    return {
        "reason_code": "publication_cleanup_warning",
        "boundary": "post_commit_housekeeping_only_not_bundle_authority",
    }


def _verify_staged_manifest(*, target: Path, expected_bytes: bytes) -> None:
    if target.read_bytes() != expected_bytes:
        raise ReportBundleProjectionError(
            "staged report bundle manifest does not match expected bytes."
        )


def _verify_staged_bundle_publication(
    *,
    manifest: dict[str, Any],
    manifest_path: Path,
    expected_manifest_bytes: bytes,
    delivery_path: Path,
    audit_path: Path,
) -> None:
    _verify_staged_manifest(
        target=manifest_path,
        expected_bytes=expected_manifest_bytes,
    )
    _verify_bundle_archives(
        manifest=manifest,
        delivery_path=delivery_path,
        audit_path=audit_path,
    )


def _verify_bundle_archives(
    *,
    manifest: dict[str, Any],
    delivery_path: Path,
    audit_path: Path,
) -> None:
    archives = manifest.get("bundle_archives")
    archives = archives if isinstance(archives, dict) else {}
    for key, path in (("delivery", delivery_path), ("audit", audit_path)):
        record = archives.get(key)
        if not isinstance(record, dict):
            raise ReportBundleProjectionError(
                "published report bundle archive record is missing."
            )
        if record.get("sha256") != _sha256_file(path) or record.get(
            "size_bytes"
        ) != path.stat().st_size:
            raise ReportBundleProjectionError(
                "published report bundle archive does not match manifest."
            )


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
        _write_bundle_readme(zf, surface=surface)
        for record in sorted(records, key=lambda item: str(item.get("path") or "")):
            rel = str(record.get("path") or "").strip()
            if not rel:
                continue
            source = _resolve_workspace_path(workspace, rel)
            content = source.read_bytes()
            expected_sha256 = str(record.get("sha256") or "")
            actual_sha256 = hashlib.sha256(content).hexdigest()
            if not expected_sha256 or actual_sha256 != expected_sha256:
                raise ReportBundleProjectionError(
                    f"bundle artifact changed after manifest projection: {rel}"
                )
            arcname = _archive_member_name(rel, surface=surface)
            info = zipfile.ZipInfo(arcname)
            info.date_time = (1980, 1, 1, 0, 0, 0)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, content)


def _write_bundle_readme(zf: zipfile.ZipFile, *, surface: str) -> None:
    if surface == "delivery":
        arcname = _DELIVERY_BUNDLE_README_MEMBER
        text = _DELIVERY_BUNDLE_README
    elif surface == "audit":
        arcname = _AUDIT_BUNDLE_README_MEMBER
        text = _AUDIT_BUNDLE_README
    else:
        return
    info = zipfile.ZipInfo(arcname)
    info.date_time = (1980, 1, 1, 0, 0, 0)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    zf.writestr(info, text)


def _archive_member_name(rel_path: str, *, surface: str) -> str:
    rel = Path(rel_path).as_posix()
    if surface == "delivery" and rel.startswith("output/delivery/"):
        rel = rel.removeprefix("output/delivery/")
    return f"{surface}/{rel}".replace("//", "/")


def _archive_record(
    workspace: Path,
    path: Path,
    *,
    artifact_count: int,
    content_path: Path | None = None,
) -> dict[str, Any]:
    content = content_path or path
    return {
        "path": _workspace_relative(workspace, path),
        "sha256": _sha256_file(content),
        "size_bytes": content.stat().st_size,
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
    except (OSError, UnicodeDecodeError) as exc:
        raise ReportBundleProjectionError(f"finalize_report.json is unreadable: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ReportBundleProjectionError(f"finalize_report.json is invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ReportBundleProjectionError("finalize_report.json must contain an object.")
    if payload.get("status") != "pass":
        raise ReportBundleProjectionError("finalize_report.json status must be pass.")
    reader_clean = payload.get("reader_clean")
    if not isinstance(reader_clean, dict) or reader_clean.get("status") != "pass":
        raise ReportBundleProjectionError("finalize_report.json reader_clean.status must be pass.")
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
        _validate_reader_delivery_artifact(workspace, path)
        records.append(_artifact_record(workspace, path, role="reader_delivery"))
    if not records:
        raise ReportBundleProjectionError(
            "finalize_report.json delivery_artifacts did not include packageable reader artifacts."
        )
    return records


def _validate_reader_delivery_artifact(workspace: Path, path: Path) -> None:
    rel = _workspace_relative(workspace, path)
    suffix = path.suffix.lower()
    if suffix == ".docx":
        result = detect_reader_residue_in_docx(path, artifact=rel)
    else:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise ReportBundleProjectionError(f"reader delivery artifact is unreadable: {rel}: {exc}") from exc
        result = detect_reader_residue(text, artifact=rel)
    if result.status != "pass":
        finding_kinds = sorted({finding.kind for finding in result.findings})
        detail = ", ".join(finding_kinds) or "reader_residue"
        raise ReportBundleProjectionError(
            f"reader delivery artifact failed reader-clean residue scan: {rel}: {detail}"
        )


def _audit_records(
    workspace: Path,
    finalize_report: dict[str, Any],
    *,
    hygiene: dict[str, Any],
) -> list[dict[str, Any]]:
    quality_verdict = interpret_quality_panel_closeout(workspace=workspace)
    quality_candidates: list[tuple[str, Path, str | None]] = []
    if isinstance(quality_verdict, CanonicalQualityPanelView):
        quality_candidates = [
            (
                artifact_id,
                quality_verdict.artifact_paths[artifact_id],
                quality_verdict.artifact_sha256[artifact_id],
            )
            for artifact_id in ("quality_panel", "quality_summary", "quality_panel_html")
        ]
    elif isinstance(quality_verdict, QualityPanelDegradation):
        raise ReportBundleProjectionError(
            "quality projection artifacts are not canonically bound: "
            f"{quality_verdict.reason_code}; rerun briefloop quality summarize"
        )
    elif not isinstance(quality_verdict, QualityPanelNotMaterialized):
        raise ReportBundleProjectionError(
            "quality projection artifact interpretation failed; "
            "rerun briefloop quality summarize"
        )

    candidates = [
        ("finalize_report", workspace / "output" / "intermediate" / "finalize_report.json", None),
        ("claim_ledger", workspace / "output" / "intermediate" / "claim_ledger.json", None),
        ("audited_brief", workspace / "output" / "intermediate" / "audited_brief.md", None),
        ("audit_report", workspace / "output" / "intermediate" / "audit_report.json", None),
        ("artifact_registry", workspace / "output" / "intermediate" / "artifact_registry.json", None),
        ("runtime_manifest", workspace / "output" / "intermediate" / "runtime_manifest.json", None),
        ("workflow_state", workspace / "output" / "intermediate" / "workflow_state.json", None),
        ("event_log", workspace / "output" / "intermediate" / "event_log.jsonl", None),
        (
            "auditor_gate_report",
            workspace / "output" / "intermediate" / "gates" / "auditor_quality_gate_report.json",
            None,
        ),
        (
            "finalize_gate_report",
            workspace / "output" / "intermediate" / "gates" / "finalize_quality_gate_report.json",
            None,
        ),
        ("source_appendix", workspace / "output" / "source_appendix.md", None),
        (
            "source_appendix_trace",
            _optional_report_path(workspace, finalize_report, "source_appendix_trace"),
            None,
        ),
        ("atomic_claim_graph", workspace / "output" / "intermediate" / "atomic_claim_graph.json", None),
        (
            "evidence_span_registry",
            workspace / "output" / "intermediate" / "evidence_span_registry.json",
            None,
        ),
        ("claim_support_matrix", workspace / "output" / "intermediate" / "claim_support_matrix.json", None),
        (
            "semantic_assessment_report",
            workspace / "output" / "intermediate" / "semantic_assessment_report.json",
            None,
        ),
        *quality_candidates,
    ]
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    delivery_root = (workspace / "output" / "delivery").resolve()
    for role, path, expected_sha256 in candidates:
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
        records.append(
            _artifact_record(
                workspace,
                resolved,
                role=role,
                expected_sha256=expected_sha256,
            )
        )
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


def _citation_profile_projection(finalize_report: dict[str, Any]) -> dict[str, Any]:
    if "citation_profile" not in finalize_report:
        report = citation_profile_report(
            profile=DEFAULT_CITATION_PROFILE,
            source="legacy_finalize_report_default",
        )
        report["status"] = "legacy_default"
        report["semantics"] = "reader_delivery_citation_projection_and_audit_trace_split"
        return report

    raw_profile = finalize_report.get("citation_profile")
    profile = normalize_citation_profile(raw_profile)
    if not profile:
        raise ReportBundleProjectionError("finalize_report citation profile invalid: citation_profile")
    report = citation_profile_report(
        profile=profile,
        source=str(finalize_report.get("citation_profile_source") or "finalize_report"),
        warnings=[
            str(item)
            for item in finalize_report.get("citation_profile_warnings", [])
            if isinstance(item, str)
        ],
    )
    for source_field, target_field in (
        ("citation_profile_runtime_effect", "runtime_effect"),
        ("citation_profile_reader_citation_style", "reader_citation_style"),
        ("citation_profile_reader_metadata_level", "reader_metadata_level"),
        ("citation_profile_audit_trace_level", "audit_trace_level"),
    ):
        value = finalize_report.get(source_field)
        if value is not None and str(value).strip() != str(report.get(target_field) or ""):
            raise ReportBundleProjectionError(
                f"finalize_report citation profile invalid: {source_field}"
            )
    for source_field, target_field in (
        ("citation_profile_delivery_exposes_internal_ids", "delivery_exposes_internal_ids"),
        ("citation_profile_delivery_exposes_local_paths", "delivery_exposes_local_paths"),
        ("citation_profile_audit_bundle_keeps_trace", "audit_bundle_keeps_trace"),
    ):
        if source_field in finalize_report and finalize_report[source_field] is not report[target_field]:
            raise ReportBundleProjectionError(
                f"finalize_report citation profile invalid: {source_field}"
            )
    reason = validate_citation_profile_report(report)
    if reason:
        raise ReportBundleProjectionError(f"finalize_report citation profile invalid: {reason}")
    report["status"] = "available"
    report["semantics"] = "reader_delivery_citation_projection_and_audit_trace_split"
    return report


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


def _artifact_record(
    workspace: Path,
    path: Path,
    *,
    role: str,
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    actual_sha256 = _sha256_file(path)
    if expected_sha256 is not None and actual_sha256 != expected_sha256:
        raise ReportBundleProjectionError(
            "quality projection artifact changed after canonical interpretation: "
            f"{_workspace_relative(workspace, path)}"
        )
    record = {
        "path": _workspace_relative(workspace, path),
        "role": role,
        "sha256": actual_sha256,
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
