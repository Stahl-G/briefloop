from __future__ import annotations

import json
from pathlib import Path

from multi_agent_brief.orchestrator.runtime_state import (
    append_event,
    build_completion_projection,
    initialize_runtime_state,
)
from multi_agent_brief.orchestrator.runtime_state._io import _sha256_file
from tests.helpers import write_workspace_files_under


ROOT = Path(__file__).resolve().parent.parent


def _write_workspace(tmp_path: Path) -> Path:
    return write_workspace_files_under(
        tmp_path,
        config_text="""
project:
  name: "Completion Projection Test"
output:
  path: "output"
input:
  path: "input"
""".strip(),
        user_text="# User\n",
        include_input_dir=True,
    )


def _intermediate(ws: Path) -> Path:
    path = ws / "output" / "intermediate"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _init_workspace(ws: Path) -> dict:
    state = initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_minimal_registry(ws, run_id=state["manifest"]["run_id"])
    return state


def _write_minimal_registry(ws: Path, *, run_id: str) -> None:
    _write_json(
        _intermediate(ws) / "artifact_registry.json",
        {
            "schema_version": "multi-agent-brief-artifact-registry/v1",
            "run_id": run_id,
            "updated_at": "2026-07-06T00:00:00+00:00",
            "artifacts": {},
        },
    )


def _set_workflow(ws: Path, **updates: object) -> dict:
    path = _intermediate(ws) / "workflow_state.json"
    workflow = _load_json(path)
    workflow.update(updates)
    _write_json(path, workflow)
    return workflow


def _write_finalize_report(
    ws: Path,
    *,
    status: str = "pass",
    reader_clean_status: str = "pass",
    promotion: str = "promoted",
) -> dict:
    output = ws / "output"
    intermediate = _intermediate(ws)
    claim_ledger = intermediate / "claim_ledger.json"
    claim_ledger.write_text('{"claims":[]}\n', encoding="utf-8")
    audited_brief = intermediate / "audited_brief.md"
    audited_brief.write_text("# Audited Brief\n\nClean audited text.\n", encoding="utf-8")
    audit_report = intermediate / "audit_report.json"
    audit_report.write_text('{"status":"pass","findings":[]}\n', encoding="utf-8")
    root_brief = output / "brief.md"
    root_brief.parent.mkdir(parents=True, exist_ok=True)
    root_brief.write_text("# Reader Brief\n\nClean reader text.\n", encoding="utf-8")
    delivery_dir = output / "delivery"
    delivery_dir.mkdir(parents=True, exist_ok=True)
    brief = delivery_dir / "brief.md"
    brief.write_text("# Reader Brief\n\nClean reader text.\n", encoding="utf-8")
    report = {
        "status": status,
        "finalize_transaction_id": "tx-finalize-001",
        "audited_brief": "output/intermediate/audited_brief.md",
        "reader_brief": "output/brief.md",
        "reader_clean": {"status": reader_clean_status, "sample_findings": []},
        "delivery_latest_dir": "output/delivery",
        "delivery_artifacts": ["output/delivery/brief.md"],
        "delivery_artifact_sha256": {"output/delivery/brief.md": _sha256_file(brief)},
        "delivery_promotion": promotion,
        "audit_binding": {
            "status": "pass",
            "claim_ledger_sha256": _sha256_file(claim_ledger),
            "audited_brief_sha256": _sha256_file(audited_brief),
            "audit_report_sha256": _sha256_file(audit_report),
            "findings": [],
            "warnings": [],
        },
    }
    _write_json(intermediate / "finalize_report.json", report)
    return report


