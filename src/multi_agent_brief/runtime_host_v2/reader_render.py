"""Deterministic reader projection for the v2 finalize render.

The Store-frozen audited brief is the only input.  This module derives the
reader-facing markdown: internal ``[src:<claim_id>]`` citations become
``[S#]`` labels backed by a real source appendix, chart references are
normalized to output-relative paths, and a deterministic compliance footer
(bookkeeping identifiers plus a fixed disclaimer sentence) is stamped at the
end.  No wall-clock value enters the bytes so retries replay identically.
"""

from __future__ import annotations

import re

from multi_agent_brief.contracts.v2 import RunDirection
from multi_agent_brief.core.claim_ledger import ClaimLedger
from multi_agent_brief.core.citations import remove_src_marker_spans
from multi_agent_brief.outputs.reader_projection import (
    reader_projection_source_markdown,
)
from multi_agent_brief.outputs.source_appendix import (
    build_source_appendix_from_ledger,
    replace_claim_citations_with_labels,
)

_IMAGE_PARENT_PREFIX = re.compile(r"(!\[[^\]]*\])\(\.\./(charts/[^\)]+)\)")
_DISCLAIMER_ZH = "本报告基于公开信息与既定流程生成，不构成投资建议；引用与来源见文末附录。"
_DISCLAIMER_EN = (
    "This report is generated from public information under a fixed "
    "pipeline and is not investment advice; citations and sources are "
    "listed in the appendix."
)


def normalize_reader_image_paths(markdown: str) -> str:
    """Rewrite ``../charts/...`` image targets to output-relative paths.

    The audited brief lives under ``output/intermediate/`` where
    ``../charts`` is correct, but the reader copy lives at ``output/`` and
    every downstream consumer (docx converter containment rule, static HTML
    client rendering) resolves references from the output root.
    """

    return _IMAGE_PARENT_PREFIX.sub(r"\1(\2)", markdown)


def _compliance_footer(
    *,
    run_direction: RunDirection,
    run_id: str,
    store_revision: int,
) -> str:
    zh = str(run_direction.output_language).startswith("zh")
    disclaimer = _DISCLAIMER_ZH if zh else _DISCLAIMER_EN
    window = (
        f"{run_direction.report_window_start}"
        f"~{run_direction.report_window_end}"
    )
    meta = (
        f"编号 {run_id} ｜ 修订 r{store_revision} ｜ 窗口 {window}"
        f" ｜ 基准日 {run_direction.report_date}"
        if zh
        else (
            f"report {run_id} | revision r{store_revision} | window {window}"
            f" | as-of {run_direction.report_date}"
        )
    )
    return f"**{meta}**\n\n{disclaimer}"


def render_reader_markdown(
    *,
    audited_markdown: str,
    ledger: ClaimLedger | None,
    run_direction: RunDirection,
    run_id: str,
    store_revision: int,
) -> str:
    """Derive the complete reader markdown from frozen inputs only."""

    try:
        reader = reader_projection_source_markdown(audited_markdown)
    except Exception as exc:  # ReaderProjectionSourceError
        raise RuntimeError("reader_projection_source_failed") from exc
    reader = normalize_reader_image_paths(reader)

    citation_labels: dict[str, str] = {}
    appendix_markdown = ""
    if ledger is not None and len(ledger) > 0:
        appendix = build_source_appendix_from_ledger(
            audited_markdown=reader,
            ledger=ledger,
        )
        citation_labels = dict(appendix.citation_labels)
        appendix_markdown = appendix.markdown.strip()

    if citation_labels:
        reader = replace_claim_citations_with_labels(reader, citation_labels)
    else:
        reader = remove_src_marker_spans(reader)

    reader = reader.strip()
    if appendix_markdown:
        reader = f"{reader}\n\n{appendix_markdown}"
    footer = _compliance_footer(
        run_direction=run_direction,
        run_id=run_id,
        store_revision=store_revision,
    )
    return f"{reader}\n\n---\n\n{footer}\n"
