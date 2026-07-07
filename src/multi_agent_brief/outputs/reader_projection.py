from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from multi_agent_brief.core.citations import parse_internal_citation_markers
from multi_agent_brief.outputs.reader_final_gate import (
    combine_reader_final_gate_results,
    detect_reader_residue,
    detect_reader_residue_in_docx,
)
from multi_agent_brief.outputs.source_appendix import (
    SourceAppendixResult,
    build_source_appendix,
    cited_claim_ids,
    replace_claim_citations_with_labels,
)
from multi_agent_brief.product.citation_profile import resolve_workspace_citation_profile
from multi_agent_brief.product.policy_gate_adapter import (
    policy_forbidden_phrases,
    resolve_workspace_policy_gate_adapter,
)
from multi_agent_brief.product.template_renderer import render_reader_markdown_with_template

_INTERNAL_READER_SECTION_RE = re.compile(
    r"(?:claim\s+ledger|声明账本).{0,80}(?:coverage|覆盖情况|覆盖)",
    re.IGNORECASE,
)
PROJECTABLE_READER_BLOCK_START = "<!-- briefloop:projectable-reader-start -->"
PROJECTABLE_READER_BLOCK_END = "<!-- briefloop:projectable-reader-end -->"


class ReaderProjectionSourceError(ValueError):
    """Raised when the audited brief cannot produce a canonical reader source."""


@dataclass(frozen=True)
class ReaderProjectionResidueFinding:
    kind: str
    raw: str
    claim_id: str
    status: str
    start: int
    end: int
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "raw": self.raw,
            "claim_id": self.claim_id,
            "status": self.status,
            "start": self.start,
            "end": self.end,
            "message": self.message,
        }


@dataclass(frozen=True)
class ReaderProjectionResidueReport:
    status: str
    unresolved_src_marker_count: int = 0
    malformed_src_marker_count: int = 0
    findings: list[ReaderProjectionResidueFinding] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "unresolved_src_marker_count": self.unresolved_src_marker_count,
            "malformed_src_marker_count": self.malformed_src_marker_count,
            "findings": [finding.to_dict() for finding in self.findings],
        }


@dataclass(frozen=True)
class ReaderProjectionResult:
    """Candidate reader projection rendered from frozen audit artifacts."""

    candidate_dir: str
    audited_brief: str
    audited_markdown: str
    reader_brief: str
    reader_markdown: str
    stripped_src_marker_count: int = 0
    reader_projection_residue: dict[str, Any] = field(default_factory=dict)
    source_appendix: str = ""
    source_appendix_generation: str = "not_requested"
    source_appendix_requested_by: str = "none"
    source_appendix_mode: str = "separate"
    source_appendix_source_count: int = 0
    source_appendix_cited_claim_count: int = 0
    source_appendix_resolved_claim_count: int = 0
    source_appendix_warnings: list[str] = field(default_factory=list)
    source_appendix_claim_map: dict[str, dict[str, str]] = field(default_factory=dict)
    source_appendix_trace: str = ""
    source_appendix_trace_generation: str = "not_available"
    source_appendix_trace_source_count: int = 0
    source_appendix_trace_span_count: int = 0
    source_appendix_trace_warnings: list[str] = field(default_factory=list)
    template_rendering: dict[str, Any] = field(default_factory=dict)
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
    reader_clean: dict[str, Any] = field(default_factory=dict)


