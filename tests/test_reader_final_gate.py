from __future__ import annotations

from multi_agent_brief.outputs.reader_final_gate import detect_reader_residue


def _kinds(text: str) -> list[str]:
    return [finding.kind for finding in detect_reader_residue(text, artifact="output/brief.md").findings]


def test_reader_final_gate_detects_source_markers_and_claim_ids() -> None:
    text = "\n".join(
        [
            "Claim with [src:CL-0001].",
            "Claim with [source:CL-0002].",
            "A raw [CL-0003] marker.",
            "A raw CLM-001 marker.",
            "A raw SYN_CLAIM_001 marker.",
            "A source id SYN_SRC_001 marker.",
        ]
    )

    result = detect_reader_residue(text, artifact="output/brief.md")

    assert result.status == "fail"
    assert result.counts["src_marker_count"] == 2
    assert result.counts["bare_claim_id_count"] == 5
    assert result.counts["source_id_count"] == 1
    assert result.findings[0].artifact == "output/brief.md"
    assert result.findings[0].line == 1


def test_reader_final_gate_detects_process_wording_in_english_and_chinese() -> None:
    text = "\n".join(
        [
            "The Analyst subagent prepared this section.",
            "See Claim Ledger for details.",
            "质量门禁记录在运行交接单中。",
            "事实账本不应出现在终稿。",
            "The quality_gate_report was attached.",
        ]
    )

    result = detect_reader_residue(text, artifact="output/brief.md")

    assert result.status == "fail"
    assert result.counts["process_wording_count"] >= 5


def test_reader_final_gate_detects_local_paths_and_debug_residue() -> None:
    text = "\n".join(
        [
            "Local path: /Users/example/workspace/source.md",
            "File URL: file:///tmp/private.md",
            "Windows path: C:\\Users\\example\\source.md",
            "Notebook path: /mnt/data/output.md",
            "DEBUG this must not ship.",
            "TRACE this must not ship.",
        ]
    )

    result = detect_reader_residue(text, artifact="output/brief.md")

    assert result.status == "fail"
    assert result.counts["local_path_count"] == 4
    assert result.counts["debug_residue_count"] == 2


def test_reader_final_gate_detects_blank_rows_only_inside_source_sections() -> None:
    outside = """# Brief

| A | B | C |
| --- | --- | --- |
|  |  |  |
"""
    inside = """# Brief

## Source Index

| Title | Publisher | URL |
| --- | --- | --- |
|  |  |  |
"""

    assert detect_reader_residue(outside, artifact="output/brief.md").status == "pass"
    result = detect_reader_residue(inside, artifact="output/brief.md")

    assert result.status == "fail"
    assert result.counts["blank_citation_row_count"] == 1
    assert result.findings[0].line == 7


def test_reader_final_gate_allows_reader_safe_source_appendix() -> None:
    appendix = """# Source Appendix

This appendix lists source records used by the brief; it is not a semantic proof of every statement.

## Sources

### [S1] ExampleCo Opens Demo Facility

- Publisher: Example News
- Published: 2026-06-01
- URL: https://example.com/exampleco-demo
- Used in: 1 claim-backed statement
"""

    result = detect_reader_residue(appendix, artifact="output/source_appendix.md")

    assert result.status == "pass"
    assert result.to_report_dict()["sample_findings"] == []
