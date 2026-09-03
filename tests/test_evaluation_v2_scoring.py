"""Reward is paired so omission cannot score.

Scoring reads ``RolloutOutcome.findings`` and ``blocked`` only; the
``found_defect_ids`` / ``flagged_claim_locators`` convenience views are
deliberately not consulted, so a score can never drift from the raw record.
Format compliance is aggregated here too, but it is reported in
``CorpusScore`` and never enters R.
"""

from __future__ import annotations

import pytest

from multi_agent_brief.evaluation_v2.contracts import EvaluationCase, RolloutOutcome
from multi_agent_brief.evaluation_v2.scoring import score_corpus


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


def _blocking_case(case_id: str = "b1") -> EvaluationCase:
    return _case(
        case_id,
        defects=[_defect()],
        clean_claims=["CL-0101"],
    )


def _clean_case(case_id: str = "c1") -> EvaluationCase:
    return _case(case_id, clean_claims=["CL-0101", "CL-0109"])


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


def test_perfect_run_scores_one():
    cases = [_blocking_case("b1"), _clean_case("c1")]
    outcomes = [
        _outcome("b1", findings=[_finding()], blocked=True),
        _outcome("c1"),
    ]
    score = score_corpus(cases, outcomes)
    assert score.defect_recall == 1.0
    assert score.true_negative_rate == 1.0
    assert score.reward == 1.0
    assert score.block_agreement == 1.0
    assert score.format_compliance == 1.0


def test_hand_computed_product_not_mean():
    """Full arithmetic on a hand-computed three-case split.

    Cases:
      b1 (blocking): d1 stale_source@CL-0102 (blocking),
                     d2 number_without_source@CL-0103 (blocking),
                     clean claim CL-0101 -- EXCLUDED from TNR scope
                     because b1.must_block is True.
      w1 (warning-only, must_block False): d3
                     target_priority_claim_missing_from_summary@audited_brief#L40
                     (warning), clean claim CL-0108 -- in TNR scope, unflagged.
      c1 (defect-free, must_block False): clean claims
                     CL-0101 and CL-0109.

    Outcomes:
      b1 reports d1's exact finding plus one unmatched finding (d2 missed),
      blocked=True.  w1 reports d3 as a warning finding, blocked=False.
      c1 reports one finding at CL-0101, blocked=False.

    recall = 2 detected / 3 seeded            = 2/3
    TNR    = 1 - 1 flagged / 3 in-scope clean = 1 - 1/3 = 2/3
    R      = 2/3 * 2/3                        = 4/9   (the mean would be 2/3)
    """
    b1 = _case(
        "b1",
        defects=[
            _defect("d1"),
            _defect(
                "d2",
                finding_type="number_without_source",
                locator="CL-0103",
            ),
        ],
        clean_claims=["CL-0101"],
    )
    w1 = _case(
        "w1",
        defects=[
            _defect(
                "d3",
                finding_type="target_priority_claim_missing_from_summary",
                locator="audited_brief#L40",
                expected_blocking_level="warning",
            )
        ],
        clean_claims=["CL-0108"],
    )
    c1 = _clean_case("c1")
    outcomes = [
        _outcome(
            "b1",
            findings=[
                _finding(),
                _finding(
                    finding_type="claim_support_matrix_blocking_support",
                    locator="CL-0199",
                ),
            ],
            blocked=True,
        ),
        _outcome(
            "w1",
            findings=[
                _finding(
                    finding_type="target_priority_claim_missing_from_summary",
                    locator="audited_brief#L40",
                    blocking_level="warning",
                )
            ],
        ),
        _outcome(
            "c1",
            findings=[_finding(finding_type="number_without_source", locator="CL-0101")],
        ),
    ]
    score = score_corpus([b1, w1, c1], outcomes)
    assert score.seeded_total == 3
    assert score.seeded_detected == 2
    assert score.defect_recall == pytest.approx(2 / 3)
    assert score.clean_total == 3
    assert score.clean_flagged == 1
    assert score.true_negative_rate == pytest.approx(2 / 3)
    assert score.reward == pytest.approx(4 / 9)
    assert score.reward != pytest.approx((2 / 3 + 2 / 3) / 2)
    assert score.block_agreement == 1.0
    assert score.case_count == 3
    assert score.format_compliance == 1.0