def _write_gate_report(
    ws: Path,
    *,
    stage_id: str = "finalize",
    status: str = "pass",
    blocking: bool = False,
    artifact_id: str = "reader_brief",
) -> dict:
    finding_ids = ["QG-001"] if blocking else []
    findings = [
        {
            "finding_id": "QG-001",
            "finding_type": "target_relevance_failed",
            "severity": "high",
            "blocking_level": "blocking",
            "blocking": True,
            "stage_id": stage_id,
            "gate_stage_id": stage_id,
            "artifact_id": artifact_id,
            "gate_artifact_id": "finalize_quality_gate_report",
            "repair_stage_id": stage_id,
            "repair_artifact_id": artifact_id,
            "repair_owner": "orchestrator",
            "message": "Synthetic blocking finding.",
            "metadata": {},
        }
    ] if blocking else []
    payload = {
        "schema_version": "multi-agent-brief-quality-gates/v1",
        "created_at": "2026-07-06T00:00:00+00:00",
        "updated_at": "2026-07-06T00:00:00+00:00",
        "workspace": ".",
        "report_date": "2026-07-06",
        "policy_pack": "default",
        "status": status,
        "gate_results": [
            {
                "gate_id": "coverage_omission",
                "status": "pass",
                "blocking": False,
                "finding_ids": [],
            },
            {
                "gate_id": "freshness",
                "status": "pass",
                "blocking": False,
                "finding_ids": [],
            },
            {
                "gate_id": "material_fact",
                "status": "pass",
                "blocking": False,
                "finding_ids": [],
            },
            {
                "gate_id": "target_relevance",
                "status": "fail" if blocking else status,
                "blocking": blocking,
                "finding_ids": finding_ids,
            },
        ],
        "findings": findings,
        "metadata": {
            "stage_id": stage_id,
            "gate_stage_id": stage_id,
            "gate_artifact_id": "finalize_quality_gate_report",
            "brief": "output/brief.md" if stage_id == "finalize" else "output/intermediate/audited_brief.md",
            "ledger": "output/intermediate/claim_ledger.json",
        },
    }
    path = _intermediate(ws) / "gates" / "finalize_quality_gate_report.json"
    _write_json(path, payload)
    return payload


def _append_finalize_event(ws: Path) -> None:
    manifest = _load_json(_intermediate(ws) / "runtime_manifest.json")
    append_event(
        workspace=ws,
        run_id=manifest["run_id"],
        event_type="decision_recorded",
        actor="cli",
        stage_id="finalize",
        decision="finalize",
        reason="finalize-complete recorded",
    )


def test_completion_projection_reads_recorded_finalize_delivery_truth(tmp_path: Path) -> None:
    ws = _write_workspace(tmp_path)
    _init_workspace(ws)
    _set_workflow(ws, current_stage="finalize")
    _write_finalize_report(ws)
    _write_gate_report(ws)
    _append_finalize_event(ws)

    payload = build_completion_projection(workspace=ws, repo_workdir=ROOT)

    assert payload["schema_version"] == "briefloop.completion_projection.v1"
    assert payload["delivery_truth"]["valid"] is True
    assert payload["delivery_truth"]["source"] == "finalize_report"
    assert payload["finalize_truth"]["delivery_promotion"] == "promoted"
    assert payload["next_allowed_action"] == "inspect_status_before_delivery_or_quality"


def test_completion_projection_stops_on_missing_required_control_file(tmp_path: Path) -> None:
    ws = _write_workspace(tmp_path)
    _init_workspace(ws)
    (_intermediate(ws) / "runtime_manifest.json").unlink()

    payload = build_completion_projection(workspace=ws, repo_workdir=ROOT)

    assert payload["control_files"]["runtime_manifest"] == "missing"
    assert payload["next_allowed_action"] == "inspect_unreadable_or_missing_control_files"


def test_completion_projection_requires_finalize_gate_before_delivery_guidance(tmp_path: Path) -> None:
    ws = _write_workspace(tmp_path)
    _init_workspace(ws)
    _set_workflow(ws, current_stage="finalize")
    _write_finalize_report(ws)
    _append_finalize_event(ws)

    payload = build_completion_projection(workspace=ws, repo_workdir=ROOT)

    assert payload["gate_truth"]["status"] == "missing"
    assert any(
        finding.startswith("finalize_completion_blocker:")
        and "finalize_quality_gate_report.json is required" in finding
        for finding in payload["delivery_truth"]["findings"]
    )
    assert payload["next_allowed_action"] == "run_finalize_gate_or_finalize_complete"


