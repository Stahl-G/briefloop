"""Tests for the experimental Evidence Extract product entry."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml

from multi_agent_brief.cli.main import main
from multi_agent_brief.cli.product_commands import _register_evidence_extract_scope
from multi_agent_brief.contracts.schemas.evidence_span_registry import EvidenceSpanRegistryContract
from multi_agent_brief.inputs.contracts import extracted_markdown_path
from multi_agent_brief.outputs.evidence_span_validation import (
    validate_evidence_span_registry_against_source_pack,
)
from multi_agent_brief.sources.registry import collect_all_sources, load_sources_config

ROOT = Path(__file__).resolve().parent.parent


def _workspace_file_bytes(workspace: Path) -> dict[str, bytes]:
    return {
        path.relative_to(workspace).as_posix(): path.read_bytes()
        for path in sorted(workspace.rglob("*"))
        if path.is_file()
    }


def _register_extract(
    workspace: Path,
    *,
    scope: str,
    source: list[str] | None = None,
    sources: list[str] | None = None,
    source_category: str = "other",
    language: str = "en",
    force: bool = False,
) -> dict[str, Any]:
    # The public `extract` CLI is retired (typed zero-write rejection, covered once by
    # test_extract_public_cli_is_retired_with_zero_mutation); drive the deterministic
    # registration seam directly.
    args = argparse.Namespace(
        workspace=str(workspace),
        scope=scope,
        source=source or [],
        sources=sources or [],
        source_category=source_category,
        language=language,
        force=force,
    )
    return _register_evidence_extract_scope(workspace=workspace, args=args)








def test_extract_glob_does_not_register_paired_mineru_markdown_twice(tmp_path: Path) -> None:
    workspace = tmp_path / "evidence-ws"
    source_dir = tmp_path / "source-docs"
    source_dir.mkdir()
    pdf = source_dir / "permit.pdf"
    pdf.write_bytes(b"%PDF-1.4\nplaceholder\n")
    derived = extracted_markdown_path(pdf)
    derived.write_text("# Permit PDF\n\nMinerU extracted capacity: 100 MW.\n", encoding="utf-8")

    assert main(["new", "evidence-extract", str(workspace)]) == 0

    payload = _register_extract(
        workspace,
        scope="permits",
        sources=[str(source_dir / "*")],
    )

    assert payload["source_count"] == 1
    assert payload["page_inventory_source_count"] == 1
    assert payload["page_inventory_page_count"] == 1
    assert payload["evidence_span_registry_source_count"] == 1
    assert payload["evidence_span_registry_span_count"] == 1

    copied_files = sorted(path.name for path in (workspace / "input" / "sources" / "evidence_extract").iterdir())
    assert copied_files == ["001-permit.pdf", "001-permit_pdf.mineru.md"]

    sources = yaml.safe_load((workspace / "sources.yaml").read_text(encoding="utf-8"))
    evidence_entries = [item for item in sources["manual"]["sources"] if item.get("evidence_extract_registered")]
    assert len(evidence_entries) == 1
    assert evidence_entries[0]["path"].endswith("001-permit_pdf.mineru.md")














def test_extract_does_not_persist_external_absolute_source_paths(tmp_path: Path) -> None:
    workspace = tmp_path / "evidence-ws"
    outside = tmp_path / "outside-user-folder"
    outside.mkdir()
    source = outside / "private-source.md"
    source.write_text("# Private Source\n", encoding="utf-8")
    assert main(["new", "evidence-extract", str(workspace)]) == 0

    payload = _register_extract(workspace, scope="permits", source=[str(source)])

    output = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    scope_text = (workspace / "extraction_scope.yaml").read_text(encoding="utf-8")
    audit_scope_text = (workspace / "output" / "audit" / "extraction_scope.yaml").read_text(encoding="utf-8")
    sources_text = (workspace / "sources.yaml").read_text(encoding="utf-8")
    for text in (output, scope_text, audit_scope_text, sources_text):
        assert str(source) not in text
        assert "outside-user-folder" not in text
        assert "original_path" not in text
    scope = yaml.safe_load(scope_text)
    record = scope["sources"][0]
    assert record["path"].startswith("input/sources/evidence_extract/")
    assert record["filename"] == "001-private-source.md"
    assert record["source_sha256"]
    assert record["source_size_bytes"] == source.stat().st_size


def test_extract_force_preserves_managed_source_used_as_input(tmp_path: Path) -> None:
    workspace = tmp_path / "evidence-ws"
    source = tmp_path / "source.md"
    source.write_text("# Source\n\nInitial durable bytes.\n", encoding="utf-8")
    assert main(["new", "evidence-extract", str(workspace)]) == 0

    first_payload = _register_extract(workspace, scope="initial scope", source=[str(source)])
    managed_source = workspace / first_payload["sources"][0]["path"]
    assert managed_source.exists()

    payload = _register_extract(
        workspace,
        scope="updated scope",
        source=[str(managed_source)],
        force=True,
    )

    assert payload["ok"] is True
    assert payload["source_count"] == 1
    next_managed_source = workspace / payload["sources"][0]["path"]
    assert next_managed_source.exists()
    assert next_managed_source.read_text(encoding="utf-8") == "# Source\n\nInitial durable bytes.\n"
    scope = yaml.safe_load((workspace / "extraction_scope.yaml").read_text(encoding="utf-8"))
    assert scope["scope"] == "updated scope"
    assert scope["sources"][0]["path"] == payload["sources"][0]["path"]


def test_extract_force_removes_stale_span_registry_when_no_text_sources(tmp_path: Path) -> None:
    workspace = tmp_path / "evidence-ws"
    source = tmp_path / "source.md"
    source.write_text("# Source\n\nInitial durable bytes.\n", encoding="utf-8")
    pdf = tmp_path / "source.pdf"
    pdf.write_bytes(b"%PDF-1.4\nplaceholder\n")
    assert main(["new", "evidence-extract", str(workspace)]) == 0

    first_payload = _register_extract(workspace, scope="initial scope", source=[str(source)])
    registry_path = workspace / first_payload["evidence_span_registry"]
    assert registry_path.exists()

    payload = _register_extract(
        workspace,
        scope="pdf scope",
        source=[str(pdf)],
        force=True,
    )

    assert payload["evidence_span_registry"] == ""
    assert payload["evidence_span_registry_span_count"] == 0
    assert any(item["code"] == "no_text_evidence_spans_generated" for item in payload["warnings"])
    assert not registry_path.exists()


def test_extract_force_does_not_stage_external_sources(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = tmp_path / "evidence-ws"
    initial_source = tmp_path / "initial-source.md"
    initial_source.write_text("# Source\n\nInitial durable bytes.\n", encoding="utf-8")
    next_source = tmp_path / "large-external-source.md"
    next_source.write_text("# Source\n\nReplacement durable bytes.\n", encoding="utf-8")
    assert main(["new", "evidence-extract", str(workspace)]) == 0
    _register_extract(workspace, scope="initial scope", source=[str(initial_source)])

    def fail_stage(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("external source should not be staged")

    monkeypatch.setattr(
        "multi_agent_brief.cli.product_commands._stage_extract_source_files",
        fail_stage,
    )

    payload = _register_extract(
        workspace,
        scope="updated scope",
        source=[str(next_source)],
        force=True,
    )

    next_managed_source = workspace / payload["sources"][0]["path"]
    assert next_managed_source.read_text(encoding="utf-8") == "# Source\n\nReplacement durable bytes.\n"


def test_extract_expands_home_globs(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "evidence-ws"
    home = tmp_path / "home"
    docs = home / "docs"
    docs.mkdir(parents=True)
    source = docs / "permit.md"
    source.write_text("# Permit\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    assert main(["new", "evidence-extract", str(workspace)]) == 0

    payload = _register_extract(
        workspace,
        scope="permits",
        sources=["~/docs/*.md"],
    )

    assert payload["source_count"] == 1
    assert payload["sources"][0]["filename"] == "001-permit.md"


def test_extract_rejects_non_evidence_extract_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "weekly"
    source = tmp_path / "source.md"
    source.write_text("source text\n", encoding="utf-8")

    assert main(["new", "market-weekly", str(workspace)]) == 0

    with pytest.raises(ValueError, match="only supported for evidence_extract"):
        _register_extract(workspace, scope="permits", source=[str(source)])
    assert not (workspace / "extraction_scope.yaml").exists()


def test_extract_requires_existing_source_file(tmp_path: Path) -> None:
    workspace = tmp_path / "evidence-ws"
    assert main(["new", "evidence-extract", str(workspace)]) == 0

    with pytest.raises(ValueError, match="source file not found"):
        _register_extract(workspace, scope="permits", source=[str(tmp_path / "missing.md")])
    assert not (workspace / "input" / "sources" / "evidence_extract").exists()


def test_extract_bad_sources_yaml_fails_before_writing(tmp_path: Path, capsys) -> None:
    workspace = tmp_path / "evidence-ws"
    source = tmp_path / "source.md"
    source.write_text("# Source\n", encoding="utf-8")
    assert main(["new", "evidence-extract", str(workspace)]) == 0
    capsys.readouterr()
    (workspace / "sources.yaml").write_text("source_strategy: [\n", encoding="utf-8")

    with pytest.raises(yaml.YAMLError):
        _register_extract(workspace, scope="permits", source=[str(source)])

    captured = capsys.readouterr()
    assert "Traceback" not in captured.out
    assert "Traceback" not in captured.err
    assert not (workspace / "extraction_scope.yaml").exists()
    assert not (workspace / "output" / "audit" / "extraction_scope.yaml").exists()
    assert not (workspace / "input" / "sources" / "evidence_extract").exists()


@pytest.mark.parametrize("source_flag", ["--source", "--sources"])
def test_extract_public_cli_is_retired_with_zero_mutation(
    tmp_path: Path,
    capsys,
    source_flag: str,
) -> None:
    workspace = tmp_path / "evidence-ws"
    source = tmp_path / "source.md"
    source.write_text("Alpha source bytes.\n", encoding="utf-8")
    assert main(["new", "evidence-extract", str(workspace)]) == 0
    capsys.readouterr()
    before = _workspace_file_bytes(workspace)

    # retired public `extract` CLI; the typed zero-write
    # rejection guard disappears with the legacy command surface.
    assert (
        main(
            [
                "extract",
                "--workspace",
                str(workspace),
                "--scope",
                "permits",
                source_flag,
                str(source),
                "--json",
            ]
        )
        == 1
    )
    assert capsys.readouterr().out == "runtime_command_unsupported\n"
    assert _workspace_file_bytes(workspace) == before
