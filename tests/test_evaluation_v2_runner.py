"""Runner orchestration, tested with a fake injectable rollout.

``run_split`` is a pure function of ``(corpus, rollout)``: corpus order is
preserved, exactly one rollout call happens per selected case, and the
collected outcomes flow untouched into ``score_corpus``.  Retries, timeouts,
randomness, and anything performance-dependent belong to the adapter layer,
never here, so a fake rollout is the only double these tests need.
"""

from __future__ import annotations

import dataclasses

import pytest

from multi_agent_brief.evaluation_v2.contracts import (
    CorpusScore,
    EvaluationCase,
    RolloutOutcome,
)
from multi_agent_brief.evaluation_v2.corpus import Corpus
from multi_agent_brief.evaluation_v2.runner import RolloutFn, SplitResult, run_split


def _defect(
    defect_id: str = "d1",
    finding_type: str = "stale_source",
    locator: str = "CL-0102",
    expected_blocking_level: str = "blocking",
) -> dict:
    return {
        "defect_id": defect_id,
        "finding_type": finding_type,
        "locator": locator,
        "expected_blocking_level": expected_blocking_level,
    }


def _case(case_id: str, defects=(), clean_claims=()) -> EvaluationCase:
    return EvaluationCase.model_validate(
        {
            "case_id": case_id,
            "synthetic": True,
            "source_pack": f"cases/{case_id}/sources",
            "report_date": "2026-06-08",
            "rollout": {"role": "auditor", "runtime": "codex"},
            "seeded_defects": list(defects),
            "clean_claims": list(clean_claims),
        },
        strict=True,
    )


def _corpus(*assignments: tuple[EvaluationCase, str]) -> Corpus:
    return Corpus(
        cases=tuple(case for case, _split in assignments),
        splits={case.case_id: _split for case, _split in assignments},
    )


def _mixed_corpus() -> Corpus:
    """One train case plus three val cases, val order deliberately unsorted.

    The val order is c1, b1, w1 -- not alphabetical -- so the order test
    proves corpus order is preserved rather than accidentally sorted.
    """
    train = _case("t1", clean_claims=["CL-0104"])
    b1 = _case("b1", defects=[_defect()], clean_claims=["CL-0101"])
    c1 = _case("c1", clean_claims=["CL-0101", "CL-0109"])
    w1 = _case(
        "w1",
        defects=[
            _defect(
                defect_id="d2",
                finding_type="target_priority_claim_missing_from_summary",
                locator="audited_brief#L40",
                expected_blocking_level="warning",
            )
        ],
        clean_claims=["CL-0108"],
    )
    return _corpus(
        (train, "train"),
        (c1, "val"),
        (b1, "val"),
        (w1, "val"),
    )


def _finding(
    finding_type: str = "stale_source",
    locator: str = "CL-0102",
    blocking_level: str = "blocking",
) -> dict:
    return {
        "finding_type": finding_type,
        "locator": locator,
        "blocking_level": blocking_level,
    }


def _outcome(
    case_id: str,
    findings=(),
    blocked: bool = False,
    noncompliant_finding_count: int = 0,
) -> RolloutOutcome:
    return RolloutOutcome.model_validate(
        {
            "case_id": case_id,
            "blocked": blocked,
            "findings": list(findings),
            "noncompliant_finding_count": noncompliant_finding_count,
        },
        strict=True,
    )


def _detect_all(case: EvaluationCase) -> RolloutOutcome:
    """A perfect rollout: reports every seeded defect, blocks exactly the
    blocking cases, flags no clean claim."""
    return _outcome(
        case.case_id,
        findings=[
            _finding(
                finding_type=defect.finding_type,
                locator=defect.locator,
                blocking_level=defect.expected_blocking_level,
            )
            for defect in case.seeded_defects
        ],
        blocked=case.must_block,
    )


def test_run_split_runs_only_the_requested_split_in_corpus_order():
    seen: list[str] = []

    def recording_rollout(case: EvaluationCase) -> RolloutOutcome:
        seen.append(case.case_id)
        return _detect_all(case)

    result = run_split(_mixed_corpus(), "val", recording_rollout)

    # Corpus order (c1, b1, w1) preserved, the train case is never touched,
    # and each selected case is run exactly once.
    assert seen == ["c1", "b1", "w1"]
    assert len(seen) == len(set(seen))
    assert result.split == "val"