def test_completion_projection_requires_finalize_event_before_delivery_guidance(tmp_path: Path) -> None:
    ws = _write_workspace(tmp_path)
    _init_workspace(ws)
    _set_workflow(ws, current_stage="finalize")
    _write_finalize_report(ws)
    _write_gate_report(ws)

    payload = build_completion_projection(workspace=ws, repo_workdir=ROOT)

    assert payload["event_truth"]["finalize_event_present"] is False
    assert payload["next_allowed_action"] == "run_finalize_gate_or_finalize_complete"


def test_completion_projection_uses_configured_gate_artifacts(tmp_path: Path) -> None:
    ws = _write_workspace(tmp_path)
    _init_workspace(ws)
    _set_workflow(ws, current_stage="finalize")
    _write_finalize_report(ws)
    _write_gate_report(ws, status="warning", artifact_id="claim_support_matrix")
    _append_finalize_event(ws)

    payload = build_completion_projection(workspace=ws, repo_workdir=ROOT)

    assert payload["gate_truth"]["status"] == "warning"
    assert payload["gate_truth"]["validation_errors"] == []
    assert payload["next_allowed_action"] == "inspect_status_before_delivery_or_quality"


def test_completion_projection_blocks_malformed_gate_report(tmp_path: Path) -> None:
    ws = _write_workspace(tmp_path)
    _init_workspace(ws)
    _set_workflow(ws, current_stage="finalize")
    _write_finalize_report(ws)
    _write_json(
        _intermediate(ws) / "gates" / "finalize_quality_gate_report.json",
        {"schema_version": "multi-agent-brief-quality-gates/v1", "status": "pass"},
    )
    _append_finalize_event(ws)

    payload = build_completion_projection(workspace=ws, repo_workdir=ROOT)

    assert payload["gate_truth"]["status"] == "invalid"
    assert payload["next_allowed_action"] == "stop_resolve_blocking_gate_report"


def test_completion_projection_rejects_gate_report_not_bound_to_finalize(tmp_path: Path) -> None:
    ws = _write_workspace(tmp_path)
    _init_workspace(ws)
    _set_workflow(ws, current_stage="finalize")
    _write_finalize_report(ws)
    gate = _write_gate_report(ws)
    gate["metadata"]["gate_stage_id"] = "auditor"
    gate["metadata"]["stage_id"] = "auditor"
    _write_json(_intermediate(ws) / "gates" / "finalize_quality_gate_report.json", gate)
    _append_finalize_event(ws)

    payload = build_completion_projection(workspace=ws, repo_workdir=ROOT)

    assert payload["gate_truth"]["blocking"] is True
    assert any("must be generated for finalize completion" in item for item in payload["gate_truth"]["validation_errors"])
    assert payload["delivery_truth"]["valid"] is False
    assert payload["next_allowed_action"] == "stop_resolve_blocking_gate_report"


def test_completion_projection_blocks_blocking_gate_report(tmp_path: Path) -> None:
    ws = _write_workspace(tmp_path)
    _init_workspace(ws)
    _set_workflow(ws, current_stage="finalize")
    _write_finalize_report(ws)
    _write_gate_report(ws, blocking=True)
    _append_finalize_event(ws)

    payload = build_completion_projection(workspace=ws, repo_workdir=ROOT)

    assert payload["gate_truth"]["blocking"] is True
    assert payload["next_allowed_action"] == "stop_resolve_blocking_gate_report"


def test_completion_projection_blocks_failed_finalize_report(tmp_path: Path) -> None:
    ws = _write_workspace(tmp_path)
    _init_workspace(ws)
    _set_workflow(ws, current_stage="finalize")
    _write_finalize_report(ws, status="fail")
    _write_gate_report(ws)
    _append_finalize_event(ws)

    payload = build_completion_projection(workspace=ws, repo_workdir=ROOT)

    assert payload["finalize_truth"]["report_status"] == "fail"
    assert payload["delivery_truth"]["valid"] is False
    assert payload["next_allowed_action"] == "stop_finalize_failed_no_valid_delivery"


