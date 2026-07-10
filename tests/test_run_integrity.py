from __future__ import annotations

import pytest

from multi_agent_brief.orchestrator.run_integrity import (
    interpret_run_integrity,
    project_for_read,
    require_persistable,
    workflow_with_sticky_contamination_events,
)
from multi_agent_brief.orchestrator.runtime_state import RuntimeStateError


def test_interpret_run_integrity_keeps_missing_legacy_backcompat_clean_default():
    verdict = interpret_run_integrity(None, field_present=False)

    assert project_for_read(verdict) == {
        "status": "clean",
        "reference_eligible": True,
        "clean_single_shot": True,
        "reasons": [],
    }
    assert require_persistable(verdict) == project_for_read(verdict)


def test_interpret_run_integrity_projects_malformed_payload_as_unknown():
    verdict = interpret_run_integrity("bad", field_present=True)
    classified = project_for_read(verdict)

    assert verdict.kind == "degraded"
    assert classified["status"] == "unknown"
    assert classified["reference_eligible"] is False
    assert classified["clean_single_shot"] is False
    assert classified["reasons"][0]["reason_code"] == "run_integrity_malformed"


def test_require_persistable_rejects_malformed_payload():
    verdict = interpret_run_integrity("bad", field_present=True)

    with pytest.raises(RuntimeStateError) as exc_info:
        require_persistable(verdict, path="workflow_state.json")

    assert exc_info.value.error_code == "E_TRANSACTION_INTEGRITY"
    assert exc_info.value.details["path"] == "workflow_state.json"


def test_interpret_run_integrity_rejects_invalid_persisted_statuses():
    for status in ("unknown", "incomplete"):
        verdict = interpret_run_integrity({"status": status}, field_present=True)

        assert project_for_read(verdict)["status"] == "unknown"
        assert verdict.reason_code == "run_integrity_invalid_status"
        with pytest.raises(RuntimeStateError):
            require_persistable(verdict)


def test_interpret_run_integrity_rejects_conflicting_contaminated_flags():
    verdict = interpret_run_integrity({
        "status": "contaminated",
        "reference_eligible": True,
        "clean_single_shot": False,
        "reasons": [{"reason_code": "run_reset"}],
    }, field_present=True)

    assert verdict.kind == "degraded"
    assert project_for_read(verdict)["status"] == "unknown"
    assert verdict.reason_code == "run_integrity_contaminated_reference_eligible"


@pytest.mark.parametrize(
    ("field", "reason_code"),
    [
        ("reference_eligible", "run_integrity_clean_not_reference_eligible"),
        ("clean_single_shot", "run_integrity_clean_not_single_shot"),
    ],
)
def test_interpret_run_integrity_rejects_conflicting_clean_flags(field: str, reason_code: str):
    payload = {
        "status": "clean",
        "reference_eligible": True,
        "clean_single_shot": True,
        "reasons": [],
    }
    payload[field] = False

    verdict = interpret_run_integrity(payload, field_present=True)

    assert verdict.kind == "degraded"
    assert project_for_read(verdict)["status"] == "unknown"
    assert verdict.reason_code == reason_code
    with pytest.raises(RuntimeStateError):
        require_persistable(verdict)


def test_interpret_run_integrity_canonicalizes_valid_contaminated_payload():
    verdict = interpret_run_integrity({
        "status": "contaminated",
        "reference_eligible": False,
        "clean_single_shot": False,
        "reasons": [{"reason_code": "run_reset"}],
    }, field_present=True)
    persisted = require_persistable(verdict)

    assert persisted["status"] == "contaminated"
    assert persisted["reference_eligible"] is False
    assert persisted["clean_single_shot"] is False
    assert persisted["reasons"] == [{"reason_code": "run_reset"}]


