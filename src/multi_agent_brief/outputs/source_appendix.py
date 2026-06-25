from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path, PureWindowsPath
from typing import Any
from urllib.parse import urlparse

from multi_agent_brief.contracts.source_metadata import VALID_SOURCE_CATEGORIES
from multi_agent_brief.contracts.schemas.evidence_span_registry import EvidenceSpanRegistryContract
from multi_agent_brief.core.claim_ledger import ClaimLedger
from multi_agent_brief.core.schemas import Claim


_SRC_REF_RE = re.compile(r"\[src:([^\]]+)\]")
_LOCAL_PATH_MARKERS = ("/Users/", "/home/", "/private/", "/var/folders/")
_WINDOWS_USER_RE = re.compile(r"[A-Za-z]:\\Users\\")
_INTERNAL_ID_RE = re.compile(
    r"\b(?:SYN_)?(?:CLAIM|SRC|SOURCE|CLM)_[A-Z0-9][A-Z0-9_-]*\b"
)
_TRACE_EXCERPT_LIMIT = 500
_SOURCE_CATEGORY_LABELS = {
    "clin" + "ical_registry": "Clinical registry",
    "company_press_release": "Company press release",
    "industry_database": "Industry database",
    "market_report": "Market report",
    "news_media": "News media",
    "other": "Other",
    "peer_reviewed_paper": "Peer-reviewed paper",
    "preprint": "Preprint",
    "regulator": "Regulator",
}


@dataclass
class SourceAppendixRecord:
    label: str
    title: str
    source_id: str = ""
    source_ids: list[str] = field(default_factory=list)
    publisher: str = ""
    published_at: str = ""
    retrieved_at: str = ""
    url: str = ""
    source_type: str = ""
    source_category: str = ""
    claim_count: int = 0
    span_count: int = 0
    span_roles: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SourceAppendixResult:
    status: str
    source_count: int = 0
    cited_claim_count: int = 0
    resolved_claim_count: int = 0
    warnings: list[str] = field(default_factory=list)
    markdown: str = ""
    records: list[SourceAppendixRecord] = field(default_factory=list)
    citation_labels: dict[str, str] = field(default_factory=dict)
    claim_source_map: dict[str, dict[str, str]] = field(default_factory=dict)
    trace_status: str = "not_available"
    trace_markdown: str = ""
    trace_source_count: int = 0
    trace_span_count: int = 0
    trace_warnings: list[str] = field(default_factory=list)

    def to_report_fields(
        self,
        *,
        source_appendix: str,
        requested_by: str,
        mode: str,
    ) -> dict[str, Any]:
        return {
            "source_appendix": source_appendix,
            "source_appendix_generation": self.status,
            "source_appendix_requested_by": requested_by,
            "source_appendix_mode": mode,
            "source_appendix_source_count": self.source_count,
            "source_appendix_cited_claim_count": self.cited_claim_count,
            "source_appendix_resolved_claim_count": self.resolved_claim_count,
            "source_appendix_warnings": list(self.warnings),
            "source_appendix_claim_map": dict(self.claim_source_map),
            "source_appendix_trace_generation": self.trace_status,
            "source_appendix_trace_source_count": self.trace_source_count,
            "source_appendix_trace_span_count": self.trace_span_count,
            "source_appendix_trace_warnings": list(self.trace_warnings),
        }


def cited_claim_ids(markdown: str) -> list[str]:
    """Return [src:<claim_id>] references in first-appearance order."""
    seen: set[str] = set()
    claim_ids: list[str] = []
    for match in _SRC_REF_RE.finditer(markdown):
        claim_id = match.group(1).strip()
        if not claim_id or claim_id in seen:
            continue
        seen.add(claim_id)
        claim_ids.append(claim_id)
    return claim_ids


