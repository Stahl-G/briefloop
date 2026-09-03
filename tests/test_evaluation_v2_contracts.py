"""Strict contracts for agent-rollout evaluation cases.

``must_block`` is derived from ``SeededDefect.expected_blocking_level`` rather
than asserted, because the retired annotated corpus contains warning-only cases
(``blocked: false`` carrying warning-level findings) that a boolean input field
cannot express honestly.

The measurable vocabulary is deliberately four detection types (the shrink
from ten is a measurement-design decision, pinned here); the six
finalize-family types live separately in ``GENERATION_DEFECT_TYPES`` with
inverted polarity and no corpus representation.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from multi_agent_brief.evaluation_v2.contracts import (
    EVOLVABLE_ROLES,
    FINDING_TYPES,
    GENERATION_DEFECT_TYPES,
    CorpusScore,
    EvaluationCase,
    ReportedFinding,
    RolloutOutcome,
    RolloutSpec,
    SeededDefect,
)


def _case_payload(**overrides):
    payload = {
        "case_id": "stale_source_in_weekly_pack",
        "synthetic": True,
        "source_pack": "cases/stale_source_in_weekly_pack/sources",
        "report_date": "2026-06-08",
        "rollout": {"role": "auditor", "runtime": "codex"},
        "seeded_defects": [
            {
                "defect_id": "d1",
                "finding_type": "stale_source",
                "locator": "CL-0102",
                "expected_blocking_level": "blocking",
            }
        ],
        "clean_claims": ["CL-0101"],
    }
    payload.update(overrides)
    return payload


def _defect(defect_id: str = "d1", **overrides):
    defect = {
        "defect_id": defect_id,
        "finding_type": "stale_source",
        "locator": "CL-0102",
        "expected_blocking_level": "blocking",
    }
    defect.update(overrides)
    return defect


def test_valid_case_parses():
    case = EvaluationCase.model_validate(_case_payload(), strict=True)
    assert case.case_id == "stale_source_in_weekly_pack"
    assert case.seeded_defects[0].finding_type == "stale_source"
    assert case.must_block is True


def test_case_rejects_unknown_finding_type():
    payload = _case_payload(
        seeded_defects=[
            _defect(finding_type="not_a_real_type", locator="CL-0102")
        ]
    )
    with pytest.raises(ValidationError):
        EvaluationCase.model_validate(payload, strict=True)


def test_case_rejects_generation_family_types_as_seeded_defects():
    """The shrink is enforced by the schema: final_* and target_relevance_gap
    measure generation quality (inverted polarity) and are NOT seedable
    detection defects."""
    for finding_type in sorted(GENERATION_DEFECT_TYPES):
        with pytest.raises(ValidationError):
            EvaluationCase.model_validate(
                _case_payload(
                    seeded_defects=[_defect(finding_type=finding_type)]
                ),
                strict=True,
            )


def test_case_rejects_non_synthetic():
    with pytest.raises(ValidationError):
        EvaluationCase.model_validate(_case_payload(synthetic=False), strict=True)


def test_case_rejects_extra_fields():
    with pytest.raises(ValidationError):
        EvaluationCase.model_validate(
            _case_payload(command="rm -rf /"), strict=True
        )


def test_case_rejects_must_block_as_an_input_field():
    """``must_block`` is derived; a payload asserting it is rejected."""
    with pytest.raises(ValidationError):
        EvaluationCase.model_validate(_case_payload(must_block=True), strict=True)


def test_case_rejects_invalid_report_date():
    with pytest.raises(ValidationError):
        EvaluationCase.model_validate(_case_payload(report_date="not-a-date"), strict=True)


def test_must_block_is_derived_not_a_model_field():
    case = EvaluationCase.model_validate(_case_payload(), strict=True)
    assert "must_block" not in EvaluationCase.model_fields
    assert "must_block" not in case.model_dump()
    assert case.must_block is True


def test_warning_only_case_parses_and_does_not_block():
    """Warning-level detection findings on an unblocked case: the shape a
    boolean must_block field cannot express honestly."""
    payload = _case_payload(
        case_id="warning_only_detection_case",
        seeded_defects=[
            _defect(
                defect_id="d1",
                finding_type="stale_source",
                locator="CL-0102",
                expected_blocking_level="warning",
            ),
            _defect(
                defect_id="d2",
                finding_type="target_priority_claim_missing_from_summary",
                locator="audited_brief#L40",
                expected_blocking_level="warning",
            ),
        ],
    )
    case = EvaluationCase.model_validate(payload, strict=True)
    assert len(case.seeded_defects) == 2
    assert case.must_block is False


def test_mixed_blocking_and_warning_case_must_block():
    payload = _case_payload(
        seeded_defects=[
            _defect(defect_id="d1", expected_blocking_level="blocking"),
            _defect(
                defect_id="d2",
                finding_type="number_without_source",
                locator="audited_brief#L3",
                expected_blocking_level="warning",
            ),
        ]
    )
    case = EvaluationCase.model_validate(payload, strict=True)
    assert case.must_block is True


def test_zero_defect_case_with_clean_claims_parses():
    case = EvaluationCase.model_validate(
        _case_payload(seeded_defects=[]), strict=True
    )
    assert case.seeded_defects == []
    assert case.clean_claims == ["CL-0101"]
    assert case.must_block is False


def test_defect_ids_must_be_unique_within_a_case():
    payload = _case_payload(
        seeded_defects=[
            _defect(defect_id="d1", locator="CL-0102"),
            _defect(defect_id="d1", finding_type="number_without_source", locator="CL-0103"),
        ]
    )
    with pytest.raises(ValidationError):
        EvaluationCase.model_validate(payload, strict=True)


def test_defect_rejects_info_blocking_level():
    with pytest.raises(ValidationError):
        SeededDefect.model_validate(
            _defect(expected_blocking_level="info"), strict=True
        )


def test_finding_types_are_exactly_the_four_detection_types():
    assert FINDING_TYPES == frozenset(
        {
            "claim_support_matrix_blocking_support",
            "number_without_source",
            "stale_source",
            "target_priority_claim_missing_from_summary",
        }
    )


def test_generation_defect_types_are_the_six_finalize_family_types():
    assert GENERATION_DEFECT_TYPES == frozenset(
        {
            "target_relevance_gap",
            "final_incomplete_key_case_fields",
            "final_missing_comparison_basis",
            "final_missing_limitation_section",
            "final_scope_title_mismatch",
            "final_unsupported_superlative",
        }
    )
    # The shrink from 10 to 4 is deliberate: the two vocabularies partition
    # the retired 10-type set and never overlap.
    assert not FINDING_TYPES & GENERATION_DEFECT_TYPES
    assert len(FINDING_TYPES | GENERATION_DEFECT_TYPES) == 10


def test_evolvable_roles_are_auditor_only():
    assert EVOLVABLE_ROLES == ("auditor",)


def test_rollout_spec_rejects_unknown_role_and_runtime():
    for role in ("analyst", "editor", "screener", "claim-ledger", "scout"):
        with pytest.raises(ValidationError):
            RolloutSpec.model_validate({"role": role, "runtime": "codex"}, strict=True)
    with pytest.raises(ValidationError):
        RolloutSpec.model_validate({"role": "auditor", "runtime": "claude"}, strict=True)


def test_rollout_outcome_parses():
    outcome = RolloutOutcome.model_validate(
        {
            "case_id": "c1",
            "found_defect_ids": ["d1"],
            "flagged_claim_locators": ["CL-0101"],
            "blocked": True,
        },
        strict=True,
    )
    assert outcome.found_defect_ids == ["d1"]
    assert outcome.blocked is True
    assert outcome.noncompliant_finding_count == 0


def test_rollout_outcome_carries_reported_findings_and_noncompliant_count():
    outcome = RolloutOutcome.model_validate(
        {
            "case_id": "c1",
            "found_defect_ids": ["d1"],
            "flagged_claim_locators": [],
            "blocked": False,
            "findings": [
                {
                    "finding_type": "stale_source",
                    "locator": "CL-0102",
                    "blocking_level": "warning",
                }
            ],
            "noncompliant_finding_count": 2,
        },
        strict=True,
    )
    assert isinstance(outcome.findings[0], ReportedFinding)
    assert outcome.findings[0].blocking_level == "warning"
    assert outcome.noncompliant_finding_count == 2
    with pytest.raises(ValidationError):
        RolloutOutcome.model_validate(
            {
                "case_id": "c1",
                "blocked": False,
                "findings": [
                    {
                        "finding_type": "stale_source",
                        "locator": "CL-0102",
                        "blocking_level": "info",
                    }
                ],
            },
            strict=True,
        )
    # A reported finding outside the detection vocabulary cannot even be
    # recorded as a finding -- only counted.
    with pytest.raises(ValidationError):
        RolloutOutcome.model_validate(
            {
                "case_id": "c1",
                "blocked": False,
                "findings": [
                    {
                        "finding_type": "final_unsupported_superlative",
                        "locator": "CL-0102",
                        "blocking_level": "warning",
                    }
                ],
            },
            strict=True,
        )


def test_seeded_defect_rejects_blank_locator():
    with pytest.raises(ValidationError):
        SeededDefect.model_validate(_defect(locator="  "), strict=True)


def test_corpus_score_rejects_extra_fields():
    with pytest.raises(ValidationError):
        CorpusScore.model_validate(
            {
                "defect_recall": 1.0,
                "true_negative_rate": 1.0,
                "reward": 1.0,
                "seeded_total": 0,
                "seeded_detected": 0,
                "clean_total": 0,
                "clean_flagged": 0,
                "block_agreement": 1.0,
                "format_compliance": 1.0,
                "case_count": 0,
                "bonus": 1,
            },
            strict=True,
        )


def test_corpus_score_requires_format_compliance():
    payload = {
        "defect_recall": 1.0,
        "true_negative_rate": 1.0,
        "reward": 1.0,
        "seeded_total": 0,
        "seeded_detected": 0,
        "clean_total": 0,
        "clean_flagged": 0,
        "block_agreement": 1.0,
        "case_count": 0,
    }
    with pytest.raises(ValidationError):
        CorpusScore.model_validate(payload, strict=True)
