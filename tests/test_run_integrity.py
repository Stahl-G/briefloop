from __future__ import annotations

import pytest

from multi_agent_brief.orchestrator.run_integrity import (
    interpret_run_integrity,
    project_for_read,
    require_persistable,
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