def test_run_split_all_detect_rollout_yields_hand_computed_score():
    """Perfect rollout over the mixed val split, hand-computed.

      c1: two clean claims, nothing flagged, not blocked (must_block False).
      b1: d1 stale_source@CL-0102 detected (double match), blocked; its
          clean claim is out of TNR scope because b1.must_block is True.
      w1: d2 detected as a warning finding, not blocked; clean claim
          CL-0108 unflagged.

    recall = 2/2; TNR = 1 - 0/3; R = 1.0; block agreement 3/3;
    format compliance = 2/2 (nothing noncompliant reported).
    """
    result = run_split(_mixed_corpus(), "val", _detect_all)

    assert result.score == CorpusScore(
        defect_recall=1.0,
        true_negative_rate=1.0,
        reward=1.0,
        seeded_total=2,
        seeded_detected=2,
        clean_total=3,
        clean_flagged=0,
        block_agreement=1.0,
        format_compliance=1.0,
        case_count=3,
    )


def test_run_split_scores_the_outcomes_it_collected():
    """Partial rollout: hand-computed R = 1/2 * 2/3 = 1/3.

      c1 (first in val order) flags clean claim CL-0101.
      b1 detects d1 and blocks.
      w1 reports nothing and does not block (d2 missed).

    recall = 1/2; TNR = 1 - 1/3; R = 1/3; block agreement 3/3.
    """
    by_case = {
        "c1": _outcome(
            "c1",
            findings=[
                _finding(
                    finding_type="number_without_source",
                    locator="CL-0101",
                )
            ],
        ),
        "b1": _outcome("b1", findings=[_finding()], blocked=True),
        "w1": _outcome("w1"),
    }
    rollout: RolloutFn = lambda case: by_case[case.case_id]  # noqa: E731

    result = run_split(_mixed_corpus(), "val", rollout)

    assert result.score.seeded_total == 2
    assert result.score.seeded_detected == 1
    assert result.score.defect_recall == pytest.approx(1 / 2)
    assert result.score.clean_total == 3
    assert result.score.clean_flagged == 1
    assert result.score.true_negative_rate == pytest.approx(2 / 3)
    assert result.score.reward == pytest.approx(1 / 3)
    assert result.score.block_agreement == 1.0
    assert result.score.case_count == 3


def test_run_split_rejects_outcome_for_the_wrong_case():
    calls: list[str] = []

    def wrong_rollout(case: EvaluationCase) -> RolloutOutcome:
        calls.append(case.case_id)
        return _outcome("somewhere-else")

    with pytest.raises(
        ValueError,
        match="rollout for 'c1' returned outcome for 'somewhere-else'",
    ):
        run_split(_mixed_corpus(), "val", wrong_rollout)
    # Fails fast on the first mismatch: no second rollout call happens.
    assert calls == ["c1"]


def test_run_split_rejects_empty_split():
    corpus = _corpus((_case("t1", clean_claims=["source-001.md#L4"]), "train"))

    def unused_rollout(case):  # pragma: no cover - must not be called
        raise AssertionError("should not run")

    with pytest.raises(ValueError, match="split 'val' has no cases"):
        run_split(corpus, "val", unused_rollout)


def test_split_result_exposes_outcomes_and_score():
    result = run_split(_mixed_corpus(), "val", _detect_all)

    assert isinstance(result, SplitResult)
    assert isinstance(result.outcomes, tuple)
    assert [outcome.case_id for outcome in result.outcomes] == ["c1", "b1", "w1"]
    assert all(isinstance(outcome, RolloutOutcome) for outcome in result.outcomes)
    assert isinstance(result.score, CorpusScore)
    assert result.score.case_count == len(result.outcomes)
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.outcomes = ()


def test_run_split_aggregates_noncompliant_counts_into_format_compliance():
    """A rollout that also reports unmatchable findings keeps R driven only
    by the compliant findings while format compliance drops: 3 compliant
    findings (d1, d2, plus one extra) against 3 noncompliant -> 3/6."""

    def noisy_rollout(case: EvaluationCase) -> RolloutOutcome:
        findings = [
            _finding(
                finding_type=defect.finding_type,
                locator=defect.locator,
                blocking_level=defect.expected_blocking_level,
            )
            for defect in case.seeded_defects
        ]
        if case.case_id == "b1":
            findings.append(
                _finding(finding_type="number_without_source", locator="CL-0199")
            )
            noncompliant = 3
        else:
            noncompliant = 0
        return _outcome(
            case.case_id,
            findings=findings,
            blocked=case.must_block,
            noncompliant_finding_count=noncompliant,
        )

    result = run_split(_mixed_corpus(), "val", noisy_rollout)
    assert result.score.defect_recall == 1.0
    assert result.score.true_negative_rate == 1.0
    assert result.score.reward == 1.0
    assert result.score.format_compliance == pytest.approx(3 / 6)
