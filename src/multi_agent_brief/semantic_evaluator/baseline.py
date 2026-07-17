"""Independent structured-checklist plus deterministic-lint baseline."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from multi_agent_brief.semantic_evaluator.contracts import (
    BASELINE_SCHEMA_ID,
    AdmittedReportEvidence,
    BaselinePayload,
    BoundedContext,
    ChecklistItem,
    LintItem,
    ReaderArtifact,
)
from multi_agent_brief.semantic_evaluator.errors import SemanticEvaluatorError
from multi_agent_brief.semantic_evaluator.normalization import (
    make_span_locator,
    verify_admitted_report_evidence,
    verify_bounded_context,
)
from multi_agent_brief.semantic_evaluator.profile import (
    LoadedProfile,
)
from multi_agent_brief.semantic_evaluator.resources import (
    EvaluatorResourceError,
    resource_sha256,
)
from multi_agent_brief.semantic_evaluator.serialization import (
    canonical_json_bytes,
    canonical_sha256,
)
from multi_agent_brief.semantic_evaluator.snapshot import (
    CHECKLIST_RESOURCE,
    EvaluatorResourceSnapshot,
    acquire_resource_snapshot,
)


LINT_VERSION = "deterministic_lint_v1"

_PLACEHOLDER_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:TODO|TBD)(?![A-Za-z0-9_])|待补|待确认|待核实",
    re.IGNORECASE,
)
_HEADING_RE = re.compile(r"^ {0,3}(?P<marks>#{1,6})(?:[ \t]+(?P<title>.*)|[ \t]*)$")
_EMPTY_HEADING_RE = re.compile(r"^ {0,3}#{1,6}[ \t]*#*[ \t]*$")
_FENCE_RE = re.compile(r"^ {0,3}(?P<marks>`{3,}|~{3,}).*$")
_LINK_RE = re.compile(r"\[[^\]\n]+\]\((?P<destination>[^)\n]*)\)")
_LINK_OPEN_RE = re.compile(r"\[[^\]\n]+\]\(")


def checklist_resource_sha256() -> str:
    return resource_sha256("baselines", CHECKLIST_RESOURCE)


def _lint_id(rule_id: str, block_id: str, start: int, end: int, ordinal: int) -> str:
    return f"lint-{canonical_sha256([rule_id, block_id, start, end, ordinal])[:12]}"


def _make_lint(
    *,
    artifact: ReaderArtifact,
    rule_id: str,
    message: str,
    block_id: str,
    start: int,
    end: int,
    ordinal: int,
) -> LintItem:
    span = make_span_locator(
        artifact,
        block_id=block_id,
        start_char=start,
        end_char=end,
    )
    return LintItem(
        item_id=_lint_id(rule_id, block_id, start, end, ordinal),
        ordinal=ordinal,
        rule_id=rule_id,
        message=message,
        report_spans=[span],
    )


def _normalized_heading(block_text: str) -> tuple[int, str] | None:
    match = _HEADING_RE.fullmatch(block_text)
    if match is None:
        return None
    title = (match.group("title") or "").strip()
    title = re.sub(r"[ \t]+#+[ \t]*$", "", title).strip()
    normalized = unicodedata.normalize("NFKC", title).casefold()
    return len(match.group("marks")), normalized


def deterministic_lint(artifact: ReaderArtifact) -> list[LintItem]:
    candidates: list[tuple[str, str, str, int, int]] = []
    for block in artifact.blocks:
        for match in _PLACEHOLDER_RE.finditer(block.text):
            candidates.append(
                (
                    "unresolved_placeholder",
                    "检测到未解决的占位标记，请人工核对。",
                    block.block_id,
                    match.start(),
                    match.end(),
                )
            )
    for block in artifact.blocks:
        if block.role == "heading" and _EMPTY_HEADING_RE.fullmatch(block.text):
            candidates.append(
                (
                    "empty_atx_heading",
                    "检测到空 ATX 标题，请人工核对结构。",
                    block.block_id,
                    0,
                    len(block.text),
                )
            )
    seen_headings: set[tuple[int, str]] = set()
    for block in artifact.blocks:
        if block.role != "heading":
            continue
        identity = _normalized_heading(block.text)
        if identity is None or not identity[1]:
            continue
        if identity in seen_headings:
            candidates.append(
                (
                    "duplicate_atx_heading",
                    "检测到同层级重复标题，请人工核对结构。",
                    block.block_id,
                    0,
                    len(block.text),
                )
            )
        seen_headings.add(identity)
    for block in artifact.blocks:
        if block.role != "code":
            continue
        lines = block.text.splitlines()
        opener = _FENCE_RE.match(lines[0]) if lines else None
        if opener is None:
            continue
        marks = opener.group("marks")
        closing = re.compile(rf"^ {{0,3}}{re.escape(marks[0])}{{{len(marks)},}}[ \t]*$")
        if len(lines) == 1 or closing.fullmatch(lines[-1]) is None:
            candidates.append(
                (
                    "unclosed_fenced_code",
                    "检测到未闭合的 fenced code block，请人工核对结构。",
                    block.block_id,
                    0,
                    len(lines[0]),
                )
            )
    for block in artifact.blocks:
        covered_starts: set[int] = set()
        for match in _LINK_RE.finditer(block.text):
            covered_starts.add(match.start())
            destination = match.group("destination")
            if not destination or any(
                char.isspace() or ord(char) < 32 for char in destination
            ):
                candidates.append(
                    (
                        "malformed_markdown_link_destination",
                        "检测到可确定识别的异常 Markdown 链接目标，请人工核对。",
                        block.block_id,
                        match.start(),
                        match.end(),
                    )
                )
        for match in _LINK_OPEN_RE.finditer(block.text):
            if match.start() in covered_starts:
                continue
            line_end = block.text.find("\n", match.start())
            line_end = len(block.text) if line_end < 0 else line_end
            if ")" not in block.text[match.end() : line_end]:
                candidates.append(
                    (
                        "malformed_markdown_link_destination",
                        "检测到未闭合的 Markdown 链接目标，请人工核对。",
                        block.block_id,
                        match.start(),
                        match.end(),
                    )
                )
    return [
        _make_lint(
            artifact=artifact,
            rule_id=rule_id,
            message=message,
            block_id=block_id,
            start=start,
            end=end,
            ordinal=ordinal,
        )
        for ordinal, (rule_id, message, block_id, start, end) in enumerate(candidates)
    ]


def _build_baseline(
    *,
    report_evidence: AdmittedReportEvidence,
    reader_artifact: ReaderArtifact,
    bounded_context: BoundedContext,
    loaded_profile: LoadedProfile | None = None,
    _resource_snapshot: EvaluatorResourceSnapshot | None = None,
) -> BaselinePayload:
    try:
        verify_admitted_report_evidence(
            report_evidence,
            reader_artifact=reader_artifact,
        )
        bounded_context = verify_bounded_context(bounded_context)
    except SemanticEvaluatorError as exc:
        raise SemanticEvaluatorError("baseline_input_binding_mismatch") from exc
    try:
        resources = _resource_snapshot or acquire_resource_snapshot(
            loaded_profile=loaded_profile,
            include_baseline=True,
        )
    except EvaluatorResourceError:
        raise SemanticEvaluatorError("baseline_input_binding_mismatch") from None
    profile = resources.loaded_profile
    template = resources.checklist
    if template is None:
        raise SemanticEvaluatorError("baseline_input_binding_mismatch")
    checklist_items: list[ChecklistItem] = []
    for item in template.items:
        ordinal = len(checklist_items)
        checklist_items.append(
            ChecklistItem(
                item_id=f"check-{canonical_sha256(['dimension', item.dimension_id])[:12]}",
                ordinal=ordinal,
                category="profile_dimension",
                dimension_id=item.dimension_id,
                requirement_id=None,
                requirement_type=None,
                text=item.text,
            )
        )
    for requirement in bounded_context.requirements:
        ordinal = len(checklist_items)
        checklist_items.append(
            ChecklistItem(
                item_id=f"check-{canonical_sha256(['requirement', requirement.requirement_id, requirement.type, requirement.text])[:12]}",
                ordinal=ordinal,
                category="bounded_requirement",
                dimension_id=None,
                requirement_id=requirement.requirement_id,
                requirement_type=requirement.type,
                text=f"请人工检查冻结要求 {requirement.requirement_id}（{requirement.type}）：{requirement.text}",
            )
        )
    payload: dict[str, Any] = {
        "schema_version": BASELINE_SCHEMA_ID,
        "baseline_id": f"baseline-{canonical_sha256([reader_artifact.report_sha256, bounded_context.context_sha256, profile.profile_sha256, template.sha256, LINT_VERSION])[:12]}",
        "report_sha256": reader_artifact.report_sha256,
        "bounded_context_sha256": bounded_context.context_sha256,
        "profile_sha256": profile.profile_sha256,
        "checklist_id": "structured_checklist_zh_v1",
        "lint_id": LINT_VERSION,
        "checklist_items": [item.model_dump(mode="json") for item in checklist_items],
        "lint_items": [
            item.model_dump(mode="json") for item in deterministic_lint(reader_artifact)
        ],
    }
    return BaselinePayload.model_validate(
        {**payload, "baseline_sha256": canonical_sha256(payload)}
    )


def build_baseline(
    *,
    report_evidence: AdmittedReportEvidence,
    reader_artifact: ReaderArtifact,
    bounded_context: BoundedContext,
    loaded_profile: LoadedProfile | None = None,
    _resource_snapshot: EvaluatorResourceSnapshot | None = None,
) -> BaselinePayload:
    result: BaselinePayload | None = None
    try:
        result = _build_baseline(
            report_evidence=report_evidence,
            reader_artifact=reader_artifact,
            bounded_context=bounded_context,
            loaded_profile=loaded_profile,
            _resource_snapshot=_resource_snapshot,
        )
    except (
        AttributeError,
        EvaluatorResourceError,
        KeyError,
        TypeError,
        ValueError,
        SemanticEvaluatorError,
    ):
        pass
    if result is None:
        raise SemanticEvaluatorError("baseline_input_binding_mismatch") from None
    return result


def verify_baseline_payload(
    baseline: BaselinePayload,
    *,
    report_evidence: AdmittedReportEvidence,
    reader_artifact: ReaderArtifact,
    bounded_context: BoundedContext,
    loaded_profile: LoadedProfile | None = None,
) -> BaselinePayload:
    verified: BaselinePayload | None = None
    try:
        resources = acquire_resource_snapshot(
            loaded_profile=loaded_profile,
            include_baseline=True,
        )
        strict = BaselinePayload.model_validate(baseline.model_dump(mode="json"))
        expected = build_baseline(
            report_evidence=report_evidence,
            reader_artifact=reader_artifact,
            bounded_context=bounded_context,
            _resource_snapshot=resources,
        )
        if canonical_json_bytes(strict) == canonical_json_bytes(expected):
            verified = strict
    except (
        AttributeError,
        EvaluatorResourceError,
        KeyError,
        TypeError,
        ValueError,
        SemanticEvaluatorError,
    ):
        pass
    if verified is None:
        raise SemanticEvaluatorError("baseline_input_binding_mismatch") from None
    return verified


__all__ = [
    "CHECKLIST_RESOURCE",
    "LINT_VERSION",
    "build_baseline",
    "checklist_resource_sha256",
    "deterministic_lint",
    "verify_baseline_payload",
]
