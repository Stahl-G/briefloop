"""DOCX output adapter — thin wrapper around ib_docx.convert()."""

from __future__ import annotations

from pathlib import Path

from multi_agent_brief.outputs.ib_docx import convert


def render_docx(
    markdown_path: str | Path,
    output_path: str | Path,
    *,
    title: str | None = None,
    subtitle: str | None = None,
    footer: str | None = None,
) -> Path:
    """Render a Markdown file as a styled DOCX document.

    This is the pipeline-facing entry point.  It delegates to
    :func:`multi_agent_brief.outputs.ib_docx.convert`.
    """
    return convert(
        markdown_path,
        output_path,
        title=title,
        subtitle=subtitle,
        footer=footer,
    )
