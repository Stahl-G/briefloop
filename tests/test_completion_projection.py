from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

from multi_agent_brief.orchestrator.runtime_state import (
    append_event,
    build_completion_projection,
    initialize_runtime_state,
)
from multi_agent_brief.orchestrator.runtime_state._io import _sha256_file
from multi_agent_brief.orchestrator.runtime_state.errors import RuntimeStateError
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


def test_completion_projection_requires_delivery_eligibility_for_delivery_action(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = _write_workspace(tmp_path)
    _init_workspace(ws)
    _set_workflow(ws, current_stage="finalize")
    _write_finalize_report(ws)
    _write_gate_report(ws)
    _append_finalize_event(ws)
    monkeypatch.setattr(
        importlib.import_module(
            "multi_agent_brief.orchestrator.runtime_state.completion_projection"
        ),
        "evaluate_delivery_eligibility",
        lambda *_args, **_kwargs: {
            "allowed": False,
            "reference_eligible": False,
            "reason_code": "synthetic_eligibility_block",
        },
    )

    payload = build_completion_projection(workspace=ws, repo_workdir=ROOT)

    assert payload["delivery_truth"]["valid"] is True
    assert payload["delivery_truth"]["eligibility"]["allowed"] is False
    assert payload["next_allowed_action"] == "stop_delivery_not_eligible"


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


def test_completion_projection_contaminated_without_recovery_stops(tmp_path: Path) -> None:
    """Real contaminated state (frozen artifact edited after freeze) with no
    recovery transaction must stop at human review / supersede."""
    from tests.test_runtime_state import _contaminated_editor_artifact_workspace

    ws, _old_sha, _current_sha = _contaminated_editor_artifact_workspace(tmp_path)

    payload = build_completion_projection(workspace=ws, repo_workdir=ROOT)

    assert payload["run_integrity"]["status"] == "contaminated"
    assert payload["recovery_truth"]["status"] == "awaiting_human_or_supersede"
    assert payload["next_allowed_action"] == "stop_human_review_or_supersede"


def test_completion_projection_replays_sticky_current_run_contamination(
    tmp_path: Path,
) -> None:
    ws = _write_workspace(tmp_path)
    state = _init_workspace(ws)
    append_event(
        workspace=ws,
        run_id=state["manifest"]["run_id"],
        event_type="run_integrity_contaminated",
        actor="orchestrator",
        stage_id="editor",
        artifact_id="audited_brief",
        reason="Event authority must override a stale clean workflow field.",
        metadata={
            "reason_code": "frozen_artifact_changed",
            "message": "Event authority must override a stale clean workflow field.",
        },
    )

    payload = build_completion_projection(workspace=ws, repo_workdir=ROOT)

    assert payload["run_integrity"]["status"] == "contaminated"
    assert payload["recovery_truth"]["status"] == "awaiting_human_or_supersede"
    assert payload["delivery_truth"]["eligibility"]["allowed"] is False
    assert payload["next_allowed_action"] == "stop_human_review_or_supersede"


def test_completion_projection_rejects_manifest_workflow_run_id_mismatch(
    tmp_path: Path,
) -> None:
    ws = _write_workspace(tmp_path)
    state = _init_workspace(ws)
    workflow = _load_json(_intermediate(ws) / "workflow_state.json")
    workflow["run_id"] = "run-mismatched-workflow-001"
    _write_json(_intermediate(ws) / "workflow_state.json", workflow)
    append_event(
        workspace=ws,
        run_id=state["manifest"]["run_id"],
        event_type="run_integrity_contaminated",
        actor="orchestrator",
        reason="Manifest-scoped contamination must not bind to another workflow run.",
        metadata={"reason_code": "frozen_artifact_changed"},
    )

    payload = build_completion_projection(workspace=ws, repo_workdir=ROOT)

    assert payload["control_files"]["workflow_state"] == "invalid_run_integrity"
    assert payload["run_integrity"]["status"] == "unknown"
    assert payload["recovery_truth"]["status"] == "invalid_recovery_state"
    assert payload["next_allowed_action"] == "inspect_unreadable_or_missing_control_files"


def test_completion_projection_rejects_recovery_without_current_run_contamination(
    tmp_path: Path,
) -> None:
    ws = _write_workspace(tmp_path)
    state = _init_workspace(ws)
    transaction_id = "tx-recovery-without-contamination"
    workflow = _load_json(_intermediate(ws) / "workflow_state.json")
    workflow["run_integrity"] = {
        "status": "contaminated",
        "reference_eligible": False,
        "clean_single_shot": False,
        "reasons": [{"reason_code": "synthetic_unbound_integrity"}],
    }
    workflow["current_stage"] = "auditor"
    workflow["last_repair_transaction"] = {
        "transaction_id": transaction_id,
        "stage_id": "editor",
        "decision": "repair_complete",
    }
    _write_json(_intermediate(ws) / "workflow_state.json", workflow)
    append_event(
        workspace=ws,
        run_id=state["manifest"]["run_id"],
        event_type="repair_completed",
        actor="orchestrator",
        stage_id="editor",
        reason="Synthetic recovery without contamination authority.",
        metadata={
            "transaction_id": transaction_id,
            "next_stage": "auditor",
        },
    )

    payload = build_completion_projection(workspace=ws, repo_workdir=ROOT)

    assert payload["recovery_truth"]["status"] == "invalid_recovery_state"
    assert (
        payload["recovery_truth"]["reason_code"]
        == "recovery_without_contamination_event"
    )
    assert payload["next_allowed_action"] == "stop_invalid_recovery_state"


def test_completion_projection_supersede_rerun_tracks_owner_downstream(tmp_path: Path) -> None:
    """After a real supersede transaction the run stays contaminated; the rerun
    lane comes from the bound recovery timeline plus the transaction's own
    workflow.current_stage rewind (editor supersede rewinds to auditor)."""
    from multi_agent_brief.orchestrator.runtime_state import supersede_stage_artifact_transaction
    from tests.test_runtime_state import _contaminated_editor_artifact_workspace

    ws, _old_sha, _current_sha = _contaminated_editor_artifact_workspace(tmp_path)
    supersede_stage_artifact_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="editor",
        artifact="output/intermediate/audited_brief.md",
        reason="human approved supersede after contaminated direct edit",
    )

    payload = build_completion_projection(workspace=ws, repo_workdir=ROOT)

    assert payload["run_integrity"]["status"] == "contaminated"
    recovery = payload["recovery_truth"]
    assert recovery["status"] == "downstream_rerun_pending"
    assert recovery["last_recovery_event_type"] == "repair_stage_superseded"
    assert recovery["superseded_stages"] == ["editor"]
    assert "auditor" in recovery["stale_stages"]
    assert payload["workflow"]["current_stage"] == "auditor"
    assert payload["next_allowed_action"] == "rerun_downstream_from_auditor"


