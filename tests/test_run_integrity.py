from __future__ import annotations

import pytest

from multi_agent_brief.orchestrator.run_integrity import classify_run_integrity, normalize_run_integrity


def test_normalize_run_integrity_keeps_missing_legacy_backcompat_clean_default():
    assert normalize_run_integrity(None, missing=True) == {
        "status": "clean",
        "reference_eligible": True,
        "clean_single_shot": True,
        "reasons": [],
    }


def test_normalize_run_integrity_rejects_malformed_persisted_payload():
    with pytest.raises(ValueError, match="must be an object"):
        normalize_run_integrity("bad")


def test_classify_run_integrity_treats_malformed_payload_as_unknown():
    classified = classify_run_integrity("bad")

    assert classified["status"] == "unknown"
    assert classified["reference_eligible"] is False
    assert classified["clean_single_shot"] is False
    assert classified["reasons"][0]["reason_code"] == "run_integrity_malformed"


def test_classify_run_integrity_treats_missing_legacy_field_as_clean():
    assert classify_run_integrity(None, missing=True) == {
        "status": "clean",
        "reference_eligible": True,
        "clean_single_shot": True,
        "reasons": [],
    }


def test_normalize_run_integrity_rejects_invalid_persisted_statuses():
    with pytest.raises(ValueError, match="status is invalid"):
        normalize_run_integrity({"status": "unknown"})
    with pytest.raises(ValueError, match="status is invalid"):
        normalize_run_integrity({"status": "incomplete"})


def test_classify_run_integrity_treats_invalid_status_as_unknown():
    classified = classify_run_integrity({"status": "incomplete"})

    assert classified["status"] == "unknown"
    assert classified["reference_eligible"] is False
    assert classified["reasons"][0]["reason_code"] == "run_integrity_invalid_status"


def test_normalize_run_integrity_contaminated_is_never_reference_eligible():
    normalized = normalize_run_integrity({
        "status": "contaminated",
        "reference_eligible": True,
        "clean_single_shot": True,
        "reasons": [{"reason_code": "run_reset"}],
    })

    assert normalized["status"] == "contaminated"
    assert normalized["reference_eligible"] is False
    assert normalized["clean_single_shot"] is False
    assert normalized["reasons"] == [{"reason_code": "run_reset"}]
