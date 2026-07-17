"""Deterministic Markdown block normalization and span replay."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable

from multi_agent_brief.semantic_evaluator.contracts import (
    BOUNDED_CONTEXT_SCHEMA_ID,
    READER_ARTIFACT_SCHEMA_ID,
    AdmittedReportEvidence,
    BoundedContext,
    BoundedRequirement,
    DataClass,
    ReaderArtifact,
    ReaderBlock,
    SpanLocator,
)
from multi_agent_brief.semantic_evaluator.errors import SemanticEvaluatorError
from multi_agent_brief.semantic_evaluator.serialization import (
    canonical_json_bytes,
    canonical_model_sha256,
    canonical_sha256,
    normalized_utf8_text,
    sha256_bytes,
    sha256_text,
)


NORMALIZER_VERSION = "markdown_blocks_v1"

_ATX_RE = re.compile(
    r"^(?P<indent> {0,3})(?P<marks>#{1,6})(?:[ \t]+(?P<title>.*)|[ \t]*)$"
)
_FENCE_RE = re.compile(r"^ {0,3}(?P<marks>`{3,}|~{3,}).*$")
_LIST_RE = re.compile(r"^ {0,3}(?:[-+*]|\d+[.)])[ \t]+")


@dataclass(frozen=True)
class _Line:
    start: int
    content_end: int
    full_end: int
    text: str


@dataclass(frozen=True)
class NormalizedReader:
    normalized_text: str
    artifact: ReaderArtifact


def _lines_with_offsets(text: str) -> list[_Line]:
    lines: list[_Line] = []
    cursor = 0
    for raw_line in text.splitlines(keepends=True):
        content = raw_line[:-1] if raw_line.endswith("\n") else raw_line
        content_end = cursor + len(content)
        full_end = cursor + len(raw_line)
        lines.append(_Line(cursor, content_end, full_end, content))
        cursor = full_end
    return lines


def _is_blank(line: _Line) -> bool:
    return not line.text.strip()


def _is_table_line(line: _Line) -> bool:
    stripped = line.text.strip()
    return "|" in stripped and stripped not in {"|", "||"}


def _heading_title(match: re.Match[str]) -> str:
    title = (match.group("title") or "").strip()
    return re.sub(r"[ \t]+#+[ \t]*$", "", title).strip()


def _closing_fence(line: _Line, opener: str) -> bool:
    stripped = line.text.lstrip(" ")
    if not stripped or stripped[0] != opener[0]:
        return False
    marks = stripped.split(maxsplit=1)[0]
    return set(marks) == {opener[0]} and len(marks) >= len(opener)


def normalize_markdown(markdown_bytes: bytes, *, artifact_id: str) -> NormalizedReader:
    text: str | None = None
    try:
        text = normalized_utf8_text(markdown_bytes)
    except ValueError:
        pass
    if text is None:
        raise SemanticEvaluatorError("input_not_utf8") from None
    if "\x00" in text:
        raise SemanticEvaluatorError("input_not_utf8")
    lines = _lines_with_offsets(text)
    blocks: list[ReaderBlock] = []
    section_path: list[str] = []
    index = 0

    while index < len(lines):
        line = lines[index]
        if _is_blank(line):
            index += 1
            continue
        start_index = index
        heading_match = _ATX_RE.fullmatch(line.text)
        fence_match = _FENCE_RE.match(line.text)
        if fence_match:
            role = "code"
            opener = fence_match.group("marks")
            index += 1
            while index < len(lines):
                if _closing_fence(lines[index], opener):
                    index += 1
                    break
                index += 1
        elif heading_match:
            role = "heading"
            index += 1
        elif _LIST_RE.match(line.text):
            role = "list"
            index += 1
            while index < len(lines) and not _is_blank(lines[index]):
                if _ATX_RE.fullmatch(lines[index].text) or _FENCE_RE.match(
                    lines[index].text
                ):
                    break
                if _is_table_line(lines[index]) and not _LIST_RE.match(
                    lines[index].text
                ):
                    break
                index += 1
        elif _is_table_line(line):
            role = "table"
            index += 1
            while (
                index < len(lines)
                and not _is_blank(lines[index])
                and _is_table_line(lines[index])
            ):
                index += 1
        else:
            role = "paragraph"
            index += 1
            while index < len(lines) and not _is_blank(lines[index]):
                next_line = lines[index]
                if (
                    _ATX_RE.fullmatch(next_line.text)
                    or _FENCE_RE.match(next_line.text)
                    or _LIST_RE.match(next_line.text)
                    or _is_table_line(next_line)
                ):
                    break
                index += 1

        end_index = max(start_index, index - 1)
        start_char = lines[start_index].start
        end_char = lines[end_index].content_end
        block_text = text[start_char:end_char]
        if role == "heading" and heading_match is not None:
            title = _heading_title(heading_match)
            if title:
                level = len(heading_match.group("marks"))
                section_path = section_path[: level - 1]
                section_path.append(title)
        block_number = len(blocks) + 1
        blocks.append(
            ReaderBlock(
                block_id=f"B{block_number:06d}",
                ordinal=block_number - 1,
                section_path=list(section_path),
                role=role,
                text=block_text,
                text_sha256=sha256_text(block_text),
                start_char=start_char,
                end_char=end_char,
            )
        )

    if not blocks:
        raise SemanticEvaluatorError("input_missing")
    artifact = ReaderArtifact(
        schema_version=READER_ARTIFACT_SCHEMA_ID,
        artifact_id=artifact_id,
        report_sha256=sha256_bytes(markdown_bytes),
        language="zh-CN",
        format="normalized_markdown",
        normalized_text_sha256=sha256_text(text),
        blocks=blocks,
    )
    replay_reader_artifact(artifact, text)
    return NormalizedReader(normalized_text=text, artifact=artifact)


def replay_reader_artifact(artifact: ReaderArtifact, normalized_text: str) -> None:
    if artifact.normalized_text_sha256 != sha256_text(normalized_text):
        raise SemanticEvaluatorError("input_sha_mismatch")
    for block in artifact.blocks:
        if block.end_char > len(normalized_text):
            raise SemanticEvaluatorError("input_sha_mismatch")
        if normalized_text[block.start_char : block.end_char] != block.text:
            raise SemanticEvaluatorError("input_sha_mismatch")
        if sha256_text(block.text) != block.text_sha256:
            raise SemanticEvaluatorError("input_sha_mismatch")


def make_span_locator(
    artifact: ReaderArtifact,
    *,
    block_id: str,
    start_char: int,
    end_char: int,
) -> SpanLocator:
    block = next((item for item in artifact.blocks if item.block_id == block_id), None)
    if block is None:
        raise SemanticEvaluatorError("span_block_unknown")
    if (
        isinstance(start_char, bool)
        or isinstance(end_char, bool)
        or start_char < 0
        or start_char >= end_char
        or end_char > len(block.text)
    ):
        raise SemanticEvaluatorError("span_offset_invalid")
    excerpt = block.text[start_char:end_char]
    return SpanLocator(
        report_sha256=artifact.report_sha256,
        block_id=block_id,
        start_char=start_char,
        end_char=end_char,
        excerpt_sha256=sha256_text(excerpt),
    )


def replay_span(artifact: ReaderArtifact, span: SpanLocator) -> str:
    if span.report_sha256 != artifact.report_sha256:
        raise SemanticEvaluatorError("span_report_mismatch")
    block = next(
        (item for item in artifact.blocks if item.block_id == span.block_id), None
    )
    if block is None:
        raise SemanticEvaluatorError("span_block_unknown")
    if span.start_char >= span.end_char or span.end_char > len(block.text):
        raise SemanticEvaluatorError("span_offset_invalid")
    excerpt = block.text[span.start_char : span.end_char]
    if sha256_text(excerpt) != span.excerpt_sha256:
        raise SemanticEvaluatorError("span_excerpt_hash_mismatch")
    return excerpt


def bounded_context_sha256(context: BoundedContext) -> str:
    return canonical_model_sha256(context, exclude=("context_sha256",))


def verify_bounded_context(context: BoundedContext) -> BoundedContext:
    strict: BoundedContext | None = None
    exact = False
    try:
        strict = BoundedContext.model_validate(context.model_dump(mode="json"))
        exact = canonical_json_bytes(strict) == canonical_json_bytes(
            context
        ) and strict.context_sha256 == bounded_context_sha256(strict)
    except Exception:
        pass
    if strict is None or not exact:
        raise SemanticEvaluatorError("input_sha_mismatch") from None
    return strict


def build_admitted_report_evidence(
    report_bytes: bytes,
    *,
    artifact_id: str,
) -> tuple[AdmittedReportEvidence, NormalizedReader]:
    if not report_bytes:
        raise SemanticEvaluatorError("input_missing")
    reader = normalize_markdown(report_bytes, artifact_id=artifact_id)
    payload = {
        "artifact_id": artifact_id,
        "report_bytes_hex": report_bytes.hex(),
        "report_sha256": sha256_bytes(report_bytes),
        "normalized_text_sha256": sha256_text(reader.normalized_text),
    }
    evidence = AdmittedReportEvidence.model_validate(
        {**payload, "evidence_sha256": canonical_sha256(payload)}
    )
    return evidence, reader


def verify_admitted_report_evidence(
    evidence: AdmittedReportEvidence,
    *,
    reader_artifact: ReaderArtifact | None = None,
) -> NormalizedReader:
    strict: AdmittedReportEvidence | None = None
    raw: bytes | None = None
    try:
        strict = AdmittedReportEvidence.model_validate(evidence.model_dump(mode="json"))
        raw = bytes.fromhex(strict.report_bytes_hex)
    except Exception:
        pass
    if strict is None or raw is None or raw.hex() != strict.report_bytes_hex:
        raise SemanticEvaluatorError("input_sha_mismatch") from None
    expected, reader = build_admitted_report_evidence(
        raw,
        artifact_id=strict.artifact_id,
    )
    exact = False
    try:
        exact = canonical_json_bytes(expected) == canonical_json_bytes(strict) and (
            reader_artifact is None
            or canonical_json_bytes(reader.artifact)
            == canonical_json_bytes(reader_artifact)
        )
    except Exception:
        pass
    if not exact:
        raise SemanticEvaluatorError("input_sha_mismatch") from None
    return reader


def freeze_bounded_context(
    *,
    context_id: str,
    data_class: DataClass,
    requirements: Iterable[BoundedRequirement],
) -> BoundedContext:
    payload = {
        "schema_version": BOUNDED_CONTEXT_SCHEMA_ID,
        "context_id": context_id,
        "language": "zh-CN",
        "data_class": data_class,
        "requirements": [item.model_dump(mode="json") for item in requirements],
    }
    frozen = BoundedContext.model_validate(
        {**payload, "context_sha256": canonical_sha256(payload)}
    )
    return verify_bounded_context(frozen)


__all__ = [
    "NORMALIZER_VERSION",
    "NormalizedReader",
    "build_admitted_report_evidence",
    "bounded_context_sha256",
    "freeze_bounded_context",
    "make_span_locator",
    "normalize_markdown",
    "replay_reader_artifact",
    "replay_span",
    "verify_admitted_report_evidence",
    "verify_bounded_context",
]