def build_reader_projection(
    *,
    output_dir: str | Path,
    output_formats: list[str] | tuple[str, ...] | set[str] | None = None,
    source_appendix_config: dict[str, Any] | None = None,
    workspace_dir: str | Path | None = None,
    transaction_id: str | None = None,
    candidate_root: str | Path | None = None,
) -> ReaderProjectionResult:
    """Render a reader candidate without promoting delivery artifacts."""

    out = Path(output_dir)
    workspace = (
        Path(workspace_dir).expanduser().resolve()
        if workspace_dir is not None
        else out.resolve().parent
    )
    intermediate_dir = out / "intermediate"
    audited_path = intermediate_dir / "audited_brief.md"
    if not audited_path.exists():
        raise FileNotFoundError(
            f"Audited brief not found: {audited_path}. "
            "Run prepare/audit first or write output/intermediate/audited_brief.md."
        )

    candidate_root = (
        Path(candidate_root)
        if candidate_root is not None
        else intermediate_dir / "finalize_candidate"
    )
    candidate_dir = _reader_projection_candidate_dir(
        candidate_root=candidate_root,
        transaction_id=transaction_id,
    )
    if candidate_dir.exists():
        raise FileExistsError(
            "Reader projection candidate already exists for this transaction id: "
            f"{candidate_dir}. Use a new transaction id instead of replacing an "
            "existing candidate."
        )
    candidate_dir.mkdir(parents=True)
    try:
        audited_markdown = audited_path.read_text(encoding="utf-8")
        reader_source = reader_projection_source_markdown(audited_markdown)
        formats = set(output_formats or ["markdown"])
        source_appendix_config = source_appendix_config or {}
        appendix_request = _source_appendix_request(
            output_formats=formats,
            source_appendix_config=source_appendix_config,
        )
        citation_profile = resolve_workspace_citation_profile(
            workspace,
            source_appendix_config=source_appendix_config,
        )
        appendix_path = candidate_dir / "source_appendix.md"
        appendix_trace_path = candidate_dir / "source_appendix_trace.md"
        appendix_result = _maybe_generate_source_appendix(
            reader_source_markdown=reader_source,
            ledger_path=intermediate_dir / "claim_ledger.json",
            appendix_path=appendix_path,
            trace_path=appendix_trace_path,
            requested_by=appendix_request["requested_by"],
            explicit=bool(appendix_request["explicit"]),
        )
        appendix_requested_by = (
            "cited_claims"
            if appendix_request["requested_by"] == "none" and appendix_result.source_count
            else str(appendix_request["requested_by"])
        )
        residue_report = _reader_projection_residue_report(
            reader_source,
            citation_labels=appendix_result.citation_labels,
        )
        stripped_count = _resolved_projection_count(
            reader_source,
            citation_labels=appendix_result.citation_labels,
        )
        reader_body_markdown = replace_claim_citations_with_labels(
            reader_source,
            appendix_result.citation_labels,
        )
        reader_markdown = reader_body_markdown
        if appendix_result.markdown and appendix_result.source_count:
            reader_markdown = reader_body_markdown.rstrip() + "\n\n" + appendix_result.markdown
        template_render = render_reader_markdown_with_template(
            workspace=workspace,
            markdown=reader_markdown,
        )
        reader_markdown = template_render.markdown
        reader_path = candidate_dir / "reader_brief.md"
        reader_path.write_text(reader_markdown, encoding="utf-8")

        policy_gate_adapter = resolve_workspace_policy_gate_adapter(workspace)
        reader_clean_paths = [reader_path]
        if appendix_result.markdown and appendix_path.exists():
            reader_clean_paths.append(appendix_path)
        reader_clean = build_reader_clean_report(
            markdown_paths=reader_clean_paths,
            docx_paths=[],
            forbidden_phrases=policy_forbidden_phrases(policy_gate_adapter),
        )
        reader_clean = merge_projection_residue_into_reader_clean(
            reader_clean,
            residue_report,
            artifact=str(reader_path),
        )

        return ReaderProjectionResult(
            candidate_dir=str(candidate_dir),
            audited_brief=str(audited_path),
            audited_markdown=audited_markdown,
            reader_brief=str(reader_path),
            reader_markdown=reader_markdown,
            stripped_src_marker_count=stripped_count,
            reader_projection_residue=residue_report.to_dict(),
            source_appendix=str(appendix_path) if appendix_result.markdown and appendix_path.exists() else "",
            source_appendix_generation=appendix_result.status,
            source_appendix_requested_by=appendix_requested_by,
            source_appendix_mode=str(appendix_request["mode"]),
            source_appendix_source_count=appendix_result.source_count,
            source_appendix_cited_claim_count=appendix_result.cited_claim_count,
            source_appendix_resolved_claim_count=appendix_result.resolved_claim_count,
            source_appendix_warnings=appendix_result.warnings,
            source_appendix_claim_map=appendix_result.claim_source_map,
            source_appendix_trace=(
                str(appendix_trace_path)
                if appendix_result.trace_markdown and appendix_trace_path.exists()
                else ""
            ),
            source_appendix_trace_generation=appendix_result.trace_status,
            source_appendix_trace_source_count=appendix_result.trace_source_count,
            source_appendix_trace_span_count=appendix_result.trace_span_count,
            source_appendix_trace_warnings=appendix_result.trace_warnings,
            template_rendering=template_render.to_report(),
            policy_gate_adapter=policy_gate_adapter,
            citation_profile=str(citation_profile.get("profile") or "executive"),
            citation_profile_source=str(citation_profile.get("source") or "default"),
            citation_profile_runtime_effect=str(
                citation_profile.get("runtime_effect") or "citation_profile_resolution_only"
            ),
            citation_profile_reader_citation_style=str(
                citation_profile.get("reader_citation_style") or "source_label"
            ),
            citation_profile_reader_metadata_level=str(
                citation_profile.get("reader_metadata_level") or "low_interference"
            ),
            citation_profile_audit_trace_level=str(
                citation_profile.get("audit_trace_level") or "complete_when_available"
            ),
            citation_profile_delivery_exposes_internal_ids=bool(
                citation_profile.get("delivery_exposes_internal_ids")
            ),
            citation_profile_delivery_exposes_local_paths=bool(
                citation_profile.get("delivery_exposes_local_paths")
            ),
            citation_profile_audit_bundle_keeps_trace=bool(
                citation_profile.get("audit_bundle_keeps_trace")
            ),
            citation_profile_warnings=list(citation_profile.get("warnings") or []),
            reader_clean=reader_clean,
        )
    except Exception:
        shutil.rmtree(candidate_dir, ignore_errors=True)
        raise


