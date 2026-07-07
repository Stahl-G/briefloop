from __future__ import annotations

from pathlib import Path

import pytest

from multi_agent_brief.outputs.reader_final_gate import (
    detect_reader_residue,
    detect_reader_residue_in_docx,
    merge_projection_residue_into_reader_clean,
)


def _kinds(text: str) -> list[str]:
    return [finding.kind for finding in detect_reader_residue(text, artifact="output/brief.md").findings]


def test_reader_final_gate_detects_source_marker_raw_claim_and_source_id_residue() -> None:
    text = "\n".join(
        [
            "Claim with [src:CL-0001].",
            "Claim with [source:CL-0002].",
            "A raw [CL-0003] marker.",
            "A raw CLM-001 marker.",
            "A raw SYN_CLAIM_001 marker.",
            "A raw CLAIM_123456 marker.",
            "A raw CLAIM_TEST_001 marker.",
            "A source id SYN_SRC_001 marker.",
            "A source id SRC_ABCDEF marker.",
            "A source id SRC_001 marker.",
            "A source id SOURCE_A marker.",
        ]
    )

    result = detect_reader_residue(text, artifact="output/brief.md")

    assert result.status == "fail"
    assert result.counts["src_marker_count"] == 2
    assert result.counts["bare_claim_id_count"] == 7
    assert result.counts["source_id_count"] == 4
    assert result.findings[0].artifact == "output/brief.md"
    assert result.findings[0].line == 1


def test_reader_final_gate_blocks_direct_source_marker_scans_without_claim_shape() -> None:
    text = "\n".join(
        [
            "Template leak [src:].",
            "Footer leak [src:bad id].",
            "Legacy leak [source:internal].",
            "Uppercase leak [SRC:].",
            "Mixed-case leak [Src:bad id].",
        ]
    )

    result = detect_reader_residue(text, artifact="output/brief.md")

    assert result.status == "fail"
    assert result.counts["src_marker_count"] == 5
    assert result.counts["bare_claim_id_count"] == 0
    assert [finding.text for finding in result.findings] == [
        "[src:]",
        "[src:bad id]",
        "[source:internal]",
        "[SRC:]",
        "[Src:bad id]",
    ]


def test_reader_final_gate_keeps_ordinary_source_prose_outside_citation_grammar() -> None:
    text = "\n".join(
        [
            "Primary source:10-K filing.",
            "Source:Q2-2026 report.",
            "source:https://example.com/report",
        ]
    )

    result = detect_reader_residue(text, artifact="output/brief.md")

    assert result.status == "pass"
    assert result.counts["src_marker_count"] == 0


def test_reader_final_gate_consumes_projection_residue_facts() -> None:
    reader_clean = detect_reader_residue("Reader-safe body.", artifact="output/brief.md").to_report_dict()
    residue_report = {
        "status": "fail",
        "unresolved_src_marker_count": 1,
        "malformed_src_marker_count": 1,
        "findings": [
            {
                "kind": "unresolved_src_marker",
                "raw": "[src:CL-404]",
                "claim_id": "CL-404",
                "status": "unresolved",
                "message": "source marker does not resolve to a Claim Ledger entry",
            },
            {
                "kind": "malformed_src_marker",
                "raw": "[src:]",
                "claim_id": "",
                "status": "malformed",
                "message": "source marker has an empty claim id",
            },
        ],
    }

    merged = merge_projection_residue_into_reader_clean(
        reader_clean,
        residue_report,
        artifact="output/intermediate/finalize_candidate/tx/reader_brief.md",
    )

    assert merged["status"] == "fail"
    assert merged["src_marker_count"] == 0
    assert merged["reader_projection_unresolved_src_marker_count"] == 1
    assert merged["reader_projection_malformed_src_marker_count"] == 1
    assert [finding["kind"] for finding in merged["sample_findings"]] == [
        "reader_projection_unresolved_src_marker",
        "reader_projection_malformed_src_marker",
    ]


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


def test_reader_final_gate_counts_claim_ledger_wording_once_per_occurrence() -> None:
    upper = detect_reader_residue("See Claim Ledger for details.", artifact="output/brief.md")
    lower = detect_reader_residue("See claim ledger for details.", artifact="output/brief.md")

    assert upper.counts["process_wording_count"] == 1
    assert lower.counts["process_wording_count"] == 1


def test_reader_final_gate_detects_local_paths_and_debug_residue() -> None:
    text = "\n".join(
        [
            "Local path: /Users/example/workspace/source.md",
            "File URL: file:///tmp/private.md",
            "Windows path: C:\\Users\\example\\source.md",
            "Notebook path: /mnt/data/output.md",
            "Workspace source path: input/sources/source-001.md",
            "Intermediate control path: output/intermediate/claim_ledger.json",
            "Windows workspace source path: input\\sources\\source-001.md",
            "Windows intermediate path: output\\intermediate\\claim_ledger.json",
            "Windows delivery path: output\\delivery\\brief.md",
            "DEBUG this must not ship.",
            "TRACE this must not ship.",
        ]
    )

    result = detect_reader_residue(text, artifact="output/brief.md")

    assert result.status == "fail"
    assert result.counts["local_path_count"] == 9
    assert result.counts["debug_residue_count"] == 2


