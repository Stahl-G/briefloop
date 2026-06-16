from __future__ import annotations

import json
from pathlib import Path

from multi_agent_brief.cli.main import main
from multi_agent_brief.orchestrator.runtime_state import (
    check_runtime_state,
    initialize_runtime_state,
    runtime_state_paths,
    utc_now,
)


def _workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "config.yaml").write_text("project:\n  name: repair-route-test\n", encoding="utf-8")
    (ws / "sources.yaml").write_text("manual:\n  sources: []\n", encoding="utf-8")
    (ws / "user.md").write_text("# Repair route test\n", encoding="utf-8")
    return ws


def _intermediate(ws: Path) -> Path:
    path = ws / "output" / "intermediate"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_audit_report(ws: Path, finding: dict[str, object]) -> None:
    (_intermediate(ws) / "audit_report.json").write_text(
        json.dumps(
            {
                "audit_status": "fail",
                "audit_score": 40,
                "findings": [finding],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _write_quality_gate_report(ws: Path, finding: dict[str, object]) -> None:
    path = _intermediate(ws) / "gates" / "auditor_quality_gate_report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": "multi-agent-brief-quality-gates/v1",
                "status": "fail",
                "findings": [finding],
                "metadata": {"gate_stage_id": "auditor"},
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _write_legacy_quality_gate_report(ws: Path, finding: dict[str, object]) -> None:
    path = _intermediate(ws) / "quality_gate_report.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "multi-agent-brief-quality-gates/v1",
                "status": "fail",
                "findings": [finding],
                "metadata": {"gate_stage_id": "auditor"},
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _set_workflow_stages(ws: Path, *, completed: list[str], current_stage: str) -> None:
    path = runtime_state_paths(ws)["workflow_state"]
    workflow = json.loads(path.read_text(encoding="utf-8"))
    now = utc_now()
    statuses = {}
    for stage_id in workflow.get("stage_statuses") or {}:
        if stage_id in completed:
            statuses[stage_id] = {"status": "complete", "reason": f"{stage_id} fixture complete", "updated_at": now}
        elif stage_id == current_stage:
            statuses[stage_id] = {"status": "ready", "reason": "", "updated_at": now}
        else:
            statuses[stage_id] = {"status": "pending", "reason": "", "updated_at": now}
    workflow["current_stage"] = current_stage
    workflow["blocked"] = False
    workflow["blocking_reason"] = ""
    workflow["updated_at"] = now
    workflow["stage_statuses"] = statuses
    path.write_text(json.dumps(workflow, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_valid_claim_ledger(ws: Path, statement: str = "ExampleCo opened a demo facility.") -> None:
    (_intermediate(ws) / "claim_ledger.json").write_text(
        json.dumps(
            [
                {
                    "claim_id": "CL-0001",
                    "statement": statement,
                    "source_id": "SRC-001",
                    "evidence_text": "Example evidence.",
                    "source_url": "https://example.com",
                    "source_type": "web_search",
                }
            ],
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def test_repair_route_maps_unsupported_claim_to_audited_brief(tmp_path, capsys):
    ws = _workspace(tmp_path)
    initialize_runtime_state(workspace=ws)
    before_events = runtime_state_paths(ws)["event_log"].read_bytes()
    _write_audit_report(
        ws,
        {
            "finding_id": "AUDIT_001",
            "finding_type": "unsupported_claim",
            "severity": "high",
            "artifact_id": "audited_brief",
            "description": "Claim in audited brief is unsupported by the ledger.",
        },
    )

    rc = main(["repair", "route", "--workspace", str(ws), "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["repair_owner"] == "editor"
    assert payload["allowed_artifacts"] == ["output/intermediate/audited_brief.md"]
    assert payload["must_rerun_from"] == "auditor"
    assert "output/intermediate/audit_report.json" in payload["blocked_direct_edits"]
    assert not (ws / "output" / "intermediate" / "repair_plan.json").exists()
    assert runtime_state_paths(ws)["event_log"].read_bytes() == before_events


def test_repair_route_maps_frozen_audited_brief_change_to_editor(tmp_path, capsys):
    ws = _workspace(tmp_path)
    initialize_runtime_state(workspace=ws)
    _write_valid_claim_ledger(ws)
    (_intermediate(ws) / "audited_brief.md").write_text("# Brief\n\nOriginal editor text.\n", encoding="utf-8")
    _set_workflow_stages(
        ws,
        completed=["doctor", "source-discovery", "input-governance", "scout", "screener", "claim-ledger", "analyst", "editor"],
        current_stage="auditor",
    )
    check_runtime_state(workspace=ws)
    workflow_before = runtime_state_paths(ws)["workflow_state"].read_bytes()
    registry_before = runtime_state_paths(ws)["artifact_registry"].read_bytes()
    event_log_before = runtime_state_paths(ws)["event_log"].read_bytes()
    (_intermediate(ws) / "audited_brief.md").write_text("# Brief\n\nChanged downstream patch.\n", encoding="utf-8")

    rc = main(["repair", "route", "--workspace", str(ws), "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["repair_owner"] == "editor"
    assert payload["allowed_artifacts"] == ["output/intermediate/audited_brief.md"]
    assert payload["must_rerun_from"] == "auditor"
    assert payload["run_integrity_effect"]["reference_eligible"] is False
    assert payload["source"]["kind"] == "transaction_integrity"
    assert payload["source"]["finding_type"] == "frozen_artifact_changed"
    assert runtime_state_paths(ws)["workflow_state"].read_bytes() == workflow_before
    assert runtime_state_paths(ws)["artifact_registry"].read_bytes() == registry_before
    assert runtime_state_paths(ws)["event_log"].read_bytes() == event_log_before


def test_repair_route_maps_frozen_claim_ledger_change_to_claim_ledger(tmp_path, capsys):
    ws = _workspace(tmp_path)
    initialize_runtime_state(workspace=ws)
    _write_valid_claim_ledger(ws)
    _set_workflow_stages(
        ws,
        completed=["doctor", "source-discovery", "input-governance", "scout", "screener", "claim-ledger"],
        current_stage="analyst",
    )
    check_runtime_state(workspace=ws)
    registry_before = runtime_state_paths(ws)["artifact_registry"].read_bytes()
    _write_valid_claim_ledger(ws, statement="Changed ledger text.")

    rc = main(["repair", "route", "--workspace", str(ws), "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["repair_owner"] == "claim-ledger"
    assert payload["allowed_artifacts"] == ["output/intermediate/claim_ledger.json"]
    assert payload["must_rerun_from"] == "analyst"
    assert payload["run_integrity_effect"]["reference_eligible"] is False
    assert runtime_state_paths(ws)["artifact_registry"].read_bytes() == registry_before


def test_repair_route_maps_claim_ledger_invalid_registry_to_claim_ledger(tmp_path, capsys):
    ws = _workspace(tmp_path)
    initialize_runtime_state(workspace=ws)
    registry_path = runtime_state_paths(ws)["artifact_registry"]
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": "multi-agent-brief-artifact-registry/v1",
                "run_id": "run-test",
                "artifacts": {
                    "claim_ledger": {
                        "artifact_id": "claim_ledger",
                        "path": "output/intermediate/claim_ledger.json",
                        "status": "invalid",
                        "validation_result": "claim_ledger_schema_error:claim[0].evidence_text",
                        "blocking_reason": "Claim Ledger missing evidence_text.",
                    }
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    rc = main(["repair", "route", "--workspace", str(ws), "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["repair_owner"] == "claim-ledger"
    assert payload["allowed_artifacts"] == ["output/intermediate/claim_ledger.json"]
    assert payload["must_rerun_from"] == "analyst"
    assert "output/intermediate/audited_brief.md" in payload["blocked_direct_edits"]


def test_repair_route_maps_missing_source_excerpt_to_source_discovery(tmp_path, capsys):
    ws = _workspace(tmp_path)
    initialize_runtime_state(workspace=ws)
    _write_quality_gate_report(
        ws,
        {
            "finding_id": "QG_SOURCE_001",
            "finding_type": "source_pack_missing_raw_excerpt",
            "severity": "high",
            "artifact_id": "candidate_claims",
            "message": "Source pack missing raw excerpt/snippet for cited item.",
        },
    )

    rc = main(["repair", "route", "--workspace", str(ws), "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["repair_owner"] == "source-discovery"
    assert payload["allowed_artifacts"] == ["input/sources/*"]
    assert payload["must_rerun_from"] == "input-governance"
    assert "output/intermediate/claim_ledger.json" in payload["blocked_direct_edits"]


def test_repair_route_prefers_source_discovery_metadata_over_text_heuristic(tmp_path, capsys):
    ws = _workspace(tmp_path)
    initialize_runtime_state(workspace=ws)
    _write_quality_gate_report(
        ws,
        {
            "finding_id": "QG_MATERIAL_FACT_001",
            "finding_type": "needs_recrawl_claim_used",
            "severity": "high",
            "artifact_id": "claim_ledger",
            "repair_owner": "source-discovery",
            "repair_stage_id": "source-discovery",
            "repair_artifact_id": "claim_ledger",
            "message": "Claim Ledger cites a source marked needs_recrawl.",
        },
    )

    rc = main(["repair", "route", "--workspace", str(ws), "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["repair_owner"] == "source-discovery"
    assert payload["allowed_artifacts"] == ["input/sources/*"]
    assert payload["must_rerun_from"] == "input-governance"


def test_repair_route_prefers_low_confidence_source_metadata(tmp_path, capsys):
    ws = _workspace(tmp_path)
    initialize_runtime_state(workspace=ws)
    _write_quality_gate_report(
        ws,
        {
            "finding_id": "QG_MATERIAL_FACT_002",
            "finding_type": "low_confidence_source_used",
            "severity": "high",
            "artifact_id": "claim_ledger",
            "repair_owner": "source-discovery",
            "repair_stage_id": "source-discovery",
            "repair_artifact_id": "claim_ledger",
            "message": "Claim Ledger cites a low-confidence source.",
        },
    )

    rc = main(["repair", "route", "--workspace", str(ws), "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["repair_owner"] == "source-discovery"
    assert payload["allowed_artifacts"] == ["input/sources/*"]


def test_repair_route_prefers_target_relevance_metadata(tmp_path, capsys):
    ws = _workspace(tmp_path)
    initialize_runtime_state(workspace=ws)
    _write_quality_gate_report(
        ws,
        {
            "finding_id": "QG_TARGET_RELEVANCE_001",
            "finding_type": "target_relevance_gap",
            "severity": "high",
            "artifact_id": "audited_brief",
            "repair_owner": "analyst",
            "repair_stage_id": "analyst",
            "repair_artifact_id": "audited_brief",
            "message": "Executive summary does not mention the configured target.",
        },
    )

    rc = main(["repair", "route", "--workspace", str(ws), "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["repair_owner"] == "editor"
    assert payload["allowed_artifacts"] == ["output/intermediate/audited_brief.md"]
    assert payload["must_rerun_from"] == "auditor"


def test_repair_route_prefers_target_priority_metadata(tmp_path, capsys):
    ws = _workspace(tmp_path)
    initialize_runtime_state(workspace=ws)
    _write_quality_gate_report(
        ws,
        {
            "finding_id": "QG_TARGET_RELEVANCE_002",
            "finding_type": "target_priority_claim_missing_from_summary",
            "severity": "high",
            "artifact_id": "audited_brief",
            "repair_owner": "analyst",
            "repair_stage_id": "analyst",
            "repair_artifact_id": "audited_brief",
            "message": "A high-priority target claim is missing from the summary.",
        },
    )

    rc = main(["repair", "route", "--workspace", str(ws), "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["repair_owner"] == "editor"
    assert payload["allowed_artifacts"] == ["output/intermediate/audited_brief.md"]


def test_repair_route_prefers_number_without_source_metadata(tmp_path, capsys):
    ws = _workspace(tmp_path)
    initialize_runtime_state(workspace=ws)
    _write_quality_gate_report(
        ws,
        {
            "finding_id": "QG_MATERIAL_FACT_003",
            "finding_type": "number_without_source",
            "severity": "high",
            "artifact_id": "audited_brief",
            "repair_owner": "analyst",
            "repair_stage_id": "analyst",
            "repair_artifact_id": "audited_brief",
            "message": "A number-like value appears without a source reference.",
        },
    )

    rc = main(["repair", "route", "--workspace", str(ws), "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["repair_owner"] == "editor"
    assert payload["allowed_artifacts"] == ["output/intermediate/audited_brief.md"]


def test_repair_route_analyst_without_artifact_never_allows_snapshot_edit(tmp_path, capsys):
    ws = _workspace(tmp_path)
    initialize_runtime_state(workspace=ws)
    _write_quality_gate_report(
        ws,
        {
            "finding_id": "QG_ANALYST_001",
            "finding_type": "summary_scope_gap",
            "severity": "high",
            "artifact_id": "audited_brief",
            "repair_owner": "analyst",
            "repair_stage_id": "analyst",
            "message": "Analyst draft needs a scoped rewrite before Delivery Editor review.",
        },
    )

    rc = main(["repair", "route", "--workspace", str(ws), "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["repair_owner"] == "analyst"
    assert payload["allowed_artifacts"] == ["output/intermediate/audited_brief.md"]
    assert "output/intermediate/analyst_draft_snapshot.md" not in payload["allowed_artifacts"]
    assert "output/intermediate/analyst_draft_snapshot.md" in payload["blocked_direct_edits"]
    assert payload["must_rerun_from"] == "editor"


def test_repair_route_rejects_invalid_gate_report_json(tmp_path, capsys):
    ws = _workspace(tmp_path)
    initialize_runtime_state(workspace=ws)
    path = _intermediate(ws) / "gates" / "auditor_quality_gate_report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{broken", encoding="utf-8")

    rc = main(["repair", "route", "--workspace", str(ws), "--json"])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error_code"] == "E_REPAIR_INPUT_INVALID"
    assert payload["input_errors"][0]["source"] == "auditor_quality_gate_report"


def test_repair_route_rejects_invalid_artifact_registry_json(tmp_path, capsys):
    ws = _workspace(tmp_path)
    initialize_runtime_state(workspace=ws)
    runtime_state_paths(ws)["artifact_registry"].write_text("{broken", encoding="utf-8")

    rc = main(["repair", "route", "--workspace", str(ws), "--json"])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error_code"] == "E_REPAIR_INPUT_INVALID"
    assert payload["input_errors"][0]["source"] == "artifact_registry"


def test_repair_route_ignores_legacy_gate_projection_when_scoped_report_exists(tmp_path, capsys):
    ws = _workspace(tmp_path)
    initialize_runtime_state(workspace=ws)
    finding = {
        "finding_id": "QG_TARGET_RELEVANCE_001",
        "finding_type": "target_relevance_gap",
        "severity": "high",
        "artifact_id": "audited_brief",
        "repair_owner": "analyst",
        "repair_stage_id": "analyst",
        "repair_artifact_id": "audited_brief",
        "message": "Executive summary does not mention the configured target.",
    }
    _write_quality_gate_report(ws, finding)
    _write_legacy_quality_gate_report(ws, finding)

    rc = main(["repair", "route", "--workspace", str(ws), "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["finding_count"] == 1
    assert len(payload["routes"]) == 1


def test_repair_route_no_match_is_read_only_none_route(tmp_path, capsys):
    ws = _workspace(tmp_path)
    initialize_runtime_state(workspace=ws)

    rc = main(["repair", "route", "--workspace", str(ws), "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["repair_owner"] == "none"
    assert payload["routes"] == []
    assert payload["reason"] == "No deterministic repair route found."


def test_repair_start_fails_when_no_deterministic_route_exists(tmp_path, capsys):
    ws = _workspace(tmp_path)
    initialize_runtime_state(workspace=ws)

    rc = main(["repair", "start", "--workspace", str(ws), "--json"])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error_code"] == "E_ILLEGAL_TRANSITION"
    assert "No deterministic repair route found" in payload["error"]


def test_repair_start_fails_on_invalid_gate_report_json(tmp_path, capsys):
    ws = _workspace(tmp_path)
    initialize_runtime_state(workspace=ws)
    path = _intermediate(ws) / "gates" / "auditor_quality_gate_report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{broken", encoding="utf-8")

    rc = main(["repair", "start", "--workspace", str(ws), "--json"])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error_code"] == "E_REPAIR_INPUT_INVALID"


def test_repair_complete_fails_without_active_repair(tmp_path, capsys):
    ws = _workspace(tmp_path)
    initialize_runtime_state(workspace=ws)

    rc = main([
        "repair",
        "complete",
        "--workspace",
        str(ws),
        "--reason",
        "no active repair",
        "--json",
    ])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error_code"] == "E_ILLEGAL_TRANSITION"
    assert "No active repair transaction" in payload["error"]
