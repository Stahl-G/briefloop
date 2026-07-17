"""Complete assessment universe and deterministic identity tests."""

import warnings

import pytest

from multi_agent_brief.semantic_evaluator.errors import SemanticEvaluatorError
from multi_agent_brief.semantic_evaluator.profile import (
    EXPECTED_PROFILE_INVENTORY,
    load_profile,
)
from multi_agent_brief.semantic_evaluator.serialization import canonical_model_sha256
from multi_agent_brief.semantic_evaluator.unit_planner import (
    build_assessment_plan,
    derive_assessment_unit_id,
    derive_run_id,
)


def _plan(trial_id="trial-001", report_sha="0" * 64):
    loaded = load_profile()
    return build_assessment_plan(
        trial_id=trial_id,
        report_sha256=report_sha,
        profile=loaded.profile,
        profile_sha256=loaded.profile_sha256,
    )


def test_planner_emits_exact_ordered_25_unit_inventory() -> None:
    plan = _plan()
    expected = [
        (dimension_id, sub_aspect_id)
        for dimension_id, _scope, sub_aspects in EXPECTED_PROFILE_INVENTORY
        for sub_aspect_id in sub_aspects
    ]
    assert len(plan.units) == 25
    assert [(item.dimension_id, item.sub_aspect_id) for item in plan.units] == expected
    assert [item.scope_class for item in plan.units[:18]] == ["O1"] * 18
    assert [item.scope_class for item in plan.units[18:]] == ["O2"] * 7
    assert plan.units[18].eligible_requirement_types == ["must_answer"]
    assert plan.units[19].eligible_requirement_types == ["must_include"]
    assert plan.units[-1].eligible_requirement_types == ["scope_excluded"]


def test_unit_ids_follow_frozen_canonical_identity_tuple() -> None:
    loaded = load_profile()
    plan = _plan()
    first = plan.units[0]
    assert first.assessment_unit_id == derive_assessment_unit_id(
        trial_id=plan.trial_id,
        report_sha256=plan.report_sha256,
        profile_sha256=loaded.profile_sha256,
        dimension_id=first.dimension_id,
        sub_aspect_id=first.sub_aspect_id,
    )
    assert len({item.assessment_unit_id for item in plan.units}) == 25


def test_plan_and_run_identities_are_deterministic_and_binding_sensitive() -> None:
    first = _plan()
    assert first == _plan()
    assert first.assessment_plan_sha256 == canonical_model_sha256(
        first, exclude=("assessment_plan_sha256",)
    )
    assert (
        first.assessment_plan_sha256
        != _plan(trial_id="trial-002").assessment_plan_sha256
    )
    assert (
        first.assessment_plan_sha256
        != _plan(report_sha="1" * 64).assessment_plan_sha256
    )
    values = {
        "input_binding_sha256": "2" * 64,
        "assessment_plan_sha256": first.assessment_plan_sha256,
        "instrument_sha256": "3" * 64,
    }
    assert derive_run_id(**values) == derive_run_id(**values)
    values["instrument_sha256"] = "4" * 64
    assert derive_run_id(**values) != derive_run_id(
        input_binding_sha256="2" * 64,
        assessment_plan_sha256=first.assessment_plan_sha256,
        instrument_sha256="3" * 64,
    )


def test_plan_builder_rejects_malformed_identity_value_free() -> None:
    loaded = load_profile()
    hidden_detail = "PRIVATE SYNTHETIC TRIAL VALUE"
    with pytest.raises(SemanticEvaluatorError) as caught:
        build_assessment_plan(
            trial_id=hidden_detail,
            report_sha256="0" * 64,
            profile=loaded.profile,
            profile_sha256=loaded.profile_sha256,
        )
    assert caught.value.reason_code == "assessment_plan_invalid"
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert hidden_detail not in repr(caught.value)


@pytest.mark.parametrize("mutation", ["profile_extra", "nested_extra"])
def test_plan_builder_rejects_undeclared_typed_profile_state(
    mutation: str,
) -> None:
    loaded = load_profile()
    hidden_detail = "PRIVATE SYNTHETIC PROFILE EXTRA"
    if mutation == "profile_extra":
        malformed = loaded.profile.model_copy(update={"unknown_extra": hidden_detail})
    else:
        dimension = loaded.profile.dimensions[0]
        sub_aspect = dimension.sub_aspects[0].model_copy(
            update={"unknown_extra": hidden_detail}
        )
        malformed_dimension = dimension.model_copy(
            update={"sub_aspects": [sub_aspect, *dimension.sub_aspects[1:]]}
        )
        malformed = loaded.profile.model_copy(
            update={"dimensions": [malformed_dimension, *loaded.profile.dimensions[1:]]}
        )
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter("always")
        with pytest.raises(SemanticEvaluatorError) as caught:
            build_assessment_plan(
                trial_id="trial-001",
                report_sha256="0" * 64,
                profile=malformed,
                profile_sha256=loaded.profile_sha256,
            )
    assert caught.value.reason_code == "assessment_plan_invalid"
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert hidden_detail not in repr(caught.value)
    assert not seen
