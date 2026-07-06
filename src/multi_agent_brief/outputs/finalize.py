from __future__ import annotations

import json
import re
import shutil
import hashlib
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from multi_agent_brief.contracts.schemas.audit_report import AuditReportContract
from multi_agent_brief.outputs.naming import render_output_stem
from multi_agent_brief.outputs.reader_projection import (
    build_reader_clean_report,
    build_reader_projection,
)
from multi_agent_brief.outputs.source_appendix import cited_claim_ids
from multi_agent_brief.product.policy_gate_adapter import policy_forbidden_phrases
from multi_agent_brief.product.quality_closeout import quality_panel_closeout_projection
from multi_agent_brief.product.template_conformance import project_workspace_report_template_conformance

_AUDIT_CLAIM_ID_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:CL-\d{3,}|CLM-\d{3,}|SYN_CLAIM_[A-Z0-9_-]+|CLAIM_[A-Z0-9_-]+)(?![A-Za-z0-9_])"
)


@dataclass
class FinalizeResult:
    """Result of the reader-facing delivery finalization step."""

    status: str
    audited_brief: str
    reader_brief: str
    named_reader_brief: str = ""
    reader_docx: str = ""
    named_reader_docx: str = ""
    docx_generation: str = "not_requested"
    stripped_src_marker_count: int = 0
    source_appendix: str = ""
    source_appendix_generation: str = "not_requested"
    source_appendix_requested_by: str = "none"
    source_appendix_mode: str = "separate"
    source_appendix_source_count: int = 0
    source_appendix_cited_claim_count: int = 0
    source_appendix_resolved_claim_count: int = 0
    source_appendix_warnings: list[str] | None = None
    source_appendix_claim_map: dict[str, dict[str, str]] = field(default_factory=dict)
    source_appendix_trace: str = ""
    source_appendix_trace_generation: str = "not_available"
    source_appendix_trace_source_count: int = 0
    source_appendix_trace_span_count: int = 0
    source_appendix_trace_warnings: list[str] | None = None
    delivery_markdown: str = ""
    delivery_docx: str = ""
    delivery_latest_dir: str = ""
    delivery_artifacts: list[str] = field(default_factory=list)
    delivery_artifact_sha256: dict[str, str] = field(default_factory=dict)
    delivery_snapshot_dir: str = ""
    delivery_snapshot_artifacts: list[str] = field(default_factory=list)
    delivery_snapshot_artifact_sha256: dict[str, str] = field(default_factory=dict)
    delivery_snapshot_semantics: str = "convenience_copy_not_immutable_archive"
    delivery_snapshot_error: str = ""
    template_rendering: dict[str, Any] = field(default_factory=dict)
    report_template_conformance: dict[str, Any] = field(default_factory=dict)
    reader_clean: dict[str, Any] | None = None
    audit_binding: dict[str, Any] | None = None
    policy_gate_adapter: dict[str, Any] = field(default_factory=dict)
    citation_profile: str = "executive"
    citation_profile_source: str = "default"
    citation_profile_runtime_effect: str = "citation_profile_resolution_only"
    citation_profile_reader_citation_style: str = "source_label"
    citation_profile_reader_metadata_level: str = "low_interference"
    citation_profile_audit_trace_level: str = "complete_when_available"
    citation_profile_delivery_exposes_internal_ids: bool = False
    citation_profile_delivery_exposes_local_paths: bool = False
    citation_profile_audit_bundle_keeps_trace: bool = True
    citation_profile_warnings: list[str] = field(default_factory=list)
    quality_panel_closeout: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if data["source_appendix_warnings"] is None:
            data["source_appendix_warnings"] = []
        if data["source_appendix_trace_warnings"] is None:
            data["source_appendix_trace_warnings"] = []
        if data["reader_clean"] is None:
            data["reader_clean"] = _empty_reader_clean_report()
        if data["audit_binding"] is None:
            data["audit_binding"] = _empty_audit_binding_report()
        if not data["quality_panel_closeout"]:
            data["quality_panel_closeout"] = quality_panel_closeout_projection(
                finalize_report=data,
            )
        return data


_FINALIZE_REPORT_PATH_FIELDS = (
    "audited_brief",
    "reader_brief",
    "named_reader_brief",
    "reader_docx",
    "named_reader_docx",
    "source_appendix",
    "source_appendix_trace",
    "delivery_markdown",
    "delivery_docx",
    "delivery_latest_dir",
    "delivery_snapshot_dir",
)
_FINALIZE_REPORT_PATH_LIST_FIELDS = (
    "delivery_artifacts",
    "delivery_snapshot_artifacts",
)
_FINALIZE_REPORT_PATH_HASH_FIELDS = (
    "delivery_artifact_sha256",
    "delivery_snapshot_artifact_sha256",
)


