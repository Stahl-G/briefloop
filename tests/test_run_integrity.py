from __future__ import annotations

from multi_agent_brief.orchestrator.run_integrity import normalize_run_integrity


def test_normalize_run_integrity_defaults_malformed_payload_to_clean():
    assert normalize_run_integrity("bad") == {
        "status": "clean",
        "reference_eligible": True,
        "clean_single_shot": True,
        "reasons": [],
    }


def test_normalize_run_integrity_keeps_only_persisted_statuses():
    assert normalize_run_integrity({"status": "unknown"})["status"] == "clean"
    assert normalize_run_integrity({"status": "incomplete"})["status"] == "clean"


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
