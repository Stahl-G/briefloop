"""Tests for WorkBuddy read-only diagnosis Run Card."""

from __future__ import annotations

import json
from pathlib import Path

from multi_agent_brief.cli.main import main


EVENT_LOG_SCHEMA = "multi-agent-brief-event-log/v1"
QUALITY_GATE_SCHEMA = "multi-agent-brief-quality-gates/v1"


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


def _gate_report(status: str, *, blocking: bool = False, warning: bool = False) -> dict[str, object]:
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
        "gate_results": [],
        "findings": findings,
    }


def test_workbuddy_diagnose_json_reports_run_card_and_secret_risk(
    tmp_path: Path,
    capsys,
) -> None:
    ws = _workspace(tmp_path)
    secret_value = "tvly-secret-value"
    (ws / ".env").write_text(f"TAVILY_API_KEY={secret_value}\n", encoding="utf-8")
    (ws / "output" / "intermediate" / "runtime_manifest.json").write_text(
        json.dumps({"runtime": "codebuddy", "runtime_capabilities": {"delegation_supported": True}}),
        encoding="utf-8",
    )
    (ws / "output" / "intermediate" / "workflow_state.json").write_text(
        json.dumps(
            {
                "current_stage": "finalize",
                "blocked": False,
                "run_integrity": {"status": "contaminated"},
            }
        ),
        encoding="utf-8",
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
        "run_integrity": "contaminated",
        "blocked": False,
        "latest_gate_status": "unknown",
        "finalize_report": "missing",
        "delivery_dir": "missing",
        "finalize_event": "missing",
        "delivery_event": "missing",
        "share_workspace_zip_allowed": False,
        "next_allowed_action": "stop_run_integrity_not_clean",
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
        "run_integrity:",
        "blocked:",
        "latest_gate_status:",
        "finalize_report:",
        "delivery_dir:",
        "finalize_event:",
        "delivery_event:",
        "share_workspace_zip_allowed:",
        "next_allowed_action:",
    ):
        assert field in output
    assert "delivery complete" not in output.lower()
    assert "delivered" not in output.lower()
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
    (ws / "output" / "delivery").mkdir(parents=True)
    (intermediate / "runtime_manifest.json").write_text(
        json.dumps({"runtime": "codebuddy"}),
        encoding="utf-8",
    )
    (intermediate / "workflow_state.json").write_text(
        json.dumps({"current_stage": "finalize", "run_integrity": {"status": "clean"}}),
        encoding="utf-8",
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
    raw = capsys.readouterr().out
    assert "secret-value" not in raw
    payload = json.loads(raw)
    assert payload["run_card"]["finalize_report"] == "present"
    assert payload["run_card"]["delivery_dir"] == "present"
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
    (ws / "output" / "delivery").mkdir(parents=True)
    (intermediate / "runtime_manifest.json").write_text(
        json.dumps({"runtime": "codebuddy"}),
        encoding="utf-8",
    )
    (intermediate / "workflow_state.json").write_text(
        json.dumps({"current_stage": "finalize", "run_integrity": {"status": "clean"}}),
        encoding="utf-8",
    )
    (intermediate / "artifact_registry.json").write_text(
        json.dumps({"artifacts": {}}),
        encoding="utf-8",
    )
    (intermediate / "finalize_report.json").write_text(
        json.dumps({"status": "pass"}),
        encoding="utf-8",
    )

    rc = main(["workbuddy", "diagnose", "--workspace", str(ws), "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["run_card"]["finalize_report"] == "present"
    assert payload["run_card"]["delivery_dir"] == "present"
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
    (intermediate / "runtime_manifest.json").write_text(
        json.dumps({"runtime": "codebuddy"}),
        encoding="utf-8",
    )
    (intermediate / "workflow_state.json").write_text(
        json.dumps({"current_stage": "auditor", "run_integrity": {"status": "clean"}}),
        encoding="utf-8",
    )
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
    (intermediate / "runtime_manifest.json").write_text(
        json.dumps({"runtime": "codebuddy"}),
        encoding="utf-8",
    )
    (intermediate / "workflow_state.json").write_text(
        json.dumps({"current_stage": "auditor", "run_integrity": {"status": "clean"}}),
        encoding="utf-8",
    )
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
    (intermediate / "runtime_manifest.json").write_text(
        json.dumps({"runtime": "codebuddy"}),
        encoding="utf-8",
    )
    (intermediate / "workflow_state.json").write_text(
        json.dumps({"current_stage": "auditor", "run_integrity": {"status": "clean"}}),
        encoding="utf-8",
    )
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
    (intermediate / "runtime_manifest.json").write_text(
        json.dumps({"runtime": "codebuddy"}),
        encoding="utf-8",
    )
    (intermediate / "workflow_state.json").write_text(
        json.dumps({"current_stage": "doctor", "run_integrity": {"status": "clean"}}),
        encoding="utf-8",
    )
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
    (ws / "output" / "delivery").mkdir(parents=True)
    (intermediate / "runtime_manifest.json").write_text(
        json.dumps({"runtime": "codebuddy"}),
        encoding="utf-8",
    )
    (intermediate / "workflow_state.json").write_text(
        json.dumps({"current_stage": "finalize", "run_integrity": {"status": "clean"}}),
        encoding="utf-8",
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
    assert payload["run_card"]["next_allowed_action"] == "inspect_status_before_delivery_or_quality"


def test_workbuddy_diagnose_uses_shared_run_integrity_interpreter(
    tmp_path: Path,
    capsys,
) -> None:
    ws = _workspace(tmp_path)
    intermediate = ws / "output" / "intermediate"
    (intermediate / "runtime_manifest.json").write_text(
        json.dumps({"runtime": "codebuddy"}),
        encoding="utf-8",
    )
    (intermediate / "workflow_state.json").write_text(
        json.dumps(
            {
                "current_stage": "finalize",
                "run_integrity": {
                    "status": "clean",
                    "reference_eligible": False,
                    "clean_single_shot": True,
                    "reasons": [],
                },
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
    assert payload["run_card"]["run_integrity"] == "unknown"
    assert payload["workflow"]["run_integrity"]["reasons"] == [
        {
            "reason_code": "run_integrity_clean_not_reference_eligible",
            "message": "workflow_state.run_integrity clean runs must be reference eligible.",
        }
    ]
    assert payload["run_card"]["next_allowed_action"] == "stop_run_integrity_not_clean"


def test_workbuddy_diagnose_stops_on_blocked_workflow_state(
    tmp_path: Path,
    capsys,
) -> None:
    ws = _workspace(tmp_path)
    intermediate = ws / "output" / "intermediate"
    (ws / "output" / "delivery").mkdir(parents=True)
    (intermediate / "runtime_manifest.json").write_text(
        json.dumps({"runtime": "codebuddy"}),
        encoding="utf-8",
    )
    (intermediate / "workflow_state.json").write_text(
        json.dumps(
            {
                "current_stage": "finalize",
                "blocked": True,
                "blocking_reason": "human review required",
                "run_integrity": {"status": "clean"},
            }
        ),
        encoding="utf-8",
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
    (intermediate / "runtime_manifest.json").write_text(
        json.dumps({"runtime": "codebuddy"}),
        encoding="utf-8",
    )
    (intermediate / "workflow_state.json").write_text(
        json.dumps({"current_stage": "auditor", "run_integrity": {"status": "clean"}}),
        encoding="utf-8",
    )
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
    (intermediate / "runtime_manifest.json").write_text(
        json.dumps({"runtime": "codebuddy"}),
        encoding="utf-8",
    )
    (intermediate / "workflow_state.json").write_text(
        json.dumps({"current_stage": "finalize", "run_integrity": {"status": "clean"}}),
        encoding="utf-8",
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
