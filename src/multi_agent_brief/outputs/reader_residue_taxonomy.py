from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Iterator, Literal

READER_PROJECTION_TRANSFORM_TYPE = "reader_projection"
READER_PROJECTION_TRANSFORM_VERSION = "v1"
READER_PROJECTION_TOOL_IDENTITY = "briefloop.reader_projection"
READER_PROJECTION_ALLOWED_OPERATIONS = (
    "citation_projection",
    "residue_neutralization",
    "marked_block_removal",
    "disclaimer_rewrite",
)

INTERNAL_CLAIM_ID_PATTERN = (
    r"(?:"
    r"CLM-\d+"
    r"|CL-\d+"
    r"|(?:[A-Z][A-Z0-9]*_)?CLAIM_[A-Z0-9][A-Z0-9_-]*"
    r"|claim-\d+"
    r")"
)
INTERNAL_CLAIM_ID_RE = re.compile(rf"^{INTERNAL_CLAIM_ID_PATTERN}$")
SRC_MARKER_RE = re.compile(r"\[(?:src|source):[^\]]+\]", re.IGNORECASE)
BRACKETED_SOURCE_MARKER_RE = re.compile(r"\[(?:src|source):\s*([^\]]*)\]", re.IGNORECASE)
BARE_SOURCE_MARKER_PREFIX_RE = re.compile(r"(?i)(?<!\[)\b(?:src|source):")
SOURCE_MARKER_START_RE = re.compile(r"(?i)\[(?:src|source):|(?<!\[)\b(?:src|source):")
CLAIM_ID_FRAGMENT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")
BARE_SRC_REF_RE = BARE_SOURCE_MARKER_PREFIX_RE
CLAIM_ID_RE = re.compile(
    rf"(?<![A-Za-z0-9_])(?:\[(?:{INTERNAL_CLAIM_ID_PATTERN})\]|{INTERNAL_CLAIM_ID_PATTERN})(?![A-Za-z0-9_-])"
)
SOURCE_ID_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:[A-Z][A-Z0-9]*_)?(?:SRC|SOURCE)_[A-Z0-9][A-Z0-9_-]*(?![A-Za-z0-9_])"
)
CONTEXTUAL_SRC_ID_RE = re.compile(
    r"(?i)(?:source[_\s-]*id|source\s+ref(?:erence)?|来源\s*ID|源\s*ID)[:：\s`'\"]*(SRC-\d{3,})"
)
LOCAL_PATH_RE = re.compile(r"(?:/Users/[^\s)]+|/mnt/data/[^\s)]+|file://[^\s)]+|[A-Za-z]:\\[^\s)]+)")
DEBUG_RE = re.compile(r"\b(?:DEBUG|TRACE)\b")
ATOM_ID_RE = re.compile(r"(?<![A-Za-z0-9_])AC-\d{4}-\d{2}(?![A-Za-z0-9_])")
SPAN_ID_RE = re.compile(r"(?<![A-Za-z0-9_])ESP-\d{3,4}-\d{2}(?![A-Za-z0-9_])")

PROCESS_WORDINGS = (
    "Analyst subagent",
    "Auditor subagent",
    "Claim Ledger",
    "source appendix generated from cited Claim Ledger",
    "Human review required before distribution",
    "audited_brief",
    "artifact_registry",
    "workflow_state",
    "quality_gate_report",
    "runtime_manifest",
    "event_log",
    "agent_handoff",
    "claim_ledger.json",
    "finalize_report.json",
    "atomic_claim_graph",
    "Atomic Claim Graph",
    "atom_id",
    "事实账本",
    "声明账本",
    "分析师子代理",
    "审计师子代理",
    "审计员子代理",
    "运行交接单",
    "运行清单",
    "工作流状态",
    "产物注册表",
    "质量门禁",
)

STANDARD_CLAIM_LEDGER_DISCLAIMERS = (
    (
        "This brief draws from the frozen Claim Ledger.",
        "This brief draws from registered source evidence.",
    ),
)