def test_completion_projection_recontamination_after_supersede_stops_again(tmp_path: Path) -> None:
    """An old supersede cannot vouch for a new contamination: after a real
    supersede, a second direct edit of the superseded artifact recontaminates
    the run and the projection must stop again."""
    from multi_agent_brief.orchestrator.runtime_state import (
        check_runtime_state,
        supersede_stage_artifact_transaction,
    )
    from tests.test_runtime_state import _contaminated_editor_artifact_workspace

    ws, _old_sha, _current_sha = _contaminated_editor_artifact_workspace(tmp_path)
    supersede_stage_artifact_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="editor",
        artifact="output/intermediate/audited_brief.md",
        reason="human approved supersede after contaminated direct edit",
    )
    audited = ws / "output" / "intermediate" / "audited_brief.md"
    audited.write_text("# Brief\n\nSecond direct post-supersede edit. [src:CL-001]\n", encoding="utf-8")
    with pytest.raises(RuntimeStateError):
        check_runtime_state(workspace=ws, repo_workdir=ROOT)

    payload = build_completion_projection(workspace=ws, repo_workdir=ROOT)

    assert payload["run_integrity"]["status"] == "contaminated"
    assert payload["recovery_truth"]["status"] == "awaiting_human_or_supersede"
    assert payload["next_allowed_action"] == "stop_human_review_or_supersede"


