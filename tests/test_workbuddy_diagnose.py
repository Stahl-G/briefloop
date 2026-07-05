"""Tests for WorkBuddy read-only diagnosis Run Card."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from multi_agent_brief.cli.main import main


EVENT_LOG_SCHEMA = "multi-agent-brief-event-log/v1"
QUALITY_GATE_SCHEMA = "multi-agent-brief-quality-gates/v1"
RUNTIME_MANIFEST_SCHEMA = "multi-agent-brief-runtime-manifest/v1"
WORKFLOW_STATE_SCHEMA = "multi-agent-brief-workflow-state/v1"


def _workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "input").mkdir()
    (ws / "output" / "intermediate").mkdir(parents=True)
    (ws / "config.yaml").write_text("project:\n  name: Test\n", encoding="utf-8")
    (ws / "sources.yaml").write_text(
        "source_strategy:\n"
        "  profile: conservative\n"
        "  enabled_providers:\n"
        "    - manual\n"
        "manual:\n"
        "  enabled: true\n"
        "  sources:\n"
        "    - name: Local\n"
        "      path: input/\n",
        encoding="utf-8",
    )
    return ws


def _write_manifest(intermediate: Path, **extra: object) -> None:
    payload: dict[str, object] = {
        "schema_version": RUNTIME_MANIFEST_SCHEMA,
        "runtime": "codebuddy",
    }
    payload.update(extra)
    (intermediate / "runtime_manifest.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )


def _write_workflow(intermediate: Path, **extra: object) -> None:
    payload: dict[str, object] = {
        "schema_version": WORKFLOW_STATE_SCHEMA,
        "current_stage": "doctor",
        "blocked": False,
        "run_integrity": {"status": "clean"},
    }
    payload.update(extra)
    (intermediate / "workflow_state.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )


def _write_condition(workspace: Path, **extra: object) -> None:
    payload: dict[str, object] = {
        "experiment_id": "MABW-080",
        "case_id": "CASE-001",
        "condition": "A",
        "assessment_target": "auditable_brief",
    }
    payload.update(extra)
    path = workspace / "experiment" / "080" / "condition.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _event(event_type: str, **extra: object) -> dict[str, object]:
    return {
        "schema_version": EVENT_LOG_SCHEMA,
        "event_id": f"evt-{event_type}",
        "run_id": "RUN-TEST",
        "created_at": "2026-07-05T00:00:00Z",
        "event_type": event_type,
        "actor": "cli",
        **extra,
    }


def _gate_report(
    status: str,
    *,
    blocking: bool = False,
    warning: bool = False,
    gate_results: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    findings: list[dict[str, object]] = []
    if blocking or warning:
        blocking_level = "blocking" if blocking else "warning"
        findings.append(
            {
                "finding_id": f"F-{status}-001",
                "finding_type": "diagnostic_fixture",
                "severity": "high" if blocking else "medium",
                "blocking_level": blocking_level,
                "blocking": blocking,
            }
        )
    return {
        "schema_version": QUALITY_GATE_SCHEMA,
        "status": status,
        "gate_results": gate_results or [],
        "findings": findings,
    }


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_valid_delivery_finalize_report(workspace: Path) -> None:
    delivery = workspace / "output" / "delivery"
    delivery.mkdir(parents=True, exist_ok=True)
    brief = delivery / "brief.md"
    brief.write_text("# Brief\n\nReader-safe delivery.\n", encoding="utf-8")
    intermediate = workspace / "output" / "intermediate"
    (intermediate / "finalize_report.json").write_text(
        json.dumps(
            {
                "status": "pass",
                "reader_clean": {"status": "pass"},
                "delivery_promotion": "promoted",
                "delivery_artifacts": ["output/delivery/brief.md"],
                "delivery_artifact_sha256": {
                    "output/delivery/brief.md": _sha256_file(brief),
                },
            }
        ),
        encoding="utf-8",
    )


def test_workbuddy_diagnose_json_reports_run_card_and_secret_risk(
    tmp_path: Path,
    capsys,
) -> None:
    ws = _workspace(tmp_path)
    secret_value = "tvly-secret-value"
    (ws / ".env").write_text(f"TAVILY_API_KEY={secret_value}\n", encoding="utf-8")
    _write_manifest(
        ws / "output" / "intermediate",
        runtime_capabilities={"delegation_supported": True},
    )
    _write_workflow(
        ws / "output" / "intermediate",
        current_stage="finalize",
        blocked=False,
        run_integrity={"status": "contaminated"},
    )
    (ws / "output" / "intermediate" / "artifact_registry.json").write_text(
        json.dumps(
            {
                "artifacts": {
                    "audited_brief": {"status": "valid", "validation_result": "valid_minimum"},
                    "input_classification": {
                        "status": "invalid",
                        "validation_result": "input_classification_schema_error:context[0].path",
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    rc = main(["workbuddy", "diagnose", "--workspace", str(ws), "--json"])

    assert rc == 0
    raw = capsys.readouterr().out
    assert secret_value not in raw
    payload = json.loads(raw)
    assert payload["schema_version"] == "briefloop.workbuddy_diagnose.v1"
    assert payload["runtime_effect"] == "read_only_diagnostic"
    assert payload["run_card"] == {
        "runtime": "codebuddy",
        "current_stage": "finalize",
        "assessment_target": "not_applicable",
        "assessment_target_status": "not_applicable",
        "run_integrity": "contaminated",
        "blocked": False,
        "latest_gate_status": "unknown",
        "finalize_report": "missing",
        "delivery_dir": "missing",
        "finalize_status": "missing",
        "reader_clean_status": "unknown",
        "delivery_valid": False,
        "delivery_promotion": "none",
        "delivery_paths_current": False,
        "run_integrity_status": "contaminated",
        "finalize_event": "missing",
        "delivery_event": "missing",
        "share_workspace_zip_allowed": False,
        "next_allowed_action": "stop_human_review_or_fresh_workspace",
    }
    assert payload["secret_risk"]["env_present"] is True
    assert payload["secret_risk"]["nonempty_env_keys"] == ["TAVILY_API_KEY"]
    assert payload["secret_risk"]["secret_values_reported"] is False
    assert payload["secret_risk"]["share_workspace_zip_allowed"] is False
    assert payload["artifacts"]["invalid_or_stale"] == [
        {
            "artifact_id": "input_classification",
            "status": "invalid",
            "validation_result": "input_classification_schema_error:context[0].path",
        }
    ]
    assert payload["finalize"]["exists"] is False
    assert payload["delivery"]["exists"] is False
    assert payload["control_files"]["workflow_state"] == "present"
    assert payload["control_files"]["runtime_manifest"] == "present"
    assert payload["control_files"]["artifact_registry"] == "present"
    assert payload["control_files"]["event_log"] == "missing"


def test_workbuddy_diagnose_text_prints_run_card_without_delivery_claim(
    tmp_path: Path,
    capsys,
) -> None:
    ws = _workspace(tmp_path)

    rc = main(["workbuddy", "diagnose", "--workspace", str(ws)])

    assert rc == 0
    output = capsys.readouterr().out
    for field in (
        "runtime:",
        "current_stage:",
        "assessment_target:",
        "assessment_target_status:",
        "run_integrity:",
        "blocked:",
        "latest_gate_status:",
        "finalize_report:",
        "delivery_dir:",
        "finalize_status:",
        "reader_clean_status:",
        "delivery_valid:",
        "delivery_promotion:",
        "delivery_paths_current:",
        "run_integrity_status:",
        "finalize_event:",
        "delivery_event:",
        "share_workspace_zip_allowed:",
        "next_allowed_action:",
    ):
        assert field in output
    assert "delivery complete" not in output.lower()
    assert "delivered" not in output.lower()


def test_workbuddy_diagnose_failed_finalize_reports_no_valid_delivery(
    tmp_path: Path,
    capsys,
) -> None:
    ws = _workspace(tmp_path)
    intermediate = ws / "output" / "intermediate"
    _write_manifest(intermediate)
    _write_workflow(intermediate, current_stage="finalize", run_integrity={"status": "clean"})
    (intermediate / "artifact_registry.json").write_text(
        json.dumps({"artifacts": {}}),
        encoding="utf-8",
    )
    staging = intermediate / "finalize_staging"
    staging.mkdir()
    (staging / "brief.md").write_text("# Brief\n\nBad [CL-0001].\n", encoding="utf-8")
    (intermediate / "finalize_report.json").write_text(
        json.dumps(
            {
                "status": "fail",
                "reader_clean": {"status": "fail"},
                "delivery_promotion": "skipped_reader_clean_failed",
                "staging_reader_brief": "output/intermediate/finalize_staging/brief.md",
                "delivery_artifacts": [],
            }
        ),
        encoding="utf-8",
    )

    rc = main(["workbuddy", "diagnose", "--workspace", str(ws), "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    run_card = payload["run_card"]
    assert run_card["finalize_status"] == "fail"
    assert run_card["reader_clean_status"] == "fail"
    assert run_card["delivery_valid"] is False
    assert run_card["delivery_promotion"] == "skipped_reader_clean_failed"
    assert run_card["delivery_paths_current"] is False
    assert run_card["next_allowed_action"] == "do_not_edit_audited_brief_rerun_finalize_or_repair"
    assert payload["delivery"]["valid"] is False


def test_workbuddy_diagnose_text_reports_failed_finalize_without_final_claim(
    tmp_path: Path,
    capsys,
) -> None:
    ws = _workspace(tmp_path)
    intermediate = ws / "output" / "intermediate"
    _write_manifest(intermediate)
    _write_workflow(intermediate, current_stage="finalize", run_integrity={"status": "clean"})
    (intermediate / "artifact_registry.json").write_text(json.dumps({"artifacts": {}}), encoding="utf-8")
    (intermediate / "finalize_report.json").write_text(
        json.dumps(
            {
                "status": "fail",
                "reader_clean": {"status": "fail"},
                "delivery_promotion": "skipped_reader_clean_failed",
                "delivery_artifacts": [],
            }
        ),
        encoding="utf-8",
    )

    rc = main(["workbuddy", "diagnose", "--workspace", str(ws)])

    assert rc == 0
    output = capsys.readouterr().out
    assert "Draft/audit completed, finalize failed, no valid delivery." in output
    assert "Final brief generated" not in output
    assert "Delivery complete" not in output
    assert "read_only_workbuddy_run_card_not_gate_delivery_release_or_semantic_proof" in output
    assert "Doctor: not_run_read_only" in output
    assert "Output directory writable" not in output
    assert "Output directory not writable" not in output


def test_workbuddy_diagnose_json_fails_soft_on_corrupt_utf8_control_json(
    tmp_path: Path,
    capsys,
) -> None:
    ws = _workspace(tmp_path)
    (ws / "output" / "intermediate" / "workflow_state.json").write_bytes(b"\xff\xfe\x00")

    rc = main(["workbuddy", "diagnose", "--workspace", str(ws), "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["workflow"]["workflow_state_status"] == "unreadable_utf8"
    assert payload["control_files"]["workflow_state"] == "unreadable_utf8"
    assert payload["run_card"]["next_allowed_action"] == "inspect_unreadable_or_missing_control_files"


def test_workbuddy_diagnose_secret_risk_controls_next_action_for_finalized_workspace(
    tmp_path: Path,
    capsys,
) -> None:
    ws = _workspace(tmp_path)
    intermediate = ws / "output" / "intermediate"
    (ws / ".env").write_text("TAVILY_API_KEY=secret-value\n", encoding="utf-8")
    _write_manifest(intermediate)
    _write_workflow(intermediate, current_stage="finalize", run_integrity={"status": "clean"})
    (intermediate / "artifact_registry.json").write_text(
        json.dumps({"artifacts": {}}),
        encoding="utf-8",
    )
    _write_valid_delivery_finalize_report(ws)
    (intermediate / "event_log.jsonl").write_text(
        json.dumps(_event("decision_recorded", decision="finalize", stage_id="finalize")) + "\n"
        + json.dumps(_event("delivery_succeeded")) + "\n",
        encoding="utf-8",
    )

    rc = main(["workbuddy", "diagnose", "--workspace", str(ws), "--json"])

    assert rc == 0
    raw = capsys.readouterr().out
    assert "secret-value" not in raw
    payload = json.loads(raw)
    assert payload["run_card"]["finalize_report"] == "present"
    assert payload["run_card"]["delivery_dir"] == "present"
    assert payload["run_card"]["finalize_status"] == "pass"
    assert payload["run_card"]["reader_clean_status"] == "pass"
    assert payload["run_card"]["delivery_promotion"] == "promoted"
    assert payload["run_card"]["delivery_paths_current"] is True
    assert payload["run_card"]["delivery_valid"] is True
    assert payload["run_card"]["finalize_event"] == "present"
    assert payload["run_card"]["delivery_event"] == "present"
    assert payload["run_card"]["share_workspace_zip_allowed"] is False
    assert payload["run_card"]["next_allowed_action"] == "do_not_share_workspace_zip_secret_risk"


def test_workbuddy_diagnose_flags_finalize_delivery_file_event_gap(
    tmp_path: Path,
    capsys,
) -> None:
    ws = _workspace(tmp_path)
    intermediate = ws / "output" / "intermediate"
    _write_manifest(intermediate)
    _write_workflow(intermediate, current_stage="finalize", run_integrity={"status": "clean"})
    (intermediate / "artifact_registry.json").write_text(
        json.dumps({"artifacts": {}}),
        encoding="utf-8",
    )
    _write_valid_delivery_finalize_report(ws)

    rc = main(["workbuddy", "diagnose", "--workspace", str(ws), "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["run_card"]["finalize_report"] == "present"
    assert payload["run_card"]["delivery_dir"] == "present"
    assert payload["run_card"]["delivery_valid"] is True
    assert payload["run_card"]["delivery_paths_current"] is True
    assert payload["run_card"]["finalize_event"] == "missing"
    assert payload["run_card"]["delivery_event"] == "missing"
    assert payload["run_card"]["next_allowed_action"] == "inspect_finalize_delivery_event_gap"


def test_workbuddy_diagnose_reads_gate_report_status_not_registry_validity(
    tmp_path: Path,
    capsys,
) -> None:
    ws = _workspace(tmp_path)
    intermediate = ws / "output" / "intermediate"
    gates = intermediate / "gates"
    gates.mkdir()
    _write_manifest(intermediate)
    _write_workflow(intermediate, current_stage="auditor", run_integrity={"status": "clean"})
    (intermediate / "artifact_registry.json").write_text(
        json.dumps(
            {
                "artifacts": {
                    "auditor_quality_gate_report": {
                        "status": "valid",
                        "validation_result": "valid_quality_gate_report_schema",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    (gates / "auditor_quality_gate_report.json").write_text(
        json.dumps(_gate_report("fail", blocking=True)),
        encoding="utf-8",
    )

    rc = main(["workbuddy", "diagnose", "--workspace", str(ws), "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert (
        payload["run_card"]["latest_gate_status"]
        == "auditor_quality_gate_report:fail:blocking_findings=1"
    )
    assert payload["run_card"]["next_allowed_action"] == "stop_resolve_blocking_gate_report"


def test_workbuddy_diagnose_does_not_count_warning_only_gate_findings_as_blocking(
    tmp_path: Path,
    capsys,
) -> None:
    ws = _workspace(tmp_path)
    intermediate = ws / "output" / "intermediate"
    gates = intermediate / "gates"
    gates.mkdir()
    _write_manifest(intermediate)
    _write_workflow(intermediate, current_stage="auditor", run_integrity={"status": "clean"})
    (intermediate / "artifact_registry.json").write_text(
        json.dumps({"artifacts": {}}),
        encoding="utf-8",
    )
    (gates / "auditor_quality_gate_report.json").write_text(
        json.dumps(_gate_report("warning", warning=True)),
        encoding="utf-8",
    )

    rc = main(["workbuddy", "diagnose", "--workspace", str(ws), "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert (
        payload["run_card"]["latest_gate_status"]
        == "auditor_quality_gate_report:warning:blocking_findings=0"
    )
    assert payload["run_card"]["next_allowed_action"] == "continue_current_stage_or_handoff_workflow"


def test_workbuddy_diagnose_prefers_current_stage_gate_over_stale_finalize_gate(
    tmp_path: Path,
    capsys,
) -> None:
    ws = _workspace(tmp_path)
    intermediate = ws / "output" / "intermediate"
    gates = intermediate / "gates"
    gates.mkdir()
    _write_manifest(intermediate)
    _write_workflow(intermediate, current_stage="auditor", run_integrity={"status": "clean"})
    (intermediate / "artifact_registry.json").write_text(
        json.dumps({"artifacts": {}}),
        encoding="utf-8",
    )
    (gates / "finalize_quality_gate_report.json").write_text(
        json.dumps(_gate_report("fail", blocking=True)),
        encoding="utf-8",
    )
    (gates / "auditor_quality_gate_report.json").write_text(
        json.dumps(_gate_report("pass")),
        encoding="utf-8",
    )

    rc = main(["workbuddy", "diagnose", "--workspace", str(ws), "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert (
        payload["run_card"]["latest_gate_status"]
        == "auditor_quality_gate_report:pass:blocking_findings=0"
    )
    assert payload["run_card"]["next_allowed_action"] == "continue_current_stage_or_handoff_workflow"


def test_workbuddy_diagnose_uses_workflow_stage_before_finalize_guidance(
    tmp_path: Path,
    capsys,
) -> None:
    ws = _workspace(tmp_path)
    intermediate = ws / "output" / "intermediate"
    _write_manifest(intermediate)
    _write_workflow(intermediate, current_stage="doctor", run_integrity={"status": "clean"})
    (intermediate / "artifact_registry.json").write_text(
        json.dumps({"artifacts": {}}),
        encoding="utf-8",
    )

    rc = main(["workbuddy", "diagnose", "--workspace", str(ws), "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["run_card"]["current_stage"] == "doctor"
    assert payload["run_card"]["finalize_report"] == "missing"
    assert payload["run_card"]["delivery_dir"] == "missing"
    assert payload["run_card"]["next_allowed_action"] == "continue_current_stage_or_handoff_workflow"


def test_workbuddy_diagnose_recognizes_finalize_decision_event(
    tmp_path: Path,
    capsys,
) -> None:
    ws = _workspace(tmp_path)
    intermediate = ws / "output" / "intermediate"
    _write_manifest(intermediate)
    _write_workflow(intermediate, current_stage="finalize", run_integrity={"status": "clean"})
    (intermediate / "artifact_registry.json").write_text(
        json.dumps({"artifacts": {}}),
        encoding="utf-8",
    )
    _write_valid_delivery_finalize_report(ws)
    (intermediate / "event_log.jsonl").write_text(
        json.dumps(
            _event(
                "decision_recorded",
                decision="finalize",
                stage_id="finalize",
                metadata={"transaction_id": "tx-finalize"},
            )
        )
        + "\n"
        + json.dumps(_event("delivery_succeeded")) + "\n",
        encoding="utf-8",
    )

    rc = main(["workbuddy", "diagnose", "--workspace", str(ws), "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["run_card"]["finalize_event"] == "present"
    assert payload["run_card"]["delivery_event"] == "present"
    assert payload["run_card"]["delivery_valid"] is True
    assert payload["run_card"]["next_allowed_action"] == "delivery_ready_for_human_review"


def test_workbuddy_diagnose_uses_shared_run_integrity_interpreter(
    tmp_path: Path,
    capsys,
) -> None:
    ws = _workspace(tmp_path)
    intermediate = ws / "output" / "intermediate"
    _write_manifest(intermediate)
    _write_workflow(
        intermediate,
        current_stage="finalize",
        run_integrity={
            "status": "clean",
            "reference_eligible": False,
            "clean_single_shot": True,
            "reasons": [],
        },
    )
    (intermediate / "artifact_registry.json").write_text(
        json.dumps({"artifacts": {}}),
        encoding="utf-8",
    )

    rc = main(["workbuddy", "diagnose", "--workspace", str(ws), "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["run_card"]["run_integrity"] == "unknown"
    assert payload["workflow"]["run_integrity"]["reasons"] == [
        {
            "reason_code": "run_integrity_clean_not_reference_eligible",
            "message": "workflow_state.run_integrity clean runs must be reference eligible.",
        }
    ]
    assert payload["run_card"]["next_allowed_action"] == "stop_human_review_or_fresh_workspace"


def test_workbuddy_diagnose_stops_on_blocked_workflow_state(
    tmp_path: Path,
    capsys,
) -> None:
    ws = _workspace(tmp_path)
    intermediate = ws / "output" / "intermediate"
    (ws / "output" / "delivery").mkdir(parents=True)
    _write_manifest(intermediate)
    _write_workflow(
        intermediate,
        current_stage="finalize",
        blocked=True,
        blocking_reason="human review required",
        run_integrity={"status": "clean"},
    )
    (intermediate / "artifact_registry.json").write_text(
        json.dumps({"artifacts": {}}),
        encoding="utf-8",
    )
    (intermediate / "finalize_report.json").write_text(
        json.dumps({"status": "pass"}),
        encoding="utf-8",
    )
    (intermediate / "event_log.jsonl").write_text(
        json.dumps(_event("decision_recorded", decision="finalize", stage_id="finalize")) + "\n"
        + json.dumps(_event("delivery_succeeded")) + "\n",
        encoding="utf-8",
    )

    rc = main(["workbuddy", "diagnose", "--workspace", str(ws), "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["workflow"]["blocked"] is True
    assert payload["workflow"]["blocking_reason"] == "human review required"
    assert payload["run_card"]["next_allowed_action"] == "stop_workflow_blocked_human_review_required"


def test_workbuddy_diagnose_rejects_malformed_gate_report_before_continuing(
    tmp_path: Path,
    capsys,
) -> None:
    ws = _workspace(tmp_path)
    intermediate = ws / "output" / "intermediate"
    gates = intermediate / "gates"
    gates.mkdir()
    _write_manifest(intermediate)
    _write_workflow(intermediate, current_stage="auditor", run_integrity={"status": "clean"})
    (intermediate / "artifact_registry.json").write_text(
        json.dumps({"artifacts": {}}),
        encoding="utf-8",
    )
    (gates / "auditor_quality_gate_report.json").write_text(
        json.dumps({"status": "pass", "findings": []}),
        encoding="utf-8",
    )

    rc = main(["workbuddy", "diagnose", "--workspace", str(ws), "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert (
        payload["run_card"]["latest_gate_status"]
        == "auditor_quality_gate_report:invalid:gate_report_invalid"
    )
    assert payload["run_card"]["next_allowed_action"] == "stop_resolve_blocking_gate_report"


def test_workbuddy_diagnose_rejects_non_strict_event_log_before_trusting_events(
    tmp_path: Path,
    capsys,
) -> None:
    ws = _workspace(tmp_path)
    intermediate = ws / "output" / "intermediate"
    (ws / "output" / "delivery").mkdir(parents=True)
    _write_manifest(intermediate)
    _write_workflow(intermediate, current_stage="finalize", run_integrity={"status": "clean"})
    (intermediate / "artifact_registry.json").write_text(
        json.dumps({"artifacts": {}}),
        encoding="utf-8",
    )
    (intermediate / "finalize_report.json").write_text(
        json.dumps({"status": "pass"}),
        encoding="utf-8",
    )
    (intermediate / "event_log.jsonl").write_text(
        json.dumps({"event_type": "delivery_succeeded"}) + "\n",
        encoding="utf-8",
    )

    rc = main(["workbuddy", "diagnose", "--workspace", str(ws), "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["control_files"]["event_log"] == "invalid_json"
    assert payload["run_card"]["finalize_event"] == "missing"
    assert payload["run_card"]["delivery_event"] == "missing"
    assert payload["run_card"]["next_allowed_action"] == "inspect_unreadable_or_missing_control_files"


def test_workbuddy_diagnose_stops_on_invalid_runtime_state_schema(
    tmp_path: Path,
    capsys,
) -> None:
    ws = _workspace(tmp_path)
    intermediate = ws / "output" / "intermediate"
    _write_manifest(intermediate)
    (intermediate / "workflow_state.json").write_text(
        json.dumps(
            {
                "current_stage": "doctor",
                "run_integrity": {"status": "clean"},
            }
        ),
        encoding="utf-8",
    )
    (intermediate / "artifact_registry.json").write_text(
        json.dumps({"artifacts": {}}),
        encoding="utf-8",
    )

    rc = main(["workbuddy", "diagnose", "--workspace", str(ws), "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["control_files"]["workflow_state"] == "invalid_schema"
    assert payload["run_card"]["next_allowed_action"] == "inspect_unreadable_or_missing_control_files"


def test_workbuddy_diagnose_stops_on_non_object_control_json(
    tmp_path: Path,
    capsys,
) -> None:
    ws = _workspace(tmp_path)
    intermediate = ws / "output" / "intermediate"
    (intermediate / "runtime_manifest.json").write_text(
        json.dumps(["not", "an", "object"]),
        encoding="utf-8",
    )
    _write_workflow(intermediate, current_stage="doctor", run_integrity={"status": "clean"})
    (intermediate / "artifact_registry.json").write_text(
        json.dumps({"artifacts": {}}),
        encoding="utf-8",
    )

    rc = main(["workbuddy", "diagnose", "--workspace", str(ws), "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["control_files"]["runtime_manifest"] == "invalid_json_shape"
    assert payload["runtime"]["manifest_present"] is False
    assert payload["run_card"]["next_allowed_action"] == "inspect_unreadable_or_missing_control_files"


def test_workbuddy_diagnose_does_not_route_auditable_target_to_finalize(
    tmp_path: Path,
    capsys,
) -> None:
    ws = _workspace(tmp_path)
    _write_condition(ws)
    intermediate = ws / "output" / "intermediate"
    _write_manifest(intermediate)
    _write_workflow(intermediate, current_stage="finalize", run_integrity={"status": "clean"})
    (intermediate / "artifact_registry.json").write_text(
        json.dumps({"artifacts": {}}),
        encoding="utf-8",
    )

    rc = main(["workbuddy", "diagnose", "--workspace", str(ws), "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["run_card"]["assessment_target"] == "auditable_brief"
    assert payload["run_card"]["assessment_target_status"] == "incomplete"
    assert payload["run_card"]["next_allowed_action"] == "inspect_auditable_brief_target_status"
    assert payload["run_card"]["next_allowed_action"] != "draft_only_run_finalize_when_allowed"


def test_workbuddy_diagnose_keeps_in_progress_auditable_target_on_current_stage(
    tmp_path: Path,
    capsys,
) -> None:
    ws = _workspace(tmp_path)
    _write_condition(ws)
    intermediate = ws / "output" / "intermediate"
    _write_manifest(intermediate)
    _write_workflow(intermediate, current_stage="analyst", run_integrity={"status": "clean"})
    (intermediate / "artifact_registry.json").write_text(
        json.dumps({"artifacts": {}}),
        encoding="utf-8",
    )

    rc = main(["workbuddy", "diagnose", "--workspace", str(ws), "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["run_card"]["assessment_target"] == "auditable_brief"
    assert payload["run_card"]["assessment_target_status"] == "incomplete"
    assert payload["run_card"]["next_allowed_action"] == "continue_current_stage_or_handoff_workflow"


def test_workbuddy_diagnose_stops_on_invalid_experiment_condition(
    tmp_path: Path,
    capsys,
) -> None:
    ws = _workspace(tmp_path)
    condition_path = ws / "experiment" / "080" / "condition.json"
    condition_path.parent.mkdir(parents=True)
    condition_path.write_text(json.dumps(["not", "an", "object"]), encoding="utf-8")
    intermediate = ws / "output" / "intermediate"
    _write_manifest(intermediate)
    _write_workflow(intermediate, current_stage="finalize", run_integrity={"status": "clean"})
    (intermediate / "artifact_registry.json").write_text(
        json.dumps({"artifacts": {}}),
        encoding="utf-8",
    )

    rc = main(["workbuddy", "diagnose", "--workspace", str(ws), "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["assessment_target"]["status"] == "invalid_condition"
    assert payload["assessment_target"]["condition_status"] == "invalid_json_shape"
    assert payload["run_card"]["assessment_target_status"] == "invalid_condition"
    assert payload["run_card"]["next_allowed_action"] == "inspect_invalid_experiment_condition"
    assert payload["run_card"]["next_allowed_action"] != "draft_only_run_finalize_when_allowed"


def test_workbuddy_diagnose_stops_on_invalid_assessment_target_metadata(
    tmp_path: Path,
    capsys,
) -> None:
    for index, invalid_value in enumerate(
        ("auditable-brief", ["auditable_brief"], {"target": "auditable_brief"}),
        start=1,
    ):
        case_dir = tmp_path / f"case_{index}"
        case_dir.mkdir()
        ws = _workspace(case_dir)
        _write_condition(ws, assessment_target=invalid_value)
        intermediate = ws / "output" / "intermediate"
        _write_manifest(intermediate)
        _write_workflow(
            intermediate,
            current_stage="finalize",
            run_integrity={"status": "clean"},
        )
        (intermediate / "artifact_registry.json").write_text(
            json.dumps({"artifacts": {}}),
            encoding="utf-8",
        )

        rc = main(["workbuddy", "diagnose", "--workspace", str(ws), "--json"])

        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["assessment_target"]["status"] == "invalid_condition"
        assert payload["assessment_target"]["reason"] == "unsupported_assessment_target"
        assert payload["run_card"]["assessment_target_status"] == "invalid_condition"
        assert payload["run_card"]["next_allowed_action"] == "inspect_invalid_experiment_condition"
        assert payload["run_card"]["next_allowed_action"] != "draft_only_run_finalize_when_allowed"


def test_workbuddy_diagnose_stops_on_failed_gate_result_even_without_blocking_finding(
    tmp_path: Path,
    capsys,
) -> None:
    ws = _workspace(tmp_path)
    intermediate = ws / "output" / "intermediate"
    gates = intermediate / "gates"
    gates.mkdir()
    _write_manifest(intermediate)
    _write_workflow(intermediate, current_stage="auditor", run_integrity={"status": "clean"})
    (intermediate / "artifact_registry.json").write_text(
        json.dumps({"artifacts": {}}),
        encoding="utf-8",
    )
    (gates / "auditor_quality_gate_report.json").write_text(
        json.dumps(
            _gate_report(
                "warning",
                gate_results=[
                    {
                        "gate_id": "freshness",
                        "status": "fail",
                        "blocking": False,
                        "finding_ids": [],
                    }
                ],
            )
        ),
        encoding="utf-8",
    )

    rc = main(["workbuddy", "diagnose", "--workspace", str(ws), "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert (
        payload["run_card"]["latest_gate_status"]
        == "auditor_quality_gate_report:warning:blocking_findings=1"
    )
    assert payload["run_card"]["next_allowed_action"] == "stop_resolve_blocking_gate_report"
