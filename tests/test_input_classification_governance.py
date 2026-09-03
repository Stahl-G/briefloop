"""Tests for input classification and feedback hygiene (v0.5.7)."""
from __future__ import annotations

from functools import partial
from pathlib import Path

from multi_agent_brief.inputs.classifier import classify_input_dir
from tests.helpers import write_workspace_files_under


_write_workspace = partial(
    write_workspace_files_under,
    config_text=(
        "project:\n  name: Test\n  language: zh-CN\n"
        "input:\n  path: input\n"
        "output:\n  path: output\n"
    ),
    include_output_dir=True,
)


def test_inputs_classify_detects_old_output_artifact_in_root(tmp_path: Path):
    ws = _write_workspace(tmp_path)
    input_dir = ws / "input"
    sources_dir = input_dir / "sources"
    sources_dir.mkdir(parents=True)

    (sources_dir / "real_source.md").write_text("# Real source\nOnly evidence.", encoding="utf-8")
    (input_dir / "audited_brief.md").write_text(
        "This old result says unsupported claim. [src:OLD_CLAIM]",
        encoding="utf-8",
    )

    # Direct deterministic seam behind the retired public `inputs classify` CLI.
    j = classify_input_dir(input_dir)

    evidence_names = [e["name"] for e in j["evidence"]]
    assert "real_source.md" in evidence_names
    assert "audited_brief.md" not in evidence_names

    skipped_names = {s["name"]: s for s in j["skipped"]}
    assert "audited_brief.md" in skipped_names
    assert skipped_names["audited_brief.md"]["reason"] == "suspicious_output_artifact"


def test_inputs_classify_records_skipped_files(tmp_path: Path):
    ws = _write_workspace(tmp_path)
    input_dir = ws / "input"

    (input_dir / "feedback").mkdir(parents=True, exist_ok=True)
    (input_dir / "sources").mkdir(parents=True, exist_ok=True)
    (input_dir / "random").mkdir(parents=True, exist_ok=True)

    (input_dir / "feedback" / "annotated_output.docx").write_text("...", encoding="utf-8")
    (input_dir / "feedback" / "screenshot.jpg").write_bytes(b"synthetic jpg")
    (input_dir / "sources" / "report.pdf").write_text("...", encoding="utf-8")
    (input_dir / "sources" / "archive.xyz").write_text("...", encoding="utf-8")
    (input_dir / "random" / "foo.md").write_text("some content", encoding="utf-8")

    # Direct deterministic seam behind the retired public `inputs classify` CLI.
    j = classify_input_dir(input_dir)

    skipped_names = {s["name"]: s for s in j["skipped"]}

    # .docx in feedback subdir
    assert "annotated_output.docx" in skipped_names
    assert skipped_names["annotated_output.docx"]["reason"] == "needs_document_extraction"
    assert skipped_names["annotated_output.docx"]["suggested_role"] == "feedback"
    assert skipped_names["annotated_output.docx"]["extract_with"] == "briefloop inputs extract"
    assert skipped_names["screenshot.jpg"]["reason"] == "needs_document_extraction"
    assert skipped_names["screenshot.jpg"]["suggested_role"] == "feedback"

    # .pdf in sources subdir
    assert "report.pdf" in skipped_names
    assert skipped_names["report.pdf"]["reason"] == "needs_document_extraction"
    assert skipped_names["report.pdf"]["suggested_role"] == "evidence"
    assert skipped_names["archive.xyz"]["reason"] == "unsupported_extension"
    assert skipped_names["archive.xyz"]["suggested_role"] == "evidence"

    # file in unknown dir
    assert "foo.md" in skipped_names
    assert skipped_names["foo.md"]["reason"] == "unknown_input_subdir"

    # evidence is empty (no real sources)
    assert len(j["evidence"]) == 0


def test_manual_provider_blocks_feedback_instruction_context_paths(tmp_path: Path):
    from multi_agent_brief.sources.manual import ManualProvider
    from multi_agent_brief.sources.base import SourceQuery

    ws = tmp_path / "ws"
    ws.mkdir()
    input_dir = ws / "input"
    (input_dir / "feedback").mkdir(parents=True)
    (input_dir / "sources").mkdir(parents=True)
    (input_dir / "feedback" / "notes.md").write_text("please fix typo", encoding="utf-8")
    (input_dir / "sources" / "real.md").write_text("real evidence", encoding="utf-8")

    provider = ManualProvider()
    query = SourceQuery()

    # Block feedback dir
    config = {"sources": [{"path": str(input_dir / "feedback"), "name": "feedback-dir"}]}
    items = provider.collect(query, config)
    assert len(items) == 1, f"Expected 1 error item, got {len(items)}"
    assert items[0].source_type == "manual_error"
    assert items[0].metadata["error_type"] == "non_evidence_path_blocked"

    # Allow sources dir
    config2 = {"sources": [{"path": str(input_dir / "sources"), "name": "sources-dir"}]}
    items2 = provider.collect(query, config2)
    assert len(items2) == 1
    assert items2[0].source_type == "local_file"

    # Root-level input/ still works
    config3 = {"sources": [{"path": str(input_dir), "name": "input-root"}]}
    items3 = provider.collect(query, config3)
    # Should include real.md (from sources subdir — skip) AND feedback/README (skipped)
    # Actually iterdir only sees top-level, so if no top-level files, it returns empty
    # Let's add a top-level file
    (input_dir / "top_level.md").write_text("top level", encoding="utf-8")
    items3 = provider.collect(query, config3)
    assert any(it.source_type == "local_file" and "top level" in it.title.lower() for it in items3), \
        f"Expected top_level.md as evidence, got: {[it.title for it in items3]}"