def test_completion_projection_repair_complete_enables_downstream_rerun(tmp_path: Path) -> None:
    """The legal owner-stage repair path (repair start -> owner edit -> repair
    complete) is also a recovery transaction: after it, the operator reruns
    downstream instead of being told to supersede again."""
    from multi_agent_brief.orchestrator.runtime_state import (
        complete_repair_transaction,
        complete_stage_transaction,
        initialize_runtime_state,
        start_repair_transaction,
    )
    from tests.test_runtime_state import (
        _set_current_stage,
        _valid_claim_ledger_payload,
        _write_json_artifact,
    )

    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _write_json_artifact(ws, "claim_ledger.json", _valid_claim_ledger_payload())
    _set_current_stage(ws, "analyst")
    audited = _intermediate(ws) / "audited_brief.md"
    audited.write_text("# Brief\n\nAnalyst draft. [src:CL-001]\n", encoding="utf-8")
    complete_stage_transaction(workspace=ws, repo_workdir=ROOT, stage_id="analyst", reason="analyst complete")
    audited.write_text("# Brief\n\nEditor-polished draft. [src:CL-001]\n", encoding="utf-8")
    complete_stage_transaction(workspace=ws, repo_workdir=ROOT, stage_id="editor", reason="editor complete")
    audited.write_text("# Brief\n\nDirect post-freeze edit. [src:CL-001]\n", encoding="utf-8")
    start_repair_transaction(workspace=ws, repo_workdir=ROOT)
    audited.write_text("# Brief\n\nOwner repair edit. [src:CL-001]\n", encoding="utf-8")
    complete_repair_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        reason="editor repaired audited brief from deterministic route",
    )

    payload = build_completion_projection(workspace=ws, repo_workdir=ROOT)

    assert payload["run_integrity"]["status"] == "contaminated"
    recovery = payload["recovery_truth"]
    assert recovery["status"] == "downstream_rerun_pending"
    assert recovery["last_recovery_event_type"] == "repair_completed"
    current_stage = payload["workflow"]["current_stage"]
    assert payload["next_allowed_action"] == f"rerun_downstream_from_{current_stage}"


def test_completion_projection_recovery_binding_mismatch_fails_closed(tmp_path: Path) -> None:
    """A recovery event that does not bind to workflow.last_repair_transaction
    is not recovery authority; the projection fails closed."""
    from multi_agent_brief.orchestrator.runtime_state import supersede_stage_artifact_transaction
    from tests.test_runtime_state import _contaminated_editor_artifact_workspace

    ws, _old_sha, _current_sha = _contaminated_editor_artifact_workspace(tmp_path)
    supersede_stage_artifact_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="editor",
        artifact="output/intermediate/audited_brief.md",
        reason="human approved supersede after contaminated direct edit",
    )
    workflow = _load_json(_intermediate(ws) / "workflow_state.json")
    tampered = dict(workflow["last_repair_transaction"])
    tampered["transaction_id"] = "tx-tampered-000"
    _set_workflow(ws, last_repair_transaction=tampered)

    payload = build_completion_projection(workspace=ws, repo_workdir=ROOT)

    assert payload["recovery_truth"]["status"] == "invalid_recovery_state"
    assert payload["next_allowed_action"] == "stop_invalid_recovery_state"