def build_reader_clean_report(
    *,
    markdown_paths: list[Path],
    docx_paths: list[Path],
    forbidden_phrases: tuple[str, ...] | list[str] = (),
) -> dict[str, Any]:
    results = [
        detect_reader_residue(
            path.read_text(encoding="utf-8"),
            artifact=str(path),
            forbidden_phrases=forbidden_phrases,
        )
        for path in markdown_paths
    ]
    results.extend(
        detect_reader_residue_in_docx(path, artifact=str(path), forbidden_phrases=forbidden_phrases)
        for path in docx_paths
    )
    return combine_reader_final_gate_results(results).to_report_dict()


def merge_projection_residue_into_reader_clean(
    reader_clean: dict[str, Any],
    residue_report: ReaderProjectionResidueReport | dict[str, Any],
    *,
    artifact: str,
) -> dict[str, Any]:
    """Merge parser-backed projection residue facts into a reader-clean report."""

    report = (
        residue_report.to_dict()
        if isinstance(residue_report, ReaderProjectionResidueReport)
        else dict(residue_report or {})
    )
    findings = report.get("findings") if isinstance(report.get("findings"), list) else []
    unresolved_count = int(report.get("unresolved_src_marker_count") or 0)
    malformed_count = int(report.get("malformed_src_marker_count") or 0)
    if not findings and unresolved_count == 0 and malformed_count == 0:
        return reader_clean

    merged = dict(reader_clean)
    merged["status"] = "fail"
    merged["reader_projection_unresolved_src_marker_count"] = unresolved_count
    merged["reader_projection_malformed_src_marker_count"] = malformed_count
    sample_findings = list(merged.get("sample_findings") or [])
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        status = str(finding.get("status") or "unknown")
        raw = str(finding.get("raw") or "")
        message = str(finding.get("message") or "Reader projection contains unresolved citation residue.")
        sample_findings.append(
            {
                "kind": f"reader_projection_{status}_src_marker",
                "text": raw,
                "line": None,
                "artifact": artifact,
                "message": message,
                "claim_id": str(finding.get("claim_id") or ""),
            }
        )
    merged["sample_findings"] = sample_findings[:10]
    return merged


def _reader_projection_residue_report(
    markdown: str,
    *,
    citation_labels: dict[str, str],
) -> ReaderProjectionResidueReport:
    findings: list[ReaderProjectionResidueFinding] = []
    unresolved_count = 0
    malformed_count = 0
    for marker in parse_internal_citation_markers(
        markdown,
        valid_claim_ids=set(citation_labels),
        include_bare_claim_ids=False,
    ):
        if marker.status == "resolved":
            continue
        if marker.status == "malformed":
            malformed_count += 1
        else:
            unresolved_count += 1
        findings.append(
            ReaderProjectionResidueFinding(
                kind=f"{marker.status}_src_marker",
                raw=marker.raw,
                claim_id=marker.claim_id,
                status=marker.status,
                start=marker.start,
                end=marker.end,
                message=marker.message,
            )
        )
    return ReaderProjectionResidueReport(
        status="fail" if findings else "pass",
        unresolved_src_marker_count=unresolved_count,
        malformed_src_marker_count=malformed_count,
        findings=findings,
    )


