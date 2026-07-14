"""Tests for public-safe evaluation cases."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml

from multi_agent_brief.cli.main import main
from multi_agent_brief.evaluation_cases.fixtures import evaluation_cases_root
from multi_agent_brief.evaluation_cases.runner import (
    EvaluationCaseRunError,
    _run_action,
)
from multi_agent_brief.orchestrator.runtime_state import initialize_runtime_state
from tests.helpers import write_minimal_workspace


ROOT = Path(__file__).resolve().parent.parent


def test_runtime_handoff_action_requires_and_honors_explicit_runtime(tmp_path: Path) -> None:
    workspace = write_minimal_workspace(tmp_path / "ws")
    initialize_runtime_state(
        workspace=workspace,
        runtime="codex",
        repo_workdir=ROOT,
    )
    context = {"workspace": workspace, "repo_workdir": ROOT}

    result = _run_action(
        action="runtime.run_handoff",
        args={"runtime": "codex", "skip_doctor": True},
        context=context,
    )

    assert result["ok"] is True
    handoff = json.loads(
        (workspace / "output/intermediate/agent_handoff.json").read_text(encoding="utf-8")
    )
    assert handoff["runtime"] == "codex"
    with pytest.raises(EvaluationCaseRunError, match="explicit canonical runtime"):
        _run_action(
            action="runtime.run_handoff",
            args={"skip_doctor": True},
            context=context,
        )


def _copy_packaged_cases(tmp_path: Path) -> tuple[Path, dict]:
    with evaluation_cases_root() as packaged_root:
        custom_root = tmp_path / "evaluation_cases"
        shutil.copytree(packaged_root, custom_root)

    manifest_path = custom_root / "manifest.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    return custom_root, manifest


def _write_manifest(custom_root: Path, manifest: dict) -> None:
    (custom_root / "manifest.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False),
        encoding="utf-8",
    )


def test_eval_cases_validate_and_run_packaged_cases(capsys):
    rc = main(["eval-cases", "validate", "--json"])

    assert rc == 0
    validation = json.loads(capsys.readouterr().out)
    assert validation["ok"] is True
    assert validation["case_count"] == 25

    rc = main(["eval-cases", "run", "--repo-workdir", str(ROOT), "--json"])

    assert rc == 0
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is True
    assert result["passed_count"] == 25
    assert result["failed_count"] == 0
    assert {
        "unsupported_material_fact",
        "stale_current_claim",
        "reader_facing_target_relevance",
        "feedback_triage_required",
        "planned_blocking_issue_cannot_continue",
        "provenance_projection_minimal",
        "control_switchboard_selection_is_not_execution",
        "reader_facing_source_appendix",
        "reader_clean_failed_no_delivery_promotion",
        "static_hermes_no_skip_finalize",
        "source_evidence_pack_blocks_non_evidence_file",
        "release_readiness_forged_event_blocker",
        "unauthorized_institution_branding_blocks_release",
        "mixed_metric_scope_support_blocker",
        "media_only_legal_policy_blocks_research_review",
        "company_event_missing_latest_official_check",
        "third_party_price_snapshot_formal_block",
        "formal_release_missing_human_approval",
        "trajectory_retry_budget_exhausted",
        "guidance_manifestation_not_observable",
        "same_evidence_reader_quality_regression",
        "final_abstract_quality_warning_surface",
        "unapproved_entry_not_materialized",
        "approved_guidance_materialized",
        "reverted_entry_removed_from_next_snapshot",
    } == {case["case_id"] for case in result["results"]}


def test_eval_cases_reader_clean_failed_no_delivery_promotion(capsys):
    rc = main([
        "eval-cases",
        "run",
        "--case-id",
        "reader_clean_failed_no_delivery_promotion",
        "--repo-workdir",
        str(ROOT),
        "--json",
    ])

    assert rc == 0
    result = json.loads(capsys.readouterr().out)
    case = result["results"][0]
    assert case["passed"] is True
    assert [item["action"] for item in case["actions"]] == [
        "completion.project",
        "workbuddy.diagnose",
    ]
    projection_action = case["actions"][0]
    assert projection_action["delivery_truth"]["valid"] is False
    assert projection_action["finalize_truth"]["reader_clean_status"] == "fail"
    assert projection_action["finalize_truth"]["delivery_promotion"] == "skipped_reader_clean_failed"
    assert projection_action["next_allowed_action"] == "stop_finalize_failed_no_valid_delivery"
    diagnose_action = case["actions"][1]
    assert diagnose_action["run_card"]["delivery_valid"] is False
    assert diagnose_action["run_card"]["delivery_truth"] == "not_valid"
    assert diagnose_action["run_card"]["next_allowed_action"] == "stop_finalize_failed_no_valid_delivery"


def test_eval_cases_final_abstract_quality_warning_surface(capsys):
    rc = main([
        "eval-cases",
        "run",
        "--case-id",
        "final_abstract_quality_warning_surface",
        "--repo-workdir",
        str(ROOT),
        "--json",
    ])

    assert rc == 0
    result = json.loads(capsys.readouterr().out)
    case = result["results"][0]
    assert case["passed"] is True
    assert [item["action"] for item in case["actions"]] == [
        "gates.check",
        "state.check",
        "quality.summarize",
    ]


def test_eval_cases_same_evidence_reader_quality_regression(capsys):
    rc = main([
        "eval-cases",
        "run",
        "--case-id",
        "same_evidence_reader_quality_regression",
        "--repo-workdir",
        str(ROOT),
        "--json",
    ])

    assert rc == 0
    result = json.loads(capsys.readouterr().out)
    case = result["results"][0]
    assert case["passed"] is True
    assert [item["action"] for item in case["actions"]] == [
        "state.check",
        "status.show",
        "quality.summarize",
        "packs.bundle",
        "state.check",
        "status.show",
    ]
    quality_action = case["actions"][2]
    assert quality_action["quality_panel"] == "output/intermediate/quality_panel.json"
    assert quality_action["quality_summary"] == "output/intermediate/quality_summary.md"
    assert quality_action["quality_panel_html"] == "output/intermediate/quality_panel.html"
    bundle_action = case["actions"][3]
    assert bundle_action["report_bundle_manifest"] == "output/report_bundle_manifest.json"
    assert bundle_action["delivery_bundle_archive"] == "output/delivery_bundle.zip"
    assert bundle_action["audit_bundle_archive"] == "output/audit_bundle.zip"


def test_eval_cases_issue_96_release_and_evidence_blockers(capsys):
    case_ids = [
        "unauthorized_institution_branding_blocks_release",
        "mixed_metric_scope_support_blocker",
        "media_only_legal_policy_blocks_research_review",
        "company_event_missing_latest_official_check",
        "third_party_price_snapshot_formal_block",
        "formal_release_missing_human_approval",
    ]
    for case_id in case_ids:
        rc = main([
            "eval-cases",
            "run",
            "--case-id",
            case_id,
            "--repo-workdir",
            str(ROOT),
            "--json",
        ])

        assert rc == 0, case_id
        result = json.loads(capsys.readouterr().out)
        case = result["results"][0]
        assert case["passed"] is True, case
        if case_id in {
            "unauthorized_institution_branding_blocks_release",
            "formal_release_missing_human_approval",
        }:
            assert case["actions"][0]["action"] == "release.check"
            assert case["actions"][0]["exit_code"] == 1
            assert case["actions"][0]["ok"] is False
            assert case["actions"][1]["action"] == "state.check"


def test_eval_cases_improvement_approved_case_materializes_snapshot(capsys):
    rc = main([
        "eval-cases",
        "run",
        "--case-id",
        "approved_guidance_materialized",
        "--repo-workdir",
        str(ROOT),
        "--json",
    ])

    assert rc == 0
    result = json.loads(capsys.readouterr().out)
    case = result["results"][0]
    assert case["passed"] is True
    assert case["actions"] == [
        {"action": "runtime.run_handoff", "exit_code": 0, "ok": True},
        {"action": "state.check", "exit_code": 0, "ok": True},
    ]


def test_provenance_projection_fixture_contains_required_stage_artifacts():
    with evaluation_cases_root() as packaged_root:
        workspace = packaged_root / "cases" / "provenance_projection_minimal" / "workspace"
        for rel_path in (
            "output/intermediate/candidate_claims.json",
            "output/intermediate/screened_candidates.json",
            "output/intermediate/claim_ledger.json",
            "output/intermediate/audited_brief.md",
            "output/intermediate/audit_report.json",
            "output/intermediate/quality_gate_report.json",
        ):
            assert (workspace / rel_path).exists(), rel_path


def test_eval_cases_single_case_reports_expected_failed_action(capsys):
    rc = main([
        "eval-cases",
        "run",
        "--case-id",
        "planned_blocking_issue_cannot_continue",
        "--repo-workdir",
        str(ROOT),
        "--json",
    ])

    assert rc == 0
    result = json.loads(capsys.readouterr().out)
    case = result["results"][0]
    assert case["passed"] is True
    assert case["observed_exit_code"] == 1
    assert case["actions"][-1]["action"] == "state.stage_complete"
    assert case["actions"][-1]["exit_code"] == 1
    observed_failure = " ".join(
        [
            case["actions"][-1].get("error", ""),
            json.dumps(case["actions"][-1].get("details", {}), ensure_ascii=False),
        ]
    )
    assert "unresolved blocking feedback issues" in observed_failure
    assert case["actions"][0]["action"] == "state.check"
    assert case["actions"][0]["exit_code"] == 0


def test_eval_cases_reject_shell_string_commands(tmp_path, capsys):
    custom_root, manifest = _copy_packaged_cases(tmp_path)
    manifest["cases"][0]["commands"] = ["multi-agent-brief gates check --workspace ws"]
    _write_manifest(custom_root, manifest)

    rc = main([
        "eval-cases",
        "validate",
        "--cases-dir",
        str(custom_root),
        "--json",
    ])

    assert rc == 1
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is False
    assert any("structured action" in error for error in result["errors"])


def test_eval_cases_reject_contains_text_absolute_and_traversal(tmp_path, capsys):
    custom_root, manifest = _copy_packaged_cases(tmp_path)
    manifest["cases"][-1]["expected"]["contains_text"] = [
        {"scope": "repo", "file": "/etc/hosts", "text": "localhost"},
        {"scope": "cases", "file": "../manifest.yaml", "text": "schema_version"},
        {"scope": "cases", "file": "C:\\Users\\example\\secret.txt", "text": "secret"},
    ]
    _write_manifest(custom_root, manifest)

    rc = main([
        "eval-cases",
        "validate",
        "--cases-dir",
        str(custom_root),
        "--json",
    ])

    assert rc == 1
    result = json.loads(capsys.readouterr().out)
    errors = " ".join(result["errors"])
    assert "must be relative" in errors
    assert "must not contain path traversal" in errors


def test_eval_cases_reject_feedback_ingest_absolute_and_traversal_paths(tmp_path, capsys):
    custom_root, manifest = _copy_packaged_cases(tmp_path)
    feedback_case = next(
        item for item in manifest["cases"] if item["case_id"] == "feedback_triage_required"
    )

    for feedback_path in ("/etc/hosts", "../outside.md", "C:\\Users\\example\\secret.txt"):
        feedback_case["commands"][0]["args"]["feedback"] = feedback_path
        _write_manifest(custom_root, manifest)

        rc = main([
            "eval-cases",
            "validate",
            "--cases-dir",
            str(custom_root),
            "--json",
        ])

        assert rc == 1
        result = json.loads(capsys.readouterr().out)
        errors = " ".join(result["errors"])
        assert "args.feedback" in errors
        assert "must be relative" in errors or "must not contain path traversal" in errors


def test_eval_cases_expected_actions_detect_wrong_failed_step(tmp_path, capsys):
    custom_root, manifest = _copy_packaged_cases(tmp_path)
    case = next(
        item
        for item in manifest["cases"]
        if item["case_id"] == "planned_blocking_issue_cannot_continue"
    )
    case["commands"][0]["args"] = {"strict": True}
    _write_manifest(custom_root, manifest)

    rc = main([
        "eval-cases",
        "run",
        "--cases-dir",
        str(custom_root),
        "--case-id",
        "planned_blocking_issue_cannot_continue",
        "--repo-workdir",
        str(ROOT),
        "--json",
    ])

    assert rc == 1
    result = json.loads(capsys.readouterr().out)
    case_result = result["results"][0]
    assert case_result["passed"] is False
    assert case_result["observed_exit_code"] == 1
    assert case_result["actions"] == [{"action": "state.check", "exit_code": 1, "ok": False}]
    assert any("expected_actions length" in error for error in case_result["errors"])


def test_eval_cases_reject_synthetic_false(tmp_path, capsys):
    custom_root, manifest = _copy_packaged_cases(tmp_path)
    manifest["synthetic"] = False
    _write_manifest(custom_root, manifest)

    rc = main([
        "eval-cases",
        "validate",
        "--cases-dir",
        str(custom_root),
        "--json",
    ])

    assert rc == 1
    result = json.loads(capsys.readouterr().out)
    assert any("synthetic: true" in error for error in result["errors"])


def test_eval_cases_reject_disallowed_action(tmp_path, capsys):
    custom_root, manifest = _copy_packaged_cases(tmp_path)
    manifest["cases"][0]["commands"][0]["action"] = "shell.exec"
    _write_manifest(custom_root, manifest)

    rc = main([
        "eval-cases",
        "validate",
        "--cases-dir",
        str(custom_root),
        "--json",
    ])

    assert rc == 1
    result = json.loads(capsys.readouterr().out)
    assert any("not allowlisted" in error for error in result["errors"])


def test_eval_cases_reject_malformed_artifact_status_expectation(tmp_path, capsys):
    custom_root, manifest = _copy_packaged_cases(tmp_path)
    manifest["cases"][0]["expected"]["artifact_statuses"] = [
        {"status": "invalid"},
        {"artifact_id": "source_evidence_pack_manifest", "status": 7},
    ]
    _write_manifest(custom_root, manifest)

    rc = main([
        "eval-cases",
        "validate",
        "--cases-dir",
        str(custom_root),
        "--json",
    ])

    assert rc == 1
    result = json.loads(capsys.readouterr().out)
    errors = " ".join(result["errors"])
    assert "artifact_statuses[0].artifact_id is required" in errors
    assert "artifact_statuses[1].status must be a string" in errors


def test_eval_cases_reject_artifact_statuses_on_static_case(tmp_path, capsys):
    custom_root, manifest = _copy_packaged_cases(tmp_path)
    static_case = next(
        item
        for item in manifest["cases"]
        if item["case_id"] == "static_hermes_no_skip_finalize"
    )
    static_case["expected"]["artifact_statuses"] = [
        {"artifact_id": "source_evidence_pack_manifest", "status": "invalid"},
    ]
    _write_manifest(custom_root, manifest)

    rc = main([
        "eval-cases",
        "validate",
        "--cases-dir",
        str(custom_root),
        "--json",
    ])

    assert rc == 1
    result = json.loads(capsys.readouterr().out)
    assert any(
        "expected.artifact_statuses requires a workspace case" in error
        for error in result["errors"]
    )


def test_eval_cases_artifact_statuses_detect_wrong_registry_status(tmp_path, capsys):
    custom_root, manifest = _copy_packaged_cases(tmp_path)
    case = next(
        item
        for item in manifest["cases"]
        if item["case_id"] == "source_evidence_pack_blocks_non_evidence_file"
    )
    case["expected"]["artifact_statuses"][0]["status"] = "valid"
    _write_manifest(custom_root, manifest)

    rc = main([
        "eval-cases",
        "run",
        "--cases-dir",
        str(custom_root),
        "--case-id",
        "source_evidence_pack_blocks_non_evidence_file",
        "--repo-workdir",
        str(ROOT),
        "--json",
    ])

    assert rc == 1
    result = json.loads(capsys.readouterr().out)
    case_result = result["results"][0]
    assert case_result["passed"] is False
    assert any("status expected 'valid', got 'invalid'" in error for error in case_result["errors"])


def test_eval_cases_reject_recursive_non_synthetic_claim_and_source_ids(tmp_path, capsys):
    custom_root, manifest = _copy_packaged_cases(tmp_path)
    ledger_path = (
        custom_root
        / "cases"
        / "stale_current_claim"
        / "workspace"
        / "output"
        / "intermediate"
        / "claim_ledger.json"
    )
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    ledger[0]["metadata"]["nested_ref"] = {
        "claim_id": "REAL_CLAIM_001",
        "source_id": "REAL_SRC_001",
    }
    ledger_path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_manifest(custom_root, manifest)

    rc = main([
        "eval-cases",
        "validate",
        "--cases-dir",
        str(custom_root),
        "--json",
    ])

    assert rc == 1
    result = json.loads(capsys.readouterr().out)
    errors = " ".join(result["errors"])
    assert "SYN_CLAIM_" in errors
    assert "SYN_SRC_" in errors
