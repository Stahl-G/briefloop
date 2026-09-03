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


def compliance_footer_text(
    *,
    run_direction: RunDirection,
    run_id: str,
    store_revision: int,
) -> str:
    """Single-line plain-text footer for non-markdown reader artifacts."""

    zh = str(run_direction.output_language).startswith("zh")
    disclaimer = _DISCLAIMER_ZH if zh else _DISCLAIMER_EN
    window = (
        f"{run_direction.report_window_start}"
        f"~{run_direction.report_window_end}"
    )
    if zh:
        return (
            f"{run_id} r{store_revision} {window} 基准日 "
            f"{run_direction.report_date} — {disclaimer}"
        )
    return (
        f"{run_id} r{store_revision} {window} as-of "
        f"{run_direction.report_date} — {disclaimer}"
    )


def render_reader_markdown(
    *,
    audited_markdown: str,
    ledger: ClaimLedger | None,
    run_direction: RunDirection,
    run_id: str,
    store_revision: int,
    upcoming_events: list[dict[str, object]] | None = None,
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

    reader = _inject_catalyst_calendar(reader, run_direction, upcoming_events)
    reader = reader.strip()
    if appendix_markdown:
        reader = f"{reader}\n\n{appendix_markdown}"
    footer = _compliance_footer(
        run_direction=run_direction,
        run_id=run_id,
        store_revision=store_revision,
    )
    return f"{reader}\n\n---\n\n{footer}\n"


def _inject_catalyst_calendar(
    markdown: str,
    run_direction: RunDirection,
    upcoming_events: list[dict[str, object]] | None,
) -> str:
    """Render the deterministic catalyst calendar into the reader copy.

    Python owns the table: only events strictly after the report date,
    same-day events all shown, and an explicit empty-state line when
    nothing qualifies.  Injected after the citation-to-label pass so no
    internal ids can leak into the reader output.
    """

    from multi_agent_brief.quality_gates.metric_pairing import (
        render_catalyst_calendar,
    )

    events = [
        item
        for item in (upcoming_events or [])
        if isinstance(item, dict)
        and str(item.get("date") or "") > str(run_direction.report_date)
    ]
    zh = str(run_direction.output_language).startswith("zh")
    table = render_catalyst_calendar(events, zh=zh)
    lines = markdown.splitlines()
    for index, line in enumerate(lines):
        if not line.startswith("#"):
            continue
        title = line.lstrip("#").strip()
        if "催化剂" not in title and "catalyst" not in title.lower():
            continue
        # Python owns the whole section: replace the agent placeholder
        # body with the deterministic table (agent prose never survives
        # here, so no hand-written calendar can masquerade as derived).
        end = index + 1
        while end < len(lines) and not lines[end].startswith("#"):
            end += 1
        return "\n".join(lines[: index + 1]) + "\n\n" + table + "\n" + (
            "\n".join(lines[end:]) if end < len(lines) else ""
        )
    # A missing calendar section is only backfilled for runs whose
    # frozen skeleton actually requires the calendar intent.
    if "investment_view_calendar" not in run_direction.required_section_intents:
        return markdown
    heading = "## 催化剂日历（Catalyst Calendar）" if zh else "## Catalyst Calendar"
    return markdown.rstrip() + f"\n\n{heading}\n\n{table}\n"