def test_completion_projection_blocks_failed_reader_clean(tmp_path: Path) -> None:
    ws = _write_workspace(tmp_path)
    _init_workspace(ws)
    _set_workflow(ws, current_stage="finalize")
    _write_finalize_report(ws, reader_clean_status="fail", promotion="skipped_reader_clean_failed")
    _write_gate_report(ws)
    _append_finalize_event(ws)

    payload = build_completion_projection(workspace=ws, repo_workdir=ROOT)

    assert payload["finalize_truth"]["reader_clean_status"] == "fail"
    assert payload["next_allowed_action"] == "stop_finalize_failed_no_valid_delivery"


def test_completion_projection_blocks_stale_registry_artifact(tmp_path: Path) -> None:
    ws = _write_workspace(tmp_path)
    _init_workspace(ws)
    _set_workflow(ws, current_stage="finalize")
    _write_finalize_report(ws)
    _write_gate_report(ws)
    _append_finalize_event(ws)
    registry_path = _intermediate(ws) / "artifact_registry.json"
    registry = _load_json(registry_path)
    registry["artifacts"]["audited_brief"] = {
        "status": "stale",
        "validation_result": "sha_mismatch",
    }
    _write_json(registry_path, registry)

    payload = build_completion_projection(workspace=ws, repo_workdir=ROOT)

    assert payload["artifacts"]["invalid_or_stale"][0]["artifact_id"] == "audited_brief"
    assert payload["delivery_truth"]["valid"] is False
    assert payload["next_allowed_action"] == "inspect_invalid_or_stale_artifacts"


def test_completion_projection_rechecks_missing_delivery_artifact(tmp_path: Path) -> None:
    ws = _write_workspace(tmp_path)
    _init_workspace(ws)
    _set_workflow(ws, current_stage="finalize")
    _write_finalize_report(ws)
    _write_gate_report(ws)
    _append_finalize_event(ws)
    (ws / "output" / "delivery" / "brief.md").unlink()

    payload = build_completion_projection(workspace=ws, repo_workdir=ROOT)

    assert payload["delivery_truth"]["valid"] is False
    assert any(
        finding.startswith("finalize_completion_blocker:")
        and "references missing delivery artifact" in finding
        for finding in payload["delivery_truth"]["findings"]
    )
    assert payload["next_allowed_action"] == "inspect_invalid_or_incomplete_finalize_report_delivery_truth"


def test_completion_projection_rechecks_dirty_delivery_artifact(tmp_path: Path) -> None:
    ws = _write_workspace(tmp_path)
    _init_workspace(ws)
    _set_workflow(ws, current_stage="finalize")
    _write_finalize_report(ws)
    _write_gate_report(ws)
    _append_finalize_event(ws)
    (ws / "output" / "delivery" / "brief.md").write_text(
        "# Reader Brief\n\nTampered reader text.\n",
        encoding="utf-8",
    )

    payload = build_completion_projection(workspace=ws, repo_workdir=ROOT)

    assert payload["delivery_truth"]["valid"] is False
    assert any(
        finding.startswith("finalize_completion_blocker:")
        and "delivery artifact hash mismatch" in finding
        for finding in payload["delivery_truth"]["findings"]
    )
    assert payload["next_allowed_action"] == "inspect_invalid_or_incomplete_finalize_report_delivery_truth"


def test_completion_projection_consumes_audit_binding_verdict(tmp_path: Path) -> None:
    ws = _write_workspace(tmp_path)
    _init_workspace(ws)
    _set_workflow(ws, current_stage="finalize")
    _write_finalize_report(ws)
    _write_gate_report(ws)
    _append_finalize_event(ws)
    (ws / "output" / "intermediate" / "audited_brief.md").write_text(
        "# Audited Brief\n\nChanged after finalize report.\n",
        encoding="utf-8",
    )

    payload = build_completion_projection(workspace=ws, repo_workdir=ROOT)

    assert payload["delivery_truth"]["valid"] is False
    assert any(
        finding.startswith("finalize_completion_blocker:")
        and "audit_binding.audited_brief_sha256 does not match current artifact bytes" in finding
        for finding in payload["delivery_truth"]["findings"]
    )
    assert payload["next_allowed_action"] == "inspect_invalid_or_incomplete_finalize_report_delivery_truth"


