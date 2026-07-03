"""Tests for input classification and feedback hygiene (v0.5.7)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from functools import partial
from pathlib import Path

import pytest
from multi_agent_brief.cli.main import main
from multi_agent_brief.outputs.finalize import finalize_reader_outputs
from tests.helpers import write_workspace_files_under

CLI = sys.executable, "-m", "multi_agent_brief.cli.main"
ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"


def _cli_env() -> dict[str, str]:
    env = os.environ.copy()
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(SRC) + (os.pathsep + existing if existing else "")
    return env


_write_workspace = partial(
    write_workspace_files_under,
    config_text=(
        "project:\n  name: Test\n  language: zh-CN\n"
        "input:\n  path: input\n"
        "output:\n  path: output\n"
    ),
    include_output_dir=True,
)


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


# ────────────────────────────────────────────────────────────────────
# Test 1: respects config input.path
# ────────────────────────────────────────────────────────────────────

def test_inputs_classify_respects_config_input_path(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "config.yaml").write_text(
        "project:\n  name: Test\n  language: zh-CN\n"
        "input:\n  path: custom_input\n"
        "output:\n  path: output\n",
        encoding="utf-8",
    )
    custom_input = ws / "custom_input"
    sources_dir = custom_input / "sources"
    sources_dir.mkdir(parents=True)
    (sources_dir / "real_source.md").write_text("# Real source\nThis is evidence.", encoding="utf-8")

    # There should be NO input/ directory
    assert not (ws / "input").exists()

    result = subprocess.run(
        [*CLI, "inputs", "classify", "--config", str(ws / "config.yaml")],
        capture_output=True, text=True, cwd=str(ws), env=_cli_env(),
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"

    j = json.loads((ws / "output" / "input_classification.json").read_text(encoding="utf-8"))
    evidence_names = [e["name"] for e in j["evidence"]]
    assert "real_source.md" in evidence_names
    assert len(j["feedback"]) == 0
    assert len(j["instruction"]) == 0
    assert len(j["context"]) == 0


# ────────────────────────────────────────────────────────────────────
# Test 2: suspicious old output in input root → not evidence
# ────────────────────────────────────────────────────────────────────

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

    result = subprocess.run(
        [*CLI, "inputs", "classify", "--config", str(ws / "config.yaml")],
        capture_output=True, text=True, cwd=str(ws), env=_cli_env(),
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"

    j = json.loads((ws / "output" / "input_classification.json").read_text(encoding="utf-8"))

    evidence_names = [e["name"] for e in j["evidence"]]
    assert "real_source.md" in evidence_names
    assert "audited_brief.md" not in evidence_names

    skipped_names = {s["name"]: s for s in j["skipped"]}
    assert "audited_brief.md" in skipped_names
    assert skipped_names["audited_brief.md"]["reason"] == "suspicious_output_artifact"


def test_feedback_only_text_does_not_contaminate_evidence_or_reader_artifacts(tmp_path: Path):
    """Feedback remains available to feedback workflows without becoming evidence."""
    sentinel = "FEEDBACK_ONLY_SENTINEL_PR78_DO_NOT_TREAT_AS_FACT"
    ws = _write_workspace(tmp_path)
    input_dir = ws / "input"
    sources_dir = input_dir / "sources"
    feedback_dir = input_dir / "feedback"
    sources_dir.mkdir(parents=True, exist_ok=True)
    feedback_dir.mkdir(parents=True, exist_ok=True)
    (sources_dir / "market_source.md").write_text(
        "# Market source\n\nExampleCo opened a public demo facility in June 2026.",
        encoding="utf-8",
    )
    (feedback_dir / "operator_feedback.md").write_text(
        f"Please make the tone sharper. {sentinel}",
        encoding="utf-8",
    )

    result = main(["inputs", "classify", "--config", str(ws / "config.yaml"), "--quiet"])

    assert result == 0
    classification = json.loads((ws / "output" / "input_classification.json").read_text(encoding="utf-8"))
    assert [item["name"] for item in classification["evidence"]] == ["market_source.md"]
    assert [item["name"] for item in classification["feedback"]] == ["operator_feedback.md"]
    assert sentinel in (feedback_dir / "operator_feedback.md").read_text(encoding="utf-8")

    result = main(["run", "--workspace", str(ws), "--skip-doctor"])

    assert result == 0
    handoff_json_path = ws / "output" / "intermediate" / "agent_handoff.json"
    handoff_md_path = ws / "output" / "intermediate" / "agent_handoff.md"
    handoff_payload = json.loads(handoff_json_path.read_text(encoding="utf-8"))
    handoff_markdown = handoff_md_path.read_text(encoding="utf-8")
    combined_handoff = json.dumps(handoff_payload, ensure_ascii=False) + "\n" + handoff_markdown
    assert sentinel not in combined_handoff
    assert "input/feedback" in combined_handoff
    assert "remain non-evidence" in combined_handoff
    assert "eligible evidence files under input/sources count as evidence" in combined_handoff
    assert "binary inputs require extracted Markdown before use" in combined_handoff
    assert "only extracted files under input/sources are evidence" not in combined_handoff

    intermediate = ws / "output" / "intermediate"
    intermediate.mkdir(parents=True, exist_ok=True)
    (intermediate / "candidate_claims.json").write_text(
        json.dumps(
            [
                {
                    "candidate_id": "CAND-001",
                    "claim": "ExampleCo opened a public demo facility in June 2026.",
                    "source_id": "SRC-001",
                }
            ],
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (intermediate / "screened_candidates.json").write_text(
        json.dumps(
            [{"candidate_id": "CAND-001", "screening_status": "selected"}],
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (intermediate / "claim_ledger.json").write_text(
        json.dumps(
            [
                {
                    "claim_id": "CL-0001",
                    "statement": "ExampleCo opened a public demo facility in June 2026.",
                    "source_id": "SRC-001",
                    "evidence_text": "ExampleCo opened a public demo facility in June 2026.",
                    "source_url": "https://example.com/exampleco-demo",
                    "source_type": "local_file",
                    "metadata": {
                        "source_title": "ExampleCo Demo Facility",
                        "publisher": "Example News",
                        "source_category": "news_media",
                    },
                }
            ],
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (intermediate / "audited_brief.md").write_text(
        "# Feedback Hygiene Brief\n\n"
        "ExampleCo opened a public demo facility in June 2026. [src:CL-0001]\n",
        encoding="utf-8",
    )

    result = finalize_reader_outputs(
        output_dir=ws / "output",
        project_name="Feedback Hygiene Brief",
        output_formats=["markdown", "docx", "source_appendix"],
        output_named_outputs=True,
        output_filename_template="{project_name}_{report_date}",
        output_filename_tokens={
            "project_name": "Feedback_Hygiene_Brief",
            "report_date": "2026-06-30",
        },
    )

    text_artifacts = [
        intermediate / "candidate_claims.json",
        intermediate / "screened_candidates.json",
        intermediate / "claim_ledger.json",
        intermediate / "audited_brief.md",
        ws / "output" / "brief.md",
        ws / "output" / "Feedback_Hygiene_Brief_2026-06-30.md",
        ws / "output" / "source_appendix.md",
        ws / "output" / "delivery" / "brief.md",
    ]
    for path in text_artifacts:
        assert path.exists(), f"missing expected artifact: {path}"
        assert sentinel not in path.read_text(encoding="utf-8"), str(path)

    assert result.delivery_docx
    assert sentinel not in _docx_text(Path(result.delivery_docx))


# ────────────────────────────────────────────────────────────────────
# Test 3: skipped records unsupported files (not silently ignored)
# ────────────────────────────────────────────────────────────────────

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

    result = subprocess.run(
        [*CLI, "inputs", "classify", "--config", str(ws / "config.yaml")],
        capture_output=True, text=True, cwd=str(ws), env=_cli_env(),
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"

    j = json.loads((ws / "output" / "input_classification.json").read_text(encoding="utf-8"))

    skipped_names = {s["name"]: s for s in j["skipped"]}

    # .docx in feedback subdir
    assert "annotated_output.docx" in skipped_names
    assert skipped_names["annotated_output.docx"]["reason"] == "needs_document_extraction"
    assert skipped_names["annotated_output.docx"]["suggested_role"] == "feedback"
    assert skipped_names["annotated_output.docx"]["extract_with"] == "multi-agent-brief inputs extract"
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


def test_inputs_extract_converts_non_text_inputs_with_mineru(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    ws = _write_workspace(tmp_path)
    input_dir = ws / "input"
    (input_dir / "sources").mkdir(parents=True, exist_ok=True)
    (input_dir / "context").mkdir(parents=True, exist_ok=True)
    (input_dir / "feedback").mkdir(parents=True, exist_ok=True)

    (input_dir / "sources" / "report.pdf").write_bytes(b"%PDF-1.4 synthetic")
    (input_dir / "context" / "prior_weekly.docx").write_bytes(b"synthetic docx")
    (input_dir / "feedback" / "screenshot.jpg").write_bytes(b"synthetic jpg")

    monkeypatch.setattr(
        "multi_agent_brief.inputs.extractor.shutil.which",
        lambda name: "/usr/bin/mineru" if name == "mineru" else None,
    )

    def fake_run(cmd, capture_output, text, timeout):  # noqa: ANN001
        assert cmd[0] == "/usr/bin/mineru"
        run_dir = Path(cmd[cmd.index("-o") + 1])
        source_name = Path(cmd[cmd.index("-p") + 1]).name
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "full.md").write_text(
            f"# Extracted {source_name}\n\nSynthetic extracted text.",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(
        "multi_agent_brief.inputs.extractor.subprocess.run",
        fake_run,
    )

    result = main(["inputs", "extract", "--config", str(ws / "config.yaml"), "--quiet"])
    assert result == 0

    source_md = input_dir / "sources" / "report_pdf.mineru.md"
    context_md = input_dir / "context" / "prior_weekly_docx.mineru.md"
    feedback_md = input_dir / "feedback" / "screenshot_jpg.mineru.md"
    assert source_md.exists()
    assert context_md.exists()
    assert feedback_md.exists()
    assert "mabw-input-extraction" in source_md.read_text(encoding="utf-8")

    report = json.loads((ws / "output" / "input_extraction_report.json").read_text(encoding="utf-8"))
    assert report["status"] == "completed"
    extracted = {item["input_relative_path"]: item for item in report["extracted"]}
    assert extracted["sources/report.pdf"]["role"] == "evidence"
    assert extracted["context/prior_weekly.docx"]["role"] == "context"
    assert extracted["feedback/screenshot.jpg"]["role"] == "feedback"

    result = main(["inputs", "classify", "--config", str(ws / "config.yaml"), "--quiet"])
    assert result == 0
    classified = json.loads((ws / "output" / "input_classification.json").read_text(encoding="utf-8"))
    assert "report_pdf.mineru.md" in [item["name"] for item in classified["evidence"]]
    assert "prior_weekly_docx.mineru.md" in [item["name"] for item in classified["context"]]
    assert "screenshot_jpg.mineru.md" in [item["name"] for item in classified["feedback"]]

    skipped = {item["name"]: item for item in classified["skipped"]}
    assert skipped["report.pdf"]["reason"] == "document_extracted"
    assert skipped["report.pdf"]["extracted_markdown"].endswith("report_pdf.mineru.md")


def test_inputs_extract_reports_missing_mineru_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    ws = _write_workspace(tmp_path)
    input_dir = ws / "input"
    (input_dir / "sources").mkdir(parents=True, exist_ok=True)
    (input_dir / "sources" / "report.pdf").write_bytes(b"%PDF-1.4 synthetic")

    monkeypatch.setattr(
        "multi_agent_brief.inputs.extractor.shutil.which",
        lambda name: None,
    )

    result = main(["inputs", "extract", "--config", str(ws / "config.yaml"), "--quiet"])
    assert result == 1
    assert not (input_dir / "sources" / "report_pdf.mineru.md").exists()

    report = json.loads((ws / "output" / "input_extraction_report.json").read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert report["skipped"][0]["reason"] == "missing_mineru_cli"


def test_inputs_extract_fails_when_mineru_file_parse_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    ws = _write_workspace(tmp_path)
    input_dir = ws / "input"
    (input_dir / "sources").mkdir(parents=True, exist_ok=True)
    (input_dir / "sources" / "report.pdf").write_bytes(b"%PDF-1.4 synthetic")

    monkeypatch.setattr(
        "multi_agent_brief.inputs.extractor.shutil.which",
        lambda name: "/usr/bin/mineru" if name == "mineru" else None,
    )

    def fake_run(cmd, capture_output, text, timeout):  # noqa: ANN001
        return subprocess.CompletedProcess(cmd, 2, stdout="", stderr="parse failed")

    monkeypatch.setattr(
        "multi_agent_brief.inputs.extractor.subprocess.run",
        fake_run,
    )

    result = main(["inputs", "extract", "--config", str(ws / "config.yaml"), "--quiet"])
    assert result == 1

    report = json.loads((ws / "output" / "input_extraction_report.json").read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert report["skipped"][0]["reason"] == "mineru_cli_failed"


# ────────────────────────────────────────────────────────────────────
# Test 4: custom output creates parent dirs
# ────────────────────────────────────────────────────────────────────

def test_inputs_classify_custom_output_creates_parent(tmp_path: Path):
    ws = _write_workspace(tmp_path)
    (ws / "input" / "sources").mkdir(parents=True, exist_ok=True)
    (ws / "input" / "sources" / "real.md").write_text("# real", encoding="utf-8")

    deep_output = ws / "nonexistent" / "sub" / "input_classification.json"
    assert not deep_output.parent.exists()

    result = subprocess.run(
        [*CLI, "inputs", "classify", "--config", str(ws / "config.yaml"),
         "--output", str(deep_output)],
        capture_output=True, text=True, cwd=str(ws), env=_cli_env(),
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert deep_output.exists()
    j = json.loads(deep_output.read_text(encoding="utf-8"))
    assert "real.md" in [e["name"] for e in j["evidence"]]


def test_inputs_classify_custom_output_does_not_create_default_output_dir(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "config.yaml").write_text(
        "project:\n  name: Test\n  language: zh-CN\n"
        "input:\n  path: input\n"
        "output:\n  path: configured_output\n",
        encoding="utf-8",
    )
    (ws / "input" / "sources").mkdir(parents=True)
    (ws / "input" / "sources" / "real.md").write_text("# real", encoding="utf-8")

    custom_output = ws / "custom" / "classification.json"
    result = main(
        [
            "inputs",
            "classify",
            "--config",
            str(ws / "config.yaml"),
            "--output",
            str(custom_output),
            "--quiet",
        ]
    )

    assert result == 0
    assert custom_output.exists()
    assert not (ws / "configured_output").exists()


# ────────────────────────────────────────────────────────────────────
# Test 5: ManualProvider blocks non-evidence paths
# ────────────────────────────────────────────────────────────────────

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


# ────────────────────────────────────────────────────────────────────
# Test 6: finalize still strips [src:] markers
# ────────────────────────────────────────────────────────────────────

def test_finalize_reader_outputs_strip_src_markers(tmp_path: Path):
    ws = _write_workspace(tmp_path)
    intermediate = ws / "output" / "intermediate"
    intermediate.mkdir(parents=True)

    audited = intermediate / "audited_brief.md"
    audited.write_text(
        "# Test Brief\n\nThe company announced a new product. [src:CLM_001]\n\n"
        "More details followed. [src:CLM_002]",
        encoding="utf-8",
    )

    result = subprocess.run(
        [*CLI, "finalize", "--config", str(ws / "config.yaml")],
        capture_output=True, text=True, cwd=str(ws), env=_cli_env(),
    )
    assert result.returncode == 0, f"finalize failed: {result.stderr}"

    reader = ws / "output" / "brief.md"
    assert reader.exists(), f"brief.md not created. Files in output: {list((ws/'output').iterdir())}"
    content = reader.read_text(encoding="utf-8")
    assert "[src:" not in content, f"Found [src:] in reader output:\n{content}"
    assert "The company announced" in content
