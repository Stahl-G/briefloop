from __future__ import annotations

import json

from multi_agent_brief.orchestrator.timing import derive_control_timing, derive_control_timing_from_path


def _event(event_id: str, event_type: str, created_at: str, **extra):
    payload = {
        "schema_version": "multi-agent-brief-event-log/v1",
        "event_id": event_id,
        "run_id": "run-test",
        "created_at": created_at,
        "event_type": event_type,
        "actor": "cli",
        "stage_id": None,
        "artifact_id": None,
        "decision": None,
        "reason": "",
        "metadata": {},
    }
    payload.update(extra)
    return payload


def _completion(event_id: str, created_at: str, stage_id: str, decision: str = "continue"):
    return _event(
        event_id,
        "decision_recorded",
        created_at,
        stage_id=stage_id,
        decision=decision,
        metadata={"transaction_id": f"tx-{event_id}"},
    )


def _workflow(*, contaminated: bool = False, finalized: bool = True):
    workflow = {
        "current_stage": None if finalized else "source-discovery",
        "stage_statuses": {
            "doctor": {"status": "complete"},
            "source-discovery": {"status": "complete" if finalized else "ready"},
            "finalize": {"status": "complete" if finalized else "pending"},
        },
        "run_integrity": {
            "status": "clean",
            "reference_eligible": True,
            "clean_single_shot": True,
            "reasons": [],
        },
    }
    if contaminated:
        workflow["run_integrity"] = {
            "status": "contaminated",
            "reference_eligible": False,
            "clean_single_shot": False,
            "reasons": [{"reason_code": "run_reset", "message": "reset"}],
        }
    return workflow


def test_control_timing_complete_sequence_produces_stage_buckets():
    records = [
        _event("e0", "run_initialized", "2026-06-14T00:00:00Z"),
        _completion("e1", "2026-06-14T00:01:00Z", "doctor"),
        _completion("e2", "2026-06-14T00:03:00Z", "source-discovery"),
        _completion("e3", "2026-06-14T00:05:00Z", "finalize", "finalize"),
    ]

    timing = derive_control_timing(event_records=records, workflow_state=_workflow())

    assert timing["schema_version"] == "mabw.control_timing.v1"
    assert timing["status"] == "available"
    assert timing["total_elapsed_seconds"] == 300.0
    assert [stage["stage_id"] for stage in timing["stages"]] == ["doctor", "source-discovery"]
    assert timing["stages"][0]["elapsed_seconds"] == 60.0
    assert timing["stages"][1]["elapsed_seconds"] == 120.0
    assert timing["finalize"]["elapsed_seconds"] == 120.0


def test_control_timing_missing_start_boundary_is_partial_not_guessed():
    records = [
        _completion("e1", "2026-06-14T00:01:00Z", "doctor"),
    ]

    timing = derive_control_timing(event_records=records, workflow_state=_workflow(finalized=False))

    assert timing["status"] == "partial"
    assert timing["stages"][0]["status"] == "unknown"
    assert timing["stages"][0]["elapsed_seconds"] is None
    assert "doctor: start boundary missing or invalid" in timing["warnings"]


def test_control_timing_ignores_non_transaction_decision_events():
    records = [
        _event("e0", "run_initialized", "2026-06-14T00:00:00Z"),
        _event("e1", "decision_recorded", "2026-06-14T00:01:00Z", stage_id="doctor", decision="continue"),
    ]

    timing = derive_control_timing(event_records=records, workflow_state=_workflow(finalized=False))

    assert timing["status"] == "incomplete"
    assert any(stage["stage_id"] == "doctor" and stage["status"] == "incomplete" for stage in timing["stages"])
    assert "completion_events_missing" in timing["warnings"]


def test_control_timing_marks_non_object_event_log_line_invalid(tmp_path):
    event_log = tmp_path / "event_log.jsonl"
    event_log.write_text("[]\n", encoding="utf-8")

    timing = derive_control_timing_from_path(event_log)

    assert timing["status"] == "invalid_event_log"
    assert any("must contain an object" in warning for warning in timing["warnings"])


def test_control_timing_marks_invalid_schema_event_log_line_invalid(tmp_path):
    event_log = tmp_path / "event_log.jsonl"
    event_log.write_text(
        '{"schema_version":"bad","event_id":"e0","run_id":"run-test","event_type":"run_initialized","actor":"cli"}\n',
        encoding="utf-8",
    )

    timing = derive_control_timing_from_path(event_log)

    assert timing["status"] == "invalid_event_log"
    assert any("Unsupported event log schema" in warning for warning in timing["warnings"])


def test_control_timing_rejects_cross_run_event_records(tmp_path):
    event_log = tmp_path / "event_log.jsonl"
    records = [
        _event("e0", "run_initialized", "2026-06-14T00:00:00Z", run_id="other-run"),
        _completion("e1", "2026-06-14T00:01:00Z", "doctor"),
    ]
    event_log.write_text("".join(json.dumps(record, sort_keys=True) + "\n" for record in records), encoding="utf-8")
    workflow = _workflow(finalized=False)
    workflow["run_id"] = "run-test"

    timing = derive_control_timing_from_path(event_log, workflow_state=workflow, expected_run_id="run-test")

    assert timing["status"] == "invalid_event_log"
    assert any("run_id mismatch" in warning for warning in timing["warnings"])


def test_control_timing_missing_completion_for_completed_stage_is_incomplete():
    records = [_event("e0", "run_initialized", "2026-06-14T00:00:00Z")]

    timing = derive_control_timing(event_records=records, workflow_state=_workflow())

    assert timing["status"] == "incomplete"
    assert "completion_events_missing" in timing["warnings"]
    assert any(stage["stage_id"] == "doctor" and stage["status"] == "incomplete" for stage in timing["stages"])
    assert timing["finalize"]["status"] == "incomplete"


def test_control_timing_completed_workflow_missing_stage_completion_is_incomplete():
    records = [
        _event("e0", "run_initialized", "2026-06-14T00:00:00Z"),
        _completion("e1", "2026-06-14T00:01:00Z", "doctor"),
    ]

    timing = derive_control_timing(event_records=records, workflow_state=_workflow())

    assert timing["status"] == "incomplete"
    assert any(stage["stage_id"] == "source-discovery" and stage["status"] == "incomplete" for stage in timing["stages"])
    assert timing["finalize"]["status"] == "incomplete"


def test_control_timing_missing_upstream_completion_makes_finalize_unknown():
    records = [
        _event("e0", "run_initialized", "2026-06-14T00:00:00Z"),
        _completion("e1", "2026-06-14T00:01:00Z", "doctor"),
        _completion("e3", "2026-06-14T00:05:00Z", "finalize", "finalize"),
    ]

    timing = derive_control_timing(event_records=records, workflow_state=_workflow())

    assert timing["status"] == "incomplete"
    assert any(stage["stage_id"] == "source-discovery" and stage["status"] == "incomplete" for stage in timing["stages"])
    assert timing["finalize"]["status"] == "unknown"
    assert timing["finalize"]["elapsed_seconds"] is None


def test_control_timing_contaminated_run_is_labeled_contaminated():
    records = [
        _event("e0", "run_initialized", "2026-06-14T00:00:00Z"),
        _completion("e1", "2026-06-14T00:01:00Z", "doctor"),
    ]

    timing = derive_control_timing(event_records=records, workflow_state=_workflow(contaminated=True, finalized=False))

    assert timing["status"] == "contaminated"
    assert timing["run_integrity"]["reference_eligible"] is False
    assert "run_integrity_contaminated" in timing["warnings"]
