from __future__ import annotations

import json
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


def _write_claim_ledger(path: Path) -> None:
    claims = [
        {
            "claim_id": "SYN_CLAIM_001",
            "statement": "ExampleCo opened a public demo facility in June 2026.",
            "source_id": "SYN_SRC_001",
            "evidence_text": "Full synthetic evidence text must not render.",
            "source_url": "https://example.com/exampleco-demo",
            "source_type": "web_search",
            "metadata": {
                "source_title": "ExampleCo Opens Demo Facility",
                "publisher": "Example News",
                "published_at": "2026-06-01",
            },
        },
        {
            "claim_id": "SYN_CLAIM_002",
            "statement": "ExampleCo reported Q1 revenue.",
            "source_id": "SYN_SRC_002",
            "evidence_text": "Second evidence text must not render.",
            "source_url": "https://example.com/q1-results",
            "source_type": "filing",
            "metadata": {
                "source_title": "Q1 Results",
                "publisher": "ExampleCo",
                "published_at": "2026-05-15",
            },
        },
        {
            "claim_id": "SYN_CLAIM_UNUSED",
            "statement": "Unused claim must not appear.",
            "source_id": "SYN_SRC_UNUSED",
            "evidence_text": "Unused evidence must not render.",
            "source_url": "https://example.com/unused",
            "metadata": {
                "source_title": "Unused Source",
                "publisher": "Example News",
            },
        },
    ]
    path.write_text(json.dumps(claims, ensure_ascii=False, indent=2), encoding="utf-8")


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


def test_finalize_generates_reader_facing_source_appendix_for_explicit_request(tmp_path: Path):
    output_dir = tmp_path / "output"
    intermediate = output_dir / "intermediate"
    intermediate.mkdir(parents=True)
    (intermediate / "audited_brief.md").write_text(
        "# Brief\n\n"
        "ExampleCo opened a public demo facility. [src:SYN_CLAIM_001]\n"
        "A missing internal ref should not leak. [src:SYN_CLAIM_MISSING]\n",
        encoding="utf-8",
    )
    _write_claim_ledger(intermediate / "claim_ledger.json")

    result = finalize_reader_outputs(
        output_dir=output_dir,
        project_name="ExampleCo Brief",
        output_formats=["markdown", "source_appendix"],
        output_named_outputs=False,
    )

    appendix = (output_dir / "source_appendix.md").read_text(encoding="utf-8")
    report = json.loads((intermediate / "finalize_report.json").read_text(encoding="utf-8"))
    reader = (output_dir / "brief.md").read_text(encoding="utf-8")

    assert result.source_appendix_generation == "generated_with_warnings"
    assert report["source_appendix_requested_by"] == "source_appendix"
    assert report["source_appendix_source_count"] == 1
    assert report["source_appendix_cited_claim_count"] == 2
    assert report["source_appendix_resolved_claim_count"] == 1
    assert "ExampleCo Opens Demo Facility" in appendix
    assert "Unused Source" not in appendix
    assert "SYN_CLAIM" not in appendix
    assert "SYN_SRC" not in appendix
    assert "Full synthetic evidence" not in appendix
    assert "SYN_CLAIM" not in reader


def test_finalize_legacy_source_map_skips_missing_ledger_without_failing(tmp_path: Path):
    output_dir = tmp_path / "output"
    intermediate = output_dir / "intermediate"
    intermediate.mkdir(parents=True)
    (intermediate / "audited_brief.md").write_text(
        "# Brief\n\nClaim. [src:SYN_CLAIM_001]\n",
        encoding="utf-8",
    )

    result = finalize_reader_outputs(
        output_dir=output_dir,
        project_name="ExampleCo Brief",
        output_formats=["markdown", "source_map"],
        output_named_outputs=False,
    )

    report = json.loads((intermediate / "finalize_report.json").read_text(encoding="utf-8"))
    assert result.source_appendix_generation == "skipped_missing_ledger"
    assert report["source_appendix_requested_by"] == "legacy_source_map"
    assert not (output_dir / "source_appendix.md").exists()
    assert (output_dir / "brief.md").exists()


