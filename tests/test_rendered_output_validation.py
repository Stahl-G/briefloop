"""Tests for Rendered Output Validation (PR D).

Rendered Output Validation checks DOCX output quality including
text depth, heading mapping, and bullet rendering.
"""

from __future__ import annotations

import pytest

from multi_agent_brief.audit.final_quality import (
    RenderedOutputAuditAgent,
    RenderedOutputConfig,
)
from multi_agent_brief.core.claim_ledger import ClaimLedger
from multi_agent_brief.core.schemas import PipelineContext


def test_valid_docx_passes(tmp_path):
    """A well-formed DOCX should pass validation."""
    md_path = tmp_path / "test.md"
    # Create content with enough text to pass depth check (need 7800+ chars)
    section_content = (
        "This is detailed analysis content for the brief. "
        "It includes multiple sentences with source-backed claims. "
        "The market showed significant movement this week with various factors. "
        "Competitor analysis reveals important trends in the industry. "
        "Regulatory changes may impact future operations. " * 30
    )
    content = f"# Test Brief\n\n## Section 1\n\n{section_content}\n\n"
    content += f"## Section 2\n\n{section_content}\n\n"
    content += f"## Section 3\n\n{section_content}\n"
    md_path.write_text(content, encoding="utf-8")
    docx_path = tmp_path / "output.docx"

    from multi_agent_brief.outputs.ib_docx import convert

    convert(md_path, docx_path, template="executive_brief")

    context = PipelineContext(
        project_name="Test",
        input_dir="/tmp/input",
        output_dir="/tmp/output",
    )
    context.metadata["rendered_docx_path"] = str(docx_path)

    agent = RenderedOutputAuditAgent()
    report = agent.run_audit(
        content,
        ClaimLedger(),
        context,
    )
    # Should pass or have only minor findings
    assert report.audit_status in ("pass", "warning")


def test_thin_docx_detected(tmp_path):
    """A very thin DOCX should trigger text depth finding."""
    md_path = tmp_path / "test.md"
    md_path.write_text("# Brief\n\nShort.\n", encoding="utf-8")
    docx_path = tmp_path / "output.docx"

    from multi_agent_brief.outputs.ib_docx import convert

    convert(md_path, docx_path, template="executive_brief")

    context = PipelineContext(
        project_name="Test",
        input_dir="/tmp/input",
        output_dir="/tmp/output",
    )
    context.metadata["rendered_docx_path"] = str(docx_path)

    config = RenderedOutputConfig(min_docx_text_chars=1000)
    agent = RenderedOutputAuditAgent(config)
    report = agent.run_audit("# Brief\n\nShort.", ClaimLedger(), context)
    assert any(f.finding_type == "rendered_docx_too_thin" for f in report.findings)


def test_ib_docx_renders_markdown_links_as_docx_hyperlinks(tmp_path):
    """Markdown links should become Word hyperlink relationships, not just styled text."""
    from docx import Document

    from multi_agent_brief.outputs.ib_docx import convert

    md_path = tmp_path / "links.md"
    md_path.write_text(
        "# Link Brief\n\nSource URL: [https://example.com/source](https://example.com/source)\n",
        encoding="utf-8",
    )
    docx_path = tmp_path / "links.docx"

    convert(md_path, docx_path)

    document = Document(docx_path)
    hyperlink_targets = {
        rel.target_ref
        for rel in document.part.rels.values()
        if rel.reltype.endswith("/hyperlink")
    }
    assert "https://example.com/source" in hyperlink_targets


def test_ib_docx_rejects_image_path_outside_markdown_directory(tmp_path):
    from multi_agent_brief.outputs.ib_docx import convert

    outside = tmp_path.parent / "outside.png"
    outside.write_bytes(b"not-an-image")
    md_path = tmp_path / "market.md"
    md_path.write_text(
        "# Market Brief\n\n![Outside](../outside.png)\n", encoding="utf-8"
    )

    try:
        with pytest.raises(ValueError, match="inside the report output directory"):
            convert(md_path, tmp_path / "market.docx")
    finally:
        outside.unlink(missing_ok=True)
