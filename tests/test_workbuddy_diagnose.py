"""Tests for WorkBuddy read-only diagnosis Run Card."""

from __future__ import annotations

import json
from pathlib import Path

from multi_agent_brief.cli.main import main
from multi_agent_brief.orchestrator.runtime_state import append_event, initialize_runtime_state
from multi_agent_brief.orchestrator.runtime_state._io import _sha256_file


QUALITY_GATE_SCHEMA = "multi-agent-brief-quality-gates/v1"
ARTIFACT_REGISTRY_SCHEMA = "multi-agent-brief-artifact-registry/v1"
ROOT = Path(__file__).resolve().parent.parent


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


def _intermediate(ws: Path) -> Path:
    path = ws / "output" / "intermediate"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _init_runtime(
    ws: Path,
    *,
    current_stage: str = "finalize",
    run_integrity: dict | None = None,
    blocked: bool = False,
) -> dict:
    state = initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    workflow_path = _intermediate(ws) / "workflow_state.json"
    workflow = _load_json(workflow_path)
    workflow["current_stage"] = current_stage
    workflow["blocked"] = blocked
    workflow["run_integrity"] = run_integrity or {"status": "clean"}
    _write_json(workflow_path, workflow)
    _write_json(
        _intermediate(ws) / "artifact_registry.json",
        {
            "schema_version": ARTIFACT_REGISTRY_SCHEMA,
            "run_id": state["manifest"]["run_id"],
            "updated_at": "2026-07-06T00:00:00+00:00",
            "artifacts": {},
        },
    )
    return state


def _write_finalized_delivery(ws: Path) -> None:
    intermediate = _intermediate(ws)
    claim_ledger = intermediate / "claim_ledger.json"
    claim_ledger.write_text('{"claims":[]}\n', encoding="utf-8")
    audited_brief = intermediate / "audited_brief.md"
    audited_brief.write_text("# Audited Brief\n\nClean audited text.\n", encoding="utf-8")
    audit_report = intermediate / "audit_report.json"
    audit_report.write_text('{"status":"pass","findings":[]}\n', encoding="utf-8")
    root_brief = ws / "output" / "brief.md"
    root_brief.parent.mkdir(parents=True, exist_ok=True)
    root_brief.write_text("# Reader Brief\n\nClean reader text.\n", encoding="utf-8")
    delivery_dir = ws / "output" / "delivery"
    delivery_dir.mkdir(parents=True, exist_ok=True)
    delivery_brief = delivery_dir / "brief.md"
    delivery_brief.write_text("# Reader Brief\n\nClean reader text.\n", encoding="utf-8")
    _write_json(
        intermediate / "finalize_report.json",
        {
            "status": "pass",
            "finalize_transaction_id": "tx-finalize-001",
            "audited_brief": "output/intermediate/audited_brief.md",
            "reader_brief": "output/brief.md",
            "reader_clean": {"status": "pass", "sample_findings": []},
            "delivery_latest_dir": "output/delivery",
            "delivery_artifacts": ["output/delivery/brief.md"],
            "delivery_artifact_sha256": {"output/delivery/brief.md": _sha256_file(delivery_brief)},
            "delivery_promotion": "promoted",
            "audit_binding": {
                "status": "pass",
                "claim_ledger_sha256": _sha256_file(claim_ledger),
                "audited_brief_sha256": _sha256_file(audited_brief),
                "audit_report_sha256": _sha256_file(audit_report),
                "findings": [],
                "warnings": [],
            },
        },
    )
    _write_json(
        intermediate / "gates" / "finalize_quality_gate_report.json",
        _gate_report("pass"),
    )
    manifest = _load_json(intermediate / "runtime_manifest.json")
    append_event(
        workspace=ws,
        run_id=manifest["run_id"],
        event_type="decision_recorded",
        actor="cli",
        stage_id="finalize",
        decision="finalize",
        reason="finalize-complete recorded",
    )


def _gate_report(status: str) -> dict[str, object]:
    return {
        "schema_version": QUALITY_GATE_SCHEMA,
        "created_at": "2026-07-06T00:00:00+00:00",
        "updated_at": "2026-07-06T00:00:00+00:00",
        "workspace": ".",
        "report_date": "2026-07-06",
        "policy_pack": "default",
        "status": status,
        "gate_results": [
            {"gate_id": "coverage_omission", "status": "pass", "blocking": False, "finding_ids": []},
            {"gate_id": "freshness", "status": "pass", "blocking": False, "finding_ids": []},
            {"gate_id": "material_fact", "status": "pass", "blocking": False, "finding_ids": []},
            {"gate_id": "target_relevance", "status": status, "blocking": False, "finding_ids": []},
        ],
        "findings": [],
        "metadata": {
            "stage_id": "finalize",
            "gate_stage_id": "finalize",
            "gate_artifact_id": "finalize_quality_gate_report",
            "brief": "output/brief.md",
            "ledger": "output/intermediate/claim_ledger.json",
        },
    }


