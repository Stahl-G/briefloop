from __future__ import annotations

import json
import re
import shutil
import hashlib
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any

from multi_agent_brief.tools.draft_cleanup import strip_claim_citations
from multi_agent_brief.outputs.naming import render_output_stem
from multi_agent_brief.outputs.reader_final_gate import (
    combine_reader_final_gate_results,
    detect_reader_residue,
    detect_reader_residue_in_docx,
)
from multi_agent_brief.outputs.source_appendix import (
    SourceAppendixResult,
    build_source_appendix,
    cited_claim_ids,
)

_SRC_MARKER_RE = re.compile(r"\[src:[^\]]*\]")
_AUDIT_CLAIM_ID_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:CL-\d{3,}|CLM-\d{3,}|SYN_CLAIM_[A-Z0-9_-]+|CLAIM_[A-Z0-9_-]+)(?![A-Za-z0-9_])"
)
_INTERNAL_READER_SECTION_RE = re.compile(
    r"(?:claim\s+ledger|声明账本).{0,80}(?:coverage|覆盖情况|覆盖)",
    re.IGNORECASE,
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
    delivery_markdown: str = ""
    delivery_docx: str = ""
    delivery_artifacts: list[str] = field(default_factory=list)
    delivery_artifact_sha256: dict[str, str] = field(default_factory=dict)
    reader_clean: dict[str, Any] | None = None
    audit_binding: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if data["source_appendix_warnings"] is None:
            data["source_appendix_warnings"] = []
        if data["reader_clean"] is None:
            data["reader_clean"] = _empty_reader_clean_report()
        if data["audit_binding"] is None:
            data["audit_binding"] = _empty_audit_binding_report()
        return data


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
) -> FinalizeResult:
    """Regenerate reader-facing artifacts from internal audited markdown.

    Agent-assisted workflows write or rewrite ``output/intermediate/audited_brief.md``
    before reader-facing delivery artifacts are rendered.
    This function is the final delivery gate: it preserves the cited audited
    artifact for auditability, then writes reader-facing Markdown/DOCX outputs as
    deterministic ``strip_claim_citations(audited_brief)`` derivatives.
    """
    out = Path(output_dir)
    intermediate_dir = out / "intermediate"
    audited_path = intermediate_dir / "audited_brief.md"
    if not audited_path.exists():
        raise FileNotFoundError(
            f"Audited brief not found: {audited_path}. "
            "Run prepare/audit first or write output/intermediate/audited_brief.md."
        )

    out.mkdir(parents=True, exist_ok=True)
    intermediate_dir.mkdir(parents=True, exist_ok=True)

    audited_markdown = audited_path.read_text(encoding="utf-8")
    stripped_count = len(_SRC_MARKER_RE.findall(audited_markdown))
    base_reader_markdown = _strip_internal_reader_sections(
        strip_claim_citations(audited_markdown)
    )
    formats = set(output_formats or ["markdown"])
    appendix_request = _source_appendix_request(
        output_formats=formats,
        source_appendix_config=source_appendix_config or {},
    )
    appendix_path = out / "source_appendix.md"
    if appendix_path.exists():
        appendix_path.unlink()
    appendix_result = _maybe_generate_source_appendix(
        audited_markdown=audited_markdown,
        ledger_path=intermediate_dir / "claim_ledger.json",
        appendix_path=appendix_path,
        requested_by=appendix_request["requested_by"],
        explicit=bool(appendix_request["explicit"]),
    )
    reader_markdown = base_reader_markdown
    if appendix_result.markdown and appendix_result.source_count:
        reader_markdown = base_reader_markdown.rstrip() + "\n\n" + appendix_result.markdown

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
        # [src:CLAIM_ID] markers when regeneration fails or dependencies are missing.
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
        source_appendix=str(appendix_path) if appendix_result.markdown and appendix_path.exists() else "",
        source_appendix_generation=appendix_result.status,
        source_appendix_requested_by=str(appendix_request["requested_by"]),
        source_appendix_mode=str(appendix_request["mode"]),
        source_appendix_source_count=appendix_result.source_count,
        source_appendix_cited_claim_count=appendix_result.cited_claim_count,
        source_appendix_resolved_claim_count=appendix_result.resolved_claim_count,
        source_appendix_warnings=appendix_result.warnings,
        audit_binding=_audit_binding_report(
            intermediate_dir=intermediate_dir,
            audited_markdown=audited_markdown,
        ),
    )
    delivery_bundle = _build_delivery_bundle(
        output_dir=out,
        brief_path=brief_path,
        docx_path=docx_path if docx_path.exists() else None,
        named_docx_path=named_docx_path,
    )
    result.delivery_markdown = delivery_bundle["delivery_markdown"]
    result.delivery_docx = delivery_bundle["delivery_docx"]
    result.delivery_artifacts = delivery_bundle["delivery_artifacts"]
    result.delivery_artifact_sha256 = delivery_bundle["delivery_artifact_sha256"]

    report_path = intermediate_dir / "finalize_report.json"
    reader_clean = _reader_clean_report(
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
    )
    result.reader_clean = reader_clean
    report_path.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    if result.audit_binding and result.audit_binding.get("status") == "fail":
        result.status = "fail"
        report_path.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        findings = result.audit_binding.get("findings") or []
        raise RuntimeError(
            "Audit report binding check failed: "
            f"{len(findings)} blocking finding{'s' if len(findings) != 1 else ''}. "
            f"See {report_path}."
        )
    if reader_clean["status"] == "fail":
        result.status = "fail"
        report_path.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
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
        "delivery_markdown": str(delivery_markdown),
        "delivery_docx": delivery_docx,
        "delivery_artifacts": artifacts,
        "delivery_artifact_sha256": artifact_sha256,
    }


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _reader_clean_report(
    *,
    markdown_paths: list[Path],
    docx_paths: list[Path],
) -> dict[str, Any]:
    results = [
        detect_reader_residue(path.read_text(encoding="utf-8"), artifact=str(path))
        for path in markdown_paths
    ]
    results.extend(
        detect_reader_residue_in_docx(path, artifact=str(path))
        for path in docx_paths
    )
    return combine_reader_final_gate_results(results).to_report_dict()


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