def build_source_appendix(
    *,
    audited_markdown: str,
    ledger_path: str | Path,
    evidence_span_registry_path: str | Path | None = None,
    workspace: str | Path | None = None,
) -> SourceAppendixResult:
    """Build reader-facing source appendix Markdown from cited Claim Ledger entries."""
    claim_ids = cited_claim_ids(audited_markdown)
    warnings: list[str] = []
    ledger = ClaimLedger.import_json(ledger_path)
    records_by_key: dict[str, SourceAppendixRecord] = {}
    claim_source_keys: dict[str, str] = {}
    source_ids_by_key: dict[str, list[str]] = {}
    order: list[str] = []
    resolved_claim_count = 0

    for claim_id in claim_ids:
        claim = ledger.get_claim(claim_id)
        if claim is None:
            warnings.append("A cited source reference could not be resolved.")
            continue
        resolved_claim_count += 1
        source_record, source_warnings = _record_from_claim(claim)
        warnings.extend(source_warnings)
        key = _source_key(claim, source_record)
        if key not in records_by_key:
            order.append(key)
            records_by_key[key] = source_record
        records_by_key[key].claim_count += 1
        claim_source_keys[claim_id] = key
        if claim.source_id:
            source_id = claim.source_id.strip()
            if source_id:
                source_ids = source_ids_by_key.setdefault(key, [])
                if source_id not in source_ids:
                    source_ids.append(source_id)

    records = [records_by_key[key] for key in order]
    for idx, record in enumerate(records, start=1):
        record.label = f"S{idx}"
        source_ids = source_ids_by_key.get(order[idx - 1], [])
        record.source_ids = list(source_ids)
        record.source_id = source_ids[0] if source_ids else ""
    citation_labels = {
        claim_id: records_by_key[key].label
        for claim_id, key in claim_source_keys.items()
        if key in records_by_key and records_by_key[key].label
    }
    claim_source_map = {
        claim_id: _claim_source_map_record(records_by_key[key])
        for claim_id, key in claim_source_keys.items()
        if key in records_by_key and records_by_key[key].label
    }
    trace = _build_source_appendix_trace(
        registry_path=evidence_span_registry_path,
        workspace=workspace,
        ledger_path=ledger_path,
        records=records,
    )

    status = "generated_with_warnings" if warnings else "generated"
    markdown = render_source_appendix(records, warnings=warnings)
    return SourceAppendixResult(
        status=status,
        source_count=len(records),
        cited_claim_count=len(claim_ids),
        resolved_claim_count=resolved_claim_count,
        warnings=warnings,
        markdown=markdown,
        records=records,
        citation_labels=citation_labels,
        claim_source_map=claim_source_map,
        trace_status=trace["status"],
        trace_markdown=trace["markdown"],
        trace_source_count=trace["source_count"],
        trace_span_count=trace["span_count"],
        trace_warnings=trace["warnings"],
    )