def test_finalize_explicit_source_appendix_fails_on_missing_ledger(tmp_path: Path):
    output_dir = tmp_path / "output"
    intermediate = output_dir / "intermediate"
    intermediate.mkdir(parents=True)
    (intermediate / "audited_brief.md").write_text(
        "# Brief\n\nClaim. [src:SYN_CLAIM_001]\n",
        encoding="utf-8",
    )

    with pytest.raises(FileNotFoundError):
        finalize_reader_outputs(
            output_dir=output_dir,
            project_name="ExampleCo Brief",
            output_formats=["markdown", "source_appendix"],
            output_named_outputs=False,
        )


def test_finalize_not_requested_removes_stale_source_appendix(tmp_path: Path):
    output_dir = tmp_path / "output"
    intermediate = output_dir / "intermediate"
    intermediate.mkdir(parents=True)
    (intermediate / "audited_brief.md").write_text(
        "# Brief\n\nExampleCo opened a public demo facility. [src:SYN_CLAIM_001]\n",
        encoding="utf-8",
    )
    _write_claim_ledger(intermediate / "claim_ledger.json")

    first = finalize_reader_outputs(
        output_dir=output_dir,
        project_name="ExampleCo Brief",
        output_formats=["markdown", "source_appendix"],
        output_named_outputs=False,
    )
    assert first.source_appendix_generation == "generated"
    assert (output_dir / "source_appendix.md").exists()

    second = finalize_reader_outputs(
        output_dir=output_dir,
        project_name="ExampleCo Brief",
        output_formats=["markdown"],
        output_named_outputs=False,
    )
    report = json.loads((intermediate / "finalize_report.json").read_text(encoding="utf-8"))

    assert second.source_appendix_generation == "not_requested"
    assert second.source_appendix == ""
    assert report["source_appendix"] == ""
    assert not (output_dir / "source_appendix.md").exists()


def test_finalize_legacy_missing_ledger_removes_stale_source_appendix(tmp_path: Path):
    output_dir = tmp_path / "output"
    intermediate = output_dir / "intermediate"
    intermediate.mkdir(parents=True)
    (intermediate / "audited_brief.md").write_text(
        "# Brief\n\nExampleCo opened a public demo facility. [src:SYN_CLAIM_001]\n",
        encoding="utf-8",
    )
    ledger = intermediate / "claim_ledger.json"
    _write_claim_ledger(ledger)

    finalize_reader_outputs(
        output_dir=output_dir,
        project_name="ExampleCo Brief",
        output_formats=["markdown", "source_appendix"],
        output_named_outputs=False,
    )
    assert (output_dir / "source_appendix.md").exists()
    ledger.unlink()

    result = finalize_reader_outputs(
        output_dir=output_dir,
        project_name="ExampleCo Brief",
        output_formats=["markdown", "source_map"],
        output_named_outputs=False,
    )

    assert result.source_appendix_generation == "skipped_missing_ledger"
    assert result.source_appendix == ""
    assert not (output_dir / "source_appendix.md").exists()


def test_finalize_legacy_malformed_ledger_removes_stale_source_appendix(tmp_path: Path):
    output_dir = tmp_path / "output"
    intermediate = output_dir / "intermediate"
    intermediate.mkdir(parents=True)
    (intermediate / "audited_brief.md").write_text(
        "# Brief\n\nExampleCo opened a public demo facility. [src:SYN_CLAIM_001]\n",
        encoding="utf-8",
    )
    ledger = intermediate / "claim_ledger.json"
    _write_claim_ledger(ledger)

    finalize_reader_outputs(
        output_dir=output_dir,
        project_name="ExampleCo Brief",
        output_formats=["markdown", "source_appendix"],
        output_named_outputs=False,
    )
    assert (output_dir / "source_appendix.md").exists()
    ledger.write_text("{not json", encoding="utf-8")

    result = finalize_reader_outputs(
        output_dir=output_dir,
        project_name="ExampleCo Brief",
        output_formats=["markdown", "source_map"],
        output_named_outputs=False,
    )

    assert result.source_appendix_generation == "skipped_malformed_ledger"
    assert result.source_appendix == ""
    assert not (output_dir / "source_appendix.md").exists()