def test_recovery_truth_rejects_empty_finalize_transaction_id() -> None:
    from multi_agent_brief.orchestrator.recovery_state import evaluate_recovery_truth

    payload = evaluate_recovery_truth(
        workflow={
            "run_id": "run-recovery-001",
            "last_repair_transaction": {
                "transaction_id": "tx-repair-001",
                "decision": "supersede_stage",
                "stage_id": "editor",
            },
            "last_completion_transaction": {
                "transaction_id": "",
                "stage_id": "finalize",
                "decision": "finalize",
            },
        },
        workflow_status="present",
        event_records=[
            {
                "run_id": "run-recovery-001",
                "event_type": "run_integrity_contaminated",
            },
            {
                "run_id": "run-recovery-001",
                "event_type": "repair_stage_superseded",
                "stage_id": "editor",
                "metadata": {
                    "transaction_id": "tx-repair-001",
                    "next_stage": "auditor",
                },
            },
            {
                "run_id": "run-recovery-001",
                "event_type": "decision_recorded",
                "stage_id": "finalize",
                "decision": "finalize",
                "metadata": {"transaction_id": ""},
            },
        ],
        run_integrity={"status": "contaminated_repaired"},
        run_id="run-recovery-001",
        current_stage="finalize",
        stage_order=["editor", "auditor", "finalize"],
    )

    assert payload["status"] == "invalid_recovery_state"
    assert payload["reason_code"] == "terminal_finalize_binding_invalid"


def test_completion_projection_later_orphan_recovery_event_fails_closed(tmp_path: Path) -> None:
    """A later recovery-shaped event without the workflow transaction binding
    is a partial-write signal, not an event the projection may silently skip."""
    from multi_agent_brief.orchestrator.runtime_state import supersede_stage_artifact_transaction
    from tests.test_runtime_state import _contaminated_editor_artifact_workspace

    ws, _old_sha, _current_sha = _contaminated_editor_artifact_workspace(tmp_path)
    supersede_stage_artifact_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="editor",
        artifact="output/intermediate/audited_brief.md",
        reason="human approved supersede after contaminated direct edit",
    )
    manifest = _load_json(_intermediate(ws) / "runtime_manifest.json")
    append_event(
        workspace=ws,
        run_id=manifest["run_id"],
        event_type="repair_completed",
        actor="orchestrator",
        stage_id="editor",
        reason="Synthetic orphan recovery event.",
        metadata={
            "transaction_id": "tx-orphan-recovery-001",
            "next_stage": "auditor",
        },
    )

    payload = build_completion_projection(workspace=ws, repo_workdir=ROOT)

    assert payload["recovery_truth"]["status"] == "invalid_recovery_state"
    assert payload["recovery_truth"]["reason_code"] == "recovery_transaction_binding_invalid"
    assert payload["next_allowed_action"] == "stop_invalid_recovery_state"


def test_completion_projection_recovery_survives_downstream_progress(tmp_path: Path) -> None:
    """metadata.next_stage is the rerun START stage, not a permanent
    current-stage requirement: after the auditor rerun completes for real and
    the workflow advances to finalize, recovery becomes finalize-ready instead
    of demanding another downstream rerun."""
    from multi_agent_brief.orchestrator.runtime_state import (
        complete_stage_transaction,
        supersede_stage_artifact_transaction,
    )
    from tests.test_runtime_state import (
        _contaminated_editor_artifact_workspace,
        _valid_audit_report_payload,
        _write_finalize_report,
        _write_json_artifact,
        _write_quality_gate_report,
    )

    ws, _old_sha, _current_sha = _contaminated_editor_artifact_workspace(tmp_path)
    supersede_stage_artifact_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="editor",
        artifact="output/intermediate/audited_brief.md",
        reason="human approved supersede after contaminated direct edit",
    )
    refreshed = json.loads(_valid_audit_report_payload())
    refreshed["summary"] = "Auditor rerun against the superseded brief revision."
    _write_json_artifact(ws, "audit_report.json", json.dumps(refreshed) + "\n")
    _write_quality_gate_report(ws, stage_id="auditor")
    complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="auditor",
        reason="auditor reran against the superseded revision",
    )
    _set_workflow(
        ws,
        blocked=True,
        blocking_reason="Stale blocker retained after the bound auditor rerun.",
    )

    payload = build_completion_projection(workspace=ws, repo_workdir=ROOT)

    assert payload["workflow"]["current_stage"] == "finalize"
    recovery = payload["recovery_truth"]
    assert recovery["status"] == "ready_for_finalize"
    assert recovery["rerun_start_stage"] == "auditor"
    assert recovery["finalize_allowed"] is True
    assert payload["next_allowed_action"] == "run_finalize_after_recovery"

    _write_quality_gate_report(ws, stage_id="finalize")
    _write_finalize_report(ws)
    payload = build_completion_projection(workspace=ws, repo_workdir=ROOT)

    assert payload["recovery_truth"]["status"] == "ready_for_finalize"
    assert payload["next_allowed_action"] == "run_finalize_gate_or_finalize_complete"