def test_completion_projection_stops_on_blocked_workflow(tmp_path: Path) -> None:
    ws = _write_workspace(tmp_path)
    _init_workspace(ws)
    _set_workflow(ws, blocked=True, blocking_reason="human review")

    payload = build_completion_projection(workspace=ws, repo_workdir=ROOT)

    assert payload["next_allowed_action"] == "stop_workflow_blocked_human_review_required"


def test_completion_projection_stops_on_active_repair(tmp_path: Path) -> None:
    ws = _write_workspace(tmp_path)
    _init_workspace(ws)
    _set_workflow(ws, active_repair={"stage_id": "editor"})

    payload = build_completion_projection(workspace=ws, repo_workdir=ROOT)

    assert payload["next_allowed_action"] == "stop_complete_or_inspect_active_repair"


def test_completion_projection_stops_on_empty_active_repair_object(tmp_path: Path) -> None:
    ws = _write_workspace(tmp_path)
    _init_workspace(ws)
    _set_workflow(ws, active_repair={})

    payload = build_completion_projection(workspace=ws, repo_workdir=ROOT)

    assert payload["workflow"]["active_repair_present"] is True
    assert payload["next_allowed_action"] == "stop_complete_or_inspect_active_repair"


def test_completion_projection_stops_on_contaminated_run_integrity(tmp_path: Path) -> None:
    ws = _write_workspace(tmp_path)
    _init_workspace(ws)
    _set_workflow(
        ws,
        run_integrity={
            "status": "contaminated",
            "reference_eligible": False,
            "clean_single_shot": False,
            "reasons": [{"reason_code": "frozen_artifact_changed"}],
        },
    )

    payload = build_completion_projection(workspace=ws, repo_workdir=ROOT)

    assert payload["run_integrity"]["status"] == "contaminated"
    assert payload["next_allowed_action"] == "stop_human_review_or_supersede"


def test_completion_projection_contaminated_prefers_supersede_over_workflow_blocked(tmp_path: Path) -> None:
    ws = _write_workspace(tmp_path)
    _init_workspace(ws)
    _set_workflow(
        ws,
        blocked=True,
        blocking_reason="Frozen artifact changed after stage-complete.",
        run_integrity={
            "status": "contaminated",
            "reference_eligible": False,
            "clean_single_shot": False,
            "reasons": [{"reason_code": "frozen_artifact_changed"}],
        },
    )

    payload = build_completion_projection(workspace=ws, repo_workdir=ROOT)

    assert payload["next_allowed_action"] == "stop_human_review_or_supersede"


def test_completion_projection_contaminated_repaired_requires_downstream_rerun(tmp_path: Path) -> None:
    ws = _write_workspace(tmp_path)
    _init_workspace(ws)
    _set_workflow(
        ws,
        run_integrity={
            "status": "contaminated_repaired",
            "reference_eligible": False,
            "clean_single_shot": False,
            "reasons": [{"reason_code": "frozen_artifact_changed"}],
        },
    )

    payload = build_completion_projection(workspace=ws, repo_workdir=ROOT)

    assert payload["run_integrity"]["status"] == "contaminated_repaired"
    assert payload["next_allowed_action"] == "rerun_downstream_auditor_finalize"


def test_completion_projection_rejects_invalid_experiment_condition_before_finalize(tmp_path: Path) -> None:
    ws = _write_workspace(tmp_path)
    _init_workspace(ws)
    _set_workflow(ws, current_stage="finalize")
    _write_finalize_report(ws)
    _write_gate_report(ws)
    _append_finalize_event(ws)
    condition = ws / "experiment" / "080" / "condition.json"
    _write_json(condition, {"assessment_target": ["auditable_brief"]})

    payload = build_completion_projection(workspace=ws, repo_workdir=ROOT)

    assert payload["assessment_target"]["status"] == "invalid_condition"
    assert payload["next_allowed_action"] == "inspect_invalid_experiment_condition"