def _write_blocking_gate_report(ws: Path) -> None:
    report = _gate_report("fail")
    report["gate_results"][-1] = {
        "gate_id": "target_relevance",
        "status": "fail",
        "blocking": True,
        "finding_ids": ["QG_TARGET_RELEVANCE_001"],
    }
    report["findings"] = [
        {
            "finding_id": "QG_TARGET_RELEVANCE_001",
            "finding_type": "target_relevance_gap",
            "severity": "high",
            "blocking_level": "blocking",
            "blocking": True,
            "stage_id": "editor",
            "gate_stage_id": "finalize",
            "artifact_id": "audited_brief",
            "gate_artifact_id": "finalize_quality_gate_report",
            "repair_stage_id": "editor",
            "repair_artifact_id": "audited_brief",
            "repair_owner": "editor",
            "message": "Executive summary does not mention the configured target.",
            "metadata": {},
        }
    ]
    _write_json(_intermediate(ws) / "gates" / "finalize_quality_gate_report.json", report)


def _write_nonblocking_repairable_gate_report(ws: Path) -> None:
    report = _gate_report("pass")
    report["findings"] = [
        {
            "finding_id": "QG_WARNING_001",
            "finding_type": "unsupported_claim",
            "severity": "medium",
            "blocking_level": "warning",
            "blocking": False,
            "stage_id": "editor",
            "gate_stage_id": "finalize",
            "artifact_id": "audited_brief",
            "gate_artifact_id": "finalize_quality_gate_report",
            "repair_stage_id": "editor",
            "repair_artifact_id": "audited_brief",
            "repair_owner": "editor",
            "message": "A nonblocking editor warning remains.",
            "metadata": {},
        }
    ]
    _write_json(_intermediate(ws) / "gates" / "finalize_quality_gate_report.json", report)


def _diagnose_json(ws: Path, capsys) -> dict:
    rc = main(["workbuddy", "diagnose", "--workspace", str(ws), "--json"])
    assert rc == 0
    return json.loads(capsys.readouterr().out)


def test_workbuddy_diagnose_formats_completion_projection(tmp_path: Path, capsys) -> None:
    ws = _workspace(tmp_path)
    _init_runtime(ws)
    _write_finalized_delivery(ws)

    payload = _diagnose_json(ws, capsys)

    projection = payload["completion_projection"]
    assert payload["schema_version"] == "briefloop.workbuddy_diagnose.v1"
    assert payload["runtime_effect"] == "read_only_diagnostic"
    assert payload["run_card"]["next_allowed_action"] == projection["next_allowed_action"]
    assert payload["run_card"]["delivery_valid"] is True
    assert payload["run_card"]["delivery_truth"] == projection["delivery_truth"]["status"]
    assert payload["delivery_truth"] == projection["delivery_truth"]
    assert payload["delivery_truth"]["valid"] is True
    assert payload["delivery"]["truth"] == projection["delivery_truth"]
    assert payload["finalize"]["truth"] == projection["finalize_truth"]
    assert payload["run_card"]["next_allowed_action"] == "inspect_status_before_delivery_or_quality"


def test_workbuddy_diagnose_repeats_repair_route_projection(tmp_path: Path, capsys) -> None:
    ws = _workspace(tmp_path)
    _init_runtime(ws)
    _write_finalized_delivery(ws)
    _write_blocking_gate_report(ws)

    payload = _diagnose_json(ws, capsys)

    projection = payload["completion_projection"]
    assert projection["repair_route"]["route_kind"] == "owner_stage_repair"
    assert payload["repair_route"] == projection["repair_route"]
    assert payload["run_card"]["repair_route"] == "owner_stage_repair:owner_stage_repair"
    assert payload["run_card"]["repair_owner"] == "editor"
    assert payload["run_card"]["repair_must_rerun_from"] == "auditor"
    assert payload["run_card"]["next_allowed_action"] == "inspect_repair_route_for_blocking_gate"


def test_workbuddy_diagnose_does_not_show_repair_route_for_nonblocking_gate(tmp_path: Path, capsys) -> None:
    ws = _workspace(tmp_path)
    _init_runtime(ws)
    _write_finalized_delivery(ws)
    _write_nonblocking_repairable_gate_report(ws)

    payload = _diagnose_json(ws, capsys)

    projection = payload["completion_projection"]
    assert projection["gate_truth"]["blocking"] is False
    assert projection["repair_route"]["route_kind"] == "none"
    assert payload["repair_route"] == projection["repair_route"]
    assert payload["run_card"]["repair_route"] == "none:none"
    assert payload["run_card"]["repair_owner"] == "none"
    assert payload["run_card"]["next_allowed_action"] == "inspect_status_before_delivery_or_quality"


def test_workbuddy_diagnose_doctor_error_overlays_completion_next_action(tmp_path: Path, capsys) -> None:
    ws = _workspace(tmp_path)
    _init_runtime(ws)
    _write_finalized_delivery(ws)
    (ws / "config.yaml").unlink()

    payload = _diagnose_json(ws, capsys)

    projection = payload["completion_projection"]
    assert projection["delivery_truth"]["valid"] is True
    assert projection["next_allowed_action"] == "inspect_status_before_delivery_or_quality"
    assert payload["doctor"]["status"] == "error"
    assert payload["doctor"]["errors"] == ["config.yaml missing"]
    assert payload["run_card"]["delivery_valid"] is True
    assert payload["run_card"]["delivery_truth"] == projection["delivery_truth"]["status"]
    assert payload["run_card"]["next_allowed_action"] == "stop_show_full_doctor_output"


