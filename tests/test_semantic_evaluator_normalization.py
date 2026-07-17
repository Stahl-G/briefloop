"""Markdown block identity and span replay tests."""

from __future__ import annotations

import pytest

from multi_agent_brief.semantic_evaluator.contracts import BoundedRequirement
from multi_agent_brief.semantic_evaluator.errors import SemanticEvaluatorError
from multi_agent_brief.semantic_evaluator.normalization import (
    build_admitted_report_evidence,
    freeze_bounded_context,
    make_span_locator,
    normalize_markdown,
    replay_reader_artifact,
    replay_span,
    verify_admitted_report_evidence,
    verify_bounded_context,
)


MARKDOWN_LF = (
    "# 标题\n\n"
    "段落一。\n段落二。\n\n"
    "- 项目一\n- 项目二\n\n"
    "| 列一 | 列二 |\n| --- | --- |\n| 甲 | 乙 |\n\n"
    "```text\n合成代码\n```\n"
)


def test_markdown_blocks_v1_normalizes_bom_newlines_and_lexical_roles() -> None:
    raw = ("\ufeff" + MARKDOWN_LF.replace("\n", "\r\n")).encode("utf-8")
    normalized = normalize_markdown(raw, artifact_id="reader-synthetic-1")
    assert normalized.normalized_text == MARKDOWN_LF
    assert [item.role for item in normalized.artifact.blocks] == [
        "heading",
        "paragraph",
        "list",
        "table",
        "code",
    ]
    assert [item.block_id for item in normalized.artifact.blocks] == [
        "B000001",
        "B000002",
        "B000003",
        "B000004",
        "B000005",
    ]
    assert all(item.section_path == ["标题"] for item in normalized.artifact.blocks)
    replay_reader_artifact(normalized.artifact, normalized.normalized_text)


def test_raw_report_identity_and_normalized_text_identity_are_separate() -> None:
    lf = normalize_markdown(MARKDOWN_LF.encode(), artifact_id="reader-lf")
    crlf = normalize_markdown(
        MARKDOWN_LF.replace("\n", "\r\n").encode(), artifact_id="reader-crlf"
    )
    assert lf.artifact.report_sha256 != crlf.artifact.report_sha256
    assert lf.artifact.normalized_text_sha256 == crlf.artifact.normalized_text_sha256
    assert [item.text for item in lf.artifact.blocks] == [
        item.text for item in crlf.artifact.blocks
    ]


def test_admitted_report_evidence_retains_exact_bom_newline_and_gap_bytes() -> None:
    variants = (
        MARKDOWN_LF.encode(),
        MARKDOWN_LF.replace("\n", "\r\n").encode(),
        ("\ufeff" + MARKDOWN_LF).encode(),
        MARKDOWN_LF.replace("段落一。\n", "段落一。\n\n").encode(),
    )
    observed = [
        build_admitted_report_evidence(raw, artifact_id="reader-exact")
        for raw in variants
    ]
    assert len({evidence.report_sha256 for evidence, _reader in observed}) == 4
    assert len({evidence.evidence_sha256 for evidence, _reader in observed}) == 4
    assert observed[0][1].normalized_text == observed[1][1].normalized_text
    assert observed[0][1].normalized_text == observed[2][1].normalized_text
    for evidence, reader in observed:
        replayed = verify_admitted_report_evidence(
            evidence,
            reader_artifact=reader.artifact,
        )
        assert replayed == reader


def test_span_locator_uses_python_code_point_offsets_and_exact_hash_replay() -> None:
    reader = normalize_markdown(
        "# 标题\n\n甲乙丙。\n".encode(), artifact_id="reader-span"
    )
    block = reader.artifact.blocks[1]
    span = make_span_locator(
        reader.artifact,
        block_id=block.block_id,
        start_char=1,
        end_char=3,
    )
    assert replay_span(reader.artifact, span) == "乙丙"
    stale = span.model_copy(update={"excerpt_sha256": "0" * 64})
    with pytest.raises(SemanticEvaluatorError, match="span_excerpt_hash_mismatch"):
        replay_span(reader.artifact, stale)


@pytest.mark.parametrize("raw", [b"\xff", b"# ok\x00bad"])
def test_invalid_utf8_and_nul_fail_closed(raw: bytes) -> None:
    with pytest.raises(SemanticEvaluatorError) as caught:
        normalize_markdown(raw, artifact_id="reader-invalid")
    assert caught.value.reason_code == "input_not_utf8"


def test_invalid_utf8_errors_retain_no_raw_report_bytes() -> None:
    hidden_detail = b"PRIVATE-SYNTHETIC-CANARY-DO-NOT-RENDER"
    raw = hidden_detail + b"\xffTAIL"
    for operation in (
        lambda: normalize_markdown(raw, artifact_id="reader-invalid-value-free"),
        lambda: build_admitted_report_evidence(
            raw,
            artifact_id="reader-invalid-value-free",
        ),
    ):
        with pytest.raises(SemanticEvaluatorError) as caught:
            operation()
        assert caught.value.reason_code == "input_not_utf8"
        assert caught.value.__cause__ is None
        assert caught.value.__context__ is None
        assert hidden_detail.decode() not in repr(caught.value)


def test_exported_replay_errors_retain_no_serialized_caller_values() -> None:
    hidden_detail = "PRIVATE-SYNTHETIC-EXPORTED-VALUE"
    evidence, _reader = build_admitted_report_evidence(
        MARKDOWN_LF.encode(),
        artifact_id="reader-value-free-replay",
    )
    context = freeze_bounded_context(
        context_id="context-value-free-replay",
        data_class="synthetic",
        requirements=[
            BoundedRequirement(
                requirement_id="REQ-VALUE-FREE",
                type="must_answer",
                text="说明合成状态。",
                source_locator="brief:value-free",
            )
        ],
    )
    operations = (
        lambda: verify_admitted_report_evidence(
            evidence.model_copy(update={"report_bytes_hex": hidden_detail})
        ),
        lambda: verify_bounded_context(
            context.model_copy(update={"context_sha256": hidden_detail})
        ),
    )
    for operation in operations:
        with pytest.raises(SemanticEvaluatorError) as caught:
            operation()
        assert caught.value.reason_code == "input_sha_mismatch"
        assert caught.value.__cause__ is None
        assert caught.value.__context__ is None
        assert hidden_detail not in repr(caught.value)


def test_normalization_is_deterministic_and_does_not_reflow_text() -> None:
    first = normalize_markdown(MARKDOWN_LF.encode(), artifact_id="reader-stable")
    second = normalize_markdown(MARKDOWN_LF.encode(), artifact_id="reader-stable")
    assert first == second
    assert "段落一。\n段落二。" in first.artifact.blocks[1].text