def test_finalize_cli_reports_missing_explicit_source_appendix_ledger_without_traceback(
    tmp_path: Path,
    capsys,
):
    workspace = tmp_path / "workspace"
    output_dir = workspace / "output"
    intermediate = output_dir / "intermediate"
    intermediate.mkdir(parents=True)
    (workspace / "config.yaml").write_text(
        "project:\n"
        "  name: ExampleCo Brief\n"
        "input:\n"
        "  path: input\n"
        "output:\n"
        "  path: output\n"
        "  formats:\n"
        "    - markdown\n"
        "    - source_appendix\n",
        encoding="utf-8",
    )
    (intermediate / "audited_brief.md").write_text(
        "# Brief\n\nClaim. [src:SYN_CLAIM_001]\n",
        encoding="utf-8",
    )

    rc = main(["finalize", "--config", str(workspace / "config.yaml")])
    captured = capsys.readouterr()

    assert rc == 1
    assert "[finalize] Error:" in captured.err
    assert "Claim Ledger not found" in captured.err
    assert "Traceback" not in captured.err


def test_finalize_cli_supports_legacy_outputs_alias_for_source_appendix(tmp_path: Path):
    workspace = tmp_path / "workspace"
    output_dir = workspace / "output"
    intermediate = output_dir / "intermediate"
    intermediate.mkdir(parents=True)
    (workspace / "config.yaml").write_text(
        "project:\n"
        "  name: ExampleCo Brief\n"
        "input:\n"
        "  path: input\n"
        "outputs:\n"
        "  path: output\n"
        "  formats:\n"
        "    - markdown\n"
        "  source_appendix:\n"
        "    enabled: true\n"
        "  named_outputs: false\n",
        encoding="utf-8",
    )
    (intermediate / "audited_brief.md").write_text(
        "# Brief\n\nExampleCo opened a public demo facility. [src:SYN_CLAIM_001]\n",
        encoding="utf-8",
    )
    _write_claim_ledger(intermediate / "claim_ledger.json")

    rc = main(["finalize", "--config", str(workspace / "config.yaml")])

    assert rc == 0
    assert (output_dir / "source_appendix.md").exists()
    report = json.loads((intermediate / "finalize_report.json").read_text(encoding="utf-8"))
    assert report["source_appendix_generation"] == "generated"
    assert report["source_appendix_requested_by"] == "config"


def test_finalize_append_mode_uses_same_markdown_for_named_and_docx(tmp_path: Path):
    output_dir = tmp_path / "output"
    intermediate = output_dir / "intermediate"
    intermediate.mkdir(parents=True)
    (intermediate / "audited_brief.md").write_text(
        "# Brief\n\nExampleCo opened a public demo facility. [src:SYN_CLAIM_001]\n",
        encoding="utf-8",
    )
    _write_claim_ledger(intermediate / "claim_ledger.json")

    result = finalize_reader_outputs(
        output_dir=output_dir,
        project_name="ExampleCo Brief",
        output_formats=["markdown", "docx", "source_appendix"],
        output_named_outputs=True,
        output_filename_template="{project_name}_{report_date}",
        output_filename_tokens={"project_name": "ExampleCo", "report_date": "2026-06-09"},
        source_appendix_config={"enabled": True, "mode": "append"},
    )

    reader = (output_dir / "brief.md").read_text(encoding="utf-8")
    named = (output_dir / "ExampleCo_2026-06-09.md").read_text(encoding="utf-8")
    assert "Source Appendix" in reader
    assert reader == named
    assert "Source Appendix" in (output_dir / "source_appendix.md").read_text(encoding="utf-8")
    assert "Source Appendix" in _docx_text(output_dir / "brief.docx")
    assert "Source Appendix" in _docx_text(output_dir / "ExampleCo_2026-06-09.docx")
    assert "[src:" not in reader
    assert result.source_appendix_mode == "append"
