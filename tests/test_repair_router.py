from __future__ import annotations

import json
from functools import partial
from pathlib import Path

import pytest

from multi_agent_brief.cli.main import main
from multi_agent_brief.orchestrator.runtime_state import (
    check_runtime_state,
    initialize_runtime_state,
    runtime_state_paths,
    utc_now,
)
from multi_agent_brief.repair.router import route_repair, route_repair_for_gate
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


def _write_stage_quality_gate_report(
    ws: Path,
    *,
    stage_id: str,
    finding: dict[str, object] | None,
    status: str = "fail",
    blocking: bool = True,
) -> None:
    artifact_id = "finalize_quality_gate_report" if stage_id == "finalize" else "auditor_quality_gate_report"
    path = _intermediate(ws) / "gates" / f"{artifact_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    findings = [finding] if finding is not None else []
    finding_ids = [str(finding["finding_id"])] if finding is not None and blocking else []
    path.write_text(
        json.dumps(
            {
                "schema_version": "multi-agent-brief-quality-gates/v1",
                "created_at": "2026-07-07T00:00:00+00:00",
                "updated_at": "2026-07-07T00:00:00+00:00",
                "workspace": ".",
                "report_date": "2026-07-07",
                "policy_pack": "default",
                "status": status,
                "gate_results": [
                    {
                        "gate_id": gate_id,
                        "status": "fail" if blocking and gate_id == "target_relevance" else "pass",
                        "blocking": blocking and gate_id == "target_relevance",
                        "finding_ids": finding_ids if blocking and gate_id == "target_relevance" else [],
                    }
                    for gate_id in ("coverage_omission", "freshness", "material_fact", "target_relevance")
                ],
                "findings": findings,
                "metadata": {
                    "stage_id": stage_id,
                    "gate_stage_id": stage_id,
                    "gate_artifact_id": artifact_id,
                    "brief": "output/brief.md" if stage_id == "finalize" else "output/intermediate/audited_brief.md",
                    "ledger": "output/intermediate/claim_ledger.json",
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _replace_stage_gate_findings(
    ws: Path,
    *,
    stage_id: str,
    findings: list[dict[str, object]],
    blocking_finding_ids: list[str],
) -> None:
    artifact_id = "finalize_quality_gate_report" if stage_id == "finalize" else "auditor_quality_gate_report"
    path = _intermediate(ws) / "gates" / f"{artifact_id}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["findings"] = findings
    for result in payload["gate_results"]:
        if result["gate_id"] == "target_relevance":
            result["status"] = "fail" if blocking_finding_ids else "pass"
            result["blocking"] = bool(blocking_finding_ids)
            result["finding_ids"] = blocking_finding_ids
        else:
            result["status"] = "pass"
            result["blocking"] = False
            result["finding_ids"] = []
    payload["status"] = "fail" if blocking_finding_ids else "warning"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _editor_gate_finding(finding_id: str, *, blocking: bool = True) -> dict[str, object]:
    return {
        "finding_id": finding_id,
        "finding_type": "target_relevance_gap",
        "severity": "high" if blocking else "medium",
        "blocking_level": "blocking" if blocking else "warning",
        "blocking": blocking,
        "artifact_id": "audited_brief",
        "repair_owner": "editor",
        "repair_stage_id": "editor",
        "repair_artifact_id": "audited_brief",
        "message": "Editor-owned target relevance finding.",
    }


def _human_gate_finding(finding_id: str, *, blocking: bool = True) -> dict[str, object]:
    return {
        "finding_id": finding_id,
        "finding_type": "target_mapping_ambiguous",
        "severity": "high" if blocking else "medium",
        "blocking_level": "blocking" if blocking else "warning",
        "blocking": blocking,
        "artifact_id": "audited_brief",
        "repair_owner": "human",
        "repair_stage_id": "editor",
        "repair_artifact_id": "audited_brief",
        "message": "Target mapping requires human review.",
    }


def _claim_ledger_gate_finding(finding_id: str, *, blocking: bool = True) -> dict[str, object]:
    return {
        "finding_id": finding_id,
        "finding_type": "claim_ledger_support_gap",
        "severity": "high" if blocking else "medium",
        "blocking_level": "blocking" if blocking else "warning",
        "blocking": blocking,
        "artifact_id": "claim_ledger",
        "repair_owner": "claim-ledger",
        "repair_stage_id": "claim-ledger",
        "repair_artifact_id": "claim_ledger",
        "message": "Claim Ledger support issue.",
    }


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
    assert payload["route_kind"] == "owner_stage_repair"
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
    assert payload["route_kind"] == "human_review"
    assert payload["repair_owner"] == "none"
    assert payload["review_owner"] == "human"
    assert payload["allowed_artifacts"] == []
    assert payload["source"]["route_classification"] == "input_limitation"
    assert payload["recommended_action"] == "request_human_review_or_start_fresh_workspace"


@pytest.mark.parametrize("repair_owner", ["human", "human_review", "human-review"])
def test_repair_route_treats_explicit_human_owner_as_human_review(tmp_path, capsys, repair_owner):
    ws = _workspace(tmp_path)
    initialize_runtime_state(workspace=ws)
    _write_quality_gate_report(
        ws,
        {
            "finding_id": "QG_HUMAN_001",
            "finding_type": "target_mapping_ambiguous",
            "severity": "high",
            "blocking": True,
            "artifact_id": "audited_brief",
            "repair_owner": repair_owner,
            "repair_stage_id": "editor",
            "repair_artifact_id": "audited_brief",
            "message": "Target mapping is ambiguous and requires human review.",
        },
    )

    rc = main(["repair", "route", "--workspace", str(ws), "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["route_kind"] == "human_review"
    assert payload["repair_owner"] == "none"
    assert payload["review_owner"] == "human"
    assert payload["allowed_artifacts"] == []
    assert payload["must_rerun_from"] == ""
    assert payload["recommended_action"] == "request_human_review_for_blocking_gate"
    assert payload["source"]["requested_owner"] == "human"

    rc = main(["repair", "start", "--workspace", str(ws), "--json"])

    assert rc == 1
    start_payload = json.loads(capsys.readouterr().out)
    assert start_payload["error_code"] == "E_ILLEGAL_TRANSITION"
    assert "requires human review" in start_payload["error"]


def test_repair_route_prioritizes_blocking_human_review_over_warning_repair(tmp_path, capsys):
    ws = _workspace(tmp_path)
    initialize_runtime_state(workspace=ws)
    path = _intermediate(ws) / "gates" / "auditor_quality_gate_report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": "multi-agent-brief-quality-gates/v1",
                "status": "fail",
                "findings": [
                    {
                        "finding_id": "QG_HUMAN_001",
                        "finding_type": "target_mapping_ambiguous",
                        "severity": "high",
                        "blocking": True,
                        "artifact_id": "audited_brief",
                        "repair_owner": "human",
                        "repair_stage_id": "editor",
                        "repair_artifact_id": "audited_brief",
                        "message": "Target mapping is ambiguous and requires human review.",
                    },
                    {
                        "finding_id": "QG_WARN_REPAIR_001",
                        "finding_type": "unsupported_claim",
                        "severity": "medium",
                        "blocking": False,
                        "artifact_id": "audited_brief",
                        "repair_owner": "editor",
                        "repair_stage_id": "editor",
                        "repair_artifact_id": "audited_brief",
                        "message": "Warning-only editor finding should not outrank the blocker.",
                    },
                ],
                "metadata": {"gate_stage_id": "auditor"},
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
    assert payload["route_kind"] == "human_review"
    assert payload["repair_owner"] == "none"
    assert payload["recommended_action"] == "request_human_review_for_blocking_gate"
    assert payload["source"]["finding_id"] == "QG_HUMAN_001"
    warning_routes = [
        route for route in payload["routes"] if route["source"]["finding_id"] == "QG_WARN_REPAIR_001"
    ]
    assert len(warning_routes) == 1
    assert warning_routes[0]["route_kind"] == "owner_stage_repair"
    assert warning_routes[0]["default_selected"] is False


def test_repair_route_for_gate_uses_current_finalize_gate_over_stale_auditor_gate(tmp_path):
    ws = _workspace(tmp_path)
    initialize_runtime_state(workspace=ws)
    _write_stage_quality_gate_report(
        ws,
        stage_id="auditor",
        finding=_human_gate_finding("QG_STALE_HUMAN_001"),
    )
    _write_stage_quality_gate_report(
        ws,
        stage_id="finalize",
        finding=_editor_gate_finding("QG_CURRENT_EDITOR_001"),
    )

    payload = route_repair_for_gate(
        workspace=ws,
        gate_stage_id="finalize",
        gate_artifact_id="finalize_quality_gate_report",
        repo_workdir=Path(__file__).resolve().parent.parent,
    )

    assert payload["ok"] is True
    assert payload["route_kind"] == "owner_stage_repair"
    assert payload["repair_owner"] == "editor"
    assert payload["source"]["kind"] == "finalize_quality_gate_report"
    assert payload["source"]["finding_id"] == "QG_CURRENT_EDITOR_001"
    assert all(route["source"]["kind"] == "finalize_quality_gate_report" for route in payload["routes"])

    default_payload = route_repair(workspace=ws)
    assert default_payload["ok"] is True
    assert default_payload["route_kind"] == "human_review"
    assert default_payload["source"]["kind"] == "auditor_quality_gate_report"
    assert default_payload["source"]["finding_id"] == "QG_STALE_HUMAN_001"


def test_repair_route_for_gate_returns_human_review_for_current_human_gate(tmp_path):
    ws = _workspace(tmp_path)
    initialize_runtime_state(workspace=ws)
    _write_stage_quality_gate_report(
        ws,
        stage_id="finalize",
        finding=_human_gate_finding("QG_CURRENT_HUMAN_001"),
    )

    payload = route_repair_for_gate(
        workspace=ws,
        gate_stage_id="finalize",
        gate_artifact_id="finalize_quality_gate_report",
        repo_workdir=Path(__file__).resolve().parent.parent,
    )

    assert payload["ok"] is True
    assert payload["route_kind"] == "human_review"
    assert payload["repair_owner"] == "none"
    assert payload["review_owner"] == "human"
    assert payload["source"]["finding_id"] == "QG_CURRENT_HUMAN_001"


def test_repair_route_for_gate_selects_blocking_editor_with_nonblocking_findings_present(tmp_path):
    ws = _workspace(tmp_path)
    initialize_runtime_state(workspace=ws)
    blocking_editor = _editor_gate_finding("QG_BLOCKING_EDITOR_001")
    nonblocking_claim_ledger = _claim_ledger_gate_finding("QG_WARNING_CLAIM_LEDGER_001", blocking=False)
    _write_stage_quality_gate_report(
        ws,
        stage_id="finalize",
        finding=blocking_editor,
    )
    _replace_stage_gate_findings(
        ws,
        stage_id="finalize",
        findings=[blocking_editor, nonblocking_claim_ledger],
        blocking_finding_ids=["QG_BLOCKING_EDITOR_001"],
    )

    payload = route_repair_for_gate(
        workspace=ws,
        gate_stage_id="finalize",
        gate_artifact_id="finalize_quality_gate_report",
        repo_workdir=Path(__file__).resolve().parent.parent,
    )

    assert payload["ok"] is True
    assert payload["route_kind"] == "owner_stage_repair"
    assert payload["repair_owner"] == "editor"
    assert payload["source"]["finding_id"] == "QG_BLOCKING_EDITOR_001"
    assert [route["source"]["finding_id"] for route in payload["routes"]] == ["QG_BLOCKING_EDITOR_001"]


def test_repair_route_for_gate_does_not_fall_back_to_nonblocking_legal_route(tmp_path):
    ws = _workspace(tmp_path)
    initialize_runtime_state(workspace=ws)
    _mark_fact_layer_imported(ws)
    blocking_claim_ledger = _claim_ledger_gate_finding("QG_BLOCKING_CLAIM_LEDGER_001")
    nonblocking_editor = _editor_gate_finding("QG_WARNING_EDITOR_001", blocking=False)
    _write_stage_quality_gate_report(
        ws,
        stage_id="finalize",
        finding=blocking_claim_ledger,
    )
    _replace_stage_gate_findings(
        ws,
        stage_id="finalize",
        findings=[blocking_claim_ledger, nonblocking_editor],
        blocking_finding_ids=["QG_BLOCKING_CLAIM_LEDGER_001"],
    )

    payload = route_repair_for_gate(
        workspace=ws,
        gate_stage_id="finalize",
        gate_artifact_id="finalize_quality_gate_report",
        repo_workdir=Path(__file__).resolve().parent.parent,
    )

    assert payload["ok"] is False
    assert payload["error_code"] == "E_REPAIR_NO_LEGAL_ROUTE"
    assert payload["repair_owner"] == "none"
    assert [route["source"]["finding_id"] for route in payload["routes"]] == ["QG_BLOCKING_CLAIM_LEDGER_001"]
    assert payload["routes"][0]["is_imported_fact_layer_forbidden"] is True


def test_repair_route_for_gate_returns_none_for_nonblocking_current_gate(tmp_path):
    ws = _workspace(tmp_path)
    initialize_runtime_state(workspace=ws)
    _write_stage_quality_gate_report(
        ws,
        stage_id="finalize",
        finding=_editor_gate_finding("QG_WARNING_EDITOR_001", blocking=False),
        status="warning",
        blocking=False,
    )

    payload = route_repair_for_gate(
        workspace=ws,
        gate_stage_id="finalize",
        gate_artifact_id="finalize_quality_gate_report",
        repo_workdir=Path(__file__).resolve().parent.parent,
    )

    assert payload["ok"] is True
    assert payload["route_kind"] == "none"
    assert payload["repair_owner"] == "none"
    assert payload["recommended_action"] == ""
    assert payload["reason"] == "No blocking current gate requires repair routing."
    assert payload["routes"] == []
    assert payload["finding_count"] == 0


def test_repair_route_for_gate_rejects_malformed_current_gate(tmp_path):
    ws = _workspace(tmp_path)
    initialize_runtime_state(workspace=ws)
    path = _intermediate(ws) / "gates" / "finalize_quality_gate_report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{broken", encoding="utf-8")

    payload = route_repair_for_gate(
        workspace=ws,
        gate_stage_id="finalize",
        gate_artifact_id="finalize_quality_gate_report",
        repo_workdir=Path(__file__).resolve().parent.parent,
    )

    assert payload["ok"] is False
    assert payload["error_code"] == "E_REPAIR_INPUT_INVALID"
    assert payload["gate_artifact_id"] == "finalize_quality_gate_report"


@pytest.mark.parametrize(
    ("state_key", "expected_source"),
    [
        ("runtime_manifest", "runtime_manifest"),
        ("workflow_state", "workflow_state"),
    ],
)
@pytest.mark.parametrize(
    ("payload_text", "expected_error"),
    [
        (None, "required control context file is missing"),
        ("{broken", "invalid JSON"),
        ("[]\n", "JSON payload must be an object"),
        (json.dumps({"run_id": "run-test"}, ensure_ascii=False) + "\n", "missing schema_version"),
        (json.dumps({"schema_version": "wrong-schema"}, ensure_ascii=False) + "\n", "schema_version must be"),
    ],
)
def test_repair_route_for_gate_rejects_invalid_required_control_context(
    tmp_path,
    state_key,
    expected_source,
    payload_text,
    expected_error,
):
    ws = _workspace(tmp_path)
    initialize_runtime_state(workspace=ws)
    _write_stage_quality_gate_report(
        ws,
        stage_id="finalize",
        finding=_editor_gate_finding("QG_CURRENT_EDITOR_001"),
    )
    path = runtime_state_paths(ws)[state_key]
    if payload_text is None:
        path.unlink()
    else:
        path.write_text(payload_text, encoding="utf-8")

    payload = route_repair_for_gate(
        workspace=ws,
        gate_stage_id="finalize",
        gate_artifact_id="finalize_quality_gate_report",
        repo_workdir=Path(__file__).resolve().parent.parent,
    )

    assert payload["ok"] is False
    assert payload["error_code"] == "E_REPAIR_INPUT_INVALID"
    assert payload["route_kind"] == "none"
    assert payload["gate_artifact_id"] == "finalize_quality_gate_report"
    assert payload["input_errors"][0]["source"] == expected_source
    assert expected_error in payload["input_errors"][0]["error"]


@pytest.mark.parametrize(
    ("payload_text", "expected_error"),
    [
        ("{broken", "invalid JSON"),
        ("[]\n", "JSON payload must be an object"),
        (json.dumps({"run_id": "run-test"}, ensure_ascii=False) + "\n", "missing schema_version"),
        (json.dumps({"schema_version": "wrong-schema"}, ensure_ascii=False) + "\n", "schema_version must be"),
    ],
)
def test_repair_route_for_gate_rejects_invalid_present_artifact_registry(
    tmp_path,
    payload_text,
    expected_error,
):
    ws = _workspace(tmp_path)
    initialize_runtime_state(workspace=ws)
    _write_stage_quality_gate_report(
        ws,
        stage_id="finalize",
        finding=_editor_gate_finding("QG_CURRENT_EDITOR_001"),
    )
    runtime_state_paths(ws)["artifact_registry"].write_text(payload_text, encoding="utf-8")

    payload = route_repair_for_gate(
        workspace=ws,
        gate_stage_id="finalize",
        gate_artifact_id="finalize_quality_gate_report",
        repo_workdir=Path(__file__).resolve().parent.parent,
    )

    assert payload["ok"] is False
    assert payload["error_code"] == "E_REPAIR_INPUT_INVALID"
    assert payload["route_kind"] == "none"
    assert payload["input_errors"][0]["source"] == "artifact_registry"
    assert expected_error in payload["input_errors"][0]["error"]


def test_repair_route_for_gate_allows_missing_artifact_registry_for_fresh_gate(tmp_path):
    ws = _workspace(tmp_path)
    initialize_runtime_state(workspace=ws)
    _write_stage_quality_gate_report(
        ws,
        stage_id="finalize",
        finding=_editor_gate_finding("QG_CURRENT_EDITOR_001"),
    )
    runtime_state_paths(ws)["artifact_registry"].unlink(missing_ok=True)

    payload = route_repair_for_gate(
        workspace=ws,
        gate_stage_id="finalize",
        gate_artifact_id="finalize_quality_gate_report",
        repo_workdir=Path(__file__).resolve().parent.parent,
    )

    assert payload["ok"] is True
    assert payload["route_kind"] == "owner_stage_repair"
    assert payload["repair_owner"] == "editor"
    assert payload["source"]["finding_id"] == "QG_CURRENT_EDITOR_001"


def test_repair_route_for_gate_accepts_finalize_delivery_markdown_brief_metadata(tmp_path):
    ws = _workspace(tmp_path)
    initialize_runtime_state(workspace=ws)
    path = _intermediate(ws) / "gates" / "finalize_quality_gate_report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_stage_quality_gate_report(
        ws,
        stage_id="finalize",
        finding=_editor_gate_finding("QG_CURRENT_EDITOR_001"),
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["metadata"]["brief"] = "output/delivery/brief.md"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    route = route_repair_for_gate(
        workspace=ws,
        gate_stage_id="finalize",
        gate_artifact_id="finalize_quality_gate_report",
        repo_workdir=Path(__file__).resolve().parent.parent,
    )

    assert route["ok"] is True
    assert route["route_kind"] == "owner_stage_repair"
    assert route["repair_owner"] == "editor"
    assert route["source"]["finding_id"] == "QG_CURRENT_EDITOR_001"


@pytest.mark.parametrize(
    "brief_ref",
    [
        "output/intermediate/audited_brief.md",
        "output/delivery/brief.txt",
        "output/delivery/nested/brief.md",
    ],
)
def test_repair_route_for_gate_rejects_binding_invalid_current_gate(tmp_path, brief_ref):
    ws = _workspace(tmp_path)
    initialize_runtime_state(workspace=ws)
    path = _intermediate(ws) / "gates" / "finalize_quality_gate_report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_stage_quality_gate_report(
        ws,
        stage_id="finalize",
        finding=_editor_gate_finding("QG_CURRENT_EDITOR_001"),
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["metadata"]["brief"] = brief_ref
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    route = route_repair_for_gate(
        workspace=ws,
        gate_stage_id="finalize",
        gate_artifact_id="finalize_quality_gate_report",
        repo_workdir=Path(__file__).resolve().parent.parent,
    )

    assert route["ok"] is False
    assert route["error_code"] == "E_REPAIR_INPUT_INVALID"
    assert any(
        "brief metadata must be output/brief.md or output/delivery/*.md" in error["error"]
        for error in route["input_errors"]
    )


def test_repair_route_for_gate_rejects_missing_required_gate_results(tmp_path):
    ws = _workspace(tmp_path)
    initialize_runtime_state(workspace=ws)
    path = _intermediate(ws) / "gates" / "finalize_quality_gate_report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_stage_quality_gate_report(
        ws,
        stage_id="finalize",
        finding=_editor_gate_finding("QG_CURRENT_EDITOR_001"),
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["gate_results"] = [
        result
        for result in payload["gate_results"]
        if result["gate_id"] == "target_relevance"
    ]
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    route = route_repair_for_gate(
        workspace=ws,
        gate_stage_id="finalize",
        gate_artifact_id="finalize_quality_gate_report",
        repo_workdir=Path(__file__).resolve().parent.parent,
    )

    assert route["ok"] is False
    assert route["error_code"] == "E_REPAIR_INPUT_INVALID"
    assert any("must include" in error["error"] for error in route["input_errors"])


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
            {
                "schema_version": "multi-agent-brief-quality-gates/v1",
                "status": "fail",
                "findings": [
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
                "metadata": {"gate_stage_id": "auditor"},
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
