from __future__ import annotations

import json
from pathlib import Path

from multi_agent_brief.orchestrator.recovery_state import (
    RECOVERY_AWAITING,
    RECOVERY_COMPLETED_NON_REFERENCE,
    RECOVERY_FINALIZE_COMPLETION_PENDING,
    RECOVERY_FINALIZE_RENDER_REQUIRED,
    RECOVERY_IN_PROGRESS,
    RECOVERY_INVALID,
    RECOVERY_NOT_APPLICABLE,
    RECOVERY_RERUN_PENDING,
    evaluate_recovery_state,
)
from multi_agent_brief.orchestrator.runtime_state.artifact_registry import (
    ARTIFACT_REGISTRY_SCHEMA,
)
from multi_agent_brief.orchestrator.runtime_state.event_log import EVENT_LOG_SCHEMA
from multi_agent_brief.orchestrator.runtime_state.manifest import RUNTIME_MANIFEST_SCHEMA
from multi_agent_brief.orchestrator.runtime_state.workflow import WORKFLOW_STATE_SCHEMA


ROOT = Path(__file__).resolve().parent.parent
RUN_ID = "run-recovery-test"
CONTAMINATION_ID = "event-contamination-001"
RECOVERY_ID = "repair-complete-001"


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _event(
    event_type: str,
    event_id: str,
    *,
    stage_id: str | None = None,
    decision: str | None = None,
    metadata: dict | None = None,
) -> dict:
    return {
        "schema_version": EVENT_LOG_SCHEMA,
        "event_id": event_id,
        "run_id": RUN_ID,
        "created_at": "2026-07-10T00:00:00Z",
        "event_type": event_type,
        "actor": "system",
        "stage_id": stage_id,
        "artifact_id": None,
        "decision": decision,
        "reason": event_type,
        "metadata": metadata or {},
    }


def _workspace(tmp_path: Path, *, current_stage: str | None = "editor") -> Path:
    ws = tmp_path / "workspace"
    (ws / "config.yaml").parent.mkdir(parents=True, exist_ok=True)
    (ws / "config.yaml").write_text("project_name: Recovery Test\n", encoding="utf-8")
    intermediate = ws / "output" / "intermediate"
    _write_json(
        intermediate / "runtime_manifest.json",
        {"schema_version": RUNTIME_MANIFEST_SCHEMA, "run_id": RUN_ID},
    )
    _write_json(
        intermediate / "workflow_state.json",
        {
            "schema_version": WORKFLOW_STATE_SCHEMA,
            "run_id": RUN_ID,
            "current_stage": current_stage,
            "blocked": False,
            "blocking_reason": "",
            "stage_statuses": {},
            "run_integrity": {
                "status": "clean",
                "reference_eligible": True,
                "clean_single_shot": True,
                "reasons": [],
            },
        },
    )
    (intermediate / "event_log.jsonl").write_text("", encoding="utf-8")
    return ws


def _read_workflow(ws: Path) -> dict:
    return json.loads((ws / "output/intermediate/workflow_state.json").read_text(encoding="utf-8"))


def _write_workflow(ws: Path, workflow: dict) -> None:
    _write_json(ws / "output/intermediate/workflow_state.json", workflow)


def _write_registry(ws: Path) -> None:
    _write_json(
        ws / "output/intermediate/artifact_registry.json",
        {
            "schema_version": ARTIFACT_REGISTRY_SCHEMA,
            "run_id": RUN_ID,
            "artifacts": {},
        },
    )


def _write_events(ws: Path, events: list[dict]) -> None:
    path = ws / "output/intermediate/event_log.jsonl"
    path.write_text(
        "".join(json.dumps(event, sort_keys=True) + "\n" for event in events),
        encoding="utf-8",
    )


def _mark_contaminated(ws: Path) -> dict:
    workflow = _read_workflow(ws)
    workflow["run_integrity"] = {
        "status": "contaminated",
        "reference_eligible": False,
        "clean_single_shot": False,
        "reasons": [{"reason_code": "test_contamination", "message": "changed"}],
    }
    _write_workflow(ws, workflow)
    contamination = _event("run_integrity_contaminated", CONTAMINATION_ID)
    _write_events(ws, [contamination])
    _write_registry(ws)
    return contamination


def _recovery_event(*, rerun_start_stage: str = "auditor") -> dict:
    return _event(
        "repair_completed",
        "event-repair-completed-001",
        stage_id="editor",
        decision="repair_complete",
        metadata={
            "transaction_id": RECOVERY_ID,
            "repair_start_transaction_id": "repair-start-001",
            "contamination_event_id": CONTAMINATION_ID,
            "owner_stage": "editor",
            "artifact_id": "audited_brief",
            "rerun_start_stage": rerun_start_stage,
            "reference_eligible": False,
            "stale_artifact_baselines": {
                "audit_report": {"sha256": "old-audit"},
            },
        },
    )


def _bind_recovery_pointer(ws: Path, *, rerun_start_stage: str = "auditor") -> None:
    workflow = _read_workflow(ws)
    workflow["last_repair_transaction"] = {
        "transaction_id": RECOVERY_ID,
        "run_id": RUN_ID,
        "contamination_event_id": CONTAMINATION_ID,
        "owner_stage": "editor",
        "artifact_id": "audited_brief",
        "rerun_start_stage": rerun_start_stage,
    }
    _write_workflow(ws, workflow)


def _write_bound_finalize_report(ws: Path) -> None:
    _write_json(
        ws / "output/intermediate/finalize_report.json",
        {
            "status": "pass",
            "finalize_transaction_id": "render-001",
            "reader_clean": {"status": "pass"},
            "delivery_promotion": "promoted",
            "recovery_binding": {
                "status": "bound_non_reference_recovery",
                "run_id": RUN_ID,
                "contamination_event_id": CONTAMINATION_ID,
                "recovery_transaction_id": RECOVERY_ID,
                "rerun_start_stage": "auditor",
                "reference_eligible": False,
            },
        },
    )


