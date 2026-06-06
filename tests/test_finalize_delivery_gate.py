from __future__ import annotations

from pathlib import Path

import pytest

from multi_agent_brief.cli.main import main
from multi_agent_brief.outputs.finalize import finalize_reader_outputs


def _docx_text(path: Path) -> str:
    docx = pytest.importorskip("docx", reason="python-docx not installed")
    document = docx.Document(str(path))
    paragraphs = "\n".join(p.text for p in document.paragraphs)
    tables = "\n".join(
        cell.text
        for table in document.tables
        for row in table.rows
        for cell in row.cells
    )
    return paragraphs + "\n" + tables


def test_finalize_regenerates_reader_outputs_from_audited_brief(tmp_path: Path):
    """Subagent-updated audited_brief.md must be the single source for final delivery."""
    output_dir = tmp_path / "output"
    intermediate = output_dir / "intermediate"
    intermediate.mkdir(parents=True)
    audited = intermediate / "audited_brief.md"
    audited.write_text(
        "# 上能电气 电力设备市场周报\n\n"
        "- 美国政策出现变化 [src:POLICY_123456]\n"
        "- 市场需求增长 5% [src:MARKET_ABCDEF]\n",
        encoding="utf-8",
    )

    result = finalize_reader_outputs(
        output_dir=output_dir,
        project_name="上能电气 电力设备市场周报",
        output_formats=["markdown", "docx"],
        output_named_outputs=True,
        output_filename_template="{project_name}_{report_date}",
        output_filename_tokens={"project_name": "上能电气_电力设备周报", "report_date": "2026-06-06"},
    )

    reader = (output_dir / "brief.md").read_text(encoding="utf-8")
    named = output_dir / "上能电气_电力设备周报_2026-06-06.md"

    assert "[src:" in audited.read_text(encoding="utf-8")
    assert "[src:" not in reader
    assert named.exists()
    assert "[src:" not in named.read_text(encoding="utf-8")
    assert reader == named.read_text(encoding="utf-8")
    assert result.stripped_src_marker_count == 2

    docx_path = output_dir / "brief.docx"
    assert docx_path.exists()
    assert "[src:" not in _docx_text(docx_path)
    assert "[src:" not in _docx_text(output_dir / "上能电气_电力设备周报_2026-06-06.docx")


def test_finalize_cli_strips_src_markers_after_subagent_rewrite(tmp_path: Path):
    """CLI finalization prevents audited [src:...] markers from leaking to final files."""
    workspace = tmp_path / "workspace"
    input_dir = workspace / "input"
    output_dir = workspace / "output"
    intermediate = output_dir / "intermediate"
    input_dir.mkdir(parents=True)
    intermediate.mkdir(parents=True)
    (input_dir / "source.md").write_text("dummy", encoding="utf-8")
    (workspace / "config.yaml").write_text(
        "project:\n"
        "  name: 上能电气_电力设备周报\n"
        "  audience: management\n"
        "input:\n"
        "  path: input\n"
        "output:\n"
        "  path: output\n"
        "  formats:\n"
        "    - markdown\n"
        "  named_outputs: true\n"
        "  filename_template: '{project_name}_{report_date}'\n"
        "report:\n"
        "  date: '2026-06-06'\n",
        encoding="utf-8",
    )
    audited_path = intermediate / "audited_brief.md"
    audited_path.write_text("# Brief\n\n- Claim [src:CLAIM_123456]\n", encoding="utf-8")

    assert main(["finalize", "--config", str(workspace / "config.yaml")]) == 0

    assert "[src:" in audited_path.read_text(encoding="utf-8")
    assert "[src:" not in (output_dir / "brief.md").read_text(encoding="utf-8")
    assert "[src:" not in (output_dir / "上能电气_电力设备周报_2026-06-06.md").read_text(encoding="utf-8")
    assert (intermediate / "finalize_report.json").exists()