PROJECTABLE_BLOCK_START = "<!-- briefloop:projectable-reader-start -->"
PROJECTABLE_BLOCK_END = "<!-- briefloop:projectable-reader-end -->"

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_SOURCE_APPENDIX_TITLE_RE = re.compile(
    r"(?:source\s+appendix|source\s+appendices|来源附录|来源附|来源索引|引用附录)",
    re.IGNORECASE,
)
_INTERNAL_APPENDIX_RE = re.compile(
    r"(?:Claim Ledger|声明账本|CLM-\d{3,}|CL-\d{3,}|CLAIM_[A-Z0-9][A-Z0-9_-]*|input/sources/|source[_\s-]*id|来源\s*ID|sha256|artifact_registry|workflow_state)",
    re.IGNORECASE,
)
_CITATION_CONTEXT_RE = re.compile(
    r"(?i)(?:\bsee\b|\bcite[sd]?\b|\bcitation\b|\breference[sd]?\b|\bsource[sd]?\b|来源|引用|参考|参见)"
)

ProjectionFindingKind = Literal[
    "src_marker",
    "malformed_source_marker",
    "unresolved_source_marker",
    "malformed_projectable_block",
    "unmarked_source_appendix",
    "local_path",
    "debug_residue",
    "control_file_residue",
    "internal_process_wording",
    "bare_claim_id",
    "atom_id",
    "span_id",
    "source_id",
]
SourceMarkerStatus = Literal["not_marker", "valid_marker", "malformed_marker"]


@dataclass(frozen=True)
class ReaderProjectionFinding:
    kind: ProjectionFindingKind
    line: int | None
    text: str
    message: str

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "line": self.line,
            "text": self.text,
            "message": self.message,
        }


@dataclass(frozen=True)
class ReaderProjectionTransformResult:
    markdown: str
    applied_operations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SourceMarkerParse:
    status: SourceMarkerStatus
    claim_id: str
    text: str
    start: int
    end: int

    @property
    def valid(self) -> bool:
        return self.status == "valid_marker"

    @property
    def malformed(self) -> bool:
        return self.status == "malformed_marker"


class ReaderProjectionContractError(ValueError):
    """Raised when audited reader input needs content repair, not projection."""

    def __init__(self, findings: list[ReaderProjectionFinding]):
        self.findings = findings
        summary = "; ".join(f"{finding.kind}: {finding.text}" for finding in findings[:3])
        if len(findings) > 3:
            summary += f"; +{len(findings) - 3} more"
        super().__init__(f"Reader projection contract failed: {summary}")

    def to_report(self) -> dict[str, object]:
        return {
            "status": "fail",
            "findings": [finding.to_dict() for finding in self.findings],
        }


def project_reader_markdown(
    markdown: str,
    *,
    citation_labels: dict[str, str],
) -> ReaderProjectionTransformResult:
    """Project frozen audit text into a reader candidate using bounded operations."""

    source = reader_projection_source_markdown(markdown)
    return project_reader_source_markdown(
        source.markdown,
        citation_labels=citation_labels,
        initial_operations=source.applied_operations,
    )


def reader_projection_source_markdown(markdown: str) -> ReaderProjectionTransformResult:
    """Build the canonical reader source before citation and appendix projection."""

    projected, removed_blocks = _remove_marked_projectable_blocks(markdown)
    operations: set[str] = set()
    if removed_blocks:
        operations.add("marked_block_removal")

    _raise_contract_findings(_unmarked_source_appendix_findings(projected))
    projected = re.sub(r"\n{3,}", "\n\n", projected).strip()
    return ReaderProjectionTransformResult(
        markdown=projected,
        applied_operations=[op for op in READER_PROJECTION_ALLOWED_OPERATIONS if op in operations],
    )


def project_reader_source_markdown(
    markdown: str,
    *,
    citation_labels: dict[str, str],
    initial_operations: list[str] | tuple[str, ...] = (),
) -> ReaderProjectionTransformResult:
    """Project a canonical reader source into final reader-facing Markdown."""

    projected = markdown
    operations: set[str] = set()
    operations.update(initial_operations)
    warnings: list[str] = []

    projected, disclaimer_count = _rewrite_standard_disclaimers(projected)
    if disclaimer_count:
        operations.add("disclaimer_rewrite")

    projected, citation_count, citation_warnings, citation_findings = _project_citations(
        projected,
        citation_labels=citation_labels,
    )
    if citation_count:
        operations.add("citation_projection")
    warnings.extend(citation_warnings)

    residue_findings = _contract_findings(projected)
    if citation_findings:
        residue_findings = [
            finding for finding in residue_findings if finding.kind != "src_marker"
        ]
    _raise_contract_findings(citation_findings + residue_findings)
    projected = re.sub(r"\n{3,}", "\n\n", projected).strip()
    return ReaderProjectionTransformResult(
        markdown=projected,
        applied_operations=[op for op in READER_PROJECTION_ALLOWED_OPERATIONS if op in operations],
        warnings=warnings,
    )


