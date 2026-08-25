from __future__ import annotations

from datetime import date

from multi_agent_brief.contracts.v2 import (
    AcceptedSourceRecord,
    CandidateClaimItem,
    RunDirection,
    ScreeningDecisionItem,
)
from multi_agent_brief.core_run_v2.source_temporality import (
    classify_source_temporality,
    high_priority_background_candidate_ids,
    required_aspects_uncovered,
)


def _direction() -> RunDirection:
    return RunDirection.model_validate(
        {
            **RunDirection.full_example,
            "report_date": "2026-08-10",
            "report_window_start": "2026-08-03",
            "report_window_end": "2026-08-10",
            "max_source_age_days": 7,
        },
        strict=True,
    )


def _source(**changes: object) -> AcceptedSourceRecord:
    payload = dict(AcceptedSourceRecord.full_example)
    payload.update(
        {
            "published_at": "2026-08-03",
            "retrieved_at": "2026-08-10T23:59:00Z",
            "document_kind": None,
            "opened_at": None,
            "resolved_at": None,
            **changes,
        }
    )
    return AcceptedSourceRecord.model_validate(payload, strict=True)


def test_source_temporality_includes_both_frozen_window_boundaries() -> None:
    start = classify_source_temporality(_source(), _direction())
    end = classify_source_temporality(_source(published_at="2026-08-10"), _direction())

    assert (start.role, start.basis, start.anchor_date) == (
        "current_window",
        "published_at",
        date(2026, 8, 3),
    )
    assert (end.role, end.anchor_date) == (
        "current_window",
        date(2026, 8, 10),
    )


def test_retrieved_at_never_promotes_undated_source() -> None:
    result = classify_source_temporality(
        _source(published_at=None, retrieved_at="2026-08-10T23:59:00Z"),
        _direction(),
    )

    assert (result.role, result.basis, result.anchor_date) == (
        "background",
        "none",
        None,
    )


def test_status_incident_uses_opened_at() -> None:
    result = classify_source_temporality(
        _source(
            published_at=None,
            document_kind="status_incident",
            opened_at="2026-08-05T08:00:00Z",
        ),
        _direction(),
    )

    assert (result.role, result.basis, result.anchor_date) == (
        "current_window",
        "opened_at",
        date(2026, 8, 5),
    )


def test_selected_high_background_candidate_is_rejected_by_policy() -> None:
    candidate = CandidateClaimItem.model_validate(
        {
            "candidate_id": "CAND-BACKGROUND-001",
            "source_id": "SOURCE-001",
            "statement": "Historical context",
            "evidence_text": "Historical context evidence",
            "topic": "background",
            "claim_type": "fact",
            "confidence": "high",
        },
        strict=True,
    )
    decision = ScreeningDecisionItem.model_validate(
        {
            "candidate_id": candidate.candidate_id,
            "decision": "selected",
            "priority": "high",
            "reason_code": None,
            "explanation": None,
        },
        strict=True,
    )
    source = _source(published_at=None)

    assert high_priority_background_candidate_ids(
        [candidate],
        [decision],
        {source.source_id: source},
        _direction(),
    ) == (candidate.candidate_id,)


def _candidate(aspect: str | None, candidate_id: str = "CAND-001") -> CandidateClaimItem:
    payload = {
        "candidate_id": candidate_id,
        "source_id": "SOURCE-001",
        "statement": "Synthetic statement",
        "evidence_text": "Synthetic evidence",
        "topic": "topic",
        "claim_type": "fact",
        "confidence": "high",
    }
    if aspect is not None:
        payload["aspect"] = aspect
    return CandidateClaimItem.model_validate(payload, strict=True)


def _decision(candidate_id: str, decision: str = "selected") -> ScreeningDecisionItem:
    return ScreeningDecisionItem.model_validate(
        {
            "candidate_id": candidate_id,
            "decision": decision,
            "priority": None,
            "reason_code": None,
            "explanation": None,
        },
        strict=True,
    )


def _aspect_direction() -> RunDirection:
    return _direction().model_copy(
        update={"required_claim_aspects": ["earnings_growth", "cash_flow_dilution"]}
    )


def test_required_aspect_without_evidence_or_gap_is_uncovered() -> None:
    candidates = [_candidate("earnings_growth")]
    uncovered = required_aspects_uncovered(
        candidates=candidates,
        coverage_gap_aspects=[],
        decisions=[_decision("CAND-001")],
        direction=_aspect_direction(),
    )
    assert uncovered == ("cash_flow_dilution",)


def test_scout_gap_entry_covers_a_missing_aspect() -> None:
    uncovered = required_aspects_uncovered(
        candidates=[_candidate("earnings_growth")],
        coverage_gap_aspects=["cash_flow_dilution"],
        decisions=[_decision("CAND-001")],
        direction=_aspect_direction(),
    )
    assert uncovered == ()


def test_screener_cannot_drop_last_selected_candidate_of_an_aspect() -> None:
    uncovered = required_aspects_uncovered(
        candidates=[
            _candidate("earnings_growth"),
            _candidate("cash_flow_dilution", "CAND-002"),
        ],
        coverage_gap_aspects=[],
        decisions=[
            _decision("CAND-001"),
            _decision("CAND-002", decision="excluded"),
        ],
        direction=_aspect_direction(),
    )
    assert uncovered == ("cash_flow_dilution",)


def test_no_required_aspects_means_no_uncovered() -> None:
    assert (
        required_aspects_uncovered(
            candidates=[_candidate(None)],
            coverage_gap_aspects=[],
            decisions=[_decision("CAND-001")],
            direction=_direction(),
        )
        == ()
    )
