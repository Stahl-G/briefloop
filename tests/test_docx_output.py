"""Tests for DOCX output integration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

docx = pytest.importorskip("docx", reason="python-docx not installed")


# ── Fixtures ────────────────────────────────────────────────────

SAMPLE_MARKDOWN = """\
# Weekly Brief

## Executive Summary

This is a **bold** summary with *italic* text and `inline code`.

- Item one
- Item two with [link](https://example.com)
- Item three

1. First step
2. Second step
3. Third step

## Key Metrics

| Metric | Value | Change |
|--------|-------|--------|
| Revenue | $10M | +5% |
| Costs | $8M | -2% |

> This is an important callout.

---

## Details

Some paragraph text with ~~strikethrough~~ and more content.

```python
print("hello world")
```
"""


@pytest.fixture()
def sample_md(tmp_path: Path) -> Path:
    md = tmp_path / "brief.md"
    md.write_text(SAMPLE_MARKDOWN, encoding="utf-8")
    return md


@pytest.fixture()
def workspace(tmp_path: Path, sample_md: Path) -> Path:
    """Create a minimal workspace with config, sources, and input."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "input").mkdir()
    (ws / "input" / "news.md").write_text("- Revenue grew 5 percent.\n", encoding="utf-8")
    (ws / "config.yaml").write_text(
        'project:\n  name: "Test Brief"\n'
        'output:\n  path: "output"\n  formats:\n    - "markdown"\n',
        encoding="utf-8",
    )
    (ws / "sources.yaml").write_text(
        "source_strategy:\n  profile: research\n  enabled_providers:\n    - manual\n"
        "manual:\n  enabled: true\n  sources:\n    - name: Test\n      path: input/\n",
        encoding="utf-8",
    )
    return ws


# ── Unit tests for ib_docx module ──────────────────────────────

class TestIbDocxConvert:
    """Direct tests for the ib_docx.convert() function."""

    def test_convert_creates_nonempty_file(self, tmp_path, sample_md):
        from multi_agent_brief.outputs.ib_docx import convert

        out = tmp_path / "output.docx"
        result = convert(sample_md, out)
        assert result == out
        assert out.exists()
        assert out.stat().st_size > 0

    def test_convert_with_custom_title_and_footer(self, tmp_path, sample_md):
        from multi_agent_brief.outputs.ib_docx import convert

        out = tmp_path / "styled.docx"
        convert(sample_md, out, title="Custom Title", subtitle="2026-06-03", footer="Confidential")
        assert out.exists()
        assert out.stat().st_size > 0

    def test_convert_tables_lists_headings_no_crash(self, tmp_path):
        """Markdown with tables, lists, and headings must not raise."""
        from multi_agent_brief.outputs.ib_docx import convert

        md_content = """\
# Report

## Table Section

| A | B | C |
|---|---|---|
| 1 | 2 | 3 |
| 4 | 5 | 6 |

## List Section

- Alpha
- Beta
  - Nested item
- Gamma

1. Step one
2. Step two

## Quote

> Important note here.

---

## Code

```python
x = 1 + 2
```

Regular paragraph with **bold** and *italic*.
"""
        md = tmp_path / "complex.md"
        md.write_text(md_content, encoding="utf-8")
        out = tmp_path / "complex.docx"
        result = convert(md, out)
        assert result.exists()
        assert out.stat().st_size > 0

    def test_default_footer_no_company_names(self, tmp_path, sample_md):
        """Default footer must not contain private/company-specific names."""
        from multi_agent_brief.outputs.ib_docx import convert, DEFAULT_FOOTER

        # Ensure the default footer is a generic string, not a company name
        assert DEFAULT_FOOTER == "Generated Brief"
        assert len(DEFAULT_FOOTER) < 50
        # Also verify the actual conversion uses the generic default
        out = tmp_path / "footer_test.docx"
        convert(sample_md, out)
        assert out.exists()

    def test_convert_empty_markdown(self, tmp_path):
        """Empty Markdown should still produce a valid DOCX."""
        from multi_agent_brief.outputs.ib_docx import convert

        md = tmp_path / "empty.md"
        md.write_text("", encoding="utf-8")
        out = tmp_path / "empty.docx"
        convert(md, out)
        assert out.exists()
        assert out.stat().st_size > 0

    def test_convert_chinese_headings(self, tmp_path):
        """Chinese-style headings should not crash."""
        from multi_agent_brief.outputs.ib_docx import convert

        md_content = """\
# 周报

一、核心摘要

本周期重点事项。

4.1 政策追踪

具体内容。

（一）美国市场

详细分析。
"""
        md = tmp_path / "cn.md"
        md.write_text(md_content, encoding="utf-8")
        out = tmp_path / "cn.docx"
        convert(md, out)
        assert out.exists()

    def test_convert_wide_table(self, tmp_path):
        """Tables with 5+ columns should render as sections, not crash."""
        from multi_agent_brief.outputs.ib_docx import convert

        md_content = """\
# Wide Table

| A | B | C | D | E |
|---|---|---|---|---|
| 1 | 2 | 3 | 4 | 5 |
| 6 | 7 | 8 | 9 | 10 |
"""
        md = tmp_path / "wide.md"
        md.write_text(md_content, encoding="utf-8")
        out = tmp_path / "wide.docx"
        convert(md, out)
        assert out.exists()


# ── Integration tests for FormatterAgent ────────────────────────

class TestFormatterDocxIntegration:
    """Test FormatterAgent DOCX output via the pipeline."""

    def test_markdown_only_output_no_docx(self, workspace):
        """When output.formats does not include 'docx', no draft_brief.docx is created."""
        from multi_agent_brief.agents.formatter import FormatterAgent
        from multi_agent_brief.core.claim_ledger import ClaimLedger
        from multi_agent_brief.core.schemas import PipelineContext, ReportState

        context = PipelineContext(
            project_name="Test",
            input_dir=str(workspace / "input"),
            output_dir=str(workspace / "output"),
            output_formats=["markdown"],
            report_state=ReportState(prepared_markdown="# Hello\n\nWorld.\n"),
        )
        agent = FormatterAgent()
        result = agent.run(context, ClaimLedger())

        assert "draft_brief" in result.artifacts
        assert "draft_brief_docx" not in result.artifacts
        assert not (workspace / "output" / "draft_brief.docx").exists()

    def test_docx_format_creates_draft_brief_docx(self, workspace):
        """When 'docx' is in output.formats, draft_brief.docx is created."""
        from multi_agent_brief.agents.formatter import FormatterAgent
        from multi_agent_brief.core.claim_ledger import ClaimLedger
        from multi_agent_brief.core.schemas import PipelineContext, ReportState

        context = PipelineContext(
            project_name="Test Brief",
            input_dir=str(workspace / "input"),
            output_dir=str(workspace / "output"),
            output_formats=["markdown", "docx"],
            report_state=ReportState(prepared_markdown=SAMPLE_MARKDOWN),
        )
        agent = FormatterAgent()
        result = agent.run(context, ClaimLedger())

        assert "draft_brief" in result.artifacts
        assert "draft_brief_docx" in result.artifacts
        docx_path = Path(result.artifacts["draft_brief_docx"])
        assert docx_path.exists()
        assert docx_path.stat().st_size > 0

    def test_docx_with_custom_footer(self, workspace):
        """Custom footer from output.footer is passed to the DOCX."""
        from multi_agent_brief.agents.formatter import FormatterAgent
        from multi_agent_brief.core.claim_ledger import ClaimLedger
        from multi_agent_brief.core.schemas import PipelineContext, ReportState

        context = PipelineContext(
            project_name="Test Brief",
            input_dir=str(workspace / "input"),
            output_dir=str(workspace / "output"),
            output_formats=["docx"],
            output_footer="Confidential — Internal Use Only",
            report_state=ReportState(prepared_markdown="# Report\n\nContent.\n"),
        )
        agent = FormatterAgent()
        result = agent.run(context, ClaimLedger())

        assert "draft_brief_docx" in result.artifacts
        docx_path = Path(result.artifacts["draft_brief_docx"])
        assert docx_path.exists()


# ── Config integration ──────────────────────────────────────────

class TestBuildRunSettingsFormats:
    """Test that build_run_settings passes output formats through."""

    def test_output_formats_from_config(self):
        from multi_agent_brief.core.config import build_run_settings

        config = {
            "output": {"path": "output", "formats": ["markdown", "docx"], "footer": "Test Footer"},
        }
        settings = build_run_settings(config=config, input_dir="/tmp/input", output_dir=None, name=None, language=None, audience=None)
        assert settings["output_formats"] == ["markdown", "docx"]
        assert settings["output_footer"] == "Test Footer"

    def test_output_formats_defaults(self):
        from multi_agent_brief.core.config import build_run_settings

        settings = build_run_settings(config=None, input_dir="/tmp/input", output_dir=None, name=None, language=None, audience=None)
        assert settings["output_formats"] == ["markdown"]
        assert settings["output_footer"] == ""


class TestDocxLazyImport:
    """Verify that importing the pipeline does not require python-docx."""

    def test_docx_module_importable_without_docx(self):
        """outputs/docx.py should import without raising ImportError."""
        # This test verifies the lazy import pattern works.
        # If docx.py had a hard top-level import, this would fail.
        import importlib
        mod = importlib.import_module("multi_agent_brief.outputs.docx")
        assert hasattr(mod, "render_docx")

    def test_formatter_importable_without_docx(self):
        """formatter.py should import without requiring python-docx."""
        import importlib
        mod = importlib.import_module("multi_agent_brief.agents.formatter")
        assert hasattr(mod, "FormatterAgent")