def test_warning_level_detection_counts_toward_recall():
    """Flagging a warning-level seeded defect as a warning found that defect."""
    w1 = _case(
        "w1",
        defects=[
            _defect(
                finding_type="target_priority_claim_missing_from_summary",
                locator="audited_brief#L40",
                expected_blocking_level="warning",
            )
        ],
    )
    outcome = _outcome(
        "w1",
        findings=[
            _finding(
                finding_type="target_priority_claim_missing_from_summary",
                locator="audited_brief#L40",
                blocking_level="warning",
            )
        ],
    )
    score = score_corpus([w1], [outcome])
    assert score.seeded_detected == 1
    assert score.defect_recall == 1.0


def test_blocking_level_disagreement_credits_recall_lowers_agreement():
    """Role reports the blocking defect as a warning and does not block."""
    case = _blocking_case("b1")
    outcome = _outcome(
        "b1",
        findings=[_finding(blocking_level="warning")],
        blocked=False,
    )
    score = score_corpus([case], [outcome])
    assert score.defect_recall == 1.0
    assert score.block_agreement == 0.0
    # Blocking case clean claims are out of TNR scope, so R stays perfect:
    # the level disagreement lives in block_agreement, never in R.
    assert score.reward == 1.0


def test_tnr_excludes_blocking_case_clean_claims():
    """b1's flagged clean claim must not enter the TNR denominator."""
    cases = [_blocking_case("b1"), _clean_case("c1")]
    outcomes = [
        _outcome(
            "b1",
            findings=[
                _finding(),
                _finding(
                    finding_type="number_without_source",
                    locator="CL-0101",
                ),
            ],
            blocked=True,
        ),
        _outcome("c1"),
    ]
    score = score_corpus(cases, outcomes)
    assert score.clean_total == 2
    assert score.clean_flagged == 0
    assert score.true_negative_rate == 1.0


def test_tnr_flags_by_locator_regardless_of_finding_type():
    """A clean claim is flagged when ANY reported finding's locator equals
    it -- a false positive is a false positive whatever type vocabulary it
    was filed under."""
    case = _clean_case("c1")
    outcome = _outcome(
        "c1",
        findings=[
            # Right locator, "wrong" type: still flags the clean claim.
            _finding(finding_type="stale_source", locator="CL-0101")
        ],
    )
    score = score_corpus([case], [outcome])
    assert score.clean_total == 2
    assert score.clean_flagged == 1
    assert score.true_negative_rate == pytest.approx(1 / 2)


def test_noncompliant_findings_cannot_flag_a_clean_claim():
    """Noncompliant findings carry no locator and never enter ``findings``,
    so however many are counted they cannot flag a clean claim -- and they
    do not raise any false-positive credit either."""
    case = _clean_case("c1")
    outcome = _outcome("c1", noncompliant_finding_count=5)
    score = score_corpus([case], [outcome])
    assert score.clean_flagged == 0
    assert score.true_negative_rate == 1.0
    # ...but they DO lower format compliance (see below).
    assert score.format_compliance == 0.0


def test_one_flagged_clean_claim_lowers_reward():
    """All defects detected, one clean claim flagged: R must drop below 1.0."""
    cases = [_blocking_case("b1"), _clean_case("c1")]
    outcomes = [
        _outcome("b1", findings=[_finding()], blocked=True),
        _outcome(
            "c1",
            findings=[_finding(finding_type="number_without_source", locator="CL-0101")],
        ),
    ]
    score = score_corpus(cases, outcomes)
    assert score.defect_recall == 1.0
    assert score.true_negative_rate == pytest.approx(1 / 2)
    assert score.reward == pytest.approx(1 / 2)
    assert score.reward < 1.0


def test_flagging_everything_scores_zero():
    """The omission attractor's mirror: over-blocking must not pay."""
    cases = [_blocking_case("b1"), _clean_case("c1")]
    outcomes = [
        _outcome(
            "b1",
            findings=[
                _finding(),
                _finding(
                    finding_type="number_without_source",
                    locator="CL-0101",
                ),
            ],
            blocked=True,
        ),
        _outcome(
            "c1",
            findings=[
                _finding(finding_type="number_without_source", locator="CL-0101"),
                _finding(finding_type="number_without_source", locator="CL-0109"),
            ],
            blocked=True,
        ),
    ]
    score = score_corpus(cases, outcomes)
    assert score.defect_recall == 1.0
    assert score.true_negative_rate == 0.0
    assert score.reward == 0.0


def test_detecting_nothing_scores_zero():
    """Passing everything through must not pay either."""
    cases = [_blocking_case("b1"), _clean_case("c1")]
    outcomes = [_outcome("b1"), _outcome("c1")]
    score = score_corpus(cases, outcomes)
    assert score.defect_recall == 0.0
    assert score.true_negative_rate == 1.0
    assert score.reward == 0.0


