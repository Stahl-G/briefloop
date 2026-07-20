"""Tests for WorkBuddy read-only diagnosis Run Card."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from multi_agent_brief.cli.main import main
from multi_agent_brief.orchestrator.runtime_state import append_event, initialize_runtime_state
from multi_agent_brief.orchestrator.runtime_state._io import _sha256_file
from multi_agent_brief.workbuddy.diagnose import (
    build_workbuddy_diagnosis,
    format_workbuddy_diagnosis,
)


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
    state = initialize_runtime_state(runtime="operator", workspace=ws, repo_workdir=ROOT)
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


def _diagnose_json(ws: Path) -> dict:
    # Direct deterministic seam behind the retired public `workbuddy diagnose`
    # CLI: the command only rendered this read-only projection.
    return build_workbuddy_diagnosis(workspace=ws)


def _workspace_file_bytes(ws: Path) -> dict[str, bytes]:
    return {
        path.relative_to(ws).as_posix(): path.read_bytes()
        for path in ws.rglob("*")
        if path.is_file()
    }


@pytest.mark.parametrize("json_mode", [True, False], ids=["json", "text"])
def test_workbuddy_diagnose_public_cli_is_retired_with_zero_writes(
    tmp_path: Path,
    capsys,
    json_mode: bool,
) -> None:
    # retired public `workbuddy diagnose` CLI; the read-only
    # Run Card projection is only reachable through the direct
    # build_workbuddy_diagnosis/format_workbuddy_diagnosis seam.
    ws = _workspace(tmp_path)
    _init_runtime(ws)
    before_files = _workspace_file_bytes(ws)

    argv = ["workbuddy", "diagnose", "--workspace", str(ws)]
    if json_mode:
        argv.append("--json")
    rc = main(argv)

    assert rc == 1
    assert capsys.readouterr().out.strip() == "legacy_workspace_unsupported"
    assert _workspace_file_bytes(ws) == before_files


def test_workbuddy_diagnose_formats_completion_projection(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    _init_runtime(ws)
    _write_finalized_delivery(ws)

    payload = _diagnose_json(ws)

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
    assert payload["recovery_state"] == projection["recovery_state"]
    assert payload["run_card"]["recovery_status"] == projection["recovery_state"]["status"]
    assert payload["run_card"]["recovery_action"] == projection["recovery_state"][
        "recommended_recovery_action"
    ]
    assert payload["delivery"]["outcome"] == projection["event_truth"]["delivery_outcome"]
    assert payload["run_card"]["next_allowed_action"] == "inspect_status_before_delivery_or_quality"


@pytest.mark.parametrize(
    "event_type",
    [
        "delivery_bundle_prepared",
        "delivery_draft_created",
        "delivery_succeeded",
    ],
    ids=["local-prepared", "gmail-draft", "external-send-success"],
)
def test_workbuddy_diagnose_projects_exact_current_delivery_outcome(
    tmp_path: Path,
    event_type: str,
) -> None:
    ws = _workspace(tmp_path)
    state = _init_runtime(ws)
    _write_finalized_delivery(ws)
    append_event(
        workspace=ws,
        run_id=state["manifest"]["run_id"],
        event_type=event_type,
        actor="cli",
        reason=event_type,
        metadata={"render_transaction_id": "tx-finalize-001"},
    )

    payload = _diagnose_json(ws)

    assert payload["delivery_truth"]["valid"] is True
    assert payload["event_truth"]["delivery_outcome"] == event_type
    assert payload["delivery"]["outcome"] == event_type
    assert payload["run_card"]["delivery_event"] == event_type


def test_workbuddy_diagnose_doctor_error_overlays_completion_next_action(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    _init_runtime(ws)
    _write_finalized_delivery(ws)
    (ws / "config.yaml").unlink()

    payload = _diagnose_json(ws)

    projection = payload["completion_projection"]
    assert projection["delivery_truth"]["valid"] is True
    assert projection["next_allowed_action"] == "inspect_status_before_delivery_or_quality"
    assert payload["doctor"]["status"] == "error"
    assert payload["doctor"]["errors"] == ["config.yaml missing"]
    assert payload["run_card"]["delivery_valid"] is True
    assert payload["run_card"]["delivery_truth"] == projection["delivery_truth"]["status"]
    assert payload["run_card"]["next_allowed_action"] == "stop_show_full_doctor_output"


def test_workbuddy_diagnose_does_not_infer_delivery_from_directory(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    _init_runtime(ws, current_stage="finalize")
    delivery_dir = ws / "output" / "delivery"
    delivery_dir.mkdir(parents=True)
    (delivery_dir / "brief.md").write_text("# Stale orphan delivery\n", encoding="utf-8")

    payload = _diagnose_json(ws)

    projection = payload["completion_projection"]
    assert projection["delivery_truth"]["valid"] is False
    assert payload["delivery_truth"] == projection["delivery_truth"]
    assert payload["delivery_truth"]["valid"] is False
    assert payload["run_card"]["delivery_valid"] is False
    assert payload["run_card"]["delivery_truth"] == "not_valid"
    assert payload["run_card"]["next_allowed_action"] == projection["next_allowed_action"]
    assert payload["run_card"]["next_allowed_action"] != "inspect_status_before_delivery_or_quality"


def test_workbuddy_diagnose_follows_projection_for_dirty_delivery(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    (ws / ".env").write_text("TAVILY_API_KEY=redacted-test-secret\n", encoding="utf-8")
    _init_runtime(ws)
    _write_finalized_delivery(ws)
    (ws / "output" / "delivery" / "brief.md").write_text("# Tampered\n", encoding="utf-8")

    payload = _diagnose_json(ws)

    projection = payload["completion_projection"]
    assert projection["delivery_truth"]["valid"] is False
    assert payload["run_card"]["delivery_valid"] is False
    assert payload["run_card"]["next_allowed_action"] == projection["next_allowed_action"]
    assert payload["run_card"]["next_allowed_action"] == "inspect_invalid_or_incomplete_finalize_report_delivery_truth"
    assert payload["run_card"]["secret_risk_present"] is True


def test_workbuddy_diagnose_secret_risk_overlays_only_benign_completion_action(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    secret_value = "tvly-secret-value"
    (ws / ".env").write_text(f"TAVILY_API_KEY={secret_value}\n", encoding="utf-8")
    _init_runtime(ws)
    _write_finalized_delivery(ws)

    payload = _diagnose_json(ws)

    # The serialized projection must never leak secret values (the retired CLI
    # printed exactly this payload).
    raw = json.dumps(payload, indent=2, sort_keys=True)
    assert secret_value not in raw
    projection = payload["completion_projection"]
    assert projection["next_allowed_action"] == "inspect_status_before_delivery_or_quality"
    assert payload["run_card"]["delivery_truth"] == projection["delivery_truth"]["status"]
    assert payload["run_card"]["next_allowed_action"] == "do_not_share_workspace_zip_secret_risk"
    assert payload["run_card"]["secret_risk_present"] is True
    assert payload["run_card"]["share_workspace_zip_allowed"] is False
    assert payload["secret_risk"]["nonempty_env_keys"] == ["TAVILY_API_KEY"]
    assert payload["secret_risk"]["secret_values_reported"] is False


def test_workbuddy_diagnose_json_fails_soft_on_corrupt_utf8_control_json(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    _init_runtime(ws, current_stage="doctor")
    (ws / "output" / "intermediate" / "workflow_state.json").write_bytes(b"\xff\xfe\x00")

    payload = _diagnose_json(ws)

    projection = payload["completion_projection"]
    assert projection["control_files"]["workflow_state"] == "unreadable_utf8"
    assert payload["workflow"]["workflow_state_status"] == "unreadable_utf8"
    assert payload["run_card"]["next_allowed_action"] == projection["next_allowed_action"]
    assert payload["run_card"]["next_allowed_action"] == "inspect_unreadable_or_missing_control_files"


def test_workbuddy_diagnose_text_prints_projection_run_card(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    _init_runtime(ws, current_stage="doctor")

    # Direct seam for the retired text renderer: the CLI printed exactly this.
    output = format_workbuddy_diagnosis(_diagnose_json(ws))
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