def _finalize_report_payload(
    result: FinalizeResult,
    *,
    output_dir: Path,
    workspace_dir: Path | None = None,
) -> dict[str, Any]:
    """Serialize finalize reports with workspace-relative path identities."""

    payload = result.to_dict()
    workspace = (
        workspace_dir.resolve()
        if workspace_dir is not None
        else output_dir.resolve().parent
    )
    for field_name in _FINALIZE_REPORT_PATH_FIELDS:
        payload[field_name] = _workspace_relative_value(workspace, payload.get(field_name))
    for field_name in _FINALIZE_REPORT_PATH_LIST_FIELDS:
        values = payload.get(field_name) or []
        payload[field_name] = [_workspace_relative_value(workspace, value) for value in values]
    for field_name in _FINALIZE_REPORT_PATH_HASH_FIELDS:
        values = payload.get(field_name) or {}
        if isinstance(values, dict):
            payload[field_name] = {
                _workspace_relative_value(workspace, key): value
                for key, value in values.items()
            }
    return payload


def _write_finalize_report(
    path: Path,
    result: FinalizeResult,
    *,
    output_dir: Path,
    workspace_dir: Path | None = None,
) -> None:
    path.write_text(
        json.dumps(
            _finalize_report_payload(result, output_dir=output_dir, workspace_dir=workspace_dir),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _workspace_relative_value(workspace: Path, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    path = Path(value).expanduser()
    if not path.is_absolute():
        return path.as_posix()
    try:
        return path.resolve().relative_to(workspace).as_posix()
    except ValueError:
        return str(value)


def finalize_reader_outputs(
    *,
    output_dir: str | Path,
    project_name: str,
    output_formats: list[str] | tuple[str, ...] | None = None,
    output_footer: str = "",
    output_named_outputs: bool = True,
    output_filename_template: str = "",
    output_filename_tokens: dict[str, str] | None = None,
    docx_template: str = "default",
    source_appendix_config: dict[str, Any] | None = None,
    workspace_dir: str | Path | None = None,
) -> FinalizeResult:
    """Regenerate reader-facing artifacts from internal audited markdown.

    Agent-assisted workflows must finish any owner-stage edits to
    ``output/intermediate/audited_brief.md`` before finalization begins.
    This function reads that frozen audited artifact as input and writes only
    reader-facing Markdown/DOCX delivery outputs plus finalize control records,
    with internal claim citations rendered as reader-facing source labels when
    Claim Ledger evidence is available.
    """
    out = Path(output_dir)
    workspace = (
        Path(workspace_dir).expanduser().resolve()
        if workspace_dir is not None
        else out.resolve().parent
    )
    intermediate_dir = out / "intermediate"
    formats = set(output_formats or ["markdown"])
    projection = build_reader_projection(
        output_dir=out,
        output_formats=formats,
        source_appendix_config=source_appendix_config or {},
        workspace_dir=workspace,
    )
    audited_path = Path(projection.audited_brief)
    audited_markdown = projection.audited_markdown
    stripped_count = projection.stripped_src_marker_count
    appendix_path = out / "source_appendix.md"
    appendix_trace_path = out / "source_appendix_trace.md"
    if appendix_path.exists():
        appendix_path.unlink()
    if appendix_trace_path.exists():
        appendix_trace_path.unlink()
    if projection.source_appendix:
        shutil.copyfile(Path(projection.source_appendix), appendix_path)
    if projection.source_appendix_trace:
        shutil.copyfile(Path(projection.source_appendix_trace), appendix_trace_path)
    reader_markdown = projection.reader_markdown

    brief_path = out / "brief.md"
    brief_path.write_text(reader_markdown, encoding="utf-8")

    named_brief_path: Path | None = None
    if output_named_outputs:
        tokens = dict(output_filename_tokens or {})
        tokens.setdefault("project_name", project_name)
        tokens.setdefault("title", project_name)
        named_stem = render_output_stem(output_filename_template, tokens) if output_filename_template else ""
        if named_stem:
            named_brief_path = out / f"{named_stem}.md"
            if named_brief_path != brief_path:
                named_brief_path.write_text(reader_markdown, encoding="utf-8")

    docx_status = "not_requested"
    docx_path = out / "brief.docx"
    named_docx_path: Path | None = None
    if "docx" in formats:
        # Avoid leaving a stale rendered file that may still contain internal
        # [src:<claim_id>] markers when regeneration fails or dependencies are missing.
        if docx_path.exists():
            docx_path.unlink()
        if named_brief_path is not None:
            possible_named_docx = named_brief_path.with_suffix(".docx")
            if possible_named_docx.exists():
                possible_named_docx.unlink()
        try:
            from multi_agent_brief.outputs.ib_docx import convert

            convert(
                brief_path,
                docx_path,
                title=project_name,
                footer=output_footer or None,
                template=docx_template or "default",
            )
            docx_status = "generated"
            if named_brief_path is not None and named_brief_path.stem != "brief":
                named_docx_path = named_brief_path.with_suffix(".docx")
                shutil.copyfile(docx_path, named_docx_path)
        except ImportError:
            docx_status = "skipped_missing_dependency"
        except Exception:
            docx_status = "failed"
            raise

    result = FinalizeResult(
        status="pass",
        audited_brief=str(audited_path),
        reader_brief=str(brief_path),
        named_reader_brief=str(named_brief_path or ""),
        reader_docx=str(docx_path) if docx_path.exists() else "",
        named_reader_docx=str(named_docx_path or ""),
        docx_generation=docx_status,
        stripped_src_marker_count=stripped_count,
        source_appendix=str(appendix_path) if projection.source_appendix and appendix_path.exists() else "",
        source_appendix_generation=projection.source_appendix_generation,
        source_appendix_requested_by=projection.source_appendix_requested_by,
        source_appendix_mode=projection.source_appendix_mode,
        source_appendix_source_count=projection.source_appendix_source_count,
        source_appendix_cited_claim_count=projection.source_appendix_cited_claim_count,
        source_appendix_resolved_claim_count=projection.source_appendix_resolved_claim_count,
        source_appendix_warnings=projection.source_appendix_warnings,
        source_appendix_claim_map=projection.source_appendix_claim_map,
        source_appendix_trace=(
            str(appendix_trace_path)
            if projection.source_appendix_trace and appendix_trace_path.exists()
            else ""
        ),
        source_appendix_trace_generation=projection.source_appendix_trace_generation,
        source_appendix_trace_source_count=projection.source_appendix_trace_source_count,
        source_appendix_trace_span_count=projection.source_appendix_trace_span_count,
        source_appendix_trace_warnings=projection.source_appendix_trace_warnings,
        template_rendering=projection.template_rendering,
        audit_binding=_audit_binding_report(
            intermediate_dir=intermediate_dir,
            audited_markdown=audited_markdown,
        ),
        policy_gate_adapter=projection.policy_gate_adapter,
        citation_profile=projection.citation_profile,
        citation_profile_source=projection.citation_profile_source,
        citation_profile_runtime_effect=projection.citation_profile_runtime_effect,
        citation_profile_reader_citation_style=projection.citation_profile_reader_citation_style,
        citation_profile_reader_metadata_level=projection.citation_profile_reader_metadata_level,
        citation_profile_audit_trace_level=projection.citation_profile_audit_trace_level,
        citation_profile_delivery_exposes_internal_ids=projection.citation_profile_delivery_exposes_internal_ids,
        citation_profile_delivery_exposes_local_paths=projection.citation_profile_delivery_exposes_local_paths,
        citation_profile_audit_bundle_keeps_trace=projection.citation_profile_audit_bundle_keeps_trace,
        citation_profile_warnings=projection.citation_profile_warnings,
    )
    delivery_bundle = _build_delivery_bundle(
        output_dir=out,
        brief_path=brief_path,
        docx_path=docx_path if docx_path.exists() else None,
        named_docx_path=named_docx_path,
    )
    result.delivery_markdown = delivery_bundle["delivery_markdown"]
    result.delivery_docx = delivery_bundle["delivery_docx"]
    result.delivery_latest_dir = delivery_bundle["delivery_latest_dir"]
    result.delivery_artifacts = delivery_bundle["delivery_artifacts"]
    result.delivery_artifact_sha256 = delivery_bundle["delivery_artifact_sha256"]

    report_path = intermediate_dir / "finalize_report.json"
    reader_clean = build_reader_clean_report(
        markdown_paths=[
            path
            for path in (
                Path(result.delivery_markdown) if result.delivery_markdown else None,
                appendix_path if appendix_path.exists() else None,
            )
            if path is not None and path.exists()
        ],
        docx_paths=[
            path
            for path in (Path(result.delivery_docx) if result.delivery_docx else None,)
            if path is not None and path.exists()
        ],
        forbidden_phrases=policy_forbidden_phrases(projection.policy_gate_adapter),
    )
    result.reader_clean = reader_clean
    result.report_template_conformance = project_workspace_report_template_conformance(workspace)
    if result.audit_binding and result.audit_binding.get("status") == "fail":
        result.status = "fail"
        _write_finalize_report(report_path, result, output_dir=out, workspace_dir=workspace)
        findings = result.audit_binding.get("findings") or []
        raise RuntimeError(
            "Audit report binding check failed: "
            f"{len(findings)} blocking finding{'s' if len(findings) != 1 else ''}. "
            f"See {report_path}."
        )
    if reader_clean["status"] == "fail":
        result.status = "fail"
        _write_finalize_report(report_path, result, output_dir=out, workspace_dir=workspace)
        finding_count = len(reader_clean.get("sample_findings", []))
        total_count = sum(
            int(value)
            for key, value in reader_clean.items()
            if key.endswith("_count") and isinstance(value, int)
        )
        raise RuntimeError(
            "Reader final output gate failed: "
            f"{total_count or finding_count} blocking residue findings. "
            f"See {report_path}."
        )
    try:
        delivery_snapshot = _build_delivery_snapshot(
            output_dir=out,
            delivery_artifacts=[Path(path) for path in result.delivery_artifacts],
        )
    except Exception as exc:
        result.status = "fail"
        result.delivery_snapshot_error = f"{type(exc).__name__}: {exc}"
        _write_finalize_report(report_path, result, output_dir=out, workspace_dir=workspace)
        raise RuntimeError(
            f"Delivery snapshot creation failed. See {report_path}."
        ) from exc
    result.delivery_snapshot_dir = delivery_snapshot["delivery_snapshot_dir"]
    result.delivery_snapshot_artifacts = delivery_snapshot["delivery_snapshot_artifacts"]
    result.delivery_snapshot_artifact_sha256 = delivery_snapshot["delivery_snapshot_artifact_sha256"]
    _write_finalize_report(report_path, result, output_dir=out, workspace_dir=workspace)
    return result


def _build_delivery_bundle(
    *,
    output_dir: Path,
    brief_path: Path,
    docx_path: Path | None,
    named_docx_path: Path | None,
) -> dict[str, Any]:
    """Create the minimal reader delivery bundle.

    The root output files remain available for compatibility and audit/debugging,
    while ``output/delivery`` is the only surface intended to be handed to the
    final reader.
    """
    delivery_dir = output_dir / "delivery"
    if delivery_dir.exists():
        for child in delivery_dir.iterdir():
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
    else:
        delivery_dir.mkdir(parents=True, exist_ok=True)

    delivery_markdown = delivery_dir / "brief.md"
    shutil.copyfile(brief_path, delivery_markdown)

    delivery_docx = ""
    source_docx = named_docx_path if named_docx_path and named_docx_path.exists() else docx_path
    if source_docx is not None and source_docx.exists():
        docx_target = delivery_dir / source_docx.name
        shutil.copyfile(source_docx, docx_target)
        delivery_docx = str(docx_target)

    artifacts = [str(delivery_markdown)]
    if delivery_docx:
        artifacts.append(delivery_docx)
    artifact_sha256 = {artifact: _sha256_file(Path(artifact)) for artifact in artifacts}
    return {
        "delivery_latest_dir": str(delivery_dir),
        "delivery_markdown": str(delivery_markdown),
        "delivery_docx": delivery_docx,
        "delivery_artifacts": artifacts,
        "delivery_artifact_sha256": artifact_sha256,
    }


def _build_delivery_snapshot(
    *,
    output_dir: Path,
    delivery_artifacts: list[Path],
) -> dict[str, Any]:
    """Copy latest reader delivery files into a convenience snapshot directory."""
    history_root = output_dir / "delivery-history"
    history_root.mkdir(parents=True, exist_ok=True)
    snapshot_name = _delivery_snapshot_name(output_dir)
    snapshot_dir = _resolve_delivery_snapshot_dir(
        history_root=history_root,
        snapshot_name=snapshot_name,
        delivery_artifacts=delivery_artifacts,
    )
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    snapshot_artifacts: list[str] = []
    for artifact in delivery_artifacts:
        target = snapshot_dir / artifact.name
        if not target.exists() or _sha256_file(target) != _sha256_file(artifact):
            shutil.copyfile(artifact, target)
        snapshot_artifacts.append(str(target))
    return {
        "delivery_snapshot_dir": str(snapshot_dir),
        "delivery_snapshot_artifacts": snapshot_artifacts,
        "delivery_snapshot_artifact_sha256": {
            artifact: _sha256_file(Path(artifact)) for artifact in snapshot_artifacts
        },
    }


def _delivery_snapshot_name(output_dir: Path) -> str:
    manifest = output_dir / "intermediate" / "runtime_manifest.json"
    if manifest.exists():
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            run_id = str(payload.get("run_id") or "").strip()
            if run_id:
                return _safe_snapshot_component(run_id)
        except (OSError, json.JSONDecodeError):
            pass
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _safe_snapshot_component(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip(".-")
    return cleaned or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _resolve_delivery_snapshot_dir(
    *,
    history_root: Path,
    snapshot_name: str,
    delivery_artifacts: list[Path],
) -> Path:
    candidate = history_root / snapshot_name
    if not candidate.exists() or _snapshot_matches_delivery(candidate, delivery_artifacts):
        return candidate
    suffix = 2
    while True:
        suffixed = history_root / f"{snapshot_name}-{suffix}"
        if not suffixed.exists() or _snapshot_matches_delivery(suffixed, delivery_artifacts):
            return suffixed
        suffix += 1


def _snapshot_matches_delivery(snapshot_dir: Path, delivery_artifacts: list[Path]) -> bool:
    if not snapshot_dir.is_dir():
        return False
    expected_names = {artifact.name for artifact in delivery_artifacts}
    existing_files = {path.name for path in snapshot_dir.iterdir() if path.is_file()}
    if existing_files != expected_names:
        return False
    return all(
        (snapshot_dir / artifact.name).exists()
        and _sha256_file(snapshot_dir / artifact.name) == _sha256_file(artifact)
        for artifact in delivery_artifacts
    )


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _empty_reader_clean_report() -> dict[str, Any]:
    return {
        "status": "pass",
        "src_marker_count": 0,
        "bare_claim_id_count": 0,
        "source_id_count": 0,
        "process_wording_count": 0,
        "blank_citation_row_count": 0,
        "local_path_count": 0,
        "debug_residue_count": 0,
        "atom_id_count": 0,
        "policy_forbidden_phrase_count": 0,
        "sample_findings": [],
    }


def _empty_audit_binding_report() -> dict[str, Any]:
    return {
        "status": "not_checked",
        "claim_ledger_sha256": "",
        "audited_brief_sha256": "",
        "audit_report_sha256": "",
        "ledger_claim_count": 0,
        "audited_brief_cited_claim_count": 0,
        "findings": [],
        "warnings": [],
    }


@dataclass(frozen=True)
class FinalizeAuditBindingVerdict:
    """Single interpretation of finalize_report audit binding."""

    kind: str
    value: dict[str, Any]
    reasons: tuple[str, ...] = ()


def interpret_finalize_audit_binding(
    *,
    workspace: str | Path,
    finalize_report: dict[str, Any],
) -> FinalizeAuditBindingVerdict:
    binding = finalize_report.get("audit_binding")
    if not isinstance(binding, dict):
        return _degraded_finalize_audit_binding("finalize_report.json audit_binding.status must be pass.")
    if binding.get("status") != "pass":
        return _degraded_finalize_audit_binding("finalize_report.json audit_binding.status must be pass.")
    findings = binding.get("findings", [])
    if not isinstance(findings, list):
        return _degraded_finalize_audit_binding(
            "finalize_report.json audit_binding.findings must be a list when present."
        )
    if findings:
        return _degraded_finalize_audit_binding(
            "finalize_report.json audit_binding.findings must be empty when audit_binding.status is pass."
        )

    ws = Path(workspace).expanduser().resolve()
    reasons: list[str] = []
    audit_binding_paths = {
        "claim_ledger_sha256": ws / "output" / "intermediate" / "claim_ledger.json",
        "audited_brief_sha256": ws / "output" / "intermediate" / "audited_brief.md",
        "audit_report_sha256": ws / "output" / "intermediate" / "audit_report.json",
    }
    for field, path in audit_binding_paths.items():
        value = binding.get(field)
        if not isinstance(value, str) or not value.strip():
            reasons.append(f"finalize_report.json audit_binding.{field} is required.")
            continue
        if not path.exists():
            reasons.append(f"finalize_report.json audit_binding.{field} target is missing: {path}.")
            continue
        try:
            current_sha256 = _sha256_file(path)
        except OSError as exc:
            reasons.append(f"finalize_report.json audit_binding.{field} target could not be read: {exc}")
            continue
        if value != current_sha256:
            reasons.append(f"finalize_report.json audit_binding.{field} does not match current artifact bytes.")
    if reasons:
        return FinalizeAuditBindingVerdict(
            kind="degraded",
            value=_finalize_audit_binding_projection(binding, status="blocked"),
            reasons=tuple(reasons),
        )
    return FinalizeAuditBindingVerdict(
        kind="canonical",
        value=_finalize_audit_binding_projection(binding, status="pass"),
    )


def project_finalize_audit_binding_for_read(verdict: FinalizeAuditBindingVerdict) -> dict[str, Any]:
    return dict(verdict.value)


def require_finalize_audit_binding_pass(verdict: FinalizeAuditBindingVerdict) -> list[str]:
    if verdict.kind == "canonical":
        return []
    return list(verdict.reasons)


def _degraded_finalize_audit_binding(reason: str) -> FinalizeAuditBindingVerdict:
    return FinalizeAuditBindingVerdict(
        kind="degraded",
        value={
            "status": "blocked",
            "binding_status": "unknown",
        },
        reasons=(reason,),
    )


def _finalize_audit_binding_projection(binding: dict[str, Any], *, status: str) -> dict[str, Any]:
    return {
        "status": status,
        "binding_status": binding.get("status"),
        "claim_ledger_sha256": binding.get("claim_ledger_sha256"),
        "audited_brief_sha256": binding.get("audited_brief_sha256"),
        "audit_report_sha256": binding.get("audit_report_sha256"),
    }


def _audit_binding_report(
    *,
    intermediate_dir: Path,
    audited_markdown: str,
) -> dict[str, Any]:
    """Check that an existing audit report still matches this run.

    This is a control-plane consistency check, not semantic fact verification.
    It catches stale audit reports that mention old Claim Ledger entries or
    still require repair while finalize would otherwise publish clean reader
    artifacts.
    """
    ledger_path = intermediate_dir / "claim_ledger.json"
    audited_brief_path = intermediate_dir / "audited_brief.md"
    audit_report_path = intermediate_dir / "audit_report.json"
    cited_ids = cited_claim_ids(audited_markdown)
    ledger_ids: set[str] = set()
    ledger_sha = ""
    audited_brief_sha = _sha256_file(audited_brief_path) if audited_brief_path.exists() else ""
    audit_sha = ""
    findings: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    if ledger_path.exists():
        ledger_sha = _sha256_file(ledger_path)
        try:
            ledger_ids = _claim_ids_from_ledger(ledger_path)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            findings.append(
                {
                    "kind": "malformed_claim_ledger",
                    "message": f"Claim Ledger could not be read for audit binding: {exc}",
                }
            )
    if audit_report_path.exists():
        audit_sha = _sha256_file(audit_report_path)

    report: dict[str, Any] = {
        "status": "pass",
        "claim_ledger_sha256": ledger_sha,
        "audited_brief_sha256": audited_brief_sha,
        "audit_report_sha256": audit_sha,
        "ledger_claim_count": len(ledger_ids),
        "audited_brief_cited_claim_count": len(cited_ids),
        "findings": findings,
        "warnings": warnings,
    }

    if not audit_report_path.exists():
        report["status"] = "not_checked"
        return report

    try:
        payload = json.loads(audit_report_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        findings.append(
            {
                "kind": "malformed_audit_report",
                "message": f"audit_report.json is not valid JSON: {exc}",
            }
        )
        report["status"] = "fail"
        return report
    if not isinstance(payload, dict):
        findings.append(
            {
                "kind": "malformed_audit_report",
                "message": "audit_report.json must be an object.",
            }
        )
        report["status"] = "fail"
        return report

    contract_violations = AuditReportContract.validate(payload)
    contract_errors = [violation for violation in contract_violations if violation.severity == "error"]
    if contract_errors:
        findings.append(
            {
                "kind": "malformed_audit_report_contract",
                "message": "audit_report.json does not satisfy the current AuditReport contract.",
                "count": len(contract_errors),
                "errors": [
                    {
                        "field": violation.field,
                        "error": violation.error,
                    }
                    for violation in contract_errors[:10]
                ],
            }
        )

    audit_status = str(payload.get("audit_status") or "").strip().lower()
    if audit_status == "fail":
        findings.append(
            {
                "kind": "audit_status_failed",
                "message": "audit_report.json records audit_status=fail.",
            }
        )

    structured_findings = payload.get("findings")
    if isinstance(structured_findings, list):
        high_findings: list[dict[str, Any]] = [
            finding
            for finding in structured_findings
            if isinstance(finding, dict) and str(finding.get("severity") or "").strip().lower() == "high"
        ]
        if high_findings:
            findings.append(
                {
                    "kind": "audit_high_severity_findings",
                    "message": "audit_report.json contains high-severity findings.",
                    "count": len(high_findings),
                    "finding_ids": [
                        str(finding.get("finding_id") or "")
                        for finding in high_findings[:10]
                        if str(finding.get("finding_id") or "")
                    ],
                }
            )

    if payload.get("passed") is False:
        findings.append(
            {
                "kind": "audit_not_passed",
                "message": "audit_report.json records passed=false.",
            }
        )
    blocking_findings = payload.get("blocking_findings")
    if isinstance(blocking_findings, list) and blocking_findings:
        findings.append(
            {
                "kind": "audit_blocking_findings",
                "message": "audit_report.json still contains blocking_findings.",
                "count": len(blocking_findings),
            }
        )
    recommendation = str(payload.get("recommendation") or "").strip().lower()
    if recommendation in {"repair_required", "block", "blocked", "reject"}:
        findings.append(
            {
                "kind": "audit_recommendation_not_ready",
                "message": f"audit_report.json recommendation is {recommendation}.",
            }
        )

    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    if isinstance(metadata.get("audit_binding"), dict):
        warnings.append(
            {
                "kind": "legacy_audit_binding_ignored",
                "message": (
                    "audit_report.json metadata.audit_binding is deprecated and ignored; "
                    "Python workflow_state/artifact_registry hashes are authoritative."
                ),
            }
        )

    _append_python_audit_binding_findings(
        findings=findings,
        intermediate_dir=intermediate_dir,
        current_claim_ledger_sha256=ledger_sha,
        current_audited_brief_sha256=audited_brief_sha,
        current_audit_report_sha256=audit_sha,
    )

    mentioned_ids = set(_AUDIT_CLAIM_ID_RE.findall(json.dumps(payload, ensure_ascii=False)))
    unknown_ids = sorted(mentioned_ids - ledger_ids)
    if unknown_ids:
        findings.append(
            {
                "kind": "audit_mentions_unknown_claim_ids",
                "message": "audit_report.json mentions claim IDs that are absent from the current Claim Ledger.",
                "claim_ids": unknown_ids[:20],
            }
        )

    report["findings"] = findings
    report["warnings"] = warnings
    report["status"] = "fail" if findings else "pass"
    return report


def _claim_ids_from_ledger(path: Path) -> set[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        raw_claims = data.get("claims") or data.get("items") or []
    else:
        raw_claims = data
    if not isinstance(raw_claims, list):
        raise ValueError("Claim Ledger must be a list or object with claims/items.")
    claim_ids: set[str] = set()
    for item in raw_claims:
        if not isinstance(item, dict):
            continue
        claim_id = item.get("claim_id")
        if isinstance(claim_id, str) and claim_id.strip():
            claim_ids.add(claim_id.strip())
    return claim_ids


def _read_json_object_for_binding(
    path: Path,
    findings: list[dict[str, Any]],
    *,
    kind: str,
) -> dict[str, Any] | None:
    if not path.exists():
        findings.append(
            {
                "kind": "audit_binding_missing_control_chain",
                "field": kind,
                "message": f"{path.name} is required for Python audit binding verification.",
            }
        )
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        findings.append(
            {
                "kind": "audit_binding_malformed_control_chain",
                "field": kind,
                "message": f"{path.name} is not valid JSON: {exc}",
            }
        )
        return None
    if not isinstance(payload, dict):
        findings.append(
            {
                "kind": "audit_binding_malformed_control_chain",
                "field": kind,
                "message": f"{path.name} must be a JSON object.",
            }
        )
        return None
    return payload


def _control_chain_sha(
    findings: list[dict[str, Any]],
    *,
    value: Any,
    field: str,
    source: str,
) -> str | None:
    if not isinstance(value, str) or not value.strip():
        findings.append(
            {
                "kind": "audit_binding_missing_control_chain",
                "field": field,
                "source": source,
                "message": f"{source} is missing required sha256 field {field}.",
            }
        )
        return None
    return value.strip()


def _append_sha_binding_finding(
    findings: list[dict[str, Any]],
    *,
    field: str,
    registry_sha256: str | None,
    workflow_sha256: str | None,
    current_sha256: str,
) -> None:
    if not registry_sha256 or not workflow_sha256:
        return
    if registry_sha256 != workflow_sha256 or registry_sha256 != current_sha256:
        findings.append(
            {
                "kind": "audit_binding_mismatch",
                "field": field,
                "registry_sha256": registry_sha256,
                "workflow_sha256": workflow_sha256,
                "current_sha256": current_sha256,
                "message": (
                    f"{field} does not match Python control-chain binding; route repair "
                    "back to the owner stage instead of downstream in-place changes."
                ),
            }
        )


def _append_python_audit_binding_findings(
    *,
    findings: list[dict[str, Any]],
    intermediate_dir: Path,
    current_claim_ledger_sha256: str,
    current_audited_brief_sha256: str,
    current_audit_report_sha256: str,
) -> None:
    workflow = _read_json_object_for_binding(
        intermediate_dir / "workflow_state.json",
        findings,
        kind="workflow_state",
    )
    registry = _read_json_object_for_binding(
        intermediate_dir / "artifact_registry.json",
        findings,
        kind="artifact_registry",
    )
    if workflow is None or registry is None:
        return

    artifacts = registry.get("artifacts") if isinstance(registry.get("artifacts"), dict) else {}
    claim_record = artifacts.get("claim_ledger") if isinstance(artifacts.get("claim_ledger"), dict) else {}
    audited_brief_record = artifacts.get("audited_brief") if isinstance(artifacts.get("audited_brief"), dict) else {}
    audit_record = artifacts.get("audit_report") if isinstance(artifacts.get("audit_report"), dict) else {}
    statuses = workflow.get("stage_statuses") if isinstance(workflow.get("stage_statuses"), dict) else {}
    auditor = statuses.get("auditor") if isinstance(statuses.get("auditor"), dict) else {}
    metadata = auditor.get("metadata") if isinstance(auditor.get("metadata"), dict) else {}
    upstream = (
        metadata.get("upstream_artifact_sha256")
        if isinstance(metadata.get("upstream_artifact_sha256"), dict)
        else {}
    )
    produced = (
        metadata.get("produced_artifact_sha256")
        if isinstance(metadata.get("produced_artifact_sha256"), dict)
        else {}
    )

    registry_ledger_sha = _control_chain_sha(
        findings,
        value=claim_record.get("sha256"),
        field="artifacts.claim_ledger.sha256",
        source="artifact_registry.json",
    )
    workflow_ledger_sha = _control_chain_sha(
        findings,
        value=upstream.get("claim_ledger"),
        field="stage_statuses.auditor.metadata.upstream_artifact_sha256.claim_ledger",
        source="workflow_state.json",
    )
    registry_audited_brief_sha = _control_chain_sha(
        findings,
        value=audited_brief_record.get("sha256"),
        field="artifacts.audited_brief.sha256",
        source="artifact_registry.json",
    )
    workflow_audited_brief_sha = _control_chain_sha(
        findings,
        value=upstream.get("audited_brief"),
        field="stage_statuses.auditor.metadata.upstream_artifact_sha256.audited_brief",
        source="workflow_state.json",
    )
    registry_audit_sha = _control_chain_sha(
        findings,
        value=audit_record.get("sha256"),
        field="artifacts.audit_report.sha256",
        source="artifact_registry.json",
    )
    workflow_audit_sha = _control_chain_sha(
        findings,
        value=produced.get("audit_report"),
        field="stage_statuses.auditor.metadata.produced_artifact_sha256.audit_report",
        source="workflow_state.json",
    )

    _append_sha_binding_finding(
        findings,
        field="claim_ledger_sha256",
        registry_sha256=registry_ledger_sha,
        workflow_sha256=workflow_ledger_sha,
        current_sha256=current_claim_ledger_sha256,
    )
    _append_sha_binding_finding(
        findings,
        field="audited_brief_sha256",
        registry_sha256=registry_audited_brief_sha,
        workflow_sha256=workflow_audited_brief_sha,
        current_sha256=current_audited_brief_sha256,
    )
    _append_sha_binding_finding(
        findings,
        field="audit_report_sha256",
        registry_sha256=registry_audit_sha,
        workflow_sha256=workflow_audit_sha,
        current_sha256=current_audit_report_sha256,
    )
