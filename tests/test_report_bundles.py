"""Tests for experimental product-layer bundle projections."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import textwrap
import zipfile
from pathlib import Path

import pytest
import yaml

from multi_agent_brief.product import bundle_projection
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
from tests.helpers import sha256_file as _sha256_file

ROOT = Path(__file__).resolve().parent.parent
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


@pytest.mark.explicit_e2e
@pytest.mark.timeout(900)
def test_non_editable_wheel_matches_internal_bundle_read_boundary(
    tmp_path: Path,
) -> None:
    workspace = _finalized_workspace(tmp_path)
    build_root = tmp_path / "build-root"
    build_root.mkdir()
    shutil.copy2(ROOT / "pyproject.toml", build_root / "pyproject.toml")
    shutil.copy2(ROOT / "README.md", build_root / "README.md")
    shutil.copytree(ROOT / "src", build_root / "src")
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
        cwd=build_root,
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
