"""Tests for the experimental Evidence Extract product entry."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from multi_agent_brief.cli.main import main
from multi_agent_brief.cli.product_commands import _register_evidence_extract_scope


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
    # The public `extract` CLI is retired; drive the deterministic
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


def test_extract_rejects_non_evidence_extract_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "weekly"
    source = tmp_path / "source.md"
    source.write_text("source text\n", encoding="utf-8")

    assert main(["new", "market-weekly", str(workspace)]) == 0

    with pytest.raises(ValueError, match="only supported for evidence_extract"):
        _register_extract(workspace, scope="permits", source=[str(source)])
    assert not (workspace / "extraction_scope.yaml").exists()