def test_empty_seeded_set_yields_recall_one():
    cases = [_clean_case("c1")]
    outcomes = [_outcome("c1")]
    score = score_corpus(cases, outcomes)
    assert score.seeded_total == 0
    assert score.defect_recall == 1.0
    assert score.reward == 1.0


def test_empty_clean_set_yields_tnr_one():
    case = _case("b1", defects=[_defect()], clean_claims=[])
    outcome = _outcome("b1", findings=[_finding()], blocked=True)
    score = score_corpus([case], [outcome])
    assert score.clean_total == 0
    assert score.true_negative_rate == 1.0


def test_same_type_wrong_locator_is_not_detected():
    case = _blocking_case("b1")
    outcome = _outcome("b1", findings=[_finding(locator="CL-0115")], blocked=True)
    score = score_corpus([case], [outcome])
    assert score.seeded_detected == 0
    assert score.defect_recall == 0.0


def test_same_locator_wrong_type_is_not_detected():
    case = _blocking_case("b1")
    outcome = _outcome(
        "b1",
        findings=[_finding(finding_type="number_without_source")],
        blocked=True,
    )
    score = score_corpus([case], [outcome])
    assert score.seeded_detected == 0
    assert score.defect_recall == 0.0


def test_convenience_views_do_not_drive_scoring():
    """found_defect_ids / flagged_claim_locators alone credit nothing."""
    cases = [_blocking_case("b1"), _clean_case("c1")]
    ghost_ids = RolloutOutcome.model_validate(
        {
            "case_id": "b1",
            "found_defect_ids": ["d1"],
            "blocked": True,
        },
        strict=True,
    )
    ghost_flags = RolloutOutcome.model_validate(
        {
            "case_id": "c1",
            "flagged_claim_locators": ["CL-0101"],
            "blocked": False,
        },
        strict=True,
    )
    score = score_corpus(cases, [ghost_ids, ghost_flags])
    assert score.seeded_detected == 0
    assert score.defect_recall == 0.0
    assert score.clean_flagged == 0
    assert score.true_negative_rate == 1.0
    assert score.reward == 0.0


# ---------------------------------------------------------------------------
# Format compliance: reported alongside R, never inside it
# ---------------------------------------------------------------------------


def test_format_compliance_aggregates_across_outcomes():
    """compliant / (compliant + noncompliant), pooled over every outcome."""
    cases = [_blocking_case("b1"), _clean_case("c1")]
    outcomes = [
        # 2 compliant + 1 noncompliant
        _outcome(
            "b1",
            findings=[_finding(), _finding(finding_type="number_without_source", locator="CL-0150")],
            blocked=True,
            noncompliant_finding_count=1,
        ),
        # 0 compliant + 3 noncompliant
        _outcome("c1", noncompliant_finding_count=3),
    ]
    score = score_corpus(cases, outcomes)
    assert score.format_compliance == pytest.approx(2 / 6)


def test_format_compliance_is_one_when_nothing_reported():
    cases = [_clean_case("c1")]
    outcomes = [_outcome("c1")]
    score = score_corpus(cases, outcomes)
    assert score.format_compliance == 1.0


def test_format_compliance_never_enters_reward():
    """A fully noncompliant report (scored as: nothing detected, nothing
    flagged) keeps R at its omission-floor value while compliance is 0 --
    the two numbers stay independent."""
    cases = [_blocking_case("b1")]
    compliant_only = _outcome("b1", findings=[], blocked=False, noncompliant_finding_count=4)
    score = score_corpus(cases, [compliant_only])
    assert score.format_compliance == 0.0
    assert score.defect_recall == 0.0
    assert score.true_negative_rate == 1.0
    assert score.reward == 0.0


def test_duplicate_outcome_raises():
    cases = [_blocking_case("b1")]
    outcomes = [
        _outcome("b1", findings=[_finding()], blocked=True),
        _outcome("b1", findings=[_finding()], blocked=True),
    ]
    with pytest.raises(ValueError, match="duplicate rollout outcome"):
        score_corpus(cases, outcomes)


def test_unknown_outcome_case_id_raises():
    cases = [_blocking_case("b1")]
    outcomes = [_outcome("b1", findings=[_finding()], blocked=True), _outcome("ghost")]
    with pytest.raises(ValueError, match="unknown rollout outcome"):
        score_corpus(cases, outcomes)


def test_missing_outcome_raises():
    with pytest.raises(ValueError, match="missing rollout outcome"):
        score_corpus([_blocking_case("b1")], [])
