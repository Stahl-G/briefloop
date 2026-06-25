"""Tests for Rendered Output Validation (PR D).

Rendered Output Validation checks DOCX output quality including
text depth, heading mapping, and bullet rendering.
"""
from __future__ import annotations

from pathlib import Path

from multi_agent_brief.audit.final_quality import (
    RenderedOutputAuditAgent,
    RenderedOutputConfig,
)
from multi_agent_brief.core.claim_ledger import ClaimLedger
from multi_agent_brief.core.schemas import PipelineContext


class TestRenderedOutputConfig:
    """Test RenderedOutputConfig defaults."""

    def test_default_config_enabled(self):
        config = RenderedOutputConfig()
        assert config.enabled is True

    def test_default_checks_enabled(self):
        config = RenderedOutputConfig()
        assert config.check_heading_mapping is True
        assert config.check_bullet_rendering is True
        assert config.check_table_rendering is True
        assert config.check_text_depth is True
        assert config.check_footer_fields is True


class TestRenderedOutputAuditAgent:
    """Test RenderedOutputAuditAgent."""

    def test_disabled_config_returns_pass(self):
        config = RenderedOutputConfig(enabled=False)
        agent = RenderedOutputAuditAgent(config)
        report = agent.run_audit("# Test", ClaimLedger())
        assert report.audit_status == "pass"
        assert report.metadata.get("rendered_output") == "disabled"

    def test_no_docx_path_returns_pass(self):
        context = PipelineContext(
            project_name="Test",
            input_dir="/tmp/input",
            output_dir="/tmp/output",
        )
        agent = RenderedOutputAuditAgent()
        report = agent.run_audit("# Test", ClaimLedger(), context)
        assert report.audit_status == "pass"
        assert report.metadata.get("skipped") == "no_docx_path"

    def test_missing_docx_file_returns_fail(self, tmp_path):
        missing_path = tmp_path / "missing.docx"
        context = PipelineContext(
            project_name="Test",
            input_dir="/tmp/input",
            output_dir="/tmp/output",
        )
        context.metadata["rendered_docx_path"] = str(missing_path)
        agent = RenderedOutputAuditAgent()
        report = agent.run_audit("# Test", ClaimLedger(), context)
        assert report.audit_status == "fail"
        assert any(f.finding_type == "missing_rendered_docx" for f in report.findings)


class TestRenderedOutputWithRealDocx:
    """Test rendered output validation with real DOCX files."""

    def test_valid_docx_passes(self, tmp_path):
        """A well-formed DOCX should pass validation."""
        md_path = tmp_path / "test.md"
        # Create content with enough text to pass depth check (need 7800+ chars)
        section_content = (
            "This is detailed analysis content for the brief. "
            "It includes multiple sentences with source-backed claims. "
            "The market showed significant movement this week with various factors. "
            "Competitor analysis reveals important trends in the industry. "
            "Regulatory changes may impact future operations. "
        * 30)
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

    def test_thin_docx_detected(self, tmp_path):
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


class TestRenderedOutputReportJson:
    """Test that rendered_output_report.json is generated correctly."""

    def test_report_json_structure(self, tmp_path):
        """Rendered output report should have correct structure."""
        from multi_agent_brief.outputs.ib_docx import convert

        md_path = tmp_path / "test.md"
        md_path.write_text(
            "# Test Brief\n\n## Section\n\nContent " * 50,
            encoding="utf-8",
        )
        docx_path = tmp_path / "output.docx"
        convert(md_path, docx_path, template="executive_brief")

        context = PipelineContext(
            project_name="Test",
            input_dir="/tmp/input",
            output_dir="/tmp/output",
        )
        context.metadata["rendered_docx_path"] = str(docx_path)

        agent = RenderedOutputAuditAgent()
        report = agent.run_audit(
            "# Test Brief\n\n## Section\n\nContent " * 10,
            ClaimLedger(),
            context,
        )

        report_dict = report.to_dict()
        assert "audit_status" in report_dict
        assert "metadata" in report_dict
        assert report_dict["metadata"].get("gate") == "rendered_output"
        assert report_dict["metadata"].get("rendered_docx_path") == str(docx_path)


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
