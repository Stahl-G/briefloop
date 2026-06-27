"""Experimental ReportTemplate Markdown renderer.

This module materializes the existing ReportTemplate section-order contract for
reader Markdown that has already passed through finalize's citation/source
normalization path. It does not assess support, approve delivery, or bypass
reader-final gates.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from multi_agent_brief.product.template_conformance import (
    _add_report_title_alias,
    _add_workspace_company_aliases,
    _label_matches,
    _markdown_headings,
    _match_heading_to_section,
    _out_of_order_sections,
    _section_aliases,
    _workspace_company,
)
from multi_agent_brief.product.template_projection import project_workspace_report_template

REPORT_TEMPLATE_RENDERER_BOUNDARY = "product_report_template_renderer_mvp"


@dataclass
class TemplateRenderResult:
    """Result of applying a ReportTemplate section-order renderer."""

    status: str
    markdown: str
    template_id: str = ""
    report_type: str = ""
    section_order: list[str] = field(default_factory=list)
    rendered_section_count: int = 0
    missing_sections: list[str] = field(default_factory=list)
    out_of_order_sections: list[str] = field(default_factory=list)
    extra_headings: list[str] = field(default_factory=list)
    duplicate_sections: list[str] = field(default_factory=list)
    boundary: str = REPORT_TEMPLATE_RENDERER_BOUNDARY
    runtime_effect: str = "reader_markdown_section_order_projection"
    blocking: bool = False

    def to_report(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("markdown", None)
        return data


@dataclass(frozen=True)
class _SectionMatch:
    section: str
    heading: str
    line: int
    level: int


def render_reader_markdown_with_template(
    *,
    workspace: str | Path,
    markdown: str,
) -> TemplateRenderResult:
    """Return reader Markdown ordered by the resolved ReportTemplate when safe.

    Missing sections, extra top-level headings, or duplicate section headings
    are diagnostic-only. In those cases the input Markdown is returned
    unchanged so no content is silently dropped.
    """

    ws = Path(workspace)
    template = project_workspace_report_template(ws)
    template_id = str(template.get("template_id") or "")
    report_type = str(template.get("report_type") or "")
    expected_sections = [
        str(item).strip()
        for item in template.get("section_order", [])
        if isinstance(item, str) and item.strip()
    ]
    if template.get("status") != "resolved" or not expected_sections:
        return TemplateRenderResult(
            status="not_available",
            markdown=markdown,
            template_id=template_id,
            report_type=report_type,
            section_order=expected_sections,
        )

    section_aliases = _section_aliases(template.get("section_aliases"), expected_sections)
    _add_report_title_alias(section_aliases, template.get("report_title"))
    _add_workspace_company_aliases(section_aliases, _workspace_company(ws))
    matches, extra_headings, duplicate_sections = _matched_section_headings(
        markdown=markdown,
        expected_sections=expected_sections,
        section_aliases=section_aliases,
        report_title=str(template.get("report_title") or ""),
    )
    match_by_section = {match.section: match for match in matches}
    missing_sections = [
        section for section in expected_sections
        if section not in match_by_section
    ]
    out_of_order_sections = _out_of_order_sections(
        [match.section for match in matches],
        [expected_sections.index(match.section) for match in matches],
    )
    if missing_sections or extra_headings or duplicate_sections:
        return TemplateRenderResult(
            status="skipped_unresolved_sections",
            markdown=markdown,
            template_id=template_id,
            report_type=report_type,
            section_order=expected_sections,
            rendered_section_count=len(matches),
            missing_sections=missing_sections,
            out_of_order_sections=out_of_order_sections,
            extra_headings=extra_headings[:20],
            duplicate_sections=duplicate_sections,
        )

    rendered = _render_section_chunks(
        markdown=markdown,
        expected_sections=expected_sections,
        matches=matches,
    )
    return TemplateRenderResult(
        status="rendered" if rendered != _normalize_final_newline(markdown) else "already_ordered",
        markdown=rendered,
        template_id=template_id,
        report_type=report_type,
        section_order=expected_sections,
        rendered_section_count=len(matches),
        out_of_order_sections=out_of_order_sections,
    )


def _matched_section_headings(
    *,
    markdown: str,
    expected_sections: list[str],
    section_aliases: dict[str, list[str]],
    report_title: str,
) -> tuple[list[_SectionMatch], list[str], list[str]]:
    headings = _markdown_headings(markdown)
    matches: list[_SectionMatch] = []
    extra_headings: list[str] = []
    duplicate_sections: list[str] = []
    seen_sections: set[str] = set()
    first_h1_seen = False
    active_h1_section: str | None = None
    for heading in headings:
        level = int(heading["level"])
        text = str(heading["text"])
        if level > 2:
            continue
        if level == 2 and active_h1_section and active_h1_section != "cover":
            continue
        is_first_h1 = False
        if level == 1 and not first_h1_seen:
            first_h1_seen = True
            is_first_h1 = True
        if level == 1:
            active_h1_section = None

        section = _match_heading_to_section(text, expected_sections, section_aliases)
        if section is None:
            if is_first_h1 and "cover" in expected_sections:
                section = "cover"
            elif is_first_h1 and _label_matches(text, report_title):
                continue
            else:
                extra_headings.append(text)
                continue

        if section in seen_sections:
            duplicate_sections.append(section)
            continue
        seen_sections.add(section)
        matches.append(_SectionMatch(
            section=section,
            heading=text,
            line=int(heading["line"]),
            level=level,
        ))
        if level == 1:
            active_h1_section = section
    return matches, extra_headings, sorted(set(duplicate_sections))


def _render_section_chunks(
    *,
    markdown: str,
    expected_sections: list[str],
    matches: list[_SectionMatch],
) -> str:
    lines = markdown.splitlines()
    matches_by_source_order = sorted(matches, key=lambda item: item.line)
    chunks: dict[str, list[str]] = {}
    first_line = matches_by_source_order[0].line if matches_by_source_order else 1
    preamble = lines[: max(first_line - 1, 0)]
    for idx, match in enumerate(matches_by_source_order):
        start = max(match.line - 1, 0)
        end = (
            max(matches_by_source_order[idx + 1].line - 1, start)
            if idx + 1 < len(matches_by_source_order)
            else len(lines)
        )
        chunks[match.section] = lines[start:end]

    rendered_chunks: list[str] = []
    if any(line.strip() for line in preamble):
        rendered_chunks.append("\n".join(preamble).rstrip())
    for section in expected_sections:
        chunk = chunks.get(section)
        if chunk and any(line.strip() for line in chunk):
            rendered_chunks.append("\n".join(chunk).rstrip())
    return "\n\n".join(rendered_chunks).rstrip() + "\n"


def _normalize_final_newline(value: str) -> str:
    return value.rstrip() + "\n" if value.strip() else ""