def test_completion_projection_source_discovery_supersede_advances_multiple_stages(tmp_path: Path) -> None:
    """A source-discovery supersede rewinds to input-governance; completing
    input-governance for real advances the workflow and recovery must follow
    the current stage downstream of the rerun start."""
    from multi_agent_brief.orchestrator.runtime_state import (
        check_runtime_state,
        complete_stage_transaction,
        initialize_runtime_state,
        supersede_stage_artifact_transaction,
    )
    from tests.test_runtime_state import (
        _set_current_stage,
        _valid_candidate_claims_payload,
        _valid_claim_ledger_payload,
        _valid_screened_candidates_payload,
        _write_json_artifact,
    )

    ws = _write_workspace(tmp_path)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    _set_current_stage(ws, "claim-ledger")
    (ws / "source_candidates.yaml").write_text(
        "schema_version: mabw.source_candidates.v1\n"
        "artifact_type: source_discovery_candidates\n"
        "recommended_sources:\n"
        "  - name: Original Source\n"
        "    url: https://example.com/original\n",
        encoding="utf-8",
    )
    _write_json_artifact(ws, "candidate_claims.json", _valid_candidate_claims_payload())
    _write_json_artifact(ws, "screened_candidates.json", _valid_screened_candidates_payload())
    _write_json_artifact(ws, "claim_ledger.json", _valid_claim_ledger_payload())
    check_runtime_state(workspace=ws, repo_workdir=ROOT)
    (ws / "source_candidates.yaml").write_text(
        "schema_version: mabw.source_candidates.v1\n"
        "artifact_type: source_discovery_candidates\n"
        "recommended_sources:\n"
        "  - name: Superseded Source\n"
        "    url: https://example.com/superseded\n",
        encoding="utf-8",
    )
    with pytest.raises(RuntimeStateError):
        check_runtime_state(workspace=ws, repo_workdir=ROOT)
    supersede_stage_artifact_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="source-discovery",
        artifact="source_candidates.yaml",
        reason="human approved supersede after source candidate edit",
    )

    payload = build_completion_projection(workspace=ws, repo_workdir=ROOT)
    assert payload["workflow"]["current_stage"] == "input-governance"
    assert payload["recovery_truth"]["status"] == "downstream_rerun_pending"
    assert payload["next_allowed_action"] == "rerun_downstream_from_input-governance"

    complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="input-governance",
        reason="input governance reran after source supersede",
    )

    payload = build_completion_projection(workspace=ws, repo_workdir=ROOT)
    current_stage = payload["workflow"]["current_stage"]
    recovery = payload["recovery_truth"]
    assert recovery["status"] == "downstream_rerun_pending"
    assert recovery["rerun_start_stage"] == "input-governance"
    assert payload["next_allowed_action"] == f"rerun_downstream_from_{current_stage}"


def test_completion_projection_supersede_rerun_prefers_rerun_over_workflow_blocked(tmp_path: Path) -> None:
    from multi_agent_brief.orchestrator.runtime_state import supersede_stage_artifact_transaction
    from tests.test_runtime_state import _contaminated_editor_artifact_workspace

    ws, _old_sha, _current_sha = _contaminated_editor_artifact_workspace(tmp_path)
    supersede_stage_artifact_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="editor",
        artifact="output/intermediate/audited_brief.md",
        reason="human approved supersede after contaminated direct edit",
    )
    _set_workflow(ws, blocked=True, blocking_reason="Synthetic blocker on top of real supersede state.")

    payload = build_completion_projection(workspace=ws, repo_workdir=ROOT)

    assert payload["next_allowed_action"] == "rerun_downstream_from_auditor"


