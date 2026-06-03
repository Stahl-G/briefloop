"""DOCX output adapter — thin wrapper around ib_docx.convert().

This module does NOT import python-docx at module level, so importing it
from a Markdown-only pipeline does not require python-docx to be installed.
"""

from __future__ import annotations

from pathlib import Path


def render_docx(
    markdown_path: str | Path,
    output_path: str | Path,
    *,
    title: str | None = None,
    subtitle: str | None = None,
    footer: str | None = None,
) -> Path:
    """Render a Markdown file as a styled DOCX document.

    Raises ImportError if python-docx is not installed.
    """
    from multi_agent_brief.outputs.ib_docx import convert

    return convert(
        markdown_path,
        output_path,
        title=title,
        subtitle=subtitle,
        footer=footer,
    )