def _resolved_projection_count(
    markdown: str,
    *,
    citation_labels: dict[str, str],
) -> int:
    return sum(
        1
        for marker in parse_internal_citation_markers(
            markdown,
            valid_claim_ids=set(citation_labels),
            include_bare_claim_ids=False,
        )
        if marker.status == "resolved"
    )


def _projection_transaction_id(transaction_id: str | None) -> str:
    if transaction_id:
        raw = transaction_id.strip()
        cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", raw)
        if (
            cleaned
            and cleaned not in {".", ".."}
            and set(cleaned) != {"."}
            and "/" not in raw
            and "\\" not in raw
        ):
            return cleaned[:96]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{uuid4().hex[:12]}"


def _reader_projection_candidate_dir(*, candidate_root: Path, transaction_id: str | None) -> Path:
    candidate_id = _projection_transaction_id(transaction_id)
    root = candidate_root.resolve()
    candidate = (candidate_root / candidate_id).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise RuntimeError(
            f"Reader projection candidate path escaped finalize_candidate root: {candidate}"
        ) from exc
    if candidate == root:
        raise RuntimeError("Reader projection candidate path must be below finalize_candidate root.")
    return candidate


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
    reader_source_markdown: str,
    ledger_path: Path,
    appendix_path: Path,
    trace_path: Path,
    requested_by: str,
    explicit: bool,
) -> SourceAppendixResult:
    cited_ids = cited_claim_ids(reader_source_markdown)
    auto_from_citations = requested_by == "none" and bool(cited_ids) and ledger_path.exists()
    if requested_by == "none" and not auto_from_citations:
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
            audited_markdown=reader_source_markdown,
            ledger_path=ledger_path,
            evidence_span_registry_path=ledger_path.parent / "evidence_span_registry.json",
            workspace=ledger_path.parents[2] if len(ledger_path.parents) > 2 else ledger_path.parent,
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
    if result.trace_markdown and result.trace_span_count:
        trace_path.write_text(result.trace_markdown, encoding="utf-8")
    return result


def reader_projection_source_markdown(markdown: str) -> str:
    """Return the single canonical Markdown source for reader projection.

    Source Appendix lookup and reader body projection must consume this value
    instead of independently rescanning the raw audited brief.
    """

    without_blocks = _strip_projectable_reader_blocks(markdown)
    return _strip_internal_reader_sections(without_blocks)


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _strip_projectable_reader_blocks(markdown: str) -> str:
    lines = markdown.splitlines()
    kept: list[str] = []
    in_block = False
    block_start_line = 0

    for line_number, line in enumerate(lines, start=1):
        stripped = line.strip()
        has_start = PROJECTABLE_READER_BLOCK_START in line
        has_end = PROJECTABLE_READER_BLOCK_END in line
        if has_start and has_end:
            raise ReaderProjectionSourceError(
                f"Malformed projectable reader block at line {line_number}: "
                "start and end markers must be on separate lines."
            )
        if has_start and stripped != PROJECTABLE_READER_BLOCK_START:
            raise ReaderProjectionSourceError(
                f"Malformed projectable reader block at line {line_number}: "
                "start marker must appear alone on its line."
            )
        if has_end and stripped != PROJECTABLE_READER_BLOCK_END:
            raise ReaderProjectionSourceError(
                f"Malformed projectable reader block at line {line_number}: "
                "end marker must appear alone on its line."
            )
        if has_start:
            if in_block:
                raise ReaderProjectionSourceError(
                    f"Malformed projectable reader block at line {line_number}: nested start marker."
                )
            in_block = True
            block_start_line = line_number
            continue
        if has_end:
            if not in_block:
                raise ReaderProjectionSourceError(
                    f"Malformed projectable reader block at line {line_number}: end marker without start."
                )
            in_block = False
            block_start_line = 0
            continue
        if not in_block:
            kept.append(line)

    if in_block:
        raise ReaderProjectionSourceError(
            f"Malformed projectable reader block at line {block_start_line}: missing end marker."
        )
    return "\n".join(kept).rstrip() + "\n"


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
