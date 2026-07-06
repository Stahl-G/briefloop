"""Preflight contract for frozen audited briefs before reader projection."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Literal


AUDITED_BRIEF_CONTRACT_SCHEMA_VERSION = "briefloop.audited_brief_contract.v1"

FindingKind = Literal[
    "unmarked_internal_source_appendix",
    "internal_process_wording",
    "local_path",
    "source_list_internal_id",
    "unterminated_internal_block",
]

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_SOURCE_APPENDIX_TITLE_RE = re.compile(
    r"(?i)^(?:source\s+appendix|source\s+index|references|引用附录|来源附录|来源索引|引用索引)$"
)
_SRC_MARKER_RE = re.compile(r"\[(?:src|source):[^\]]+\]", re.IGNORECASE)
_CLAIM_ID_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:CLM-\d{3,}|CL-\d{3,}|(?:[A-Z][A-Z0-9]*_)?CLAIM_[A-Z0-9][A-Z0-9_-]*)(?![A-Za-z0-9_])"
)
_SOURCE_ID_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:SRC-\d{3,}|(?:[A-Z][A-Z0-9]*_)?(?:SRC|SOURCE)_[A-Z0-9][A-Z0-9_-]*)(?![A-Za-z0-9_])"
)
_LOCAL_PATH_RE = re.compile(r"(?:/Users/[^\s)]+|/private/[^\s)]+|/tmp/[^\s)]+|file://[^\s)]+|[A-Za-z]:\\[^\s)]+)")
_INTERNAL_BLOCK_START_RE = re.compile(r"^\s*<!--\s*briefloop:internal(?:\s+start|-start)?\s*-->\s*$", re.IGNORECASE)
_INTERNAL_BLOCK_END_RE = re.compile(r"^\s*<!--\s*(?:/briefloop:internal|briefloop:internal\s+end|briefloop:internal-end)\s*-->\s*$", re.IGNORECASE)

_PROCESS_WORDINGS = (
    "Claim Ledger",
    "source appendix generated from cited Claim Ledger",
    "artifact_registry",
    "workflow_state",
    "runtime_manifest",
    "agent_handoff",
    "quality_gate_report",
    "claim_ledger.json",
    "finalize_report.json",
    "input/sources/",
    "事实账本",
    "声明账本",
    "产物注册表",
    "工作流状态",
    "运行清单",
    "运行交接单",
    "质量门禁",
)


@dataclass(frozen=True)
class AuditedBriefContractFinding:
    kind: FindingKind
    line: int | None
    text: str
    message: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class AuditedBriefContractResult:
    status: Literal["pass", "fail"]
    schema_version: str
    artifact: str
    finding_count: int
    findings: list[AuditedBriefContractFinding]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "status": self.status,
            "artifact": self.artifact,
            "finding_count": self.finding_count,
            "findings": [finding.to_dict() for finding in self.findings],
            "runtime_effect": "finalize_preflight_only_not_delivery_authority",
        }


class AuditedBriefContractError(ValueError):
    """Raised when an audited brief cannot enter reader projection."""

    def __init__(self, result: AuditedBriefContractResult) -> None:
        super().__init__(
            "audited_brief.md failed the reader projection preflight contract"
        )
        self.result = result


def validate_audited_brief_contract(
    markdown: str,
    *,
    artifact: str = "output/intermediate/audited_brief.md",
) -> AuditedBriefContractResult:
    """Validate deterministic boundaries for audited brief reader projection.

    The finalizer may transform internal ``[src:...]`` citation tokens into
    reader-facing labels. It must not silently project away unmarked internal
    appendices, control-file prose, local paths, or source-list rows that expose
    internal claim/source IDs.
    """

    findings: list[AuditedBriefContractFinding] = []
    source_appendix_level: int | None = None
    in_internal_block = False

    for line_number, line in enumerate(markdown.splitlines(), start=1):
        if _INTERNAL_BLOCK_START_RE.match(line):
            in_internal_block = True
            continue
        if _INTERNAL_BLOCK_END_RE.match(line):
            in_internal_block = False
            continue
        if in_internal_block:
            continue

        heading = _HEADING_RE.match(line)
        if heading:
            level = len(heading.group(1))
            title = heading.group(2).strip()
            if source_appendix_level is not None and level <= source_appendix_level:
                source_appendix_level = None
            if _SOURCE_APPENDIX_TITLE_RE.match(title):
                source_appendix_level = level
                continue

        sanitized_line = _SRC_MARKER_RE.sub("", line)
        if source_appendix_level is not None:
            _collect_source_appendix_findings(
                findings,
                line=sanitized_line,
                line_number=line_number,
            )

        _collect_process_wording_findings(
            findings,
            line=sanitized_line,
            line_number=line_number,
        )
        _collect_local_path_findings(
            findings,
            line=sanitized_line,
            line_number=line_number,
        )

    if in_internal_block:
        findings.append(
            AuditedBriefContractFinding(
                kind="unterminated_internal_block",
                line=None,
                text="briefloop:internal",
                message="Audited brief contains an unterminated internal block marker.",
            )
        )

    status: Literal["pass", "fail"] = "fail" if findings else "pass"
    return AuditedBriefContractResult(
        status=status,
        schema_version=AUDITED_BRIEF_CONTRACT_SCHEMA_VERSION,
        artifact=artifact,
        finding_count=len(findings),
        findings=findings,
    )


def require_audited_brief_contract_pass(result: AuditedBriefContractResult) -> None:
    if result.status != "pass":
        raise AuditedBriefContractError(result)


def _collect_source_appendix_findings(
    findings: list[AuditedBriefContractFinding],
    *,
    line: str,
    line_number: int,
) -> None:
    internal_terms = [
        *_PROCESS_WORDINGS,
        "source_id",
        "source id",
        "claim_id",
        "claim id",
        "来源 ID",
        "声明 ID",
    ]
    if _matches_any(line, internal_terms):
        findings.append(
            AuditedBriefContractFinding(
                kind="unmarked_internal_source_appendix",
                line=line_number,
                text=_shorten(line),
                message=(
                    "Audited brief contains an unmarked source appendix that exposes "
                    "internal control IDs, source paths, or Claim Ledger wording."
                ),
            )
        )
        return
    if _looks_like_source_list_row(line) and (
        _CLAIM_ID_RE.search(line) or _SOURCE_ID_RE.search(line)
    ):
        findings.append(
            AuditedBriefContractFinding(
                kind="source_list_internal_id",
                line=line_number,
                text=_shorten(line),
                message="Audited brief source-list row exposes internal claim/source IDs.",
            )
        )
        return
    if _CLAIM_ID_RE.search(line) or _SOURCE_ID_RE.search(line):
        findings.append(
            AuditedBriefContractFinding(
                kind="unmarked_internal_source_appendix",
                line=line_number,
                text=_shorten(line),
                message=(
                    "Audited brief contains an unmarked source appendix that exposes "
                    "internal claim/source IDs."
                ),
            )
        )


def _collect_process_wording_findings(
    findings: list[AuditedBriefContractFinding],
    *,
    line: str,
    line_number: int,
) -> None:
    for wording in _PROCESS_WORDINGS:
        if _matches_any(line, (wording,)):
            findings.append(
                AuditedBriefContractFinding(
                    kind="internal_process_wording",
                    line=line_number,
                    text=_shorten(wording),
                    message=(
                        "Audited brief contains reader-facing internal workflow or "
                        "control-plane wording before projection."
                    ),
                )
            )


def _collect_local_path_findings(
    findings: list[AuditedBriefContractFinding],
    *,
    line: str,
    line_number: int,
) -> None:
    for match in _LOCAL_PATH_RE.finditer(line):
        findings.append(
            AuditedBriefContractFinding(
                kind="local_path",
                line=line_number,
                text=_shorten(match.group(0)),
                message="Audited brief contains a local filesystem path before projection.",
            )
        )


def _matches_any(line: str, values: tuple[str, ...] | list[str]) -> bool:
    lower = line.lower()
    for value in values:
        if _has_cjk(value):
            if value in line:
                return True
        elif value.lower() in lower:
            return True
    return False


def _looks_like_source_list_row(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith(("-", "*", "|")) or bool(re.match(r"^\d+\.\s+", stripped))


def _has_cjk(value: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in value)


def _shorten(value: str, *, limit: int = 160) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"
