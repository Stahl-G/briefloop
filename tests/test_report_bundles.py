"""Tests for experimental product-layer bundle projections."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import zipfile
from pathlib import Path

import pytest
import yaml

from multi_agent_brief.cli.main import main
from multi_agent_brief.product import bundle_projection, projection_platform
from multi_agent_brief.product.bundle_projection import (
    ReportBundleProjectionError,
    build_report_bundle_manifest,
    write_report_bundle_manifest,
)
from multi_agent_brief.product.quality_panel import (
    write_quality_panel,
    write_quality_panel_html,
    write_quality_summary,
)
from multi_agent_brief.product.template_registry import ReportTemplateRegistry
from multi_agent_brief.product.workspace_hygiene import classify_workspace_member
from tests.helpers import sha256_file as _sha256_file

ROOT = Path(__file__).resolve().parent.parent
EXPECTED_TEMPLATE_IDS = {
    "evidence_extract",
    "market_weekly",
    "management_monthly",
    "solar_industry_periodic",
}
requires_safe_bundle_publication = pytest.mark.skipif(
    not bundle_projection._supports_safe_bundle_publication(),
    reason="safe local bundle publication capability unavailable",
)
requires_safe_bundle_read = pytest.mark.skipif(
    not bundle_projection._supports_safe_bundle_read(),
    reason="safe local bundle member-read capability unavailable",
)


def _finalized_workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    delivery = ws / "output" / "delivery"
    intermediate = ws / "output" / "intermediate"
    gates = intermediate / "gates"
    delivery.mkdir(parents=True)
    gates.mkdir(parents=True)
    (ws / "config.yaml").write_text("project:\n  name: Bundle Test\n", encoding="utf-8")
    (ws / "report_spec.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": "briefloop.report_spec.v1",
                "report_pack": "market_weekly",
                "report_type": "market_weekly",
                "title": "Market Weekly Brief",
                "cadence": "weekly",
                "audience": {"label": "business reader", "language": "en-US"},
                "source_policy": {
                    "mode": "local_first",
                    "hidden_autonomous_crawling": False,
                },
                "control_spine": {
                    "claim_ledger": True,
                    "artifact_registry": True,
                    "quality_gates": True,
                    "event_log": True,
                    "archive": True,
                    "source_appendix": True,
                    "support_records": True,
                    "human_delivery_approval": True,
                    "frozen_artifact_integrity": True,
                },
                "outputs": ["markdown", "docx"],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    brief = delivery / "brief.md"
    brief.write_text("# Reader Brief\n\nClean reader text.\n", encoding="utf-8")
    trace = ws / "output" / "source_appendix_trace.md"
    trace.write_text("# Audit trace only\n", encoding="utf-8")
    appendix = ws / "output" / "source_appendix.md"
    appendix.write_text("# Source Appendix\n", encoding="utf-8")
    control_files = {
        "claim_ledger.json": {"claims": []},
        "audited_brief.md": "# Audited Brief\n\nClean audited text.\n",
        "audit_report.json": {"audit_status": "pass"},
        "artifact_registry.json": {"artifacts": {}},
        "runtime_manifest.json": {"run_id": "mabw-test-run"},
        "workflow_state.json": {"current_stage": "finalize"},
        "atomic_claim_graph.json": {"schema_version": "mabw.atomic_claim_graph.v1"},
        "claim_support_matrix.json": {"schema_version": "mabw.claim_support_matrix.v1"},
    }
    for filename, payload in control_files.items():
        text = (
            payload
            if isinstance(payload, str)
            else json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        )
        (intermediate / filename).write_text(text, encoding="utf-8")
    (intermediate / "event_log.jsonl").write_text(
        json.dumps({"event_type": "finalize_completed"}) + "\n",
        encoding="utf-8",
    )
    (gates / "auditor_quality_gate_report.json").write_text(
        json.dumps({"status": "pass"}) + "\n",
        encoding="utf-8",
    )
    (gates / "finalize_quality_gate_report.json").write_text(
        json.dumps({"status": "pass"}) + "\n",
        encoding="utf-8",
    )
    finalize_report = {
        "status": "pass",
        "reader_clean": {"status": "pass", "sample_findings": []},
        "delivery_artifacts": ["output/delivery/brief.md"],
        "delivery_artifact_sha256": {"output/delivery/brief.md": _sha256_file(brief)},
        "audit_binding": {
            "status": "pass",
            "claim_ledger_sha256": _sha256_file(intermediate / "claim_ledger.json"),
            "audited_brief_sha256": _sha256_file(intermediate / "audited_brief.md"),
            "audit_report_sha256": _sha256_file(intermediate / "audit_report.json"),
            "findings": [],
        },
        "source_appendix": "output/source_appendix.md",
        "source_appendix_trace": "output/source_appendix_trace.md",
        "source_appendix_trace_generation": "generated",
        "citation_profile": "executive",
        "citation_profile_source": "report_template.reader_contract.citation_profile",
        "citation_profile_runtime_effect": "citation_profile_resolution_only",
        "citation_profile_reader_citation_style": "source_label",
        "citation_profile_reader_metadata_level": "low_interference",
        "citation_profile_audit_trace_level": "complete_when_available",
        "citation_profile_delivery_exposes_internal_ids": False,
        "citation_profile_delivery_exposes_local_paths": False,
        "citation_profile_audit_bundle_keeps_trace": True,
        "citation_profile_warnings": [],
    }
    (intermediate / "finalize_report.json").write_text(
        json.dumps(finalize_report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return ws


def _write_quality_projection_artifacts(ws: Path) -> None:
    panel = write_quality_panel(workspace=ws)
    write_quality_summary(workspace=ws, panel_payload=panel)
    write_quality_panel_html(workspace=ws, panel_payload=panel)


def test_report_template_registry_discovers_root_and_packaged_templates() -> None:
    root = ReportTemplateRegistry.from_config_dir(ROOT / "configs" / "report_templates")
    package = ReportTemplateRegistry.from_package()

    for registry in (root, package):
        assert not registry.validation_errors
        assert registry.template_ids() == EXPECTED_TEMPLATE_IDS
        market = registry.get_by_report_type("market_weekly")
        assert market is not None
        assert market.section_order[0] == "executive_summary"
        assert market.section_order[-1] == "source_appendix"
        assert market.reader_contract["citation_profile"] == "executive"
        solar = registry.get_by_report_type("solar_industry_periodic")
        assert solar is not None
        assert solar.reader_contract["citation_profile"] == "executive"
        assert solar.section_order == (
            "cover",
            "executive_summary",
            "supply_chain_price_tracker",
            "demand_installation_outlook",
            "policy_tax_financing",
            "fx_rates_tracker",
            "company_implications",
            "source_appendix",
        )
        extract = registry.get_by_report_type("evidence_extract")
        assert extract is not None
        assert extract.reader_contract["citation_profile"] == "analyst"
        assert extract.section_order == (
            "scope",
            "extracted_points",
            "source_trace",
            "gaps_and_flags",
            "source_appendix",
        )


def test_report_template_registry_rejects_invalid_citation_profile(tmp_path: Path) -> None:
    payload = yaml.safe_load(
        (ROOT / "configs" / "report_templates" / "market_weekly.yaml").read_text(encoding="utf-8")
    )
    payload["reader_contract"] = dict(payload["reader_contract"])
    payload["reader_contract"]["citation_profile"] = "public_release"
    (tmp_path / "market_weekly.yaml").write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    registry = ReportTemplateRegistry.from_config_dir(tmp_path)

    assert any(
        item["field"] == "market_weekly.yaml.reader_contract.citation_profile"
        for item in registry.validation_errors
    )


def test_report_template_config_parity_between_root_and_package_copy() -> None:
    root_dir = ROOT / "configs" / "report_templates"
    package_dir = ROOT / "src" / "multi_agent_brief" / "configs" / "report_templates"

    for path in sorted(root_dir.glob("*.yaml")):
        package_path = package_dir / path.name
        assert package_path.exists()
        assert yaml.safe_load(path.read_text(encoding="utf-8")) == yaml.safe_load(
            package_path.read_text(encoding="utf-8")
        )


@requires_safe_bundle_read
def test_report_bundle_manifest_splits_delivery_and_audit_artifacts(tmp_path: Path) -> None:
    ws = _finalized_workspace(tmp_path)

    manifest = build_report_bundle_manifest(workspace=ws)

    assert manifest["template"]["template_id"] == "market_weekly"
    assert manifest["template"]["section_order"][0] == "executive_summary"
    assert manifest["citation_profile"]["status"] == "available"
    assert manifest["citation_profile"]["profile"] == "executive"
    assert manifest["citation_profile"]["source"] == "report_template.reader_contract.citation_profile"
    assert manifest["citation_profile"]["delivery_exposes_internal_ids"] is False
    assert manifest["citation_profile"]["delivery_exposes_local_paths"] is False
    assert manifest["citation_profile"]["audit_bundle_keeps_trace"] is True
    delivery_paths = {item["path"] for item in manifest["delivery_bundle"]["artifacts"]}
    audit_paths = {item["path"] for item in manifest["audit_bundle"]["artifacts"]}
    assert delivery_paths == {"output/delivery/brief.md"}
    assert "output/source_appendix_trace.md" in audit_paths
    assert "output/source_appendix.md" in audit_paths
    assert "output/intermediate/finalize_report.json" in audit_paths
    assert "output/intermediate/claim_ledger.json" in audit_paths
    assert "output/intermediate/audited_brief.md" in audit_paths
    assert not any(path.startswith("output/delivery/") for path in audit_paths)
    assert manifest["delivery_bundle"]["semantics"] == "reader_facing_artifacts_only"
    assert manifest["audit_bundle"]["semantics"] == "audit_control_artifacts_only_not_reader_delivery"
    assert manifest["packaging_hygiene"]["status"] == "clean"
    assert manifest["packaging_hygiene"]["excluded_artifacts"] == []


@requires_safe_bundle_read
def test_report_bundle_manifest_rejects_invalid_finalize_citation_profile(tmp_path: Path) -> None:
    ws = _finalized_workspace(tmp_path)
    report_path = ws / "output" / "intermediate" / "finalize_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["citation_profile"] = "public_release"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(ReportBundleProjectionError, match="citation profile invalid"):
        build_report_bundle_manifest(workspace=ws)


@requires_safe_bundle_read
def test_report_bundle_manifest_rejects_forged_reader_citation_exposure(tmp_path: Path) -> None:
    ws = _finalized_workspace(tmp_path)
    report_path = ws / "output" / "intermediate" / "finalize_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["citation_profile_delivery_exposes_internal_ids"] = True
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(ReportBundleProjectionError, match="citation profile invalid"):
        build_report_bundle_manifest(workspace=ws)


@requires_safe_bundle_read
@requires_safe_bundle_publication
def test_report_bundle_archives_reject_reader_residue_even_with_matching_hash(tmp_path: Path) -> None:
    ws = _finalized_workspace(tmp_path)
    brief = ws / "output" / "delivery" / "brief.md"
    brief.write_text(
        "# Reader Brief\n\n"
        "Leaked internal citation [src:SYN_CLAIM_001] and local path /Users/example/source.md\n",
        encoding="utf-8",
    )
    report_path = ws / "output" / "intermediate" / "finalize_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["delivery_artifact_sha256"]["output/delivery/brief.md"] = _sha256_file(brief)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(ReportBundleProjectionError, match="reader-clean residue scan"):
        write_report_bundle_manifest(workspace=ws, write_archives=True)

    assert not (ws / "output" / "delivery_bundle.zip").exists()
    assert not (ws / "output" / "audit_bundle.zip").exists()


@requires_safe_bundle_read
@requires_safe_bundle_publication
def test_report_bundle_archives_reject_evidence_span_id_residue(tmp_path: Path) -> None:
    ws = _finalized_workspace(tmp_path)
    brief = ws / "output" / "delivery" / "brief.md"
    brief.write_text(
        "# Reader Brief\n\n"
        "The reader text accidentally exposes audit span ESP-001-01. [S1]\n",
        encoding="utf-8",
    )
    report_path = ws / "output" / "intermediate" / "finalize_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["delivery_artifact_sha256"]["output/delivery/brief.md"] = _sha256_file(brief)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(ReportBundleProjectionError, match="span_id"):
        write_report_bundle_manifest(workspace=ws, write_archives=True)

    assert not (ws / "output" / "delivery_bundle.zip").exists()
    assert not (ws / "output" / "audit_bundle.zip").exists()


@requires_safe_bundle_read
def test_report_bundle_manifest_rejects_failed_reader_clean_report(tmp_path: Path) -> None:
    ws = _finalized_workspace(tmp_path)
    report_path = ws / "output" / "intermediate" / "finalize_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["reader_clean"] = {"status": "fail", "src_marker_count": 1, "sample_findings": []}
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(ReportBundleProjectionError, match="reader_clean.status must be pass"):
        build_report_bundle_manifest(workspace=ws)


@requires_safe_bundle_read
def test_report_bundle_manifest_reports_unreadable_finalize_report(tmp_path: Path) -> None:
    ws = _finalized_workspace(tmp_path)
    (ws / "output" / "intermediate" / "finalize_report.json").write_bytes(b"\xff\xfe\xfa")

    with pytest.raises(ReportBundleProjectionError, match="finalize_report.json is unreadable"):
        build_report_bundle_manifest(workspace=ws)


@requires_safe_bundle_read
def test_report_bundle_manifest_includes_quality_artifacts_in_audit_only(tmp_path: Path) -> None:
    ws = _finalized_workspace(tmp_path)
    _write_quality_projection_artifacts(ws)

    manifest = build_report_bundle_manifest(workspace=ws)

    delivery_paths = {item["path"] for item in manifest["delivery_bundle"]["artifacts"]}
    audit_paths = {item["path"] for item in manifest["audit_bundle"]["artifacts"]}
    quality_paths = {
        "output/intermediate/quality_panel.json",
        "output/intermediate/quality_summary.md",
        "output/intermediate/quality_panel.html",
    }
    assert quality_paths <= audit_paths
    assert delivery_paths.isdisjoint(quality_paths)
    quality_roles = {
        item["path"]: item["role"]
        for item in manifest["audit_bundle"]["artifacts"]
        if item["path"] in quality_paths
    }
    assert quality_roles == {
        "output/intermediate/quality_panel.json": "quality_panel",
        "output/intermediate/quality_summary.md": "quality_summary",
        "output/intermediate/quality_panel.html": "quality_panel_html",
    }


@requires_safe_bundle_read
def test_report_bundle_manifest_rejects_hand_edited_quality_panel_html(tmp_path: Path) -> None:
    ws = _finalized_workspace(tmp_path)
    _write_quality_projection_artifacts(ws)
    html_path = ws / "output" / "intermediate" / "quality_panel.html"
    html_path.write_text(
        html_path.read_text(encoding="utf-8").replace("Quality Panel", "Quality Panel Edited", 1),
        encoding="utf-8",
    )

    try:
        build_report_bundle_manifest(workspace=ws)
    except ReportBundleProjectionError as exc:
        assert "quality projection artifact invalid" in str(exc)
        assert "output/intermediate/quality_panel.html" in str(exc)
        assert "quality_panel_html_stale_or_hand_edited" in str(exc)
        assert "rerun briefloop quality summarize" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("Expected stale Quality Panel HTML rejection")


@requires_safe_bundle_read
def test_report_bundle_manifest_rejects_stale_quality_summary(tmp_path: Path) -> None:
    ws = _finalized_workspace(tmp_path)
    _write_quality_projection_artifacts(ws)
    summary_path = ws / "output" / "intermediate" / "quality_summary.md"
    summary_path.write_text(
        summary_path.read_text(encoding="utf-8").replace("read-only operator view", "edited operator view", 1),
        encoding="utf-8",
    )

    try:
        build_report_bundle_manifest(workspace=ws)
    except ReportBundleProjectionError as exc:
        assert "quality projection artifact invalid" in str(exc)
        assert "output/intermediate/quality_summary.md" in str(exc)
        assert "quality_summary_stale_or_hand_edited" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("Expected stale Quality Summary rejection")


@requires_safe_bundle_read
def test_report_bundle_manifest_rejects_modified_quality_panel_source(tmp_path: Path) -> None:
    ws = _finalized_workspace(tmp_path)
    _write_quality_projection_artifacts(ws)
    panel_path = ws / "output" / "intermediate" / "quality_panel.json"
    panel = json.loads(panel_path.read_text(encoding="utf-8"))
    panel["generated_at"] = "2099-01-01T00:00:00Z"
    panel_path.write_text(json.dumps(panel, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    try:
        build_report_bundle_manifest(workspace=ws)
    except ReportBundleProjectionError as exc:
        assert "quality projection artifact invalid" in str(exc)
        assert "output/intermediate/quality_summary.md" in str(exc)
        assert "quality_summary_stale_or_hand_edited" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("Expected modified Quality Panel source rejection")


@requires_safe_bundle_read
def test_report_bundle_manifest_excludes_packaging_junk(tmp_path: Path) -> None:
    ws = _finalized_workspace(tmp_path)
    delivery_junk = ws / "output" / "delivery" / ".DS_Store"
    delivery_junk.write_text("macOS metadata\n", encoding="utf-8")
    hidden = ws / "output" / "delivery" / ".hidden.md"
    hidden.write_text("hidden\n", encoding="utf-8")
    probe = (
        ws
        / "output"
        / "delivery"
        / ".briefloop-pub-probe-owned"
        / "member.md"
    )
    probe.parent.mkdir()
    probe.write_text("probe\n", encoding="utf-8")
    nested = ws / "output" / "delivery" / "nested"
    nested.mkdir()
    (nested / "briefloop.db").write_bytes(b"nested")
    nested_member = nested / "member.md"
    nested_member.write_text("nested\n", encoding="utf-8")
    trace_junk = ws / "output" / ".~lock.source_appendix_trace.md#"
    trace_junk.write_text("editor lock\n", encoding="utf-8")
    report_path = ws / "output" / "intermediate" / "finalize_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    for path in (delivery_junk, hidden, probe, nested_member):
        relative = path.relative_to(ws).as_posix()
        report["delivery_artifacts"].append(relative)
        report["delivery_artifact_sha256"][relative] = _sha256_file(path)
    report["source_appendix_trace"] = "output/.~lock.source_appendix_trace.md#"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    manifest = build_report_bundle_manifest(workspace=ws)

    delivery_paths = {item["path"] for item in manifest["delivery_bundle"]["artifacts"]}
    audit_paths = {item["path"] for item in manifest["audit_bundle"]["artifacts"]}
    excluded = {
        item["path"]: item["reason"]
        for item in manifest["packaging_hygiene"]["excluded_artifacts"]
    }
    assert manifest["packaging_hygiene"]["excluded_artifacts"] == sorted(
        manifest["packaging_hygiene"]["excluded_artifacts"],
        key=lambda item: (item["surface"], item["path"], item["reason"]),
    )
    assert "output/delivery/.DS_Store" not in delivery_paths
    assert "output/delivery/.hidden.md" not in delivery_paths
    assert (
        "output/delivery/.briefloop-pub-probe-owned/member.md"
        not in delivery_paths
    )
    assert "output/delivery/nested/member.md" not in delivery_paths
    assert "output/.~lock.source_appendix_trace.md#" not in audit_paths
    assert manifest["packaging_hygiene"]["status"] == "excluded_packaging_junk"
    assert excluded == {
        "output/.~lock.source_appendix_trace.md#": (
            "workspace_member_packaging_residue"
        ),
        "output/delivery/.DS_Store": "workspace_member_packaging_residue",
        "output/delivery/.briefloop-pub-probe-owned/member.md": (
            "workspace_member_publication_probe"
        ),
        "output/delivery/.hidden.md": "workspace_member_hidden",
        "output/delivery/nested/member.md": "workspace_member_nested_workspace",
    }


def test_shared_hygiene_excludes_probe_hidden_symlink_and_nested_workspace(
    tmp_path: Path,
) -> None:
    ws = _finalized_workspace(tmp_path)
    hidden = ws / "output" / "delivery" / ".hidden.md"
    hidden.write_text("hidden\n", encoding="utf-8")
    probe = ws / "output" / ".briefloop-pub-probe-owned" / "member"
    probe.parent.mkdir()
    probe.write_text("probe\n", encoding="utf-8")
    link = ws / "output" / "delivery" / "linked.md"
    link.symlink_to(ws / "output" / "delivery" / "brief.md")
    nested = ws / "output" / "delivery" / "nested"
    nested.mkdir()
    (nested / "briefloop.db").write_bytes(b"nested")
    nested_member = nested / "member.md"
    nested_member.write_text("nested\n", encoding="utf-8")
    residue = ws / "output" / "delivery" / "staging.tmp" / "brief.md"
    residue.parent.mkdir()
    residue.write_text("residue\n", encoding="utf-8")

    decisions = {
        path: classify_workspace_member(ws, path, surface="bundle")
        for path in (hidden, probe, link, nested_member, residue)
    }

    assert decisions[hidden].reason_code == "workspace_member_hidden"
    assert decisions[probe].reason_code == "workspace_member_publication_probe"
    assert decisions[link].reason_code == "workspace_member_symlink"
    assert decisions[nested_member].reason_code == "workspace_member_nested_workspace"
    assert decisions[residue].reason_code == "workspace_member_packaging_residue"
    assert all(item.status == "exclude" for item in decisions.values())


@requires_safe_bundle_read
@requires_safe_bundle_publication
def test_audit_bundle_never_reads_or_archives_symlink_target_bytes(
    tmp_path: Path,
) -> None:
    ws = _finalized_workspace(tmp_path)
    private = ws / "input" / "sources" / "private.txt"
    private.parent.mkdir(parents=True)
    private.write_text("PRIVATE-TARGET-BYTES\n", encoding="utf-8")
    semantic = ws / "output" / "intermediate" / "semantic_assessment_report.json"
    semantic.symlink_to(private)

    manifest = write_report_bundle_manifest(
        workspace=ws,
        write_archives=True,
    )

    excluded = manifest["packaging_hygiene"]["excluded_artifacts"]
    assert {
        (item["path"], item["reason"])
        for item in excluded
    } >= {
        (
            "output/intermediate/semantic_assessment_report.json",
            "workspace_member_symlink",
        )
    }
    with zipfile.ZipFile(ws / "output" / "audit_bundle.zip") as archive:
        names = archive.namelist()
        assert not any("semantic_assessment_report" in name for name in names)
        assert b"PRIVATE-TARGET-BYTES" not in b"".join(
            archive.read(name) for name in names
        )


@requires_safe_bundle_read
@requires_safe_bundle_publication
def test_audit_archive_rejects_member_swapped_after_manifest(
    tmp_path: Path,
) -> None:
    ws = _finalized_workspace(tmp_path)
    semantic = ws / "output" / "intermediate" / "semantic_assessment_report.json"
    semantic.write_text('{"status":"advisory"}\n', encoding="utf-8")
    manifest = build_report_bundle_manifest(workspace=ws)
    private = ws / "input" / "sources" / "private.txt"
    private.parent.mkdir(parents=True)
    private.write_text("PRIVATE-SWAPPED-BYTES\n", encoding="utf-8")
    semantic.unlink()
    semantic.symlink_to(private)

    with pytest.raises(
        ReportBundleProjectionError,
        match="bundle member is not hygienic",
    ):
        bundle_projection._write_bundle_archives(ws, manifest)

    archive = ws / "output" / "audit_bundle.zip"
    if archive.exists():
        with zipfile.ZipFile(archive) as handle:
            assert b"PRIVATE-SWAPPED-BYTES" not in b"".join(
                handle.read(name) for name in handle.namelist()
            )


@requires_safe_bundle_read
def test_report_bundle_manifest_preserves_utf8_paths_with_ascii_fallback(tmp_path: Path) -> None:
    ws = _finalized_workspace(tmp_path)
    localized = ws / "output" / "delivery" / "行业周报.md"
    localized.write_text("# 行业周报\n", encoding="utf-8")
    report_path = ws / "output" / "intermediate" / "finalize_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["delivery_artifacts"].append("output/delivery/行业周报.md")
    report["delivery_artifact_sha256"]["output/delivery/行业周报.md"] = _sha256_file(localized)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    manifest = build_report_bundle_manifest(workspace=ws)

    record = next(
        item
        for item in manifest["delivery_bundle"]["artifacts"]
        if item["path"] == "output/delivery/行业周报.md"
    )
    assert record["path"] == "output/delivery/行业周报.md"
    assert record["ascii_fallback_name"].startswith("artifact-")
    assert record["ascii_fallback_name"].endswith(".md")


@requires_safe_bundle_read
def test_report_bundle_ascii_fallback_names_do_not_collide(tmp_path: Path) -> None:
    ws = _finalized_workspace(tmp_path)
    report_path = ws / "output" / "intermediate" / "finalize_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    paths = [
        "output/delivery/行业周报-v1.md",
        "output/delivery/市场周报-v1.md",
    ]
    for rel in paths:
        path = ws / rel
        path.write_text(f"# {path.stem}\n", encoding="utf-8")
        report["delivery_artifacts"].append(rel)
        report["delivery_artifact_sha256"][rel] = _sha256_file(path)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    manifest = build_report_bundle_manifest(workspace=ws)

    fallback_names = [
        item["ascii_fallback_name"]
        for item in manifest["delivery_bundle"]["artifacts"]
        if item["path"] in paths
    ]
    assert len(fallback_names) == 2
    assert len(set(fallback_names)) == 2
    assert all(name.startswith("v1-") and name.endswith(".md") for name in fallback_names)


@requires_safe_bundle_read
def test_report_bundle_manifest_rejects_stale_delivery_hash(tmp_path: Path) -> None:
    ws = _finalized_workspace(tmp_path)
    (ws / "output" / "delivery" / "brief.md").write_text("changed\n", encoding="utf-8")

    try:
        build_report_bundle_manifest(workspace=ws)
    except ReportBundleProjectionError as exc:
        assert "delivery artifact hash mismatch" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("Expected stale delivery hash rejection")


@requires_safe_bundle_read
def test_report_bundle_manifest_requires_passing_finalize_audit_binding(tmp_path: Path) -> None:
    ws = _finalized_workspace(tmp_path)
    (ws / "output" / "intermediate" / "audited_brief.md").write_text(
        "# Tampered\n\nChanged after finalize.\n",
        encoding="utf-8",
    )

    try:
        build_report_bundle_manifest(workspace=ws)
    except ReportBundleProjectionError as exc:
        assert "audit_binding must pass" in str(exc)
        assert (
            "audit_binding.audited_brief_sha256 does not match current artifact bytes"
            in str(exc)
        )
    else:  # pragma: no cover
        raise AssertionError("Expected stale audit binding rejection")


@requires_safe_bundle_read
def test_report_bundle_manifest_requires_audited_brief_binding_target(tmp_path: Path) -> None:
    ws = _finalized_workspace(tmp_path)
    (ws / "output" / "intermediate" / "audited_brief.md").unlink()

    try:
        build_report_bundle_manifest(workspace=ws)
    except ReportBundleProjectionError as exc:
        assert "audit_binding must pass" in str(exc)
        assert "audit_binding.audited_brief_sha256 target is missing" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("Expected missing audited brief binding rejection")


@requires_safe_bundle_read
def test_report_bundle_manifest_rejects_missing_delivery_hash_map(tmp_path: Path) -> None:
    ws = _finalized_workspace(tmp_path)
    report_path = ws / "output" / "intermediate" / "finalize_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report.pop("delivery_artifact_sha256")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    try:
        build_report_bundle_manifest(workspace=ws)
    except ReportBundleProjectionError as exc:
        assert "delivery_artifact_sha256 must be a non-empty object" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("Expected missing delivery hash map rejection")


@requires_safe_bundle_read
def test_report_bundle_manifest_rejects_missing_per_artifact_hash(tmp_path: Path) -> None:
    ws = _finalized_workspace(tmp_path)
    report_path = ws / "output" / "intermediate" / "finalize_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["delivery_artifact_sha256"] = {"output/delivery/other.md": "a" * 64}
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    try:
        build_report_bundle_manifest(workspace=ws)
    except ReportBundleProjectionError as exc:
        assert "delivery artifact hash missing: output/delivery/brief.md" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("Expected missing per-artifact hash rejection")


@requires_safe_bundle_read
@requires_safe_bundle_publication
def test_packs_bundle_cli_writes_manifest_without_copying_trace_to_delivery(
    tmp_path: Path,
) -> None:
    ws = _finalized_workspace(tmp_path)

    # retired public `packs bundle` CLI; the manifest invariant
    # runs through the direct deterministic bundle-projection seam.
    payload = write_report_bundle_manifest(workspace=ws)

    manifest_path = ws / payload["manifest_path"]
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["delivery_bundle"]["artifact_count"] == 1
    assert manifest["audit_bundle"]["artifact_count"] >= 6
    assert not (ws / "output" / "delivery" / "source_appendix_trace.md").exists()
    assert not (ws / "output" / "delivery_bundle.zip").exists()
    assert not (ws / "output" / "audit_bundle.zip").exists()
    assert manifest["non_goals"] == [
        "delivery_approval",
        "gate_bypass",
        "publication_authorization",
        "semantic_support_assessment",
    ]


@requires_safe_bundle_read
@requires_safe_bundle_publication
def test_packs_bundle_cli_writes_clean_archives_from_manifest(
    tmp_path: Path,
) -> None:
    ws = _finalized_workspace(tmp_path)
    _write_quality_projection_artifacts(ws)
    delivery_readme_artifact = ws / "output" / "delivery" / "README.md"
    delivery_readme_artifact.write_text(
        "# Reader Delivery Notes\n\nThis is a reader-facing delivery artifact.\n",
        encoding="utf-8",
    )
    report_path = ws / "output" / "intermediate" / "finalize_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["delivery_artifacts"].append("output/delivery/README.md")
    report["delivery_artifact_sha256"]["output/delivery/README.md"] = _sha256_file(
        delivery_readme_artifact
    )
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (ws / "output" / "delivery" / ".DS_Store").write_text("macOS junk\n", encoding="utf-8")
    legacy_zip = ws / "output" / "output.zip"
    with zipfile.ZipFile(legacy_zip, "w") as zf:
        zf.writestr("__MACOSX/._brief.md", "junk")
        zf.writestr("output/delivery/.DS_Store", "junk")

    # retired public `packs bundle --write-archives` CLI; the
    # archive invariant runs through the direct deterministic projection seam.
    payload = write_report_bundle_manifest(workspace=ws, write_archives=True)

    manifest_path = ws / payload["manifest_path"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    archives = manifest["bundle_archives"]
    assert archives["status"] == "generated"
    assert manifest["supplemental_guidance"] == {
        "status": "available_when_archives_are_written",
        "semantics": "supplemental_guidance_non_authoritative_not_counted_as_artifacts",
        "artifact_count_policy": "excluded_from_delivery_bundle_and_audit_bundle_artifact_count",
        "delivery_archive_member": "delivery/_BUNDLE_README.md",
        "audit_archive_member": "audit/_BUNDLE_README.md",
    }
    assert manifest["delivery_bundle"]["artifact_count"] == len(
        manifest["delivery_bundle"]["artifacts"]
    )
    assert manifest["audit_bundle"]["artifact_count"] == len(manifest["audit_bundle"]["artifacts"])
    delivery_zip = ws / archives["delivery"]["path"]
    audit_zip = ws / archives["audit"]["path"]
    assert delivery_zip.exists()
    assert audit_zip.exists()
    assert archives["delivery"]["sha256"] == _sha256_file(delivery_zip)
    assert archives["audit"]["sha256"] == _sha256_file(audit_zip)
    first_delivery_sha = archives["delivery"]["sha256"]
    first_audit_sha = archives["audit"]["sha256"]

    with zipfile.ZipFile(delivery_zip) as zf:
        delivery_member_names = zf.namelist()
        delivery_names = set(delivery_member_names)
        delivery_readme = zf.read("delivery/_BUNDLE_README.md").decode("utf-8")
        reader_readme = zf.read("delivery/README.md").decode("utf-8")
    with zipfile.ZipFile(audit_zip) as zf:
        audit_member_names = zf.namelist()
        audit_names = set(audit_member_names)
        audit_readme = zf.read("audit/_BUNDLE_README.md").decode("utf-8")

    assert delivery_member_names.count("delivery/_BUNDLE_README.md") == 1
    assert delivery_member_names.count("delivery/README.md") == 1
    assert "delivery/_BUNDLE_README.md" in delivery_names
    assert "delivery/brief.md" in delivery_names
    assert "delivery/README.md" in delivery_names
    assert "reader-facing delivery artifact" in reader_readme
    assert "reader-facing report" in delivery_readme
    assert "Audit/control artifacts are intentionally excluded" in delivery_readme
    assert "does not prove semantic truth" in delivery_readme
    assert "approve publication" in delivery_readme
    assert audit_member_names.count("audit/_BUNDLE_README.md") == 1
    assert "audit/_BUNDLE_README.md" in audit_names
    assert "audit/output/intermediate/finalize_report.json" in audit_names
    assert "audit/output/intermediate/audited_brief.md" in audit_names
    assert "audit/output/intermediate/quality_panel.json" in audit_names
    assert "audit/output/intermediate/quality_summary.md" in audit_names
    assert "audit/output/intermediate/quality_panel.html" in audit_names
    assert "quality_summary.md" in audit_readme
    assert "claim_ledger.json" in audit_readme
    assert "not reader delivery" in audit_readme
    assert "release authority" in audit_readme
    assert not any("quality_panel" in name for name in delivery_names)
    all_names = delivery_names | audit_names
    assert not any("__MACOSX" in name for name in all_names)
    assert not any(name.endswith(".DS_Store") for name in all_names)
    assert not any(name.endswith("output.zip") for name in all_names)

    rerun_payload = write_report_bundle_manifest(workspace=ws, write_archives=True)
    rerun_manifest = json.loads((ws / rerun_payload["manifest_path"]).read_text(encoding="utf-8"))
    assert rerun_manifest["bundle_archives"]["delivery"]["sha256"] == first_delivery_sha
    assert rerun_manifest["bundle_archives"]["audit"]["sha256"] == first_audit_sha


@pytest.mark.parametrize(
    "relative_target",
    (
        "output/delivery_bundle.zip",
        "output/audit_bundle.zip",
        "output/report_bundle_manifest.json",
    ),
)
@requires_safe_bundle_read
@requires_safe_bundle_publication
def test_bundle_projection_preserves_symlinked_final_targets(
    tmp_path: Path,
    relative_target: str,
) -> None:
    ws = _finalized_workspace(tmp_path)
    outside = tmp_path / f"outside-{Path(relative_target).name}"
    outside.write_bytes(b"external sentinel")
    target = ws / relative_target
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        target.symlink_to(outside)
    except OSError:
        pytest.skip("file symlinks unavailable")

    with pytest.raises(
        ReportBundleProjectionError,
        match="not a replaceable regular file",
    ):
        write_report_bundle_manifest(workspace=ws, write_archives=True)

    assert target.is_symlink()
    assert outside.read_bytes() == b"external sentinel"
    assert not list((ws / "output").glob(".briefloop-bundle-*.tmp"))


@requires_safe_bundle_read
@requires_safe_bundle_publication
def test_bundle_projection_rejects_replaced_output_parent_without_writing_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = _finalized_workspace(tmp_path)
    original_stage = bundle_projection._stage_zip_projection
    calls = 0

    def stage_and_replace_parent(**kwargs):
        nonlocal calls
        staged = original_stage(**kwargs)
        calls += 1
        if calls == 1:
            (ws / "output").rename(ws / "owned-output")
            (ws / "output").mkdir()
        return staged

    monkeypatch.setattr(
        bundle_projection,
        "_stage_zip_projection",
        stage_and_replace_parent,
    )

    with pytest.raises(ReportBundleProjectionError):
        write_report_bundle_manifest(workspace=ws, write_archives=True)

    assert list((ws / "output").iterdir()) == []
    assert not list((ws / "owned-output").glob(".briefloop-bundle-*.tmp"))


@requires_safe_bundle_read
@requires_safe_bundle_publication
def test_bundle_projection_rejects_symlinked_manifest_parent_without_external_write(
    tmp_path: Path,
) -> None:
    ws = _finalized_workspace(tmp_path)
    outside = tmp_path / "outside-parent"
    outside.mkdir()
    alias = ws / "projection-alias"
    try:
        alias.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks unavailable")

    with pytest.raises(
        ReportBundleProjectionError,
        match="parent is unavailable",
    ):
        write_report_bundle_manifest(
            workspace=ws,
            output_path="projection-alias/manifest.json",
        )

    assert list(outside.iterdir()) == []


@requires_safe_bundle_read
@requires_safe_bundle_publication
def test_bundle_projection_preserves_final_leaf_that_appears_after_preflight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = _finalized_workspace(tmp_path)
    target = ws / "output" / "delivery_bundle.zip"
    original_stage = bundle_projection._stage_zip_projection
    calls = 0

    def stage_and_add_unknown_leaf(**kwargs):
        nonlocal calls
        staged = original_stage(**kwargs)
        calls += 1
        if calls == 1:
            target.write_bytes(b"unknown replacement")
        return staged

    monkeypatch.setattr(
        bundle_projection,
        "_stage_zip_projection",
        stage_and_add_unknown_leaf,
    )

    with pytest.raises(
        ReportBundleProjectionError,
        match="target changed",
    ):
        write_report_bundle_manifest(workspace=ws, write_archives=True)

    assert target.read_bytes() == b"unknown replacement"
    assert not (ws / "output" / "audit_bundle.zip").exists()
    assert not (ws / "output" / "report_bundle_manifest.json").exists()
    assert not list((ws / "output").glob(".briefloop-bundle-*.tmp"))


@requires_safe_bundle_read
@requires_safe_bundle_publication
def test_bundle_projection_preserves_final_leaf_replaced_after_preflight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = _finalized_workspace(tmp_path)
    target = ws / "output" / "delivery_bundle.zip"
    target.write_bytes(b"previous projection")
    original_stage = bundle_projection._stage_zip_projection
    calls = 0

    def stage_and_replace_leaf(**kwargs):
        nonlocal calls
        staged = original_stage(**kwargs)
        calls += 1
        if calls == 1:
            replacement = ws / "output" / "replacement.zip"
            replacement.write_bytes(b"unknown replacement")
            replacement.replace(target)
        return staged

    monkeypatch.setattr(
        bundle_projection,
        "_stage_zip_projection",
        stage_and_replace_leaf,
    )

    with pytest.raises(
        ReportBundleProjectionError,
        match="target changed",
    ):
        write_report_bundle_manifest(workspace=ws, write_archives=True)

    assert target.read_bytes() == b"unknown replacement"
    assert not list((ws / "output").glob(".briefloop-bundle-*.tmp"))


@requires_safe_bundle_read
@requires_safe_bundle_publication
def test_bundle_projection_preserves_manifest_leaf_that_appears_after_preflight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = _finalized_workspace(tmp_path)
    target = ws / "output" / "report_bundle_manifest.json"
    original_stage = bundle_projection._stage_bytes_projection

    def stage_and_add_unknown_leaf(**kwargs):
        staged = original_stage(**kwargs)
        target.write_bytes(b"unknown manifest")
        return staged

    monkeypatch.setattr(
        bundle_projection,
        "_stage_bytes_projection",
        stage_and_add_unknown_leaf,
    )

    with pytest.raises(
        ReportBundleProjectionError,
        match="target changed",
    ):
        write_report_bundle_manifest(workspace=ws, write_archives=True)

    assert target.read_bytes() == b"unknown manifest"
    assert not list((ws / "output").glob(".briefloop-bundle-*.tmp"))


def test_bundle_publication_uses_the_shared_platform_classifier() -> None:
    assert (
        bundle_projection._supports_safe_bundle_publication
        is projection_platform.supports_retained_directory_publication
    )


@pytest.mark.parametrize("function_name", ("open", "stat", "mkdir", "unlink"))
def test_shared_publication_capability_requires_every_retained_relative_primitive(
    monkeypatch: pytest.MonkeyPatch,
    function_name: str,
) -> None:
    missing = getattr(projection_platform.os, function_name)
    monkeypatch.setattr(
        projection_platform.os,
        "supports_dir_fd",
        projection_platform.os.supports_dir_fd - {missing},
    )

    assert projection_platform.supports_retained_directory_publication() is False
    assert bundle_projection._supports_safe_bundle_publication() is False


def test_shared_publication_capability_requires_no_follow_observation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        projection_platform.os,
        "supports_follow_symlinks",
        projection_platform.os.supports_follow_symlinks
        - {projection_platform.os.stat},
    )

    assert projection_platform.supports_retained_directory_publication() is False
    assert bundle_projection._supports_safe_bundle_publication() is False


@pytest.mark.parametrize("flag_name", ("O_DIRECTORY", "O_NOFOLLOW"))
def test_shared_publication_capability_requires_no_follow_directory_open(
    monkeypatch: pytest.MonkeyPatch,
    flag_name: str,
) -> None:
    monkeypatch.setattr(projection_platform.os, flag_name, 0, raising=False)

    assert projection_platform.supports_retained_directory_publication() is False
    assert bundle_projection._supports_safe_bundle_publication() is False


@pytest.mark.parametrize("function_name", ("open", "stat"))
def test_bundle_read_capability_requires_every_retained_relative_primitive(
    monkeypatch: pytest.MonkeyPatch,
    function_name: str,
) -> None:
    missing = getattr(bundle_projection.os, function_name)
    monkeypatch.setattr(
        bundle_projection.os,
        "supports_dir_fd",
        bundle_projection.os.supports_dir_fd - {missing},
    )

    assert bundle_projection._supports_safe_bundle_read() is False


def test_bundle_read_capability_requires_no_follow_observation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        bundle_projection.os,
        "supports_follow_symlinks",
        bundle_projection.os.supports_follow_symlinks - {bundle_projection.os.stat},
    )

    assert bundle_projection._supports_safe_bundle_read() is False


def test_shared_publication_capability_requires_relative_replace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def path_only_replace(source: str, target: str) -> None:
        del source, target

    monkeypatch.setattr(projection_platform.os, "replace", path_only_replace)

    assert projection_platform.supports_retained_directory_publication() is False
    assert bundle_projection._supports_safe_bundle_publication() is False


def test_bundle_publication_unsupported_is_zero_write_and_preserves_identities(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = _finalized_workspace(tmp_path)
    output = ws / "output"
    sentinels = {
        output / "report_bundle_manifest.json": b"existing manifest",
        output / "delivery_bundle.zip": b"existing delivery",
        output / "audit_bundle.zip": b"existing audit",
        output / ".briefloop-bundle-existing.tmp": b"existing temp-like file",
    }
    for path, payload in sentinels.items():
        path.write_bytes(payload)
    before = {
        path: (path.read_bytes(), path.lstat().st_dev, path.lstat().st_ino)
        for path in sentinels
    }
    before_names = sorted(path.relative_to(ws).as_posix() for path in ws.rglob("*"))
    monkeypatch.setattr(
        bundle_projection,
        "_supports_safe_bundle_read",
        lambda: True,
    )
    monkeypatch.setattr(
        bundle_projection,
        "_supports_safe_bundle_publication",
        lambda: False,
    )

    with pytest.raises(
        ReportBundleProjectionError,
        match="^bundle_projection_publication_unsupported$",
    ):
        write_report_bundle_manifest(workspace=ws, write_archives=True)

    assert {
        path: (path.read_bytes(), path.lstat().st_dev, path.lstat().st_ino)
        for path in sentinels
    } == before
    assert sorted(path.relative_to(ws).as_posix() for path in ws.rglob("*")) == before_names


def test_bundle_publication_unsupported_does_not_create_custom_parent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = _finalized_workspace(tmp_path)
    target = ws / "projection" / "report_bundle_manifest.json"
    monkeypatch.setattr(
        bundle_projection,
        "_supports_safe_bundle_read",
        lambda: True,
    )
    monkeypatch.setattr(
        bundle_projection,
        "_supports_safe_bundle_publication",
        lambda: False,
    )

    with pytest.raises(
        ReportBundleProjectionError,
        match="^bundle_projection_publication_unsupported$",
    ):
        write_report_bundle_manifest(workspace=ws, output_path=target)

    assert not target.parent.exists()


def test_bundle_read_unsupported_precedes_member_observation_and_all_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = _finalized_workspace(tmp_path)
    before = {
        path.relative_to(ws).as_posix(): (
            path.read_bytes(),
            path.lstat().st_dev,
            path.lstat().st_ino,
        )
        for path in ws.rglob("*")
        if path.is_file()
    }
    before_names = sorted(path.relative_to(ws).as_posix() for path in ws.rglob("*"))
    monkeypatch.setattr(
        bundle_projection,
        "_supports_safe_bundle_read",
        lambda: False,
    )

    def forbidden_member_read(*args, **kwargs):
        del args, kwargs
        raise AssertionError("member reader must not run")

    monkeypatch.setattr(
        bundle_projection,
        "_read_verified_workspace_member",
        forbidden_member_read,
    )

    with pytest.raises(
        ReportBundleProjectionError,
        match="^bundle_projection_read_unsupported$",
    ):
        build_report_bundle_manifest(workspace=ws)
    with pytest.raises(
        ReportBundleProjectionError,
        match="^bundle_projection_read_unsupported$",
    ):
        write_report_bundle_manifest(workspace=ws, write_archives=True)

    assert {
        path.relative_to(ws).as_posix(): (
            path.read_bytes(),
            path.lstat().st_dev,
            path.lstat().st_ino,
        )
        for path in ws.rglob("*")
        if path.is_file()
    } == before
    assert sorted(path.relative_to(ws).as_posix() for path in ws.rglob("*")) == before_names


def test_bundle_member_reader_never_leaks_not_implemented(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = _finalized_workspace(tmp_path)
    monkeypatch.setattr(
        bundle_projection,
        "_supports_safe_bundle_read",
        lambda: True,
    )

    def unsupported_open(*args, **kwargs):
        del args, kwargs
        raise NotImplementedError("dir_fd unavailable")

    monkeypatch.setattr(bundle_projection.os, "open", unsupported_open)

    with pytest.raises(
        ReportBundleProjectionError,
        match="^workspace member is unreadable or changed:",
    ):
        bundle_projection._read_verified_workspace_member(
            ws,
            "output/intermediate/finalize_report.json",
            surface="bundle",
        )


@requires_safe_bundle_read
def test_bundle_manifest_pure_build_remains_available_when_publication_is_unsupported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = _finalized_workspace(tmp_path)
    before = {
        path.relative_to(ws).as_posix(): (
            path.read_bytes(),
            path.lstat().st_dev,
            path.lstat().st_ino,
        )
        for path in ws.rglob("*")
        if path.is_file()
    }
    monkeypatch.setattr(
        bundle_projection,
        "_supports_safe_bundle_publication",
        lambda: False,
    )

    manifest = build_report_bundle_manifest(workspace=ws)

    assert manifest["schema_version"] == "briefloop.report_bundle_manifest.v1"
    assert {
        path.relative_to(ws).as_posix(): (
            path.read_bytes(),
            path.lstat().st_dev,
            path.lstat().st_ino,
        )
        for path in ws.rglob("*")
        if path.is_file()
    } == before


@requires_safe_bundle_read
def test_packs_bundle_rejects_manifest_output_reserved_for_archives(
    tmp_path: Path,
) -> None:
    ws = _finalized_workspace(tmp_path)

    for rel in ("output/delivery_bundle.zip", "output/audit_bundle.zip"):
        for maybe_write_archives in (False, True):
            archive_path = ws / rel
            archive_path.parent.mkdir(parents=True, exist_ok=True)
            archive_path.write_bytes(b"existing zip bytes")

            # retired public `packs bundle --output` CLI; the
            # reserved-output rejection runs through the direct projection seam.
            with pytest.raises(ReportBundleProjectionError, match="reserved for clean bundle archives"):
                write_report_bundle_manifest(
                    workspace=ws,
                    output_path=rel,
                    write_archives=maybe_write_archives,
                )
            assert archive_path.read_bytes() == b"existing zip bytes"
            archive_path.unlink()


@requires_safe_bundle_read
def test_packs_bundle_rejects_outside_output_before_writing_archives(
    tmp_path: Path,
) -> None:
    ws = _finalized_workspace(tmp_path)
    outside = tmp_path / "outside.json"

    # retired public `packs bundle --output` CLI; the
    # outside-workspace fail-closed ordering runs through the direct seam.
    with pytest.raises(ReportBundleProjectionError, match="must stay inside the workspace"):
        write_report_bundle_manifest(workspace=ws, output_path=outside, write_archives=True)

    assert not outside.exists()
    assert not (ws / "output" / "delivery_bundle.zip").exists()
    assert not (ws / "output" / "audit_bundle.zip").exists()


@requires_safe_bundle_read
def test_report_bundle_manifest_output_must_stay_in_workspace(tmp_path: Path) -> None:
    ws = _finalized_workspace(tmp_path)

    try:
        write_report_bundle_manifest(workspace=ws, output_path=tmp_path / "outside.json")
    except ReportBundleProjectionError as exc:
        assert "must stay inside the workspace" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("Expected outside manifest output rejection")


def test_packs_bundle_public_cli_is_retired_with_zero_writes(tmp_path: Path, capsys) -> None:
    ws = _finalized_workspace(tmp_path)
    variants = [
        ["packs", "bundle", "--workspace", str(ws), "--json"],
        ["packs", "bundle", "--workspace", str(ws), "--write-archives", "--json"],
        ["packs", "bundle", "--workspace", str(ws), "--output", "output/custom_manifest.json", "--json"],
        ["packs", "bundle", "--workspace", str(ws), "--output", str(tmp_path / "outside.json"), "--write-archives", "--json"],
    ]
    for args in variants:
        before = {
            path.relative_to(ws).as_posix(): path.read_bytes()
            for path in ws.rglob("*")
            if path.is_file()
        }

        rc = main(args)
        out = capsys.readouterr().out

        # retired public `packs bundle` command and its typed
        # legacy-workspace rejection with zero writes.
        assert rc == 1
        assert out == "legacy_workspace_unsupported\n"
        after = {
            path.relative_to(ws).as_posix(): path.read_bytes()
            for path in ws.rglob("*")
            if path.is_file()
        }
        assert after == before
    assert not (tmp_path / "outside.json").exists()


def test_public_docs_keep_report_bundle_internal_and_command_retired() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    readme_zh = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")
    support = (ROOT / "docs" / "support-matrix.md").read_text(encoding="utf-8")

    assert "experimental delivery / audit bundle data projection" not in readme
    assert "实验性的 delivery / audit bundle 数据投影" not in readme_zh
    assert "write a bundle manifest with `packs bundle`" not in support
    assert (
        "| Public `briefloop packs bundle` command | Unsupported/retired on "
        "SQLite workspaces;"
    ) in support
    assert "| Internal deterministic ReportBundle seam | Experimental/internal" in support
    assert "`packs bundle` delivery/audit data projection" not in support


def test_non_editable_wheel_matches_internal_bundle_read_boundary(
    tmp_path: Path,
) -> None:
    workspace = _finalized_workspace(tmp_path)
    wheel_dir = tmp_path / "wheel"
    wheel_dir.mkdir()
    build = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            ".",
            "--no-deps",
            "--no-build-isolation",
            "--wheel-dir",
            str(wheel_dir),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert build.returncode == 0, build.stdout + build.stderr
    wheel_path = next(wheel_dir.glob("briefloop-*.whl"))
    installed = tmp_path / "installed"
    installed.mkdir()
    with zipfile.ZipFile(wheel_path) as archive:
        archive.extractall(installed)

    script = textwrap.dedent(
        """
        import json
        from pathlib import Path
        import sys

        import multi_agent_brief
        from multi_agent_brief.product import bundle_projection
        from multi_agent_brief.product.bundle_projection import (
            ReportBundleProjectionError,
            build_report_bundle_manifest,
        )

        workspace = Path(sys.argv[1])
        installed = Path(sys.argv[2]).resolve()
        assert Path(multi_agent_brief.__file__).resolve().is_relative_to(installed)
        before = sorted(path.relative_to(workspace).as_posix() for path in workspace.rglob("*"))
        if bundle_projection._supports_safe_bundle_read():
            manifest = build_report_bundle_manifest(workspace=workspace)
            result = {
                "status": "available",
                "schema_version": manifest["schema_version"],
            }
        else:
            try:
                build_report_bundle_manifest(workspace=workspace)
            except ReportBundleProjectionError as exc:
                assert str(exc) == "bundle_projection_read_unsupported"
            else:
                raise AssertionError("missing safe reads must fail closed")
            result = {
                "status": "unsupported",
                "reason_code": "bundle_projection_read_unsupported",
            }
        after = sorted(path.relative_to(workspace).as_posix() for path in workspace.rglob("*"))
        assert after == before
        print(json.dumps(result, sort_keys=True))
        """
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(installed)
    script_path = tmp_path / "wheel_bundle_boundary.py"
    script_path.write_text(script, encoding="utf-8")
    run = subprocess.run(
        [sys.executable, str(script_path), str(workspace), str(installed)],
        cwd=tmp_path,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    payload = json.loads(run.stdout)
    if bundle_projection._supports_safe_bundle_read():
        assert payload == {
            "schema_version": "briefloop.report_bundle_manifest.v1",
            "status": "available",
        }
    else:
        assert payload == {
            "reason_code": "bundle_projection_read_unsupported",
            "status": "unsupported",
        }


def test_packs_templates_cli_lists_packaged_templates(capsys) -> None:
    assert main(["packs", "templates", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["ok"] is True
    assert {item["template_id"] for item in payload["templates"]} == EXPECTED_TEMPLATE_IDS
