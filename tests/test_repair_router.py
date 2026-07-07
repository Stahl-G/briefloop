from __future__ import annotations

import json
from functools import partial
from pathlib import Path

from multi_agent_brief.cli.main import main
from multi_agent_brief.orchestrator.runtime_state import (
    check_runtime_state,
    initialize_runtime_state,
    runtime_state_paths,
    utc_now,
)
from multi_agent_brief.repair.router import route_repair
from tests.helpers import write_minimal_workspace_under


_workspace = partial(
    write_minimal_workspace_under,
    project_name="repair-route-test",
    user_text="# Repair route test\n",
)


def _intermediate(ws: Path) -> Path:
    path = ws / "output" / "intermediate"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_audit_report(ws: Path, finding: dict[str, object], *, audit_status: str = "fail") -> None:
    (_intermediate(ws) / "audit_report.json").write_text(
        json.dumps(
            {
                "audit_status": audit_status,
                "audit_score": 40,
                "findings": [finding],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _quality_gate_finding(finding: dict[str, object], *, stage_id: str, gate_artifact_id: str) -> dict[str, object]:
    payload = dict(finding)
    payload.setdefault("finding_type", "target_relevance_gap")
    payload.setdefault("severity", "high")
    payload.setdefault("blocking_level", "blocking")
    payload.setdefault("blocking", payload.get("blocking_level") == "blocking")
    payload.setdefault("stage_id", stage_id)
    payload.setdefault("gate_stage_id", stage_id)
    payload.setdefault("gate_artifact_id", gate_artifact_id)
    payload.setdefault("message", "Synthetic repair route gate finding.")
    payload["blocking"] = payload.get("blocking_level") == "blocking"
    return payload


def _quality_gate_payload(
    finding: dict[str, object] | list[dict[str, object]],
    *,
    stage_id: str,
    gate_artifact_id: str,
) -> dict[str, object]:
    raw_findings = finding if isinstance(finding, list) else [finding]
    gate_findings = [
        _quality_gate_finding(item, stage_id=stage_id, gate_artifact_id=gate_artifact_id)
        for item in raw_findings
    ]
    finding_ids = [
        str(item["finding_id"])
        for item in gate_findings
        if item.get("blocking") is True
    ]
    return {
        "schema_version": "multi-agent-brief-quality-gates/v1",
        "created_at": "2026-07-06T00:00:00+00:00",
        "updated_at": "2026-07-06T00:00:00+00:00",
        "workspace": ".",
        "report_date": "2026-07-06",
        "policy_pack": "default",
        "status": "fail" if finding_ids else "warning",
        "gate_results": [
            {"gate_id": "coverage_omission", "status": "pass", "blocking": False, "finding_ids": []},
            {"gate_id": "freshness", "status": "pass", "blocking": False, "finding_ids": []},
            {"gate_id": "material_fact", "status": "pass", "blocking": False, "finding_ids": []},
            {
                "gate_id": "target_relevance",
                "status": "fail" if finding_ids else "warning",
                "blocking": bool(finding_ids),
                "finding_ids": finding_ids,
            },
        ],
        "findings": gate_findings,
        "metadata": {
            "stage_id": stage_id,
            "gate_stage_id": stage_id,
            "gate_artifact_id": gate_artifact_id,
            "brief": "output/intermediate/audited_brief.md",
            "ledger": "output/intermediate/claim_ledger.json",
        },
    }


def _write_quality_gate_report(ws: Path, finding: dict[str, object]) -> None:
    path = _intermediate(ws) / "gates" / "auditor_quality_gate_report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_quality_gate_payload(finding, stage_id="auditor", gate_artifact_id="auditor_quality_gate_report"), ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )


def _write_finalize_report_with_reader_clean_failure(ws: Path) -> None:
    (_intermediate(ws) / "finalize_report.json").write_text(
        json.dumps(
            {
                "status": "fail",
                "reader_clean": {
                    "status": "fail",
                    "bare_claim_id_count": 1,
                    "process_wording_count": 1,
                    "sample_findings": [
                        {
                            "kind": "bare_claim_id",
                            "text": "CL-0001",
                            "line": 12,
                            "artifact": str(ws / "output" / "delivery" / "brief.md"),
                            "message": "Reader-facing output contains a raw internal claim ID.",
                        },
                        {
                            "kind": "process_wording",
                            "text": "Claim Ledger",
                            "line": 18,
                            "artifact": str(ws / "output" / "delivery" / "brief.md"),
                            "message": "Reader-facing output contains internal workflow/process wording.",
                        },
                    ],
                },
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
        json.dumps(_quality_gate_payload(finding, stage_id="auditor", gate_artifact_id="auditor_quality_gate_report"), ensure_ascii=False, indent=2)
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


def _mark_fact_layer_imported(ws: Path) -> None:
    manifest_path = runtime_state_paths(ws)["runtime_manifest"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["recipe"] = "fast-rerun"
    manifest["fact_layer_import"] = {
        "schema_version": "mabw.fact_layer_import.v1",
        "source_run_id": "mabw-seed-run",
        "fact_layer_sha256": "0" * 64,
        "satisfied_stage_ids": [
            "doctor",
            "source-discovery",
            "input-governance",
            "scout",
            "screener",
            "claim-ledger",
        ],
        "imported_files": [
            {
                "artifact_id": "claim_ledger",
                "workspace_path": "output/intermediate/claim_ledger.json",
                "sha256": "1" * 64,
                "size_bytes": 2,
            }
        ],
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


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


def test_repair_route_ignores_semantic_support_proposal_findings(tmp_path):
    ws = _workspace(tmp_path)
    initialize_runtime_state(workspace=ws)
    (_intermediate(ws) / "audit_report.json").write_text(
        json.dumps(
            {
                "audit_status": "pass",
                "audit_score": 100,
                "findings": [
                    {
                        "finding_id": "SAR-0001",
                        "finding_type": "semantic_support_proposal",
                        "severity": "low",
                        "related_claim_id": "CL-0001",
                        "description": "Advisory: draft may overstate CL-0001.",
                        "recommendation": "Advisory proposal only.",
                        # A repair_owner is present but must NOT create a route.
                        "repair_owner": "editor",
                    },
                    {
                        "finding_id": "AUDIT_001",
                        "finding_type": "unsupported_claim",
                        "severity": "high",
                        "artifact_id": "audited_brief",
                        "description": "Claim in audited brief is unsupported by the ledger.",
                    },
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    result = route_repair(workspace=ws)

    assert result["ok"] is True
    # Both findings are collected, but only the real one produces a route.
    assert result["finding_count"] == 2
    assert len(result["routes"]) == 1
    assert result["routes"][0]["source"]["finding_id"] == "AUDIT_001"
    assert all(
        route["source"].get("finding_type") != "semantic_support_proposal"
        for route in result["routes"]
    )


def test_repair_route_does_not_ignore_spoofed_high_severity_proposal(tmp_path):
    ws = _workspace(tmp_path)
    initialize_runtime_state(workspace=ws)
    (_intermediate(ws) / "audit_report.json").write_text(
        json.dumps(
            {
                "audit_status": "fail",
                "audit_score": 40,
                "findings": [
                    {
                        "finding_id": "SAR-SPOOF",
                        # Not low severity -> not advisory -> must fail closed.
                        "finding_type": "semantic_support_proposal",
                        "severity": "high",
                        "repair_owner": "editor",
                        "artifact_id": "audited_brief",
                        "description": "spoofed high-severity proposal",
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    result = route_repair(workspace=ws)

    assert result["ok"] is True
    assert len(result["routes"]) == 1
    assert result["routes"][0]["source"]["finding_id"] == "SAR-SPOOF"


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


def test_repair_route_maps_finalize_reader_clean_failure_to_editor(tmp_path, capsys):
    ws = _workspace(tmp_path)
    initialize_runtime_state(workspace=ws)
    _set_workflow_stages(
        ws,
        completed=[
            "doctor",
            "source-discovery",
            "input-governance",
            "scout",
            "screener",
            "claim-ledger",
            "analyst",
            "editor",
            "auditor",
        ],
        current_stage="finalize",
    )
    _write_finalize_report_with_reader_clean_failure(ws)

    rc = main(["repair", "route", "--workspace", str(ws), "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["repair_owner"] == "editor"
    assert payload["allowed_artifacts"] == ["output/intermediate/audited_brief.md"]
    assert payload["must_rerun_from"] == "auditor"
    assert payload["recommended_action"] == "repair_editor_audited_brief_and_rerun_auditor_finalize"
    assert payload["source"]["kind"] == "finalize_report"
    assert payload["source"]["stage_id"] == "finalize"
    assert payload["source"]["finding_type"] == "reader_clean_bare_claim_id"
    assert any(route["source"]["finding_type"] == "reader_clean_process_wording" for route in payload["routes"])


def test_repair_route_ignores_stale_finalize_report_outside_finalize_stage(tmp_path, capsys):
    ws = _workspace(tmp_path)
    initialize_runtime_state(workspace=ws)
    _set_workflow_stages(
        ws,
        completed=[
            "doctor",
            "source-discovery",
            "input-governance",
            "scout",
            "screener",
            "claim-ledger",
            "analyst",
            "editor",
        ],
        current_stage="auditor",
    )
    _write_finalize_report_with_reader_clean_failure(ws)

    rc = main(["repair", "route", "--workspace", str(ws), "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["repair_owner"] == "none"
    assert payload["finding_count"] == 0
    assert not any(route.get("source", {}).get("kind") == "finalize_report" for route in payload["routes"])


def test_repair_route_marks_non_current_gate_route_not_startable(tmp_path, capsys):
    ws = _workspace(tmp_path)
    initialize_runtime_state(workspace=ws)
    _set_workflow_stages(
        ws,
        completed=[
            "doctor",
            "source-discovery",
            "input-governance",
            "scout",
            "screener",
            "claim-ledger",
            "analyst",
            "editor",
            "auditor",
        ],
        current_stage="finalize",
    )
    _write_quality_gate_report(
        ws,
        {
            "finding_id": "QG_AUDITOR_OLD",
            "finding_type": "target_relevance_gap",
            "severity": "high",
            "artifact_id": "audited_brief",
            "repair_owner": "editor",
            "repair_stage_id": "editor",
            "repair_artifact_id": "audited_brief",
            "message": "Old auditor gate route should not be startable from finalize.",
        },
    )

    rc = main(["repair", "route", "--workspace", str(ws), "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["source"]["stage_id"] == "auditor"
    assert payload["route_kind"] == "owner_stage_repair"
    assert payload["startable"] is False
    assert payload["routes"][0]["startable"] is False


def test_repair_start_rejects_non_current_gate_route_with_stage_mismatch(tmp_path, capsys):
    ws = _workspace(tmp_path)
    initialize_runtime_state(workspace=ws)
    _set_workflow_stages(
        ws,
        completed=[
            "doctor",
            "source-discovery",
            "input-governance",
            "scout",
            "screener",
            "claim-ledger",
            "analyst",
            "editor",
            "auditor",
        ],
        current_stage="finalize",
    )
    _write_quality_gate_report(
        ws,
        {
            "finding_id": "QG_AUDITOR_OLD",
            "finding_type": "target_relevance_gap",
            "severity": "high",
            "artifact_id": "audited_brief",
            "repair_owner": "editor",
            "repair_stage_id": "editor",
            "repair_artifact_id": "audited_brief",
            "message": "Old auditor gate route should not start from finalize.",
        },
    )

    rc = main(["repair", "start", "--workspace", str(ws), "--json"])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["error_code"] == "E_ILLEGAL_TRANSITION"
    assert "source stage does not match the current workflow stage" in payload["error"]
    assert payload["details"]["route_stage_id"] == "auditor"
    assert payload["details"]["current_stage"] == "finalize"


def test_repair_start_accepts_finalize_reader_clean_route_from_finalize_stage(tmp_path, capsys):
    ws = _workspace(tmp_path)
    initialize_runtime_state(workspace=ws)
    _write_valid_claim_ledger(ws)
    (_intermediate(ws) / "audited_brief.md").write_text(
        "# Brief\n\nExampleCo opened a demo facility. [src:CL-0001]\n",
        encoding="utf-8",
    )
    (_intermediate(ws) / "audit_report.json").write_text(
        json.dumps({"audit_status": "pass", "audit_score": 95, "findings": []}) + "\n",
        encoding="utf-8",
    )
    _set_workflow_stages(
        ws,
        completed=[
            "doctor",
            "source-discovery",
            "input-governance",
            "scout",
            "screener",
            "claim-ledger",
            "analyst",
            "editor",
            "auditor",
        ],
        current_stage="finalize",
    )
    check_runtime_state(workspace=ws)
    _write_finalize_report_with_reader_clean_failure(ws)

    rc = main(["repair", "start", "--workspace", str(ws), "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    repair = payload["repair"]
    workflow = payload["workflow_state"]
    assert payload["transaction"]["decision"] == "repair_start"
    assert workflow["current_stage"] == "editor"
    assert workflow["active_repair"]["repair_owner"] == "editor"
    assert repair["source"]["kind"] == "finalize_report"
    assert repair["source"]["stage_id"] == "finalize"
    assert repair["allowed_artifacts"] == ["output/intermediate/audited_brief.md"]
    assert repair["must_rerun_from"] == "auditor"


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


def test_repair_route_prioritizes_frozen_artifact_change_over_audit_text(tmp_path, capsys):
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
    _write_quality_gate_report(
        ws,
        {
            "finding_id": "QG_INPUT_001",
            "finding_type": "unsupported_claim",
            "severity": "high",
            "artifact_id": "claim_ledger",
            "repair_owner": "claim-ledger",
            "repair_stage_id": "claim-ledger",
            "repair_artifact_id": "claim_ledger",
            "message": "Claim Ledger support looks insufficient.",
        },
    )
    (_intermediate(ws) / "audited_brief.md").write_text("# Brief\n\nChanged downstream patch.\n", encoding="utf-8")

    rc = main(["repair", "route", "--workspace", str(ws), "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["repair_owner"] == "editor"
    assert payload["source"]["kind"] == "transaction_integrity"
    assert payload["source"]["finding_type"] == "frozen_artifact_changed"
    assert payload["allowed_artifacts"] == ["output/intermediate/audited_brief.md"]


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


def test_repair_route_maps_missing_claim_ledger_registry_to_claim_ledger(tmp_path, capsys):
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
                        "status": "missing",
                        "validation_result": "required_artifact_missing",
                        "blocking_reason": "Claim Ledger artifact is missing.",
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


def test_repair_route_maps_low_source_density_metadata_to_editor(tmp_path, capsys):
    ws = _workspace(tmp_path)
    initialize_runtime_state(workspace=ws)
    _write_quality_gate_report(
        ws,
        {
            "finding_id": "QG_MATERIAL_FACT_004",
            "finding_type": "low_source_density",
            "severity": "high",
            "artifact_id": "audited_brief",
            "repair_owner": "editor",
            "repair_stage_id": "editor",
            "repair_artifact_id": "audited_brief",
            "message": "The brief has too few source-linked claims for reader confidence.",
        },
    )

    rc = main(["repair", "route", "--workspace", str(ws), "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["repair_owner"] == "editor"
    assert payload["allowed_artifacts"] == ["output/intermediate/audited_brief.md"]
    assert payload["must_rerun_from"] == "auditor"


def test_repair_route_does_not_let_minimum_text_override_explicit_editor_route(tmp_path, capsys):
    ws = _workspace(tmp_path)
    initialize_runtime_state(workspace=ws)
    _write_quality_gate_report(
        ws,
        {
            "finding_id": "QG_MATERIAL_FACT_005",
            "finding_type": "number_without_source",
            "severity": "high",
            "artifact_id": "audited_brief",
            "repair_owner": "editor",
            "repair_stage_id": "editor",
            "repair_artifact_id": "audited_brief",
            "message": "The repair requires at least one source citation on the affected line.",
        },
    )

    rc = main(["repair", "route", "--workspace", str(ws), "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["repair_owner"] == "editor"
    assert payload["allowed_artifacts"] == ["output/intermediate/audited_brief.md"]


def test_repair_route_does_not_auto_repair_input_limitation_findings(tmp_path, capsys):
    ws = _workspace(tmp_path)
    initialize_runtime_state(workspace=ws)
    _write_quality_gate_report(
        ws,
        {
            "finding_id": "QG_FINAL_001",
            "finding_type": "insufficient_claims",
            "severity": "high",
            "artifact_id": "claim_ledger",
            "repair_owner": "claim-ledger",
            "repair_stage_id": "claim-ledger",
            "repair_artifact_id": "claim_ledger",
            "message": "Only 1 reportable claims selected; weekly brief requires at least 20.",
        },
    )

    rc = main(["repair", "route", "--workspace", str(ws), "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["repair_owner"] == "none"
    assert payload["allowed_artifacts"] == []
    assert payload["source"]["route_classification"] == "input_limitation"
    assert payload["recommended_action"] == "request_human_review_or_start_fresh_workspace"


def test_repair_route_human_owner_overrides_stage_metadata(tmp_path, capsys):
    ws = _workspace(tmp_path)
    initialize_runtime_state(workspace=ws)
    _write_quality_gate_report(
        ws,
        {
            "finding_id": "QG_TARGET_RELEVANCE_001",
            "finding_type": "target_mapping_ambiguous",
            "severity": "high",
            "blocking_level": "blocking",
            "blocking": True,
            "artifact_id": "audited_brief",
            "repair_owner": "human",
            "repair_stage_id": "editor",
            "repair_artifact_id": "audited_brief",
            "message": "Target entity or topic could not be derived from workspace config.",
        },
    )

    rc = main(["repair", "route", "--workspace", str(ws), "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["route_kind"] == "human_review"
    assert payload["startable"] is False
    assert payload["repair_owner"] == "human"
    assert payload["allowed_artifacts"] == []
    assert payload["must_rerun_from"] == ""
    assert payload["recommended_action"] == "request_human_review"
    assert payload["source"]["route_classification"] == "human_review"


def test_repair_start_rejects_human_review_route_before_allowed_artifacts(tmp_path, capsys):
    ws = _workspace(tmp_path)
    initialize_runtime_state(workspace=ws)
    _write_quality_gate_report(
        ws,
        {
            "finding_id": "QG_TARGET_RELEVANCE_001",
            "finding_type": "target_mapping_ambiguous",
            "severity": "high",
            "blocking_level": "blocking",
            "blocking": True,
            "artifact_id": "audited_brief",
            "repair_owner": "human",
            "repair_stage_id": "editor",
            "repair_artifact_id": "audited_brief",
            "message": "Target entity or topic could not be derived from workspace config.",
        },
    )
    before_workflow = runtime_state_paths(ws)["workflow_state"].read_bytes()

    rc = main([
        "repair",
        "start",
        "--workspace",
        str(ws),
        "--finding-id",
        "QG_TARGET_RELEVANCE_001",
        "--json",
    ])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["error_code"] == "E_REPAIR_NO_LEGAL_ROUTE"
    assert "human review" in payload["error"]
    assert "allowed artifacts" not in payload["error"].lower()
    assert payload["details"]["required_decisions"] == ["request_human_review", "block_run"]
    assert payload["details"]["repair_route"]["route_kind"] == "human_review"
    assert payload["details"]["repair_route"]["startable"] is False
    assert runtime_state_paths(ws)["workflow_state"].read_bytes() == before_workflow


def _write_imported_claim_ledger_audit_warning(ws: Path) -> None:
    _write_audit_report(
        ws,
        {
            "finding_id": "AUD-002",
            "finding_type": "claim_ledger_support_warning",
            "severity": "medium",
            "artifact_id": "claim_ledger",
            "repair_owner": "claim-ledger",
            "repair_stage_id": "claim-ledger",
            "repair_artifact_id": "claim_ledger",
            "message": "Claim Ledger support looks weak but this is a warning.",
        },
        audit_status="warning",
    )


def _write_blocking_target_relevance_gate(ws: Path) -> None:
    _write_quality_gate_report(
        ws,
        {
            "finding_id": "QG_TARGET_RELEVANCE_001",
            "finding_type": "target_relevance_gap",
            "severity": "high",
            "blocking": True,
            "artifact_id": "audited_brief",
            "repair_owner": "editor",
            "repair_stage_id": "editor",
            "repair_artifact_id": "audited_brief",
            "message": "Executive summary does not mention the configured target.",
        },
    )


def _imported_auditor_workspace_with_repair_routes(tmp_path: Path) -> Path:
    ws = _workspace(tmp_path)
    initialize_runtime_state(workspace=ws)
    _mark_fact_layer_imported(ws)
    _set_workflow_stages(
        ws,
        completed=[
            "doctor",
            "source-discovery",
            "input-governance",
            "scout",
            "screener",
            "claim-ledger",
            "analyst",
            "editor",
        ],
        current_stage="auditor",
    )
    _write_imported_claim_ledger_audit_warning(ws)
    _write_blocking_target_relevance_gate(ws)
    return ws


def test_repair_route_selects_blocking_gate_before_imported_audit_warning(tmp_path, capsys):
    ws = _imported_auditor_workspace_with_repair_routes(tmp_path)

    rc = main(["repair", "route", "--workspace", str(ws), "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["repair_owner"] == "editor"
    assert payload["source"]["finding_id"] == "QG_TARGET_RELEVANCE_001"
    assert payload["default_selected"] is True
    routes = {route["source"]["finding_id"]: route for route in payload["routes"]}
    assert routes["QG_TARGET_RELEVANCE_001"]["default_selected"] is True
    assert routes["QG_TARGET_RELEVANCE_001"]["is_blocking"] is True
    assert routes["QG_TARGET_RELEVANCE_001"]["is_imported_fact_layer_forbidden"] is False
    assert routes["AUD-002"]["default_selected"] is False
    assert routes["AUD-002"]["is_blocking"] is False
    assert routes["AUD-002"]["is_imported_fact_layer_forbidden"] is True


def test_repair_start_defaults_to_blocking_gate_route_over_imported_warning(tmp_path, capsys):
    ws = _imported_auditor_workspace_with_repair_routes(tmp_path)

    rc = main(["repair", "start", "--workspace", str(ws), "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    repair = payload["repair"]
    workflow = payload["workflow_state"]
    assert workflow["current_stage"] == "editor"
    assert repair["repair_owner"] == "editor"
    assert repair["source"]["finding_id"] == "QG_TARGET_RELEVANCE_001"
    assert repair["allowed_artifacts"] == ["output/intermediate/audited_brief.md"]
    assert repair["must_rerun_from"] == "auditor"


def test_repair_start_finding_id_selects_blocking_gate_route(tmp_path, capsys):
    ws = _imported_auditor_workspace_with_repair_routes(tmp_path)

    rc = main([
        "repair",
        "start",
        "--workspace",
        str(ws),
        "--finding-id",
        "QG_TARGET_RELEVANCE_001",
        "--json",
    ])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["repair"]["repair_owner"] == "editor"
    assert payload["repair"]["source"]["finding_id"] == "QG_TARGET_RELEVANCE_001"


def test_repair_start_route_index_selects_blocking_gate_route(tmp_path, capsys):
    ws = _imported_auditor_workspace_with_repair_routes(tmp_path)

    rc = main(["repair", "start", "--workspace", str(ws), "--route-index", "0", "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["repair"]["repair_owner"] == "editor"
    assert payload["repair"]["source"]["finding_id"] == "QG_TARGET_RELEVANCE_001"


def test_repair_start_rejects_ambiguous_route_selection_args(tmp_path, capsys):
    ws = _imported_auditor_workspace_with_repair_routes(tmp_path)

    rc = main([
        "repair",
        "start",
        "--workspace",
        str(ws),
        "--route-index",
        "0",
        "--finding-id",
        "QG_TARGET_RELEVANCE_001",
        "--json",
    ])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["error_code"] == "E_REPAIR_ROUTE_SELECTION_INVALID"


def test_repair_start_explicit_imported_fact_layer_route_fails(tmp_path, capsys):
    ws = _imported_auditor_workspace_with_repair_routes(tmp_path)
    before_workflow = runtime_state_paths(ws)["workflow_state"].read_bytes()

    rc = main(["repair", "start", "--workspace", str(ws), "--finding-id", "AUD-002", "--json"])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["error_code"] == "E_REPAIR_IMPORTED_FACT_LAYER_FORBIDDEN"
    assert "output/intermediate/claim_ledger.json" in payload["details"]["allowed_artifacts"]
    assert runtime_state_paths(ws)["workflow_state"].read_bytes() == before_workflow


def test_repair_route_explicit_imported_fact_layer_route_fails(tmp_path, capsys):
    ws = _imported_auditor_workspace_with_repair_routes(tmp_path)

    rc = main(["repair", "route", "--workspace", str(ws), "--finding-id", "AUD-002", "--json"])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error_code"] == "E_REPAIR_IMPORTED_FACT_LAYER_FORBIDDEN"
    assert payload["selected_route"]["source"]["finding_id"] == "AUD-002"
    assert payload["selected_route"]["is_imported_fact_layer_forbidden"] is True
    assert any(route["source"]["finding_id"] == "QG_TARGET_RELEVANCE_001" for route in payload["routes"])


def test_route_repair_api_explicit_imported_fact_layer_route_fails(tmp_path):
    ws = _imported_auditor_workspace_with_repair_routes(tmp_path)

    payload = route_repair(workspace=ws, finding_id="AUD-002")

    assert payload["ok"] is False
    assert payload["error_code"] == "E_REPAIR_IMPORTED_FACT_LAYER_FORBIDDEN"
    assert payload["selected_route"]["source"]["finding_id"] == "AUD-002"
    assert payload["selected_route"]["is_imported_fact_layer_forbidden"] is True


def test_repair_start_fails_when_only_imported_fact_layer_routes_exist(tmp_path, capsys):
    ws = _workspace(tmp_path)
    initialize_runtime_state(workspace=ws)
    _mark_fact_layer_imported(ws)
    _set_workflow_stages(
        ws,
        completed=[
            "doctor",
            "source-discovery",
            "input-governance",
            "scout",
            "screener",
            "claim-ledger",
            "analyst",
            "editor",
        ],
        current_stage="auditor",
    )
    _write_imported_claim_ledger_audit_warning(ws)
    before_workflow = runtime_state_paths(ws)["workflow_state"].read_bytes()

    rc = main(["repair", "start", "--workspace", str(ws), "--json"])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["error_code"] == "E_REPAIR_NO_LEGAL_ROUTE"
    assert payload["details"]["routes"][0]["source"]["finding_id"] == "AUD-002"
    assert payload["details"]["routes"][0]["is_imported_fact_layer_forbidden"] is True
    assert runtime_state_paths(ws)["workflow_state"].read_bytes() == before_workflow


def test_repair_route_fails_when_only_imported_fact_layer_routes_exist(tmp_path, capsys):
    ws = _workspace(tmp_path)
    initialize_runtime_state(workspace=ws)
    _mark_fact_layer_imported(ws)
    _set_workflow_stages(
        ws,
        completed=[
            "doctor",
            "source-discovery",
            "input-governance",
            "scout",
            "screener",
            "claim-ledger",
            "analyst",
            "editor",
        ],
        current_stage="auditor",
    )
    _write_imported_claim_ledger_audit_warning(ws)

    rc = main(["repair", "route", "--workspace", str(ws), "--json"])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error_code"] == "E_REPAIR_NO_LEGAL_ROUTE"
    assert payload["repair_owner"] == "none"
    assert payload["routes"][0]["source"]["finding_id"] == "AUD-002"
    assert payload["routes"][0]["is_imported_fact_layer_forbidden"] is True


def test_repair_route_prioritizes_input_limitation_over_routeable_findings(tmp_path, capsys):
    ws = _workspace(tmp_path)
    initialize_runtime_state(workspace=ws)
    path = _intermediate(ws) / "gates" / "auditor_quality_gate_report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            _quality_gate_payload(
                [
                    {
                        "finding_id": "QG_FINAL_001",
                        "finding_type": "insufficient_claims",
                        "severity": "high",
                        "artifact_id": "claim_ledger",
                        "repair_owner": "claim-ledger",
                        "repair_stage_id": "claim-ledger",
                        "repair_artifact_id": "claim_ledger",
                        "message": "Only 1 reportable claims selected; weekly brief requires at least 20.",
                    },
                    {
                        "finding_id": "QG_MATERIAL_FACT_001",
                        "finding_type": "number_without_source",
                        "severity": "high",
                        "artifact_id": "audited_brief",
                        "repair_owner": "editor",
                        "repair_stage_id": "editor",
                        "repair_artifact_id": "audited_brief",
                        "message": "A number-like value appears without a source reference.",
                    },
                ],
                stage_id="auditor",
                gate_artifact_id="auditor_quality_gate_report",
            ),
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    rc = main(["repair", "route", "--workspace", str(ws), "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["repair_owner"] == "none"
    assert payload["allowed_artifacts"] == []
    assert payload["source"]["route_classification"] == "input_limitation"
    assert payload["recommended_action"] == "request_human_review_or_start_fresh_workspace"
    assert any(route["repair_owner"] == "editor" for route in payload["routes"])


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


def test_repair_route_rejects_schema_invalid_gate_report_with_spoofed_route(tmp_path, capsys):
    ws = _workspace(tmp_path)
    initialize_runtime_state(workspace=ws)
    path = _intermediate(ws) / "gates" / "finalize_quality_gate_report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": "multi-agent-brief-quality-gates/v1",
                "status": "fail",
                "metadata": {
                    "stage_id": "finalize",
                    "gate_stage_id": "finalize",
                    "gate_artifact_id": "finalize_quality_gate_report",
                },
                "findings": [
                    {
                        "finding_id": "QG_SPOOFED_001",
                        "finding_type": "target_relevance_gap",
                        "severity": "high",
                        "blocking_level": "blocking",
                        "blocking": True,
                        "artifact_id": "audited_brief",
                        "repair_owner": "editor",
                        "repair_stage_id": "editor",
                        "repair_artifact_id": "audited_brief",
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    rc = main(["repair", "route", "--workspace", str(ws), "--json"])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error_code"] == "E_REPAIR_INPUT_INVALID"
    assert payload["input_errors"][0]["source"] == "finalize_quality_gate_report"
    assert "gate_results must be a list" in payload["input_errors"][0]["error"]


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


def test_final_abstract_quality_warning_does_not_open_repair_route(tmp_path, capsys):
    ws = _workspace(tmp_path)
    initialize_runtime_state(workspace=ws)
    path = _intermediate(ws) / "gates" / "auditor_quality_gate_report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": "multi-agent-brief-quality-gates/v1",
                "status": "warning",
                "gate_results": [
                    {
                        "gate_id": "final_abstract_quality",
                        "status": "warning",
                        "blocking": False,
                        "finding_ids": ["QG_FINAL_ABSTRACT_QUALITY_001"],
                    }
                ],
                "findings": [
                    {
                        "finding_id": "QG_FINAL_ABSTRACT_QUALITY_001",
                        "gate_id": "final_abstract_quality",
                        "finding_type": "final_missing_limitation_section",
                        "severity": "medium",
                        "blocking_level": "warning",
                        "blocking": False,
                        "repair_owner": "none",
                        "stage_id": "editor",
                        "artifact_id": "audited_brief",
                        "repair_stage_id": None,
                        "repair_artifact_id": None,
                        "description": "Advisory final abstract warning.",
                        "recommendation": "Review manually; do not open repair.",
                        "metadata": {"repair_boundary": "advisory_non_routable"},
                    }
                ],
                "metadata": {"gate_stage_id": "auditor"},
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    route_rc = main(["repair", "route", "--workspace", str(ws), "--json"])
    assert route_rc == 0
    route_payload = json.loads(capsys.readouterr().out)
    assert route_payload["repair_owner"] == "none"
    assert route_payload["routes"] == []

    start_rc = main(["repair", "start", "--workspace", str(ws), "--json"])
    assert start_rc == 1
    start_payload = json.loads(capsys.readouterr().out)
    assert start_payload["error_code"] == "E_ILLEGAL_TRANSITION"
    workflow = json.loads(runtime_state_paths(ws)["workflow_state"].read_text(encoding="utf-8"))
    assert "active_repair" not in workflow


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


def test_repair_start_records_non_reference_contaminated_repair_semantics(tmp_path, capsys):
    ws = _workspace(tmp_path)
    initialize_runtime_state(workspace=ws)
    _set_workflow_stages(
        ws,
        completed=["doctor", "source-discovery", "input-governance", "scout", "screener", "claim-ledger", "analyst", "editor"],
        current_stage="auditor",
    )
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
    workflow_path = runtime_state_paths(ws)["workflow_state"]
    workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    workflow["run_integrity"] = {
        "status": "contaminated",
        "reference_eligible": False,
        "clean_single_shot": False,
        "reasons": [{"reason_code": "frozen_artifact_changed", "message": "fixture contamination"}],
    }
    workflow_path.write_text(json.dumps(workflow, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    rc = main(["repair", "start", "--workspace", str(ws), "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    repair = payload["repair"]
    assert repair["run_integrity_effect"]["reference_eligible"] is False
    assert "cannot restore clean reference eligibility" in repair["run_integrity_effect"]["reason"]
    events = [
        json.loads(line)
        for line in runtime_state_paths(ws)["event_log"].read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert events[-1]["event_type"] == "repair_started"
    assert events[-1]["metadata"]["run_integrity_effect"]["reference_eligible"] is False


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
