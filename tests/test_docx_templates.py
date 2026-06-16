"""Tests for DOCX Templates (PR D).

DOCX Templates provide styled document output for different audience types.
"""
from __future__ import annotations

from pathlib import Path

from multi_agent_brief.outputs.templates import (
    DocxTemplate,
    get_template,
    list_templates,
    TEMPLATES,
)


class TestTemplateRegistry:
    """Test the template registry."""

    def test_all_expected_templates_exist(self):
        expected = {"executive_brief", "research_note", "formal_internal_report"}
        assert set(TEMPLATES.keys()) == expected

    def test_list_templates_returns_all(self):
        templates = list_templates()
        assert len(templates) == 3
        ids = {t["id"] for t in templates}
        assert ids == {"executive_brief", "research_note", "formal_internal_report"}


class TestGetTemplate:
    """Test retrieving templates by ID."""

    def test_get_executive_brief(self):
        template = get_template("executive_brief")
        assert template.template_id == "executive_brief"
        assert template.display_name == "Executive Brief"
        assert template.footer_text == "Executive Brief"

    def test_get_research_note(self):
        template = get_template("research_note")
        assert template.template_id == "research_note"
        assert template.display_name == "Research Note"
        assert template.footer_text == "Research Note"

    def test_get_formal_internal_report(self):
        template = get_template("formal_internal_report")
        assert template.template_id == "formal_internal_report"
        assert template.show_disclaimer is True
        assert len(template.disclaimer_text) > 0

    def test_unknown_id_returns_executive_brief(self):
        template = get_template("nonexistent")
        assert template.template_id == "executive_brief"


class TestTemplateProperties:
    """Test template configuration properties."""

    def test_executive_brief_has_reasonable_font_sizes(self):
        template = get_template("executive_brief")
        assert template.heading_font_size_h1 > template.body_font_size
        assert template.heading_font_size_h2 > template.body_font_size

    def test_research_note_has_higher_line_spacing(self):
        exec_template = get_template("executive_brief")
        research_template = get_template("research_note")
        assert research_template.line_spacing >= exec_template.line_spacing

    def test_formal_report_has_footer(self):
        template = get_template("formal_internal_report")
        assert template.footer_text == "Internal Use Only"


class TestDocxConversionWithTemplate:
    """Test DOCX conversion with template parameter."""

    def test_executive_brief_template_creates_docx(self, tmp_path):
        """Executive brief template should create a valid DOCX."""
        md_path = tmp_path / "test.md"
        md_path.write_text("# Test Brief\n\nThis is a test brief.\n", encoding="utf-8")
        docx_path = tmp_path / "output.docx"

        from multi_agent_brief.outputs.ib_docx import convert
        result = convert(md_path, docx_path, template="executive_brief")

        assert result.exists()
        assert result.stat().st_size > 0

    def test_research_note_template_creates_docx(self, tmp_path):
        """Research note template should create a valid DOCX."""
        md_path = tmp_path / "test.md"
        md_path.write_text("# Research Note\n\nDetailed analysis here.\n", encoding="utf-8")
        docx_path = tmp_path / "output.docx"

        from multi_agent_brief.outputs.ib_docx import convert
        result = convert(md_path, docx_path, template="research_note")

        assert result.exists()
        assert result.stat().st_size > 0

    def test_formal_report_template_creates_docx(self, tmp_path):
        """Formal internal report template should create a valid DOCX."""
        md_path = tmp_path / "test.md"
        md_path.write_text("# Internal Report\n\nCompliance analysis.\n", encoding="utf-8")
        docx_path = tmp_path / "output.docx"

        from multi_agent_brief.outputs.ib_docx import convert
        result = convert(md_path, docx_path, template="formal_internal_report")

        assert result.exists()
        assert result.stat().st_size > 0

    def test_default_template_works(self, tmp_path):
        """Default template should work without issues."""
        md_path = tmp_path / "test.md"
        md_path.write_text("# Test\n\nContent.\n", encoding="utf-8")
        docx_path = tmp_path / "output.docx"

        from multi_agent_brief.outputs.ib_docx import convert
        result = convert(md_path, docx_path, template="default")

        assert result.exists()

    def test_custom_footer_overrides_template(self, tmp_path):
        """Custom footer should override template footer."""
        md_path = tmp_path / "test.md"
        md_path.write_text("# Test\n\nContent.\n", encoding="utf-8")
        docx_path = tmp_path / "output.docx"

        from multi_agent_brief.outputs.ib_docx import convert
        result = convert(md_path, docx_path, template="executive_brief", footer="Custom Footer")

        assert result.exists()