def test_interpret_run_integrity_canonicalizes_valid_contaminated_repaired_payload():
    verdict = interpret_run_integrity({
        "status": "contaminated_repaired",
        "reference_eligible": False,
        "clean_single_shot": False,
        "reasons": [{"reason_code": "repair_completed"}],
    }, field_present=True)
    persisted = require_persistable(verdict)

    assert persisted["status"] == "contaminated_repaired"
    assert persisted["reference_eligible"] is False
    assert persisted["clean_single_shot"] is False
    assert persisted["reasons"] == [{"reason_code": "repair_completed"}]


def test_sticky_contamination_event_keeps_repaired_terminal_status():
    workflow = {
        "run_integrity": {
            "status": "contaminated_repaired",
            "reference_eligible": False,
            "clean_single_shot": False,
            "reasons": [],
        }
    }
    event_records = [
        {
            "event_type": "run_integrity_contaminated",
            "created_at": "2026-06-14T00:00:00+00:00",
            "metadata": {
                "reason_code": "prior_repair",
                "message": "Repair happened before finalization.",
            },
        }
    ]

    updated = workflow_with_sticky_contamination_events(workflow, event_records)

    assert updated["run_integrity"]["status"] == "contaminated_repaired"
    assert updated["run_integrity"]["reference_eligible"] is False
    assert updated["run_integrity"]["reasons"][0]["reason_code"] == "prior_repair"


def test_sticky_contamination_keeps_repaired_when_event_precedes_bound_finalize():
    workflow = {
        "run_id": "run-current-001",
        "last_completion_transaction": {
            "transaction_id": "tx-finalize-001",
            "stage_id": "finalize",
            "decision": "finalize",
        },
        "run_integrity": {
            "status": "contaminated_repaired",
            "reference_eligible": False,
            "clean_single_shot": False,
            "reasons": [],
        },
    }
    event_records = [
        {
            "event_type": "run_integrity_contaminated",
            "run_id": "run-current-001",
            "metadata": {"reason_code": "prior_contamination"},
        },
        {
            "event_type": "decision_recorded",
            "run_id": "run-current-001",
            "stage_id": "finalize",
            "decision": "finalize",
            "metadata": {"transaction_id": "tx-finalize-001"},
        },
    ]

    updated = workflow_with_sticky_contamination_events(workflow, event_records)

    assert updated["run_integrity"]["status"] == "contaminated_repaired"


def test_sticky_contamination_reopens_repaired_when_event_follows_bound_finalize():
    workflow = {
        "run_id": "run-current-001",
        "last_completion_transaction": {
            "transaction_id": "tx-finalize-001",
            "stage_id": "finalize",
            "decision": "finalize",
        },
        "run_integrity": {
            "status": "contaminated_repaired",
            "reference_eligible": False,
            "clean_single_shot": False,
            "reasons": [],
        },
    }
    event_records = [
        {
            "event_type": "decision_recorded",
            "run_id": "run-current-001",
            "stage_id": "finalize",
            "decision": "finalize",
            "metadata": {"transaction_id": "tx-finalize-001"},
        },
        {
            "event_type": "run_integrity_contaminated",
            "run_id": "run-current-001",
            "metadata": {"reason_code": "post_finalize_contamination"},
        },
    ]

    updated = workflow_with_sticky_contamination_events(workflow, event_records)

    assert updated["run_integrity"]["status"] == "contaminated"
    assert updated["run_integrity"]["reference_eligible"] is False
    assert updated["run_integrity"]["reasons"][-1]["reason_code"] == "post_finalize_contamination"


def test_sticky_contamination_ignores_events_from_an_old_run():
    workflow = {
        "run_id": "run-current-001",
        "run_integrity": {
            "status": "clean",
            "reference_eligible": True,
            "clean_single_shot": True,
            "reasons": [],
        },
    }
    event_records = [
        {
            "event_type": "run_integrity_contaminated",
            "run_id": "run-archived-001",
            "created_at": "2026-06-14T00:00:00+00:00",
            "metadata": {
                "reason_code": "old_run_contamination",
                "message": "This event belongs to a prior run.",
            },
        }
    ]

    updated = workflow_with_sticky_contamination_events(workflow, event_records)

    assert updated["run_integrity"]["status"] == "clean"
    assert updated["run_integrity"]["reasons"] == []
