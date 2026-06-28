"""Tests for the experimental Evidence Extract product entry."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from multi_agent_brief.cli.main import main
from multi_agent_brief.sources.registry import collect_all_sources, load_sources_config


def test_extract_registers_scope_and_local_sources(tmp_path: Path, capsys) -> None:
    workspace = tmp_path / "evidence-ws"
    source_dir = tmp_path / "source-docs"
    source_dir.mkdir()
    memo = source_dir / "permit-summary.md"
    memo.write_text("# Permit Summary\n\nCapacity: 100 MW.\n", encoding="utf-8")
    pdf = source_dir / "permit.pdf"
    pdf.write_bytes(b"%PDF-1.4\nplaceholder\n")

    assert main(["new", "evidence-extract", str(workspace)]) == 0
    capsys.readouterr()

    assert (
        main(
            [
                "extract",
                "--workspace",
                str(workspace),
                "--scope",
                "utilities, permits, production capacity",
                "--sources",
                str(memo),
                str(pdf),
                "--source-category",
                "regulator_record",
                "--json",
            ]
        )
        == 0
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["boundary"] == "evidence_extract_scope_and_source_registration_only"
    assert payload["source_count"] == 2
    assert "no_legal_conclusion" in payload["non_claims"]
    assert any(item["code"] == "binary_source_registered_only" for item in payload["warnings"])

    scope_path = workspace / "extraction_scope.yaml"
    audit_scope_path = workspace / "output" / "audit" / "extraction_scope.yaml"
    assert scope_path.exists()
    assert audit_scope_path.read_text(encoding="utf-8") == scope_path.read_text(encoding="utf-8")
    scope = yaml.safe_load(scope_path.read_text(encoding="utf-8"))
    assert scope["schema_version"] == "briefloop.extraction_scope.v1"
    assert scope["scope"] == "utilities, permits, production capacity"
    assert scope["source_count"] == 2
    assert scope["sources"][0]["source_id"] == "EVID-001"
    assert scope["sources"][0]["path"].startswith("input/sources/evidence_extract/")
    assert (workspace / scope["sources"][0]["path"]).read_text(encoding="utf-8").startswith("# Permit Summary")

    sources = yaml.safe_load((workspace / "sources.yaml").read_text(encoding="utf-8"))
    assert "manual" in sources["source_strategy"]["enabled_providers"]
    evidence_entries = [
        item
        for item in sources["manual"]["sources"]
        if item.get("evidence_extract_registered")
    ]
    assert len(evidence_entries) == 2
    assert evidence_entries[0]["category"] == "regulator"
    assert evidence_entries[0]["enabled"] is True
    assert evidence_entries[0]["registered_only"] is False
    assert evidence_entries[0]["metadata"]["source_id"] == "EVID-001"
    assert "original_path" not in evidence_entries[0]["metadata"]
    assert evidence_entries[0]["metadata"]["source_sha256"]
    assert evidence_entries[0]["metadata"]["source_size_bytes"] > 0
    assert evidence_entries[0]["path"].startswith("input/sources/evidence_extract/")
    assert evidence_entries[1]["enabled"] is False
    assert evidence_entries[1]["registered_only"] is True

    source_config = load_sources_config(workspace / "sources.yaml")
    items, provider_errors = collect_all_sources(source_config)
    assert provider_errors == []
    assert len(items) == 1
    assert items[0].metadata["path"].endswith("001-permit-summary.md")


def test_extract_does_not_persist_external_absolute_source_paths(tmp_path: Path, capsys) -> None:
    workspace = tmp_path / "evidence-ws"
    outside = tmp_path / "outside-user-folder"
    outside.mkdir()
    source = outside / "private-source.md"
    source.write_text("# Private Source\n", encoding="utf-8")
    assert main(["new", "evidence-extract", str(workspace)]) == 0
    capsys.readouterr()

    assert (
        main(
            [
                "extract",
                "--workspace",
                str(workspace),
                "--scope",
                "permits",
                "--source",
                str(source),
                "--json",
            ]
        )
        == 0
    )

    output = capsys.readouterr().out
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


def test_extract_expands_home_globs(tmp_path: Path, monkeypatch, capsys) -> None:
    workspace = tmp_path / "evidence-ws"
    home = tmp_path / "home"
    docs = home / "docs"
    docs.mkdir(parents=True)
    source = docs / "permit.md"
    source.write_text("# Permit\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    assert main(["new", "evidence-extract", str(workspace)]) == 0
    capsys.readouterr()

    assert (
        main(
            [
                "extract",
                "--workspace",
                str(workspace),
                "--scope",
                "permits",
                "--sources",
                "~/docs/*.md",
                "--json",
            ]
        )
        == 0
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["source_count"] == 1
    assert payload["sources"][0]["filename"] == "001-permit.md"


def test_extract_rejects_non_evidence_extract_workspace(tmp_path: Path, capsys) -> None:
    workspace = tmp_path / "weekly"
    source = tmp_path / "source.md"
    source.write_text("source text\n", encoding="utf-8")

    assert main(["new", "market-weekly", str(workspace)]) == 0
    capsys.readouterr()

    assert (
        main(
            [
                "extract",
                "--workspace",
                str(workspace),
                "--scope",
                "permits",
                "--source",
                str(source),
                "--json",
            ]
        )
        == 1
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "only supported for evidence_extract" in payload["error"]
    assert not (workspace / "extraction_scope.yaml").exists()


def test_extract_requires_existing_source_file(tmp_path: Path, capsys) -> None:
    workspace = tmp_path / "evidence-ws"
    assert main(["new", "evidence-extract", str(workspace)]) == 0
    capsys.readouterr()

    assert (
        main(
            [
                "extract",
                "--workspace",
                str(workspace),
                "--scope",
                "permits",
                "--source",
                str(tmp_path / "missing.md"),
                "--json",
            ]
        )
        == 1
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "source file not found" in payload["error"]
    assert not (workspace / "input" / "sources" / "evidence_extract").exists()


def test_extract_bad_sources_yaml_fails_before_writing(tmp_path: Path, capsys) -> None:
    workspace = tmp_path / "evidence-ws"
    source = tmp_path / "source.md"
    source.write_text("# Source\n", encoding="utf-8")
    assert main(["new", "evidence-extract", str(workspace)]) == 0
    capsys.readouterr()
    (workspace / "sources.yaml").write_text("source_strategy: [\n", encoding="utf-8")

    assert (
        main(
            [
                "extract",
                "--workspace",
                str(workspace),
                "--scope",
                "permits",
                "--source",
                str(source),
                "--json",
            ]
        )
        == 1
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["ok"] is False
    assert "Traceback" not in captured.out
    assert "Traceback" not in captured.err
    assert not (workspace / "extraction_scope.yaml").exists()
    assert not (workspace / "output" / "audit" / "extraction_scope.yaml").exists()
    assert not (workspace / "input" / "sources" / "evidence_extract").exists()
