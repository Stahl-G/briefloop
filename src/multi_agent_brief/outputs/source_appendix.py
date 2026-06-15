from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path, PureWindowsPath
from typing import Any
from urllib.parse import urlparse

from multi_agent_brief.core.claim_ledger import ClaimLedger
from multi_agent_brief.core.schemas import Claim


_SRC_REF_RE = re.compile(r"\[src:([^\]]+)\]")
_LOCAL_PATH_MARKERS = ("/Users/", "/home/", "/private/", "/var/folders/")
_WINDOWS_USER_RE = re.compile(r"[A-Za-z]:\\Users\\")
_INTERNAL_ID_RE = re.compile(
    r"\b(?:SYN_)?(?:CLAIM|SRC|SOURCE|CLM)_[A-Z0-9][A-Z0-9_-]*\b"
)


@dataclass
class SourceAppendixRecord:
    label: str
    title: str
    publisher: str = ""
    published_at: str = ""
    retrieved_at: str = ""
    url: str = ""
    source_type: str = ""
    claim_count: int = 0

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
) -> SourceAppendixResult:
    """Build reader-facing source appendix Markdown from cited Claim Ledger entries."""
    claim_ids = cited_claim_ids(audited_markdown)
    warnings: list[str] = []
    ledger = ClaimLedger.import_json(ledger_path)
    records_by_key: dict[str, SourceAppendixRecord] = {}
    claim_source_keys: dict[str, str] = {}
    order: list[str] = []
    resolved_claim_count = 0

    for claim_id in claim_ids:
        claim = ledger.get_claim(claim_id)
        if claim is None:
            warnings.append("A cited claim was not found in the Claim Ledger.")
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

    records = [records_by_key[key] for key in order]
    for idx, record in enumerate(records, start=1):
        record.label = f"S{idx}"
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
        title = record.title or "Local workspace source"
        lines.append(f"### [{record.label}] {title}")
        lines.append("")
        if record.publisher:
            lines.append(f"- Publisher: {record.publisher}")
        if record.published_at:
            lines.append(f"- Published: {record.published_at}")
        if record.retrieved_at:
            lines.append(f"- Retrieved: {record.retrieved_at}")
        if record.url:
            lines.append(f"- URL: {record.url}")
        if record.source_type and record.source_type != "local_file":
            lines.append(f"- Source type: {record.source_type}")
        lines.append(f"- Used in: {record.claim_count} claim-backed statement{'s' if record.claim_count != 1 else ''}")
        lines.append("")
    if warnings:
        lines.extend([
            "## Appendix Notes",
            "",
            "Some cited source metadata was incomplete or omitted from reader-facing output.",
            "",
        ])
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

    if not title:
        title = "Local workspace source" if not url else "Source record"

    return (
        SourceAppendixRecord(
            label="",
            title=title,
            publisher=publisher,
            published_at=published_at,
            retrieved_at=retrieved_at,
            url=url,
            source_type=source_type,
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
    }


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