def replace_claim_citations_with_labels(
    markdown: str,
    citation_labels: dict[str, str],
) -> str:
    """Replace internal claim citations with reader-facing source labels."""

    def _replace(match: re.Match[str]) -> str:
        label = citation_labels.get(match.group(1).strip())
        return f"[{label}]" if label else ""

    text = _SRC_REF_RE.sub(_replace, markdown)
    text = re.compile(r"\[src:[^\]]*\]").sub("", text)
    text = re.sub(r"(\[S\d+\])(?:\s+\1)+", r"\1", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def render_source_appendix(
    records: list[SourceAppendixRecord],
    *,
    warnings: list[str] | None = None,
) -> str:
    lines = [
        "# Source Appendix",
        "",
        "This appendix lists source entries linked from the brief. It is a traceability surface, not semantic proof that every statement is correct.",
        "",
        "## Sources",
        "",
    ]
    if not records:
        lines.extend(["No reader-facing sources could be resolved from cited claims.", ""])
    for record in records:
        title = record.title or "Source record"
        lines.append(f"### [{record.label}] {title}")
        lines.append("")
        if record.source_category:
            lines.append(f"- Source category: {_source_category_label(record.source_category)}")
        if record.publisher:
            lines.append(f"- Publisher/Institution: {record.publisher}")
        if record.published_at:
            lines.append(f"- Published: {record.published_at}")
        if record.retrieved_at:
            lines.append(f"- Retrieved: {record.retrieved_at}")
        if record.url:
            lines.append(f"- URL: <{_markdown_autolink_url(record.url)}>")
        if record.source_type:
            lines.append(f"- Provider type: {record.source_type}")
        lines.append(f"- Used in: {record.claim_count} claim-backed statement{'s' if record.claim_count != 1 else ''}")
        if record.span_count:
            role_summary = ", ".join(_reader_role_label(role) for role in record.span_roles)
            lines.append(
                f"- Evidence trace: {record.span_count} span{'s' if record.span_count != 1 else ''}"
                + (f"; roles: {role_summary}" if role_summary else "")
            )
        lines.append("")
    if warnings:
        lines.extend([
            "## Appendix Notes",
            "",
            "Some cited source metadata was incomplete or omitted from reader-facing output.",
            "",
        ])
        for warning in sorted(dict.fromkeys(warnings)):
            lines.append(f"- {warning}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _record_from_claim(claim: Claim) -> tuple[SourceAppendixRecord, list[str]]:
    warnings: list[str] = []
    metadata = claim.metadata or {}
    raw_title = _first_text(
        metadata.get("source_title"),
        metadata.get("title"),
        metadata.get("source_name"),
    )
    raw_publisher = _first_text(metadata.get("publisher"), metadata.get("source_name"))
    title, title_warning = _safe_display_text(raw_title, field_name="source title")
    publisher, publisher_warning = _safe_display_text(raw_publisher, field_name="publisher")
    warnings.extend(item for item in (title_warning, publisher_warning) if item)

    url, url_warning = _safe_url(claim.source_url or _first_text(metadata.get("url"), metadata.get("source_url")))
    if url_warning:
        warnings.append(url_warning)
    published_at, published_warning = _safe_display_text(
        _first_text(metadata.get("published_at"), metadata.get("publication_date")),
        field_name="published date",
    )
    retrieved_at, retrieved_warning = _safe_display_text(
        _first_text(metadata.get("retrieved_at"), metadata.get("accessed_at")),
        field_name="retrieved date",
    )
    warnings.extend(item for item in (published_warning, retrieved_warning) if item)

    source_type, type_warning = _safe_display_text(claim.source_type, field_name="source type")
    if type_warning:
        warnings.append(type_warning)

    source_category, category_warning = _safe_source_category(
        _first_text(metadata.get("source_category"), metadata.get("evidence_category"))
    )
    if category_warning:
        warnings.append(category_warning)
    if not source_category:
        warnings.append("Source metadata missing source category.")
    if not title:
        warnings.append("Source metadata missing source title/name.")
    if not publisher:
        warnings.append("Source metadata missing publisher/institution.")

    if not title:
        title = "Source record"

    return (
        SourceAppendixRecord(
            label="",
            title=title,
            source_id=claim.source_id.strip() if claim.source_id else "",
            publisher=publisher,
            published_at=published_at,
            retrieved_at=retrieved_at,
            url=url,
            source_type=source_type,
            source_category=source_category,
        ),
        warnings,
    )


def _claim_source_map_record(record: SourceAppendixRecord) -> dict[str, str]:
    return {
        "source_label": record.label,
        "source_url": record.url,
        "evidence_title": record.title,
        "source_title": record.title,
        "source_published_at": record.published_at,
        "retrieved_at": record.retrieved_at,
        "source_type": record.source_type,
        "source_category": record.source_category,
    }


def _build_source_appendix_trace(
    *,
    registry_path: str | Path | None,
    workspace: str | Path | None,
    ledger_path: str | Path,
    records: list[SourceAppendixRecord],
) -> dict[str, Any]:
    empty = {
        "status": "not_available",
        "markdown": "",
        "source_count": 0,
        "span_count": 0,
        "warnings": [],
    }
    if registry_path is None:
        return empty
    path = Path(registry_path)
    if not path.exists():
        return empty
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return _trace_skip(f"Evidence span trace skipped because evidence_span_registry.json is unreadable: {exc}")
    if not isinstance(payload, dict):
        return _trace_skip("Evidence span trace skipped because evidence_span_registry.json is not an object.")

    violations = EvidenceSpanRegistryContract.validate(payload)
    errors = [violation for violation in violations if violation.severity == "error"]
    if errors:
        return _trace_skip(
            "Evidence span trace skipped because evidence_span_registry.json is invalid: "
            f"evidence_span_registry_schema_error:{errors[0].field}"
        )

    ws = Path(workspace).expanduser().resolve() if workspace is not None else _workspace_from_ledger_path(ledger_path)
    from multi_agent_brief.orchestrator.runtime_state.evidence_span_registry import (
        EVIDENCE_SPAN_REGISTRY_VALIDATION_PREFIX,
        validate_evidence_span_registry_against_source_pack,
    )

    reason = validate_evidence_span_registry_against_source_pack(
        registry_payload=payload,
        workspace=ws,
    )
    if reason:
        return _trace_skip(
            "Evidence span trace skipped because evidence_span_registry.json does not match source bytes: "
            f"{EVIDENCE_SPAN_REGISTRY_VALIDATION_PREFIX}:{reason}"
        )

    records_by_source_id: dict[str, SourceAppendixRecord] = {}
    trace_spans_by_label: dict[str, int] = {}
    trace_roles_by_label: dict[str, set[str]] = {}
    trace_sources: list[dict[str, Any]] = []
    for record in records:
        source_ids = record.source_ids or ([record.source_id] if record.source_id else [])
        for source_id in source_ids:
            normalized = str(source_id or "").strip()
            if normalized:
                records_by_source_id[normalized] = record
    for source in sorted(
        (item for item in payload.get("sources", []) if isinstance(item, dict)),
        key=lambda item: str(item.get("source_id") or ""),
    ):
        source_id = str(source.get("source_id") or "").strip()
        record = records_by_source_id.get(source_id)
        if record is None:
            continue
        spans = [
            span
            for span in source.get("spans", [])
            if isinstance(span, dict)
        ]
        if not spans:
            continue
        roles = sorted({
            str(span.get("span_role") or "").strip()
            for span in spans
            if str(span.get("span_role") or "").strip()
        })
        trace_spans_by_label[record.label] = trace_spans_by_label.get(record.label, 0) + len(spans)
        trace_roles_by_label.setdefault(record.label, set()).update(roles)
        trace_sources.append({
            "label": record.label,
            "title": record.title,
            "source_id": source_id,
            "source_path": str(source.get("source_path") or ""),
            "spans": sorted(spans, key=lambda item: str(item.get("span_id") or "")),
        })

    if not trace_sources:
        return _trace_skip("Evidence span trace skipped because no registry sources matched cited claims.")

    for record in records:
        if not record.label:
            continue
        record.span_count = trace_spans_by_label.get(record.label, 0)
        record.span_roles = sorted(trace_roles_by_label.get(record.label, set()))

    markdown = render_source_appendix_trace(trace_sources)
    return {
        "status": "generated",
        "markdown": markdown,
        "source_count": len(trace_sources),
        "span_count": sum(len(source["spans"]) for source in trace_sources),
        "warnings": [],
    }


def _trace_skip(message: str) -> dict[str, Any]:
    return {
        "status": "skipped",
        "markdown": "",
        "source_count": 0,
        "span_count": 0,
        "warnings": [message],
    }


def render_source_appendix_trace(trace_sources: list[dict[str, Any]]) -> str:
    lines = [
        "# Evidence Span Trace Audit Copy",
        "",
        "This audit copy lists machine-checkable Evidence Span Registry entries used by the source appendix. It is a traceability surface only, not semantic proof or support sufficiency.",
        "",
    ]
    for source in trace_sources:
        title = source.get("title") or "Local workspace source"
        lines.extend([
            f"## [{source.get('label')}] {title}",
            "",
            f"- Source ID: `{source.get('source_id')}`",
            f"- Source path: `{source.get('source_path')}`",
            f"- Span count: {len(source.get('spans') or [])}",
            "",
        ])
        for span in source.get("spans") or []:
            span_id = str(span.get("span_id") or "<unknown_span>").strip()
            lines.extend([
                f"### {span_id}",
                "",
                f"- Role: {_reader_role_label(str(span.get('span_role') or ''))}",
                f"- Raw excerpt hash: `{span.get('hash') or ''}`",
            ])
            if isinstance(span.get("char_start"), int) and isinstance(span.get("char_end"), int):
                lines.append(f"- Offsets: {span['char_start']}..{span['char_end']}")
            lines.extend(["", "Raw excerpt:", ""])
            lines.extend(_blockquote(_cap_excerpt(str(span.get("raw_excerpt") or ""))))
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _workspace_from_ledger_path(ledger_path: str | Path) -> Path:
    path = Path(ledger_path).expanduser().resolve()
    try:
        return path.parents[2]
    except IndexError:
        return path.parent


def _reader_role_label(role: str) -> str:
    return role.replace("_", " ").strip()


def _source_category_label(category: str) -> str:
    return _SOURCE_CATEGORY_LABELS.get(category, category.replace("_", " ").strip().title())


def _cap_excerpt(text: str) -> str:
    if len(text) <= _TRACE_EXCERPT_LIMIT:
        return text
    return text[:_TRACE_EXCERPT_LIMIT].rstrip() + "... [truncated]"


def _blockquote(text: str) -> list[str]:
    if not text:
        return [">"]
    return ["> " + line for line in text.splitlines()]


def _source_key(claim: Claim, record: SourceAppendixRecord) -> str:
    if record.url:
        return f"url:{_normalize(record.url)}"
    if claim.source_id:
        return f"source_id:{_normalize(claim.source_id)}"
    return f"title:{_normalize(record.title)}|publisher:{_normalize(record.publisher)}"


def _first_text(*values: Any) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _safe_display_text(value: str, *, field_name: str) -> tuple[str, str]:
    value = " ".join(str(value or "").split())
    if not value:
        return "", ""
    if _contains_private_path(value):
        return "", f"Omitted {field_name} because it looked like a local path."
    if value.lower().startswith("file://"):
        return "", f"Omitted {field_name} because it used a file:// reference."
    if _contains_internal_id(value):
        return "", f"Omitted {field_name} because it looked like an internal ID."
    return value, ""


def _safe_url(value: str) -> tuple[str, str]:
    raw = str(value or "").strip()
    if not raw:
        return "", ""
    if _contains_private_path(raw) or raw.lower().startswith("file://"):
        return "", "Omitted source URL because it was local or file-based."
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return "", "Omitted source URL because it was not an HTTP(S) URL."
    return raw, ""


def _markdown_autolink_url(value: str) -> str:
    return str(value).replace("<", "%3C").replace(">", "%3E").replace(" ", "%20")


def _safe_source_category(value: str) -> tuple[str, str]:
    raw = str(value or "").strip()
    if not raw:
        return "", ""
    if raw not in VALID_SOURCE_CATEGORIES:
        return "", "Omitted source category because it was not recognized."
    return raw, ""


def _contains_private_path(value: str) -> bool:
    if any(marker in value for marker in _LOCAL_PATH_MARKERS):
        return True
    if _WINDOWS_USER_RE.search(value):
        return True
    try:
        return PureWindowsPath(value).is_absolute()
    except ValueError:
        return False


def _contains_internal_id(value: str) -> bool:
    return bool(_INTERNAL_ID_RE.search(value))


def _normalize(value: str) -> str:
    return " ".join(str(value or "").strip().lower().split())
