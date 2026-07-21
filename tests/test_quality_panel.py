"""Tests for the Product OS Quality Panel JSON projection."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from multi_agent_brief.cli.main import main
from multi_agent_brief.status import build_workspace_status, format_workspace_status
from multi_agent_brief.product.quality_panel import (
    QUALITY_PANEL_HTML_BOUNDARY,
    QUALITY_PANEL_BOUNDARY,
    QUALITY_SUMMARY_BOUNDARY,
    QualityPanelError,
    build_quality_panel,
    quality_panel_html_path,
    quality_panel_path,
    render_quality_panel_html,
    quality_summary_path,
    _status_level,
    validate_quality_panel_html,
    render_quality_summary,
    validate_quality_panel_payload,
    validate_quality_summary_markdown,
    write_quality_panel,
    write_quality_panel_html,
    write_quality_summary,
)
from multi_agent_brief.semantic_evaluator.reader import (
    LAJ_READER_BOUNDARY,
    LajReaderView,
)
from multi_agent_brief.semantic_evaluator.serialization import canonical_sha256
from tests.helpers import write_legacy_control_files, write_minimal_workspace_under


def _workspace(base_path: Path) -> Path:
    """Build the legacy module fixture without claiming a public CLI path."""

    workspace = write_minimal_workspace_under(
        base_path,
        project_name="Quality Panel Test",
    )
    write_legacy_control_files(workspace)
    return workspace


def _snapshot_workspace_files(ws: Path) -> dict[str, bytes]:
    return {
        path.relative_to(ws).as_posix(): path.read_bytes()
        for path in sorted(ws.rglob("*"))
        if path.is_file()
    }




def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()




def _write_laj_reader_view(path: Path, *, report_sha256: str) -> LajReaderView:
    finding = {
        "assessment_unit_id": "AU-000000000001",
        "scope_class": "O1",
        "dimension_id": "cross_section_consistency",
        "severity": "major",
        "impact_scope": "supporting_text",
        "report_spans": [
            {
                "report_sha256": report_sha256,
                "block_id": "B000001",
                "start_char": 0,
                "end_char": 5,
                "excerpt_sha256": "2" * 64,
            }
        ],
        "context_requirement_ids": [],
        "observation": "<script>alert(1)</script> needs human review.",
        "rationale": "The section does not reconcile its own stated values.",
        "severity_basis": "The inconsistency changes the reader interpretation.",
        "confidence_basis": "direct_single_span",
        "external_premise_disclosure": "none",
        "recommended_human_action": "inspect_manually",
        "suggested_rewrite": None,
        "finding_id": "F-000000000001",
        "status": "proposal",
    }
    payload = {
        "schema_version": "briefloop.semantic_evaluator.reader_view.v1",
        "status": "available",
        "boundary": LAJ_READER_BOUNDARY,
        "advisory_only": True,
        "shadow_only": True,
        "runtime_authority": False,
        "authority_effect": "none",
        "archive_verified": True,
        "binding": {
            "artifact_id": "reader-test",
            "report_sha256": report_sha256,
            "trial_id": "trial-reader-test",
            "shadow_receipt_id": "receipt-reader-test",
            "instrument_sha256": "3" * 64,
            "execution_sha256": "4" * 64,
            "execution_origin": "synthetic_fixture",
            "model_id": "synthetic-fixture-v4",
            "model_version": "synthetic-fixture-v4",
            "archive_manifest_sha256": "5" * 64,
            "presentation_sha256": "6" * 64,
        },
        "run_status": "completed",
        "validation_status": "accepted",
        "reason_codes": [],
        "assessed_unit_count": 1,
        "finding_count": 1,
        "withheld_finding_count": 0,
        "abstention_count": 0,
        "findings": [finding],
        "disclaimer": "Experimental advisory finding.",
    }
    view = LajReaderView.model_validate(
        {**payload, "view_sha256": canonical_sha256(payload)}
    )
    path.write_text(
        json.dumps(view.model_dump(mode="json"), ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    return view






















def test_quality_panel_direct_import_has_no_runtime_state_cycle() -> None:
    env = dict(os.environ)
    src_path = str(Path.cwd() / "src")
    env["PYTHONPATH"] = f"{src_path}{os.pathsep}{env['PYTHONPATH']}" if env.get("PYTHONPATH") else src_path

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from multi_agent_brief.product.quality_panel import "
                "build_quality_panel, render_quality_panel_html, render_quality_summary; "
                "print(build_quality_panel, render_quality_panel_html, render_quality_summary)"
            ),
        ],
        check=False,
        cwd=Path.cwd(),
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert "build_quality_panel" in result.stdout
    assert "render_quality_panel_html" in result.stdout
    assert "render_quality_summary" in result.stdout
























































def test_quality_summarize_public_cli_retired_rejects_typed_without_writes(
    tmp_path: Path,
    capsys,
) -> None:
    # retired public `quality summarize` CLI on JSON control
    # state; the authority guard answers a typed token and performs zero writes.
    ws = _workspace(tmp_path)
    reader_target = ws / "output" / "brief.md"
    reader_target.parent.mkdir(parents=True, exist_ok=True)
    reader_target.write_text("# Final reader brief\n", encoding="utf-8")
    laj_path = tmp_path / "laj.json"
    _write_laj_reader_view(laj_path, report_sha256=_sha256_file(reader_target))
    capsys.readouterr()

    for argv in (
        ["quality", "summarize", "--workspace", str(ws), "--json"],
        ["quality", "summarize", "--workspace", str(ws)],
        [
            "quality",
            "summarize",
            "--workspace",
            str(ws),
            "--laj-view",
            str(laj_path),
            "--json",
        ],
    ):
        before = _snapshot_workspace_files(ws)
        rc = main(argv)
        assert rc == 1
        assert capsys.readouterr().out.strip() == "legacy_workspace_unsupported"
        assert _snapshot_workspace_files(ws) == before

    missing = tmp_path / "missing-ws"
    rc = main(["quality", "summarize", "--workspace", str(missing), "--json"])
    assert rc == 1
    assert capsys.readouterr().out.strip() == "runtime_command_unsupported"
    assert not missing.exists()

    shell = tmp_path / "not-a-workspace"
    (shell / "output" / "intermediate").mkdir(parents=True)
    before = _snapshot_workspace_files(shell)
    rc = main(["quality", "summarize", "--workspace", str(shell), "--json"])
    assert rc == 1
    assert capsys.readouterr().out.strip() == "runtime_command_unsupported"
    assert _snapshot_workspace_files(shell) == before
    assert not (shell / "output" / "intermediate" / "quality_panel.json").exists()
    assert not (shell / "output" / "intermediate" / "quality_summary.md").exists()
    assert not (shell / "output" / "intermediate" / "quality_panel.html").exists()


def test_state_public_cli_retired_rejects_typed_without_writes(tmp_path: Path, capsys) -> None:
    # retired public `state` operator CLI surface; the authority
    # guard answers a typed token and performs zero writes.
    ws = _workspace(tmp_path)
    capsys.readouterr()

    for argv in (
        ["state", "check", "--workspace", str(ws), "--json"],
        ["state", "init", "--runtime", "operator", "--workspace", str(ws), "--reset-state"],
    ):
        before = _snapshot_workspace_files(ws)
        rc = main(argv)
        assert rc == 1
        assert capsys.readouterr().out.strip() == "legacy_workspace_unsupported"
        assert _snapshot_workspace_files(ws) == before




def test_quality_summary_validator_rejects_release_authority_shape() -> None:
    bad = (
        "# Quality Summary\n\n"
        f"Boundary: {QUALITY_SUMMARY_BOUNDARY}.\n\n"
        f"Quality-Panel-SHA256: sha256:{'0' * 64}\n\n"
        "## Overall\n\n"
        "- This report is ready to publish.\n\n"
        "## Blocking Issues\n\n- None.\n\n"
        "## Warnings\n\n- None.\n\n"
        "## Missing Or Incomplete Surfaces\n\n- None.\n\n"
        "## Source Evidence\n\n- None.\n\n"
        "## Gates And Reader Clean\n\n- None.\n\n"
        "## Claims And Support Records\n\n- None.\n\n"
        "## Recommended Next Actions\n\n- None.\n"
    )

    assert validate_quality_summary_markdown(bad) == (
        "quality_summary_schema_error:forbidden_phrase:ready_to_publish"
    )






































def test_quality_panel_payload_validator_rejects_release_authority_shape() -> None:
    payload = {
        "schema_version": "briefloop.quality_panel.v1",
        "workspace": ".",
        "run_id": "run-1",
        "runtime_effect": "projection_only",
        "boundary": QUALITY_PANEL_BOUNDARY,
        "overall_status": "pass",
        "control_integrity": {},
        "source_evidence": {},
        "gates": {},
        "claims": {},
        "delivery": {},
        "trajectory_regulation": {
            "schema_version": "briefloop.trajectory_regulation.v1",
            "status": "ok",
            "read_only": True,
            "runtime_effect": "none",
            "boundary": "trajectory_regulation_projection_only_not_state_transition_or_repair_execution",
            "run_id": "run-1",
            "current_stage": "doctor",
            "event_log_present": True,
            "event_log_corrupt_count": 0,
            "limits": {},
            "summary_counts": {},
            "stages": [],
            "recommended_actions": [],
            "non_goals": [
                "state_transition",
                "repair_execution",
                "gate_decision",
                "release_authority",
                "quality_score",
            ],
        },
        "recommended_actions": [],
        "non_goals": ["quality_score"],
    }

    assert validate_quality_panel_payload(payload) == "quality_panel_schema_error:non_goals"


def test_quality_panel_payload_validator_rejects_forged_trajectory_authority() -> None:
    trajectory = {
        "schema_version": "briefloop.trajectory_regulation.v1",
        "status": "ok",
        "read_only": True,
        "runtime_effect": "none",
        "boundary": "trajectory_regulation_projection_only_not_state_transition_or_repair_execution",
        "run_id": "run-1",
        "current_stage": "doctor",
        "event_log_present": True,
        "event_log_corrupt_count": 0,
        "limits": {},
        "summary_counts": {},
        "stages": [],
        "recommended_actions": [],
        "non_goals": [
            "state_transition",
            "repair_execution",
            "gate_decision",
            "release_authority",
            "quality_score",
        ],
    }
    payload = {
        "schema_version": "briefloop.quality_panel.v1",
        "workspace": ".",
        "run_id": "run-1",
        "runtime_effect": "projection_only",
        "boundary": QUALITY_PANEL_BOUNDARY,
        "overall_status": "pass",
        "control_integrity": {},
        "source_evidence": {},
        "gates": {},
        "claims": {},
        "delivery": {},
        "trajectory_regulation": trajectory,
        "recommended_actions": [],
        "non_goals": [
            "semantic_truth_proof",
            "release_eligibility_decision",
            "delivery_approval",
        ],
    }

    forged_trajectory = json.loads(json.dumps(payload))
    forged_trajectory["trajectory_regulation"]["runtime_effect"] = "state_transition"
    assert (
        validate_quality_panel_payload(forged_trajectory)
        == "quality_panel_schema_error:trajectory_regulation:trajectory_regulation_schema_error:runtime_effect"
    )

    forged_nested_action = json.loads(json.dumps(payload))
    forged_nested_action["trajectory_regulation"]["recommended_actions"] = [{"action": "approve_delivery"}]
    assert (
        validate_quality_panel_payload(forged_nested_action)
        == "quality_panel_schema_error:trajectory_regulation:trajectory_regulation_schema_error:recommended_actions.action"
    )

    forged_action = json.loads(json.dumps(payload))
    forged_action["recommended_actions"] = [{"action": "approve_delivery"}]
    assert validate_quality_panel_payload(forged_action) == "quality_panel_schema_error:recommended_actions.action"


def test_quality_panel_payload_validator_rejects_forged_template_conformance_authority() -> None:
    payload = {
        "schema_version": "briefloop.quality_panel.v1",
        "workspace": ".",
        "run_id": "run-test",
        "runtime_effect": "projection_only",
        "boundary": QUALITY_PANEL_BOUNDARY,
        "overall_status": "warning",
        "control_integrity": {},
        "source_evidence": {},
        "gates": {},
        "claims": {},
        "delivery": {},
        "report_template_conformance": {
            "boundary": "product_report_template_conformance_projection_only",
            "runtime_effect": "state_transition",
            "status": "warning",
            "targets": [],
            "summary_counts": {},
        },
        "recommended_actions": [],
        "non_goals": [
            "semantic_truth_proof",
            "release_eligibility_decision",
            "delivery_approval",
        ],
    }

    assert validate_quality_panel_payload(payload) == (
        "quality_panel_schema_error:report_template_conformance:"
        "report_template_conformance_schema_error:runtime_effect"
    )