def test_completion_projection_contaminated_repaired_terminal_is_not_a_rerun_demand(tmp_path: Path) -> None:
    """Drive a real complete_finalize_transaction on a contaminated run:
    run_integrity becomes contaminated_repaired (terminal, never
    reference-eligible) and the projection must fall through to the normal
    delivery flow instead of demanding a rerun."""
    from multi_agent_brief.orchestrator.runtime_state import (
        complete_finalize_transaction,
        complete_stage_transaction,
        supersede_stage_artifact_transaction,
    )
    from tests.test_runtime_state import (
        _contaminated_editor_artifact_workspace,
        _valid_audit_report_payload,
        _write_json_artifact,
        _write_quality_gate_report,
    )
    from tests.test_runtime_state import _write_finalize_report as _write_runtime_finalize_report

    ws, _old_sha, _current_sha = _contaminated_editor_artifact_workspace(tmp_path)
    supersede_stage_artifact_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="editor",
        artifact="output/intermediate/audited_brief.md",
        reason="human approved supersede after contaminated direct edit",
    )
    refreshed = json.loads(_valid_audit_report_payload())
    refreshed["summary"] = "Auditor rerun against the superseded brief revision."
    _write_json_artifact(ws, "audit_report.json", json.dumps(refreshed) + "\n")
    _write_quality_gate_report(ws, stage_id="auditor")
    complete_stage_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        stage_id="auditor",
        reason="auditor reran against the superseded revision",
    )
    _write_quality_gate_report(ws, stage_id="finalize")
    _write_runtime_finalize_report(ws)
    complete_finalize_transaction(
        workspace=ws,
        repo_workdir=ROOT,
        reason="reader artifacts finalized after repair",
    )
    _set_workflow(
        ws,
        blocked=True,
        blocking_reason="Stale blocker retained after the bound finalize transaction.",
    )

    payload = build_completion_projection(workspace=ws, repo_workdir=ROOT)

    assert payload["run_integrity"]["status"] == "contaminated_repaired"
    assert payload["recovery_truth"]["status"] == "completed_non_reference"
    assert payload["delivery_truth"]["eligibility"]["allowed"] is True
    assert payload["delivery_truth"]["eligibility"]["reference_eligible"] is False
    assert payload["next_allowed_action"] == "inspect_status_before_delivery_or_quality"

    # The executor consumes the same shared rule: a real deliver succeeds.
    from multi_agent_brief.cli.main import main as cli_main

    _set_workflow(ws, blocked=False, blocking_reason="")
    rc = cli_main(["deliver", "--workspace", str(ws), "--target", "local"])
    assert rc == 0


def test_completion_projection_active_repair_prefers_repair_over_workflow_blocked(tmp_path: Path) -> None:
    ws = _write_workspace(tmp_path)
    _init_workspace(ws)
    _set_workflow(
        ws,
        blocked=True,
        blocking_reason="Fail-closed while repair is open.",
        active_repair={"stage_id": "editor"},
    )

    payload = build_completion_projection(workspace=ws, repo_workdir=ROOT)

    assert payload["workflow"]["active_repair_present"] is True
    assert payload["next_allowed_action"] == "stop_complete_or_inspect_active_repair"


def test_completion_projection_unknown_integrity_prefers_integrity_over_workflow_blocked(tmp_path: Path) -> None:
    ws = _write_workspace(tmp_path)
    _init_workspace(ws)
    _set_workflow(
        ws,
        blocked=True,
        blocking_reason="Gate blocked human review.",
        run_integrity={
            "status": "unknown",
            "reference_eligible": False,
            "clean_single_shot": False,
        },
    )

    payload = build_completion_projection(workspace=ws, repo_workdir=ROOT)

    assert payload["next_allowed_action"] == "stop_run_integrity_not_clean"


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