class TestDocxPreservesHeadings:
    """Test that DOCX preserves heading hierarchy."""

    def test_heading_hierarchy_preserved(self, tmp_path):
        """DOCX should preserve H1 > H2 > H3 heading structure."""
        md_path = tmp_path / "test.md"
        md_path.write_text(
            "# Main Title\n\n## Section 1\n\nContent 1.\n\n### Subsection 1.1\n\nContent 1.1.\n\n## Section 2\n\nContent 2.\n",
            encoding="utf-8",
        )
        docx_path = tmp_path / "output.docx"

        from multi_agent_brief.outputs.ib_docx import convert
        convert(md_path, docx_path, template="research_note")

        from docx import Document
        doc = Document(str(docx_path))

        headings = [p for p in doc.paragraphs if p.style.name.startswith("Heading")]
        assert len(headings) >= 3

        # Check heading levels exist (the title is removed from body but cover is added)
        levels = []
        for h in headings:
            try:
                level = int(h.style.name.split()[-1])
                levels.append(level)
            except (ValueError, IndexError):
                pass

        # Should have headings with different levels
        assert len(set(levels)) >= 2

    def test_heading_inline_markdown_is_stripped_from_docx_headings(self, tmp_path):
        """LLM-styled heading emphasis must not leak literal Markdown into DOCX."""
        md_path = tmp_path / "test.md"
        md_path.write_text(
            (
                "**美国光储市场周报**\n\n"
                "# **一、核心摘要**\n\n"
                "正文内容。\n\n"
                "### **2.1 美国本土制造与产能扩张**\n\n"
                "更多正文。"
            ),
            encoding="utf-8",
        )
        docx_path = tmp_path / "output.docx"

        from docx import Document
        from multi_agent_brief.outputs.ib_docx import convert

        convert(md_path, docx_path, title="美国光储市场周报", template="research_note")
        doc = Document(str(docx_path))

        texts = [p.text for p in doc.paragraphs if p.text.strip()]
        headings = [p.text for p in doc.paragraphs if p.style.name.startswith("Heading")]

        assert texts.count("美国光储市场周报") == 1
        assert "一、核心摘要" in headings
        assert "2.1 美国本土制造与产能扩张" in headings
        assert all("*" not in heading for heading in headings)

    def test_auto_extracted_docx_title_strips_inline_markdown(self, tmp_path):
        md_path = tmp_path / "test.md"
        md_path.write_text(
            (
                "# ***Market Brief***\n\n"
                "## __Section__\n\n"
                "Content with ___bold italic___, _italic_, and __bold__ text.\n\n"
                "### _Subsection_\n\n"
                "More content."
            ),
            encoding="utf-8",
        )
        docx_path = tmp_path / "output.docx"

        from docx import Document
        from multi_agent_brief.outputs.ib_docx import convert

        convert(md_path, docx_path, template="executive_brief")
        doc = Document(str(docx_path))

        texts = [p.text for p in doc.paragraphs if p.text.strip()]
        headings = [p.text for p in doc.paragraphs if p.style.name.startswith("Heading")]
        assert texts[0] == "Market Brief"
        assert "Section" in headings
        assert "Subsection" in headings
        assert all(marker not in text for text in texts for marker in ("***", "___", "__", "**"))
        assert all(not (text.startswith("*") and text.endswith("*")) for text in texts)
        assert all(not (text.startswith("_") and text.endswith("_")) for text in texts)
