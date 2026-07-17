"""Deterministic assessment-unit and shadow identity planning."""

from __future__ import annotations

from typing import Any

from multi_agent_brief.semantic_evaluator.contracts import (
    ASSESSMENT_PLAN_SCHEMA_ID,
    AssessmentPlan,
    AssessmentUnit,
    EvaluatorProfile,
    InputBinding,
)
from multi_agent_brief.semantic_evaluator.errors import SemanticEvaluatorError
from multi_agent_brief.semantic_evaluator.profile import (
    LoadedProfile,
    load_profile,
    strict_loaded_profile_copy,
    validate_exact_profile,
)
from multi_agent_brief.semantic_evaluator.serialization import (
    canonical_model_sha256,
    canonical_sha256,
)


UNIT_PLANNER_VERSION = "assessment_units_v1"


def _derived_id(prefix: str, identity: Any) -> str:
    return f"{prefix}{canonical_sha256(identity)[:12]}"


def derive_assessment_unit_id(
    *,
    trial_id: str,
    report_sha256: str,
    profile_sha256: str,
    dimension_id: str,
    sub_aspect_id: str,
) -> str:
    return _derived_id(
        "AU-",
        [trial_id, report_sha256, profile_sha256, dimension_id, sub_aspect_id],
    )


def build_assessment_plan(
    *,
    trial_id: str,
    report_sha256: str,
    profile: EvaluatorProfile,
    profile_sha256: str,
) -> AssessmentPlan:
    validate_exact_profile(profile)
    if profile_sha256 != canonical_model_sha256(profile):
        raise SemanticEvaluatorError("profile_invalid")
    units: list[dict[str, Any]] = []
    for dimension in profile.dimensions:
        for sub_aspect in dimension.sub_aspects:
            units.append(
                AssessmentUnit(
                    assessment_unit_id=derive_assessment_unit_id(
                        trial_id=trial_id,
                        report_sha256=report_sha256,
                        profile_sha256=profile_sha256,
                        dimension_id=dimension.dimension_id,
                        sub_aspect_id=sub_aspect.sub_aspect_id,
                    ),
                    trial_id=trial_id,
                    report_sha256=report_sha256,
                    dimension_id=dimension.dimension_id,
                    sub_aspect_id=sub_aspect.sub_aspect_id,
                    scope_class=dimension.scope_class,
                    eligible_requirement_types=list(
                        sub_aspect.eligible_requirement_types
                    ),
                ).model_dump(mode="json")
            )
    plan_id = _derived_id("plan-", [trial_id, report_sha256, profile_sha256])
    payload = {
        "schema_version": ASSESSMENT_PLAN_SCHEMA_ID,
        "plan_id": plan_id,
        "trial_id": trial_id,
        "report_sha256": report_sha256,
        "profile_sha256": profile_sha256,
        "units": units,
    }
    plan = AssessmentPlan.model_validate(
        {**payload, "assessment_plan_sha256": canonical_sha256(payload)}
    )
    validate_frozen_assessment_plan(
        plan,
        loaded_profile=LoadedProfile(
            profile=profile,
            profile_sha256=profile_sha256,
        ),
    )
    return plan


def derive_run_id(
    *,
    input_binding_sha256: str,
    assessment_plan_sha256: str,
    instrument_sha256: str,
) -> str:
    return _derived_id(
        "run-",
        [input_binding_sha256, assessment_plan_sha256, instrument_sha256],
    )


def derive_attempt_ref(
    *,
    trial_id: str,
    dimension_id: str,
    attempt_ordinal: int,
    prompt_request_sha256: str,
) -> str:
    return _derived_id(
        "attempt-",
        [trial_id, dimension_id, attempt_ordinal, prompt_request_sha256],
    )


def assessment_plan_sha256(plan: AssessmentPlan) -> str:
    return canonical_model_sha256(plan, exclude=("assessment_plan_sha256",))


def validate_frozen_assessment_plan(
    plan: AssessmentPlan,
    *,
    loaded_profile: LoadedProfile | None = None,
) -> None:
    loaded = strict_loaded_profile_copy(loaded_profile or load_profile())
    if (
        plan.profile_sha256 != loaded.profile_sha256
        or plan.plan_id
        != _derived_id(
            "plan-",
            [plan.trial_id, plan.report_sha256, plan.profile_sha256],
        )
        or plan.assessment_plan_sha256 != assessment_plan_sha256(plan)
    ):
        raise SemanticEvaluatorError("assessment_plan_invalid")
    expected_entries = [
        (dimension, sub_aspect)
        for dimension in loaded.profile.dimensions
        for sub_aspect in dimension.sub_aspects
    ]
    if len(plan.units) != len(expected_entries):
        raise SemanticEvaluatorError("assessment_plan_invalid")
    for unit, (dimension, sub_aspect) in zip(plan.units, expected_entries):
        if (
            unit.trial_id != plan.trial_id
            or unit.report_sha256 != plan.report_sha256
            or unit.dimension_id != dimension.dimension_id
            or unit.sub_aspect_id != sub_aspect.sub_aspect_id
            or unit.scope_class != dimension.scope_class
            or unit.eligible_requirement_types != sub_aspect.eligible_requirement_types
            or unit.assessment_unit_id
            != derive_assessment_unit_id(
                trial_id=plan.trial_id,
                report_sha256=plan.report_sha256,
                profile_sha256=plan.profile_sha256,
                dimension_id=dimension.dimension_id,
                sub_aspect_id=sub_aspect.sub_aspect_id,
            )
        ):
            raise SemanticEvaluatorError("assessment_plan_invalid")


def derive_finding_id(
    *,
    assessment_unit_id: str,
    ordinal: int,
    proposal_identity: Any,
) -> str:
    return _derived_id("F-", [assessment_unit_id, ordinal, proposal_identity])


def derive_handoff_id(
    *,
    assessment_unit_id: str,
    ordinal: int,
    handoff_identity: Any,
) -> str:
    return _derived_id("H-", [assessment_unit_id, ordinal, handoff_identity])


def trial_request_sha256(binding: InputBinding) -> str:
    expected = canonical_model_sha256(binding, exclude=("input_binding_sha256",))
    if binding.input_binding_sha256 != expected:
        raise SemanticEvaluatorError("trial_identity_conflict")
    return binding.input_binding_sha256


def trial_identity_conflicts(existing: InputBinding, candidate: InputBinding) -> bool:
    if any(
        item.input_binding_sha256
        != canonical_model_sha256(item, exclude=("input_binding_sha256",))
        for item in (existing, candidate)
    ):
        return True
    return (
        existing.trial_id == candidate.trial_id
        and existing.input_binding_sha256 != candidate.input_binding_sha256
    )


__all__ = [
    "UNIT_PLANNER_VERSION",
    "assessment_plan_sha256",
    "build_assessment_plan",
    "derive_assessment_unit_id",
    "derive_attempt_ref",
    "derive_finding_id",
    "derive_handoff_id",
    "derive_run_id",
    "trial_identity_conflicts",
    "trial_request_sha256",
    "validate_frozen_assessment_plan",
]
