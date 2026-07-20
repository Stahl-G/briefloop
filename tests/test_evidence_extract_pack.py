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
from multi_agent_brief.orchestrator.runtime_state import check_runtime_state, initialize_runtime_state
from multi_agent_brief.orchestrator.runtime_state.evidence_span_registry import (
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


def test_extract_registers_scope_and_local_sources(tmp_path: Path) -> None:
    workspace = tmp_path / "evidence-ws"
    source_dir = tmp_path / "source-docs"
    source_dir.mkdir()
    memo = source_dir / "permit-summary.md"
    memo.write_text("# Permit Summary\n\nCapacity: 100 MW.\n", encoding="utf-8")
    pdf = source_dir / "permit.pdf"
    pdf.write_bytes(b"%PDF-1.4\nplaceholder\n")

    assert main(["new", "evidence-extract", str(workspace)]) == 0

    payload = _register_extract(
        workspace,
        scope="utilities, permits, production capacity",
        sources=[str(memo), str(pdf)],
        source_category="regulator_record",
    )

    assert payload["ok"] is True
    assert payload["boundary"] == "evidence_extract_scope_source_and_text_span_registration_only"
    assert payload["source_count"] == 2
    assert payload["evidence_span_registry"] == "output/intermediate/evidence_span_registry.json"
    assert payload["source_lock"] == "output/intermediate/evidence_extract_source_lock.json"
    assert payload["audit_source_lock"] == "output/audit/evidence_extract_source_lock.json"
    assert payload["page_inventory"] == "output/intermediate/evidence_extract_page_inventory.json"
    assert payload["audit_page_inventory"] == "output/audit/evidence_extract_page_inventory.json"
    assert payload["page_inventory_source_count"] == 1
    assert payload["page_inventory_page_count"] == 1
    assert payload["evidence_span_registry_source_count"] == 1
    assert payload["evidence_span_registry_span_count"] == 1
    assert "no_legal_conclusion" in payload["non_claims"]
    assert "no_binary_span_extraction" in payload["non_claims"]
    assert "no_semantic_support_assessment" in payload["non_claims"]
    assert any(item["code"] == "binary_source_registered_only" for item in payload["warnings"])

    scope_path = workspace / "extraction_scope.yaml"
    audit_scope_path = workspace / "output" / "audit" / "extraction_scope.yaml"
    assert scope_path.exists()
    assert audit_scope_path.read_text(encoding="utf-8") == scope_path.read_text(encoding="utf-8")
    scope = yaml.safe_load(scope_path.read_text(encoding="utf-8"))
    assert scope["schema_version"] == "briefloop.extraction_scope.v1"
    assert scope["scope"] == "utilities, permits, production capacity"
    assert scope["source_count"] == 2
    assert scope["sources"][0]["source_id"] == "SRC-001"
    assert scope["sources"][0]["path"].startswith("input/sources/evidence_extract/")
    assert (workspace / scope["sources"][0]["path"]).read_text(encoding="utf-8").startswith("# Permit Summary")
    assert scope["boundary"] == "scope_source_and_text_span_registration_only"
    assert "no_binary_span_extraction" in scope["non_claims"]
    assert "no_semantic_support_assessment" in scope["non_claims"]

    source_lock_path = workspace / "output" / "intermediate" / "evidence_extract_source_lock.json"
    audit_source_lock_path = workspace / "output" / "audit" / "evidence_extract_source_lock.json"
    assert source_lock_path.exists()
    assert audit_source_lock_path.read_text(encoding="utf-8") == source_lock_path.read_text(encoding="utf-8")
    source_lock = json.loads(source_lock_path.read_text(encoding="utf-8"))
    assert source_lock["schema_version"] == "briefloop.evidence_extract_source_lock.v1"
    assert source_lock["source_count"] == 2
    assert source_lock["sources"][0]["source_id"] == "SRC-001"
    assert source_lock["sources"][0]["path"] == scope["sources"][0]["path"]
    assert source_lock["sources"][0]["source_sha256"] == scope["sources"][0]["source_sha256"]
    assert source_lock["sources"][1]["registered_only"] is True
    assert "no_pdf_or_binary_page_extraction" in source_lock["non_claims"]

    page_inventory_path = workspace / "output" / "intermediate" / "evidence_extract_page_inventory.json"
    audit_page_inventory_path = workspace / "output" / "audit" / "evidence_extract_page_inventory.json"
    assert page_inventory_path.exists()
    assert audit_page_inventory_path.read_text(encoding="utf-8") == page_inventory_path.read_text(encoding="utf-8")
    page_inventory = json.loads(page_inventory_path.read_text(encoding="utf-8"))
    assert page_inventory["schema_version"] == "briefloop.evidence_extract_page_inventory.v1"
    assert page_inventory["source_lock_path"] == "output/intermediate/evidence_extract_source_lock.json"
    assert page_inventory["source_count"] == 2
    assert page_inventory["page_count"] == 1
    assert page_inventory["inventory_source_count"] == 1
    assert page_inventory["sources"][0]["inventory_status"] == "text_logical_page_seeded"
    assert page_inventory["sources"][0]["pages"][0]["page_id"] == "PAGE-SRC-001-001"
    assert page_inventory["sources"][0]["pages"][0]["page_basis"] == "utf8_text_file"
    assert page_inventory["sources"][1]["inventory_status"] == "unsupported_source_format_registered_only"
    assert page_inventory["sources"][1]["pages"] == []
    assert "no_pdf_or_binary_page_extraction" in page_inventory["non_claims"]

    registry_path = workspace / "output" / "intermediate" / "evidence_span_registry.json"
    registry_payload = json.loads(registry_path.read_text(encoding="utf-8"))
    assert EvidenceSpanRegistryContract.validate(registry_payload) == []
    assert validate_evidence_span_registry_against_source_pack(
        registry_payload=registry_payload,
        workspace=workspace,
    ) is None
    assert registry_payload["metadata"]["boundary"] == "deterministic_text_span_seed_not_semantic_support"
    assert registry_payload["sources"][0]["source_id"] == "SRC-001"
    assert registry_payload["sources"][0]["source_path"] == scope["sources"][0]["path"]
    span = registry_payload["sources"][0]["spans"][0]
    assert span["span_id"] == "ESP-001-01"
    assert span["page_id"] == "PAGE-SRC-001-001"
    assert span["page_number"] == 1
    assert span["raw_excerpt"].startswith("# Permit Summary")
    source_text = (workspace / scope["sources"][0]["path"]).read_text(encoding="utf-8")
    assert source_text[span["char_start"]:span["char_end"]] == span["raw_excerpt"]
    assert not any(source["source_path"].endswith(".pdf") for source in registry_payload["sources"])

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
    assert evidence_entries[0]["metadata"]["source_id"] == "SRC-001"
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

    initialize_runtime_state(runtime="operator", workspace=workspace, repo_workdir=ROOT)
    state = check_runtime_state(workspace=workspace, repo_workdir=ROOT)
    source_lock_record = state["artifact_registry"]["artifacts"]["evidence_extract_source_lock"]
    assert source_lock_record["status"] == "valid"
    assert source_lock_record["validation_result"] == "experimental_evidence_extract_source_lock"
    page_inventory_record = state["artifact_registry"]["artifacts"]["evidence_extract_page_inventory"]
    assert page_inventory_record["status"] == "valid"
    assert page_inventory_record["validation_result"] == "experimental_evidence_extract_page_inventory"


def test_extract_source_lock_invalidates_modified_registered_source(tmp_path: Path) -> None:
    workspace = tmp_path / "evidence-ws"
    source = tmp_path / "source.md"
    source.write_text("Alpha source bytes.\n", encoding="utf-8")
    assert main(["new", "evidence-extract", str(workspace)]) == 0

    payload = _register_extract(workspace, scope="permits", source=[str(source)])
    copied_source = workspace / payload["sources"][0]["path"]
    copied_source.write_text("Omega source bytes.\n", encoding="utf-8")

    initialize_runtime_state(runtime="operator", workspace=workspace, repo_workdir=ROOT)
    state = check_runtime_state(workspace=workspace, repo_workdir=ROOT)
    source_lock_record = state["artifact_registry"]["artifacts"]["evidence_extract_source_lock"]
    assert source_lock_record["status"] == "invalid"
    assert source_lock_record["validation_result"] == (
        "evidence_extract_source_lock_validation_error:source_sha256_mismatch:SRC-001"
    )


def test_extract_bridges_adjacent_mineru_markdown_for_pdf(tmp_path: Path) -> None:
    workspace = tmp_path / "evidence-ws"
    source_dir = tmp_path / "source-docs"
    source_dir.mkdir()
    pdf = source_dir / "permit.pdf"
    pdf.write_bytes(b"%PDF-1.4\nplaceholder\n")
    derived = extracted_markdown_path(pdf)
    derived.write_text("# Permit PDF\n\nMinerU extracted capacity: 100 MW.\n", encoding="utf-8")

    assert main(["new", "evidence-extract", str(workspace)]) == 0

    payload = _register_extract(workspace, scope="permits", source=[str(pdf)])

    assert payload["ok"] is True
    assert payload["source_count"] == 1
    assert payload["page_inventory_source_count"] == 1
    assert payload["page_inventory_page_count"] == 1
    assert payload["evidence_span_registry_source_count"] == 1
    assert payload["evidence_span_registry_span_count"] == 1
    assert not any(item["code"] == "binary_source_registered_only" for item in payload["warnings"])

    source_lock = json.loads(
        (workspace / "output" / "intermediate" / "evidence_extract_source_lock.json").read_text(encoding="utf-8")
    )
    locked_source = source_lock["sources"][0]
    assert locked_source["path"].endswith("001-permit.pdf")
    assert locked_source["registered_only"] is False
    assert locked_source["derived_markdown"]["path"].endswith("001-permit_pdf.mineru.md")
    assert locked_source["derived_markdown"]["derivation"] == "mineru_adjacent_markdown"
    copied_pdf = workspace / locked_source["path"]
    copied_derived = workspace / locked_source["derived_markdown"]["path"]
    assert copied_pdf.read_bytes() == pdf.read_bytes()
    assert copied_derived.read_text(encoding="utf-8") == derived.read_text(encoding="utf-8")

    page_inventory = json.loads(
        (workspace / "output" / "intermediate" / "evidence_extract_page_inventory.json").read_text(encoding="utf-8")
    )
    inventory_source = page_inventory["sources"][0]
    assert inventory_source["source_path"] == locked_source["path"]
    assert inventory_source["text_source_path"] == locked_source["derived_markdown"]["path"]
    assert inventory_source["inventory_status"] == "text_logical_page_seeded"
    assert inventory_source["pages"][0]["page_basis"] == "mineru_derived_markdown"
    assert inventory_source["pages"][0]["needs_visual_inspection"] is True

    registry_payload = json.loads(
        (workspace / "output" / "intermediate" / "evidence_span_registry.json").read_text(encoding="utf-8")
    )
    assert registry_payload["sources"][0]["source_path"] == locked_source["derived_markdown"]["path"]
    span = registry_payload["sources"][0]["spans"][0]
    assert span["raw_excerpt"].startswith("# Permit PDF")
    assert copied_derived.read_text(encoding="utf-8")[span["char_start"]:span["char_end"]] == span["raw_excerpt"]

    sources = yaml.safe_load((workspace / "sources.yaml").read_text(encoding="utf-8"))
    evidence_entry = next(item for item in sources["manual"]["sources"] if item.get("evidence_extract_registered"))
    assert evidence_entry["enabled"] is True
    assert evidence_entry["registered_only"] is False
    assert evidence_entry["path"] == locked_source["derived_markdown"]["path"]
    assert evidence_entry["metadata"]["original_source_path"] == locked_source["path"]
    assert evidence_entry["metadata"]["derived_markdown_path"] == locked_source["derived_markdown"]["path"]

    source_config = load_sources_config(workspace / "sources.yaml")
    items, provider_errors = collect_all_sources(source_config)
    assert provider_errors == []
    assert len(items) == 1
    assert "MinerU extracted capacity" in items[0].content

    initialize_runtime_state(runtime="operator", workspace=workspace, repo_workdir=ROOT)
    state = check_runtime_state(workspace=workspace, repo_workdir=ROOT)
    assert state["artifact_registry"]["artifacts"]["evidence_extract_source_lock"]["status"] == "valid"
    assert state["artifact_registry"]["artifacts"]["evidence_extract_page_inventory"]["status"] == "valid"
    assert state["artifact_registry"]["artifacts"]["evidence_span_registry"]["status"] == "valid"


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


def test_extract_source_lock_invalidates_modified_derived_mineru_markdown(tmp_path: Path) -> None:
    workspace = tmp_path / "evidence-ws"
    pdf = tmp_path / "permit.pdf"
    pdf.write_bytes(b"%PDF-1.4\nplaceholder\n")
    extracted_markdown_path(pdf).write_text("# Permit PDF\n\nOriginal derived text.\n", encoding="utf-8")
    assert main(["new", "evidence-extract", str(workspace)]) == 0

    payload = _register_extract(workspace, scope="permits", source=[str(pdf)])
    copied_derived = workspace / payload["sources"][0]["derived_markdown_path"]
    copied_derived.write_text("# Permit PDF\n\nTampered derived text.\n", encoding="utf-8")

    initialize_runtime_state(runtime="operator", workspace=workspace, repo_workdir=ROOT)
    state = check_runtime_state(workspace=workspace, repo_workdir=ROOT)
    record = state["artifact_registry"]["artifacts"]["evidence_extract_source_lock"]
    assert record["status"] == "invalid"
    assert record["validation_result"] == (
        "evidence_extract_source_lock_validation_error:derived_markdown_sha256_mismatch:SRC-001"
    )


def test_extract_source_lock_rejects_derived_markdown_outside_evidence_root(tmp_path: Path) -> None:
    workspace = tmp_path / "evidence-ws"
    pdf = tmp_path / "permit.pdf"
    pdf.write_bytes(b"%PDF-1.4\nplaceholder\n")
    extracted_markdown_path(pdf).write_text("# Permit PDF\n\nDerived text.\n", encoding="utf-8")
    assert main(["new", "evidence-extract", str(workspace)]) == 0

    _register_extract(workspace, scope="permits", source=[str(pdf)])
    outside = workspace / "input" / "context" / "permit_pdf.mineru.md"
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_text("# Derived outside evidence root\n", encoding="utf-8")
    lock_path = workspace / "output" / "intermediate" / "evidence_extract_source_lock.json"
    source_lock = json.loads(lock_path.read_text(encoding="utf-8"))
    source_lock["sources"][0]["derived_markdown"]["path"] = "input/context/permit_pdf.mineru.md"
    lock_path.write_text(json.dumps(source_lock, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    initialize_runtime_state(runtime="operator", workspace=workspace, repo_workdir=ROOT)
    state = check_runtime_state(workspace=workspace, repo_workdir=ROOT)
    record = state["artifact_registry"]["artifacts"]["evidence_extract_source_lock"]
    assert record["status"] == "invalid"
    assert record["validation_result"] == (
        "evidence_extract_source_lock_validation_error:derived_markdown_path_unsafe:SRC-001"
    )


def test_extract_source_lock_rejects_symlinked_evidence_extract_root(tmp_path: Path) -> None:
    workspace = tmp_path / "evidence-ws"
    source = tmp_path / "source.md"
    source.write_text("Alpha source bytes.\n", encoding="utf-8")
    assert main(["new", "evidence-extract", str(workspace)]) == 0

    payload = _register_extract(workspace, scope="permits", source=[str(source)])
    copied_source = workspace / payload["sources"][0]["path"]
    outside = tmp_path / "outside-evidence-root"
    outside.mkdir()
    (outside / copied_source.name).write_bytes(copied_source.read_bytes())
    shutil.rmtree(workspace / "input" / "sources" / "evidence_extract")
    (workspace / "input" / "sources" / "evidence_extract").symlink_to(
        outside,
        target_is_directory=True,
    )

    initialize_runtime_state(runtime="operator", workspace=workspace, repo_workdir=ROOT)
    state = check_runtime_state(workspace=workspace, repo_workdir=ROOT)
    source_lock_record = state["artifact_registry"]["artifacts"]["evidence_extract_source_lock"]
    assert source_lock_record["status"] == "invalid"
    assert source_lock_record["validation_result"] == (
        "evidence_extract_source_lock_validation_error:source_path_unsafe:SRC-001"
    )


def test_extract_page_inventory_rejects_unknown_source_id(tmp_path: Path) -> None:
    workspace = tmp_path / "evidence-ws"
    source = tmp_path / "source.md"
    source.write_text("Alpha source bytes.\n", encoding="utf-8")
    assert main(["new", "evidence-extract", str(workspace)]) == 0

    _register_extract(workspace, scope="permits", source=[str(source)])
    inventory_path = workspace / "output" / "intermediate" / "evidence_extract_page_inventory.json"
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    inventory["sources"][0]["source_id"] = "SRC-999"
    inventory_path.write_text(json.dumps(inventory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    initialize_runtime_state(runtime="operator", workspace=workspace, repo_workdir=ROOT)
    state = check_runtime_state(workspace=workspace, repo_workdir=ROOT)
    record = state["artifact_registry"]["artifacts"]["evidence_extract_page_inventory"]
    assert record["status"] == "invalid"
    assert record["validation_result"] == (
        "evidence_extract_page_inventory_validation_error:unknown_source_id:SRC-999"
    )


def test_extract_page_inventory_rejects_stale_source_lock_sha(tmp_path: Path) -> None:
    workspace = tmp_path / "evidence-ws"
    source = tmp_path / "source.md"
    source.write_text("Alpha source bytes.\n", encoding="utf-8")
    assert main(["new", "evidence-extract", str(workspace)]) == 0

    _register_extract(workspace, scope="permits", source=[str(source)])
    inventory_path = workspace / "output" / "intermediate" / "evidence_extract_page_inventory.json"
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    inventory["source_lock_sha256"] = "0" * 64
    inventory_path.write_text(json.dumps(inventory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    initialize_runtime_state(runtime="operator", workspace=workspace, repo_workdir=ROOT)
    state = check_runtime_state(workspace=workspace, repo_workdir=ROOT)
    record = state["artifact_registry"]["artifacts"]["evidence_extract_page_inventory"]
    assert record["status"] == "invalid"
    assert record["validation_result"] == (
        "evidence_extract_page_inventory_validation_error:source_lock_sha256_mismatch"
    )


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("page_id", "PAGE-SRC-999-001", "span_page_unknown:ESP-001-01"),
        ("page_number", 99, "span_page_number_mismatch:ESP-001-01"),
    ],
)
def test_extract_evidence_span_registry_rejects_forged_page_trace(
    tmp_path: Path,
    field: str,
    value: object,
    reason: str,
) -> None:
    workspace = tmp_path / "evidence-ws"
    source = tmp_path / "source.md"
    source.write_text("# Permit Summary\n\nCapacity: 100 MW.\n", encoding="utf-8")
    assert main(["new", "evidence-extract", str(workspace)]) == 0

    _register_extract(workspace, scope="permits", source=[str(source)])
    registry_path = workspace / "output" / "intermediate" / "evidence_span_registry.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    registry["sources"][0]["spans"][0][field] = value
    registry_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    initialize_runtime_state(runtime="operator", workspace=workspace, repo_workdir=ROOT)
    state = check_runtime_state(workspace=workspace, repo_workdir=ROOT)
    record = state["artifact_registry"]["artifacts"]["evidence_span_registry"]
    assert record["status"] == "invalid"
    assert record["validation_result"] == f"evidence_span_registry_validation_error:{reason}"


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

    # LEGACY-DELETE: retired public `extract` CLI; the typed zero-write
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