def _source_appendix_request(
    *,
    output_formats: set[str],
    source_appendix_config: dict[str, Any],
) -> dict[str, Any]:
    config_enabled = _as_bool(source_appendix_config.get("enabled"), False)
    if config_enabled:
        requested_by = "config"
        explicit = True
    elif "source_appendix" in output_formats:
        requested_by = "source_appendix"
        explicit = True
    elif "source_map" in output_formats:
        requested_by = "legacy_source_map"
        explicit = False
    else:
        requested_by = "none"
        explicit = False
    mode = str(source_appendix_config.get("mode") or "separate").strip().lower()
    if mode not in {"separate", "append"}:
        mode = "separate"
    return {
        "requested_by": requested_by,
        "explicit": explicit,
        "mode": mode,
    }


def _maybe_generate_source_appendix(
    *,
    audited_markdown: str,
    ledger_path: Path,
    appendix_path: Path,
    requested_by: str,
    explicit: bool,
) -> SourceAppendixResult:
    if requested_by == "none":
        return SourceAppendixResult(status="not_requested")
    if not ledger_path.exists():
        if explicit:
            raise FileNotFoundError(
                f"Claim Ledger not found for explicit source appendix request: {ledger_path}"
            )
        return SourceAppendixResult(
            status="skipped_missing_ledger",
            warnings=["Source appendix skipped because claim_ledger.json was missing."],
        )
    try:
        result = build_source_appendix(
            audited_markdown=audited_markdown,
            ledger_path=ledger_path,
        )
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        if explicit:
            raise ValueError(f"Claim Ledger is malformed for source appendix generation: {exc}") from exc
        return SourceAppendixResult(
            status="skipped_malformed_ledger",
            warnings=["Source appendix skipped because claim_ledger.json was malformed."],
        )
    if result.source_count == 0:
        message = "No usable cited sources could be resolved for source appendix generation."
        if explicit:
            raise RuntimeError(message)
        result.status = "generated_with_warnings"
        result.warnings.append(message)
    if result.markdown:
        appendix_path.write_text(result.markdown, encoding="utf-8")
    return result


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _strip_internal_reader_sections(markdown: str) -> str:
    """Remove process-only sections that should not reach final readers."""
    lines = markdown.splitlines()
    cleaned: list[str] = []
    skip_level: int | None = None

    for line in lines:
        heading = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if skip_level is not None:
            if heading and len(heading.group(1)) <= skip_level:
                skip_level = None
            else:
                continue

        if heading:
            title = heading.group(2).strip()
            if _INTERNAL_READER_SECTION_RE.search(title):
                skip_level = len(heading.group(1))
                while cleaned and not cleaned[-1].strip():
                    cleaned.pop()
                continue

        cleaned.append(line)

    return "\n".join(cleaned).rstrip() + "\n"