def source_appendix_reference_markdown(markdown: str) -> str:
    """Normalize mechanically citable residue for source-appendix resolution only."""

    def _replace_source_marker(marker: SourceMarkerParse) -> str:
        if not marker.valid:
            return marker.text
        return f"[src:{marker.claim_id}]"

    text = _replace_source_markers(markdown, _replace_source_marker)
    text, _ = _project_citation_like_claim_ids_as_src_refs(text)
    return text


def count_source_markers(markdown: str) -> int:
    return sum(1 for _ in _iter_source_markers(markdown))


def _project_citations(
    markdown: str,
    *,
    citation_labels: dict[str, str],
) -> tuple[str, int, list[str], list[ReaderProjectionFinding]]:
    warnings: list[str] = []
    citation_findings: list[ReaderProjectionFinding] = []
    changed = 0

    def _replace_source_marker(marker: SourceMarkerParse) -> str:
        nonlocal changed
        changed += 1
        if marker.malformed:
            citation_findings.append(
                _source_marker_finding(
                    markdown,
                    marker,
                    kind="malformed_source_marker",
                    message="Internal source marker has an unsupported claim-id shape.",
                )
            )
            return marker.text
        label = citation_labels.get(marker.claim_id)
        if label:
            return f"[{label}]"
        citation_findings.append(
            _source_marker_finding(
                markdown,
                marker,
                kind="unresolved_source_marker",
                message=(
                    "Bare source marker could not be resolved to a reader-safe citation label."
                ),
            )
        )
        return marker.text

    text = _replace_source_markers(markdown, _replace_source_marker)
    text, bare_changed = _project_citation_like_claim_ids(text, citation_labels=citation_labels)
    changed += bare_changed
    text = re.sub(r"(\[S\d+\])(?:\s+\1)+", r"\1", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text, changed, warnings, citation_findings


def parse_bracketed_source_marker(match: re.Match[str]) -> SourceMarkerParse:
    return parse_source_marker_at(match.string, match.start())


def parse_bare_source_marker(markdown: str, match: re.Match[str]) -> SourceMarkerParse | None:
    marker = parse_source_marker_at(markdown, match.start())
    if marker.status == "not_marker":
        return None
    return marker


def parse_source_marker_at(markdown: str, start: int) -> SourceMarkerParse:
    bracketed = BRACKETED_SOURCE_MARKER_RE.match(markdown, start)
    if bracketed is not None:
        raw_claim_id = bracketed.group(1).strip()
        return SourceMarkerParse(
            status=(
                "valid_marker"
                if _is_internal_claim_id(raw_claim_id)
                else "malformed_marker"
            ),
            claim_id=raw_claim_id,
            text=bracketed.group(0),
            start=bracketed.start(),
            end=bracketed.end(),
        )

    bare = BARE_SOURCE_MARKER_PREFIX_RE.match(markdown, start)
    if bare is None:
        return SourceMarkerParse(
            status="not_marker",
            claim_id="",
            text="",
            start=start,
            end=start,
        )
    fragment = CLAIM_ID_FRAGMENT_RE.match(markdown, bare.end())
    if fragment is None:
        return SourceMarkerParse(
            status="not_marker",
            claim_id="",
            text=bare.group(0),
            start=bare.start(),
            end=bare.end(),
        )
    raw_claim_id = fragment.group(0).strip()
    if not _is_internal_claim_id_candidate(raw_claim_id):
        return SourceMarkerParse(
            status="not_marker",
            claim_id=raw_claim_id,
            text=markdown[bare.start() : fragment.end()],
            start=bare.start(),
            end=fragment.end(),
        )
    return SourceMarkerParse(
        status=(
            "valid_marker"
            if _is_internal_claim_id(raw_claim_id)
            else "malformed_marker"
        ),
        claim_id=raw_claim_id,
        text=markdown[bare.start() : fragment.end()],
        start=bare.start(),
        end=fragment.end(),
    )


def _iter_source_markers(markdown: str) -> Iterator[SourceMarkerParse]:
    for match in SOURCE_MARKER_START_RE.finditer(markdown):
        marker = parse_source_marker_at(markdown, match.start())
        if marker.status == "not_marker":
            continue
        yield marker


def _replace_source_markers(
    markdown: str,
    replacement: Callable[[SourceMarkerParse], str],
) -> str:
    parts: list[str] = []
    last = 0
    for marker in _iter_source_markers(markdown):
        parts.append(markdown[last:marker.start])
        parts.append(str(replacement(marker)))
        last = marker.end
    if not parts:
        return markdown
    parts.append(markdown[last:])
    return "".join(parts)


def _is_internal_claim_id(value: str) -> bool:
    return bool(INTERNAL_CLAIM_ID_RE.fullmatch(value))


def _is_internal_claim_id_candidate(value: str) -> bool:
    upper = value.upper()
    return (
        upper.startswith("CL-")
        or upper.startswith("CLM-")
        or upper.startswith("CLAIM_")
        or "_CLAIM_" in upper
        or value.startswith("claim-")
    )


def _source_marker_finding(
    markdown: str,
    marker: SourceMarkerParse,
    *,
    kind: ProjectionFindingKind,
    message: str,
) -> ReaderProjectionFinding:
    return ReaderProjectionFinding(
        kind=kind,
        line=markdown.count("\n", 0, marker.start) + 1,
        text=_shorten(marker.text),
        message=message,
    )


def _project_citation_like_claim_ids_as_src_refs(markdown: str) -> tuple[str, int]:
    changed = 0
    parts: list[str] = []
    last = 0
    for match in CLAIM_ID_RE.finditer(markdown):
        if _inside_src_marker(markdown, match.start(), match.end()):
            continue
        raw = match.group(0)
        claim_id = raw.strip("[]")
        if not _is_citation_like_context(markdown, match.start(), match.end()):
            continue
        parts.append(markdown[last:match.start()])
        parts.append(f"[src:{claim_id}]")
        last = match.end()
        changed += 1
    if not parts:
        return markdown, 0
    parts.append(markdown[last:])
    return "".join(parts), changed


def _project_citation_like_claim_ids(
    markdown: str,
    *,
    citation_labels: dict[str, str],
) -> tuple[str, int]:
    changed = 0
    parts: list[str] = []
    last = 0
    for match in CLAIM_ID_RE.finditer(markdown):
        if _inside_src_marker(markdown, match.start(), match.end()):
            continue
        raw = match.group(0)
        claim_id = raw.strip("[]")
        label = citation_labels.get(claim_id)
        if not label or not _is_citation_like_context(markdown, match.start(), match.end()):
            continue
        parts.append(markdown[last:match.start()])
        parts.append(f"[{label}]")
        last = match.end()
        changed += 1
    if not parts:
        return markdown, 0
    parts.append(markdown[last:])
    return "".join(parts), changed


def _is_citation_like_context(markdown: str, start: int, end: int) -> bool:
    line_start = markdown.rfind("\n", 0, start) + 1
    line_end = markdown.find("\n", end)
    if line_end == -1:
        line_end = len(markdown)
    line = markdown[line_start:line_end]
    before = markdown[line_start:start]
    after = markdown[end:line_end]
    if _CITATION_CONTEXT_RE.search(line):
        return True
    left = before.rstrip()
    right = after.lstrip()
    return left.endswith("(") and right.startswith(")")


def _inside_src_marker(markdown: str, start: int, end: int) -> bool:
    marker_start = markdown.rfind("[", 0, start)
    marker_end = markdown.find("]", end)
    if marker_start == -1 or marker_end == -1:
        return False
    segment = markdown[marker_start : marker_end + 1]
    return bool(BRACKETED_SOURCE_MARKER_RE.fullmatch(segment))


def _remove_marked_projectable_blocks(markdown: str) -> tuple[str, int]:
    count = 0
    lines = markdown.splitlines()
    kept: list[str] = []
    in_block = False
    block_start_line: int | None = None
    for line_number, line in enumerate(lines, start=1):
        if PROJECTABLE_BLOCK_START in line:
            if in_block:
                raise ReaderProjectionContractError(
                    [
                        ReaderProjectionFinding(
                            kind="malformed_projectable_block",
                            line=line_number,
                            text=_shorten(line.strip()),
                            message="Projectable reader block starts before the prior block is closed.",
                        )
                    ]
                )
            in_block = True
            block_start_line = line_number
            count += 1
            continue
        if PROJECTABLE_BLOCK_END in line:
            if not in_block:
                raise ReaderProjectionContractError(
                    [
                        ReaderProjectionFinding(
                            kind="malformed_projectable_block",
                            line=line_number,
                            text=_shorten(line.strip()),
                            message="Projectable reader block ends without a matching start marker.",
                        )
                    ]
                )
            in_block = False
            block_start_line = None
            continue
        if not in_block:
            kept.append(line)
    if in_block:
        raise ReaderProjectionContractError(
            [
                ReaderProjectionFinding(
                    kind="malformed_projectable_block",
                    line=block_start_line,
                    text=PROJECTABLE_BLOCK_START,
                    message="Projectable reader block is missing its end marker.",
                )
            ]
        )
    return "\n".join(kept), count


def _rewrite_standard_disclaimers(markdown: str) -> tuple[str, int]:
    count = 0
    text = markdown
    for source, replacement in STANDARD_CLAIM_LEDGER_DISCLAIMERS:
        count += text.count(source)
        text = text.replace(source, replacement)
    return text, count


def _unmarked_source_appendix_findings(markdown: str) -> list[ReaderProjectionFinding]:
    findings: list[ReaderProjectionFinding] = []
    lines = markdown.splitlines()
    index = 0
    while index < len(lines):
        heading = _HEADING_RE.match(lines[index])
        if not heading or not _SOURCE_APPENDIX_TITLE_RE.search(heading.group(2)):
            index += 1
            continue
        level = len(heading.group(1))
        start = index + 1
        section: list[str] = [lines[index]]
        index += 1
        while index < len(lines):
            next_heading = _HEADING_RE.match(lines[index])
            if next_heading and len(next_heading.group(1)) <= level:
                break
            section.append(lines[index])
            index += 1
        section_text = "\n".join(section)
        if _INTERNAL_APPENDIX_RE.search(section_text):
            findings.append(
                ReaderProjectionFinding(
                    kind="unmarked_source_appendix",
                    line=start,
                    text=_shorten(lines[start - 1].strip()),
                    message="Audited brief contains an unmarked source appendix with internal control/evidence residue.",
                )
            )
    return findings


def _contract_findings(markdown: str) -> list[ReaderProjectionFinding]:
    findings: list[ReaderProjectionFinding] = []
    control_terms = (
        "workflow_state",
        "artifact_registry",
        "event_log",
        "runtime_manifest",
        "quality_gate_report",
        "agent_handoff",
        "finalize_report.json",
        "claim_ledger.json",
    )
    for line_number, line in enumerate(markdown.splitlines(), start=1):
        _append_regex_findings(findings, "local_path", LOCAL_PATH_RE, line, line_number, "Local path residue is not projectable.")
        _append_regex_findings(findings, "src_marker", SRC_MARKER_RE, line, line_number, "Internal source markers require citation projection.")
        _append_regex_findings(findings, "debug_residue", DEBUG_RE, line, line_number, "Debug or trace residue is not projectable.")
        _append_regex_findings(findings, "atom_id", ATOM_ID_RE, line, line_number, "Atomic Claim Graph IDs are not projectable.")
        _append_regex_findings(findings, "span_id", SPAN_ID_RE, line, line_number, "Evidence Span Registry IDs are not projectable.")
        _append_regex_findings(findings, "source_id", SOURCE_ID_RE, line, line_number, "Internal source IDs are not projectable.")
        _append_regex_findings(findings, "source_id", CONTEXTUAL_SRC_ID_RE, line, line_number, "Internal source IDs are not projectable.")
        _append_regex_findings(findings, "bare_claim_id", CLAIM_ID_RE, line, line_number, "Bare internal claim IDs require editor repair or citation-like projection.")
        for term in control_terms:
            if term.lower() in line.lower():
                findings.append(
                    ReaderProjectionFinding(
                        kind="control_file_residue",
                        line=line_number,
                        text=_shorten(line.strip()),
                        message=f"Control-plane residue is not projectable: {term}.",
                    )
                )
        for wording in PROCESS_WORDINGS:
            if wording in control_terms:
                continue
            if wording.lower() in line.lower():
                findings.append(
                    ReaderProjectionFinding(
                        kind="internal_process_wording",
                        line=line_number,
                        text=_shorten(line.strip()),
                        message=f"Internal process wording is not projectable: {wording}.",
                    )
                )
    return findings


def _append_regex_findings(
    findings: list[ReaderProjectionFinding],
    kind: ProjectionFindingKind,
    regex: re.Pattern[str],
    line: str,
    line_number: int,
    message: str,
) -> None:
    for match in regex.finditer(line):
        findings.append(
            ReaderProjectionFinding(
                kind=kind,
                line=line_number,
                text=_shorten(match.group(0)),
                message=message,
            )
        )


def _raise_contract_findings(findings: list[ReaderProjectionFinding]) -> None:
    if findings:
        raise ReaderProjectionContractError(findings)


def _shorten(text: str, limit: int = 160) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "..."
