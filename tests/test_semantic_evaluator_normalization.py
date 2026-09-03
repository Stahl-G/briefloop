"""Markdown block identity and span replay tests."""

from __future__ import annotations

from multi_agent_brief.semantic_evaluator.normalization import (
    normalize_markdown,
    replay_reader_artifact,
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
