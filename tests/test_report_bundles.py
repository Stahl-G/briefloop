"""Tests for experimental product-layer bundle projections."""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import yaml

from multi_agent_brief.cli.main import main
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

ROOT = Path(__file__).resolve().parent.parent
EXPECTED_TEMPLATE_IDS = {
    "evidence_extract",
    "market_weekly",
    "management_monthly",
    "solar_industry_periodic",
}


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


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
        solar = registry.get_by_report_type("solar_industry_periodic")
        assert solar is not None
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
        assert extract.section_order == (
            "scope",
            "extracted_points",
            "source_trace",
            "gaps_and_flags",
            "source_appendix",
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


def test_report_bundle_manifest_splits_delivery_and_audit_artifacts(tmp_path: Path) -> None:
    ws = _finalized_workspace(tmp_path)

    manifest = build_report_bundle_manifest(workspace=ws)

    assert manifest["template"]["template_id"] == "market_weekly"
    assert manifest["template"]["section_order"][0] == "executive_summary"
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


def test_report_bundle_manifest_excludes_packaging_junk(tmp_path: Path) -> None:
    ws = _finalized_workspace(tmp_path)
    delivery_junk = ws / "output" / "delivery" / ".DS_Store"
    delivery_junk.write_text("macOS metadata\n", encoding="utf-8")
    trace_junk = ws / "output" / ".~lock.source_appendix_trace.md#"
    trace_junk.write_text("editor lock\n", encoding="utf-8")
    report_path = ws / "output" / "intermediate" / "finalize_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["delivery_artifacts"].append("output/delivery/.DS_Store")
    report["delivery_artifact_sha256"]["output/delivery/.DS_Store"] = _sha256_file(delivery_junk)
    report["source_appendix_trace"] = "output/.~lock.source_appendix_trace.md#"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    manifest = build_report_bundle_manifest(workspace=ws)

    delivery_paths = {item["path"] for item in manifest["delivery_bundle"]["artifacts"]}
    audit_paths = {item["path"] for item in manifest["audit_bundle"]["artifacts"]}
    excluded_paths = {
        item["path"]
        for item in manifest["packaging_hygiene"]["excluded_artifacts"]
    }
    assert "output/delivery/.DS_Store" not in delivery_paths
    assert "output/.~lock.source_appendix_trace.md#" not in audit_paths
    assert manifest["packaging_hygiene"]["status"] == "excluded_packaging_junk"
    assert excluded_paths == {
        "output/delivery/.DS_Store",
        "output/.~lock.source_appendix_trace.md#",
    }


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


def test_report_bundle_manifest_rejects_stale_delivery_hash(tmp_path: Path) -> None:
    ws = _finalized_workspace(tmp_path)
    (ws / "output" / "delivery" / "brief.md").write_text("changed\n", encoding="utf-8")

    try:
        build_report_bundle_manifest(workspace=ws)
    except ReportBundleProjectionError as exc:
        assert "delivery artifact hash mismatch" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("Expected stale delivery hash rejection")


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


def test_packs_bundle_cli_writes_manifest_without_copying_trace_to_delivery(
    tmp_path: Path,
    capsys,
) -> None:
    ws = _finalized_workspace(tmp_path)

    assert main(["packs", "bundle", "--workspace", str(ws), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

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


def test_packs_bundle_cli_writes_clean_archives_from_manifest(
    tmp_path: Path,
    capsys,
) -> None:
    ws = _finalized_workspace(tmp_path)
    _write_quality_projection_artifacts(ws)
    (ws / "output" / "delivery" / ".DS_Store").write_text("macOS junk\n", encoding="utf-8")
    legacy_zip = ws / "output" / "output.zip"
    with zipfile.ZipFile(legacy_zip, "w") as zf:
        zf.writestr("__MACOSX/._brief.md", "junk")
        zf.writestr("output/delivery/.DS_Store", "junk")

    assert main(["packs", "bundle", "--workspace", str(ws), "--write-archives", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    manifest_path = ws / payload["manifest_path"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    archives = manifest["bundle_archives"]
    assert archives["status"] == "generated"
    delivery_zip = ws / archives["delivery"]["path"]
    audit_zip = ws / archives["audit"]["path"]
    assert delivery_zip.exists()
    assert audit_zip.exists()
    assert archives["delivery"]["sha256"] == _sha256_file(delivery_zip)
    assert archives["audit"]["sha256"] == _sha256_file(audit_zip)
    first_delivery_sha = archives["delivery"]["sha256"]
    first_audit_sha = archives["audit"]["sha256"]

    with zipfile.ZipFile(delivery_zip) as zf:
        delivery_names = set(zf.namelist())
    with zipfile.ZipFile(audit_zip) as zf:
        audit_names = set(zf.namelist())

    assert "delivery/brief.md" in delivery_names
    assert "audit/output/intermediate/finalize_report.json" in audit_names
    assert "audit/output/intermediate/audited_brief.md" in audit_names
    assert "audit/output/intermediate/quality_panel.json" in audit_names
    assert "audit/output/intermediate/quality_summary.md" in audit_names
    assert "audit/output/intermediate/quality_panel.html" in audit_names
    assert not any("quality_panel" in name for name in delivery_names)
    all_names = delivery_names | audit_names
    assert not any("__MACOSX" in name for name in all_names)
    assert not any(name.endswith(".DS_Store") for name in all_names)
    assert not any(name.endswith("output.zip") for name in all_names)

    assert main(["packs", "bundle", "--workspace", str(ws), "--write-archives", "--json"]) == 0
    rerun_payload = json.loads(capsys.readouterr().out)
    rerun_manifest = json.loads((ws / rerun_payload["manifest_path"]).read_text(encoding="utf-8"))
    assert rerun_manifest["bundle_archives"]["delivery"]["sha256"] == first_delivery_sha
    assert rerun_manifest["bundle_archives"]["audit"]["sha256"] == first_audit_sha


def test_packs_bundle_rejects_manifest_output_reserved_for_archives(
    tmp_path: Path,
    capsys,
) -> None:
    ws = _finalized_workspace(tmp_path)

    for rel in ("output/delivery_bundle.zip", "output/audit_bundle.zip"):
        for maybe_write_archives in (False, True):
            archive_path = ws / rel
            archive_path.parent.mkdir(parents=True, exist_ok=True)
            archive_path.write_bytes(b"existing zip bytes")
            args = [
                "packs",
                "bundle",
                "--workspace",
                str(ws),
                "--output",
                rel,
                "--json",
            ]
            if maybe_write_archives:
                args.insert(4, "--write-archives")

            assert main(args) == 1
            payload = json.loads(capsys.readouterr().out)
            assert payload["ok"] is False
            assert "reserved for clean bundle archives" in payload["error"]
            assert archive_path.read_bytes() == b"existing zip bytes"
            archive_path.unlink()


def test_packs_bundle_rejects_outside_output_before_writing_archives(
    tmp_path: Path,
    capsys,
) -> None:
    ws = _finalized_workspace(tmp_path)
    outside = tmp_path / "outside.json"

    assert main([
        "packs",
        "bundle",
        "--workspace",
        str(ws),
        "--write-archives",
        "--output",
        str(outside),
        "--json",
    ]) == 1
    payload = json.loads(capsys.readouterr().out)

    assert payload["ok"] is False
    assert "must stay inside the workspace" in payload["error"]
    assert not outside.exists()
    assert not (ws / "output" / "delivery_bundle.zip").exists()
    assert not (ws / "output" / "audit_bundle.zip").exists()


def test_report_bundle_manifest_output_must_stay_in_workspace(tmp_path: Path) -> None:
    ws = _finalized_workspace(tmp_path)

    try:
        write_report_bundle_manifest(workspace=ws, output_path=tmp_path / "outside.json")
    except ReportBundleProjectionError as exc:
        assert "must stay inside the workspace" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("Expected outside manifest output rejection")


def test_packs_templates_cli_lists_packaged_templates(capsys) -> None:
    assert main(["packs", "templates", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["ok"] is True
    assert {item["template_id"] for item in payload["templates"]} == EXPECTED_TEMPLATE_IDS