def test_workbuddy_diagnose_does_not_infer_delivery_from_directory(tmp_path: Path, capsys) -> None:
    ws = _workspace(tmp_path)
    _init_runtime(ws, current_stage="finalize")
    delivery_dir = ws / "output" / "delivery"
    delivery_dir.mkdir(parents=True)
    (delivery_dir / "brief.md").write_text("# Stale orphan delivery\n", encoding="utf-8")

    payload = _diagnose_json(ws, capsys)

    projection = payload["completion_projection"]
    assert projection["delivery_truth"]["valid"] is False
    assert payload["delivery_truth"] == projection["delivery_truth"]
    assert payload["delivery_truth"]["valid"] is False
    assert payload["run_card"]["delivery_valid"] is False
    assert payload["run_card"]["delivery_truth"] == "not_valid"
    assert payload["run_card"]["next_allowed_action"] == projection["next_allowed_action"]
    assert payload["run_card"]["next_allowed_action"] != "inspect_status_before_delivery_or_quality"


def test_workbuddy_diagnose_follows_projection_for_dirty_delivery(tmp_path: Path, capsys) -> None:
    ws = _workspace(tmp_path)
    (ws / ".env").write_text("TAVILY_API_KEY=redacted-test-secret\n", encoding="utf-8")
    _init_runtime(ws)
    _write_finalized_delivery(ws)
    (ws / "output" / "delivery" / "brief.md").write_text("# Tampered\n", encoding="utf-8")

    payload = _diagnose_json(ws, capsys)

    projection = payload["completion_projection"]
    assert projection["delivery_truth"]["valid"] is False
    assert payload["run_card"]["delivery_valid"] is False
    assert payload["run_card"]["next_allowed_action"] == projection["next_allowed_action"]
    assert payload["run_card"]["next_allowed_action"] == "inspect_invalid_or_incomplete_finalize_report_delivery_truth"
    assert payload["run_card"]["secret_risk_present"] is True


def test_workbuddy_diagnose_secret_risk_overlays_only_benign_completion_action(tmp_path: Path, capsys) -> None:
    ws = _workspace(tmp_path)
    secret_value = "tvly-secret-value"
    (ws / ".env").write_text(f"TAVILY_API_KEY={secret_value}\n", encoding="utf-8")
    _init_runtime(ws)
    _write_finalized_delivery(ws)

    rc = main(["workbuddy", "diagnose", "--workspace", str(ws), "--json"])

    assert rc == 0
    raw = capsys.readouterr().out
    assert secret_value not in raw
    payload = json.loads(raw)
    projection = payload["completion_projection"]
    assert projection["next_allowed_action"] == "inspect_status_before_delivery_or_quality"
    assert payload["run_card"]["delivery_truth"] == projection["delivery_truth"]["status"]
    assert payload["run_card"]["next_allowed_action"] == "do_not_share_workspace_zip_secret_risk"
    assert payload["run_card"]["secret_risk_present"] is True
    assert payload["run_card"]["share_workspace_zip_allowed"] is False
    assert payload["secret_risk"]["nonempty_env_keys"] == ["TAVILY_API_KEY"]
    assert payload["secret_risk"]["secret_values_reported"] is False


def test_workbuddy_diagnose_json_fails_soft_on_corrupt_utf8_control_json(tmp_path: Path, capsys) -> None:
    ws = _workspace(tmp_path)
    _init_runtime(ws, current_stage="doctor")
    (ws / "output" / "intermediate" / "workflow_state.json").write_bytes(b"\xff\xfe\x00")

    payload = _diagnose_json(ws, capsys)

    projection = payload["completion_projection"]
    assert projection["control_files"]["workflow_state"] == "unreadable_utf8"
    assert payload["workflow"]["workflow_state_status"] == "unreadable_utf8"
    assert payload["run_card"]["next_allowed_action"] == projection["next_allowed_action"]
    assert payload["run_card"]["next_allowed_action"] == "inspect_unreadable_or_missing_control_files"


def test_workbuddy_diagnose_text_prints_projection_run_card(tmp_path: Path, capsys) -> None:
    ws = _workspace(tmp_path)
    _init_runtime(ws, current_stage="doctor")

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
        "delivery_truth:",
        "finalize_event:",
        "delivery_event:",
        "share_workspace_zip_allowed:",
        "next_allowed_action:",
    ):
        assert field in output
    assert "delivery complete" not in output.lower()
    assert "delivered" not in output.lower()
    assert "read_only_workbuddy_run_card_formats_completion_projection_with_workbuddy_safety_overlay" in output
    assert "Doctor: not_run_read_only" in output
    assert "Output directory writable" not in output
    assert "Output directory not writable" not in output