def test_reader_final_gate_allows_public_urls_with_internal_path_segments() -> None:
    text = "\n".join(
        [
            "Source: https://example.com/output/intermediate/report",
            "Dataset: https://example.com/input/sources/source-001.md",
            "Archive: https://example.com/output/delivery/brief.md",
        ]
    )

    result = detect_reader_residue(text, artifact="output/source_appendix.md")

    assert result.status == "pass"
    assert result.counts["local_path_count"] == 0


def test_reader_final_gate_blocks_unmarked_source_appendix_internal_residue() -> None:
    text = "\n".join(
        [
            "## 来源附录",
            "Claim Ledger 条目 CL-001 来自 input/sources/source-001.md。",
        ]
    )

    result = detect_reader_residue(text, artifact="output/brief.md")

    assert result.status == "fail"
    assert result.counts["bare_claim_id_count"] == 1
    assert result.counts["process_wording_count"] == 1
    assert result.counts["local_path_count"] == 1


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


def test_reader_final_gate_detects_blank_source_id_cell_in_source_section() -> None:
    markdown = """# Brief

## Source Index

| ID | Title | Date | Priority |
| --- | --- | --- | --- |
|  | USTR Section 301对60个经济体调查 | 2026-06-04 | 高 |
"""

    result = detect_reader_residue(markdown, artifact="output/brief.md")

    assert result.status == "fail"
    assert result.counts["blank_citation_row_count"] == 1
    finding = result.findings[0]
    assert finding.kind == "blank_citation_row"
    assert finding.line == 7
    assert "blank ID/source/reference cell" in finding.message


def test_reader_final_gate_allows_reader_safe_source_appendix() -> None:
    appendix = """# Source Appendix

This appendix lists source records used by the brief; it is not a semantic proof of every statement.

## Sources

### [S1] ExampleCo Opens Demo Facility

- Source category: News media
- Retrieval source type: News media
- Underlying evidence type: Media report
- Publisher/Institution: Example News
- Published: 2026-06-01
- URL: https://example.com/exampleco-demo
- Provider type: web_search
- Used in: 1 claim-backed statement
"""

    result = detect_reader_residue(appendix, artifact="output/source_appendix.md")

    assert result.status == "pass"
    assert result.to_report_dict()["sample_findings"] == []


def test_reader_final_gate_detects_atomic_claim_graph_residue() -> None:
    markdown = "TargetCo opened a demo facility. See AC-0001-01 from the Atomic Claim Graph."

    result = detect_reader_residue(markdown, artifact="output/brief.md")

    assert result.status == "fail"
    assert result.counts["atom_id_count"] == 1
    assert any(finding.kind == "atom_id" for finding in result.findings)
    assert any(finding.kind == "process_wording" and "Atomic Claim Graph" in finding.text for finding in result.findings)


def test_reader_final_gate_detects_evidence_span_registry_residue() -> None:
    markdown = "The reader-facing brief leaked audit span ESP-001-01."

    result = detect_reader_residue(markdown, artifact="output/brief.md")

    assert result.status == "fail"
    assert result.counts["span_id_count"] == 1
    assert any(finding.kind == "span_id" and finding.text == "ESP-001-01" for finding in result.findings)


def test_reader_final_gate_allows_generic_atom_domain_wording() -> None:
    markdown = (
        "The materials appendix describes atom identity checks and atom identification methods "
        "without exposing internal graph IDs."
    )

    result = detect_reader_residue(markdown, artifact="output/brief.md")

    assert result.status == "pass"
    assert result.counts["atom_id_count"] == 0
    assert result.counts["process_wording_count"] == 0


def test_reader_final_gate_detects_natural_language_atom_id_residue() -> None:
    markdown = "Do not cite atom IDs in reader-facing prose."

    result = detect_reader_residue(markdown, artifact="output/brief.md")

    assert result.status == "fail"
    assert result.counts["atom_id_count"] == 0
    assert result.counts["process_wording_count"] == 1
    assert any(finding.kind == "process_wording" and finding.text == "atom IDs" for finding in result.findings)


def test_reader_final_gate_detects_policy_forbidden_phrases() -> None:
    markdown = "This summary should not describe a guaranteed return."

    result = detect_reader_residue(
        markdown,
        artifact="output/brief.md",
        forbidden_phrases=("guaranteed return",),
    )

    assert result.status == "fail"
    assert result.counts["policy_forbidden_phrase_count"] == 1
    assert any(finding.kind == "policy_forbidden_phrase" for finding in result.findings)


def test_reader_final_gate_still_detects_internal_atom_markers() -> None:
    markdown = "Internal field atom_id should not ship, and neither should AC-0001-01."

    result = detect_reader_residue(markdown, artifact="output/brief.md")

    assert result.status == "fail"
    assert result.counts["atom_id_count"] == 1
    assert any(finding.kind == "process_wording" and finding.text == "atom_id" for finding in result.findings)


def test_reader_final_gate_scans_docx_headers_and_footers(tmp_path: Path) -> None:
    docx = pytest.importorskip("docx", reason="python-docx not installed")
    document = docx.Document()
    document.add_paragraph("Reader-safe body.")
    section = document.sections[0]
    section.header.paragraphs[0].text = "Header leaks CLAIM_123456"
    section.footer.paragraphs[0].text = "Footer leaks SRC_001"
    path = tmp_path / "reader.docx"
    document.save(path)

    result = detect_reader_residue_in_docx(path, artifact="output/brief.docx")

    assert result.status == "fail"
    assert result.counts["bare_claim_id_count"] == 1
    assert result.counts["source_id_count"] == 1
    assert {finding.artifact for finding in result.findings} == {"output/brief.docx"}
