"""DOCX Templates — template configurations for different brief types.

Each template defines style overrides for the base ib_docx converter:
- executive_brief: Clean, high-impact style for management audiences
- research_note: Detailed, academic style for research/analyst audiences
- formal_internal_report: Formal style with disclaimers for IR/legal audiences
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class DocxTemplate:
    """Configuration for a DOCX template."""

    template_id: str
    display_name: str
    description: str

    # Style overrides
    primary_color: str = "003A70"  # deep blue
    heading_font_size_h1: int = 17
    heading_font_size_h2: int = 14
    body_font_size: int = 10.5
    line_spacing: float = 1.3

    # Cover page settings
    show_cover_page: bool = True
    cover_title_size: int = 22
    cover_subtitle_italic: bool = True

    # Footer settings
    footer_text: str = "Generated Brief"
    show_page_numbers: bool = True

    # Content settings
    show_table_of_contents: bool = False
    show_disclaimer: bool = False
    disclaimer_text: str = ""


# ── Template Registry ──────────────────────────────────────────────────────────

TEMPLATES: dict[str, DocxTemplate] = {
    "executive_brief": DocxTemplate(
        template_id="executive_brief",
        display_name="Executive Brief",
        description="Clean, high-impact style for management and executive audiences",
        primary_color="003A70",
        heading_font_size_h1=17,
        heading_font_size_h2=14,
        body_font_size=10.5,
        line_spacing=1.3,
        show_cover_page=True,
        cover_title_size=22,
        footer_text="Executive Brief",
    ),
    "research_note": DocxTemplate(
        template_id="research_note",
        display_name="Research Note",
        description="Detailed, academic style for research and analyst audiences",
        primary_color="1A5276",
        heading_font_size_h1=16,
        heading_font_size_h2=13,
        body_font_size=10,
        line_spacing=1.4,
        show_cover_page=True,
        cover_title_size=20,
        footer_text="Research Note",
    ),
    "formal_internal_report": DocxTemplate(
        template_id="formal_internal_report",
        display_name="Formal Internal Report",
        description="Formal style with disclaimers for IR and legal/compliance audiences",
        primary_color="2C3E50",
        heading_font_size_h1=16,
        heading_font_size_h2=13,
        body_font_size=10,
        line_spacing=1.35,
        show_cover_page=True,
        cover_title_size=20,
        footer_text="Internal Use Only",
        show_disclaimer=True,
        disclaimer_text=(
            "This document is for informational purposes only and does not "
            "constitute investment, legal, or tax advice. All information is "
            "provided as-is without warranty."
        ),
    ),
}


def get_template(template_id: str) -> DocxTemplate:
    """Get a template by ID.

    Returns the 'executive_brief' template if the ID is not found.
    """
    return TEMPLATES.get(template_id, TEMPLATES["executive_brief"])


def list_templates() -> list[dict[str, str]]:
    """List all available templates."""
    return [
        {
            "id": t.template_id,
            "name": t.display_name,
            "description": t.description,
        }
        for t in TEMPLATES.values()
    ]