def _evaluate(ws: Path) -> dict:
    return evaluate_recovery_state(workspace=ws, repo_workdir=ROOT)


def test_recovery_state_clean_run_is_not_applicable(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)

    payload = _evaluate(ws)

    assert payload["status"] == RECOVERY_NOT_APPLICABLE
    assert payload["reference_eligible"] is True
    assert payload["recovery_blocks_delivery"] is False


def test_recovery_state_current_contamination_awaits_recovery(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    _mark_contaminated(ws)

    payload = _evaluate(ws)

    assert payload["status"] == RECOVERY_AWAITING
    assert payload["contamination_event_id"] == CONTAMINATION_ID
    assert payload["recommended_recovery_action"] == "request_recovery_decision"


def test_recovery_state_requires_bound_active_repair(tmp_path: Path) -> None:
    ws = _workspace(tmp_path, current_stage="editor")
    contamination = _mark_contaminated(ws)
    started = _event(
        "repair_started",
        "event-repair-started-001",
        stage_id="editor",
        metadata={
            "transaction_id": "repair-start-001",
            "contamination_event_id": CONTAMINATION_ID,
        },
    )
    _write_events(ws, [contamination, started])
    workflow = _read_workflow(ws)
    workflow["active_repair"] = {
        "schema_version": "mabw.active_repair.v2",
        "run_id": RUN_ID,
        "repair_start_transaction_id": "repair-start-001",
        "repair_started_event_id": "event-repair-started-001",
        "contamination_event_id": CONTAMINATION_ID,
        "repair_owner": "editor",
        "must_rerun_from": "auditor",
        "source": {"artifact_id": "audited_brief"},
    }
    _write_workflow(ws, workflow)

    payload = _evaluate(ws)
    assert payload["status"] == RECOVERY_IN_PROGRESS

    workflow["active_repair"].pop("contamination_event_id")
    _write_workflow(ws, workflow)
    invalid = _evaluate(ws)
    assert invalid["status"] == RECOVERY_INVALID
    assert invalid["reason_code"] == "active_repair_binding_invalid"


def test_recovery_state_tracks_downstream_rerun_from_event(tmp_path: Path) -> None:
    ws = _workspace(tmp_path, current_stage="auditor")
    contamination = _mark_contaminated(ws)
    recovery = _recovery_event()
    _write_events(ws, [contamination, recovery])
    _bind_recovery_pointer(ws)

    payload = _evaluate(ws)

    assert payload["status"] == RECOVERY_RERUN_PENDING
    assert payload["rerun_start_stage"] == "auditor"
    assert payload["stale_artifact_baselines"]["audit_report"]["sha256"] == "old-audit"


def test_recovery_state_requires_current_finalize_render_and_completion(tmp_path: Path) -> None:
    ws = _workspace(tmp_path, current_stage="finalize")
    contamination = _mark_contaminated(ws)
    recovery = _recovery_event()
    _write_events(ws, [contamination, recovery])
    _bind_recovery_pointer(ws)

    missing = _evaluate(ws)
    assert missing["status"] == RECOVERY_FINALIZE_RENDER_REQUIRED

    _write_bound_finalize_report(ws)
    current = _evaluate(ws)
    assert current["status"] == RECOVERY_FINALIZE_COMPLETION_PENDING
    assert current["render_transaction_id"] == "render-001"


def test_recovery_state_validates_terminal_finalize_binding(tmp_path: Path) -> None:
    ws = _workspace(tmp_path, current_stage=None)
    contamination = _mark_contaminated(ws)
    recovery = _recovery_event()
    completion = _event(
        "decision_recorded",
        "event-finalize-complete-001",
        stage_id="finalize",
        decision="finalize",
        metadata={
            "transaction_id": "finalize-complete-001",
            "render_transaction_id": "render-001",
            "recovery_transaction_id": RECOVERY_ID,
            "contamination_event_id": CONTAMINATION_ID,
        },
    )
    _write_events(ws, [contamination, recovery, completion])
    _bind_recovery_pointer(ws)
    workflow = _read_workflow(ws)
    workflow["last_completion_transaction"] = {
        "transaction_id": "finalize-complete-001",
        "run_id": RUN_ID,
        "stage_id": "finalize",
        "decision": "finalize",
        "render_transaction_id": "render-001",
        "recovery_transaction_id": RECOVERY_ID,
        "contamination_event_id": CONTAMINATION_ID,
    }
    _write_workflow(ws, workflow)
    _write_bound_finalize_report(ws)

    payload = _evaluate(ws)

    assert payload["status"] == RECOVERY_COMPLETED_NON_REFERENCE
    assert payload["recovery_blocks_delivery"] is False
    assert payload["reference_eligible"] is False


def test_recovery_state_rejects_duplicate_event_ids(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    contamination = _mark_contaminated(ws)
    duplicate = dict(contamination)
    duplicate["event_type"] = "repair_completed"
    _write_events(ws, [contamination, duplicate])

    payload = _evaluate(ws)

    assert payload["status"] == RECOVERY_INVALID
    assert payload["reason_code"] == "event_identity_invalid"


def test_recovery_state_rejects_unbound_legacy_repaired_status(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    workflow = _read_workflow(ws)
    workflow["run_integrity"] = {
        "status": "contaminated_repaired",
        "reference_eligible": False,
        "clean_single_shot": False,
        "reasons": [],
    }
    _write_workflow(ws, workflow)

    payload = _evaluate(ws)

    assert payload["status"] == RECOVERY_INVALID
    assert payload["reason_code"] == "legacy_recovery_unbound"
