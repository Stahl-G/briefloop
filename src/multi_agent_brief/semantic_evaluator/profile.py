"""Strict loading of the frozen Chinese research-design profile."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import ValidationError
import yaml

from multi_agent_brief.semantic_evaluator.contracts import EvaluatorProfile
from multi_agent_brief.semantic_evaluator.errors import SemanticEvaluatorError
from multi_agent_brief.semantic_evaluator.resources import resource_text
from multi_agent_brief.semantic_evaluator.serialization import (
    canonical_model_sha256,
    strict_model_payload,
)


PROFILE_ID = "research_design_report_zh_v1"
PROFILE_RESOURCE = "research_design_report_zh_v1.yaml"
FROZEN_PROFILE_SHA256 = (
    "2d564f37b1a33692b58df795b57d05251e78ec9e5f891b3e0893a3ad022b4404"
)

EXPECTED_PROFILE_INVENTORY: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "cross_section_consistency",
        "O1",
        (
            "status_consistency",
            "numerical_scope_consistency",
            "conclusion_body_consistency",
        ),
    ),
    (
        "scope_definition_stability",
        "O1",
        (
            "entity_scope_stability",
            "temporal_scope_stability",
            "unit_construct_stability",
        ),
    ),
    (
        "reasoning_continuity",
        "O1",
        (
            "premise_conclusion_continuity",
            "causal_bridge_continuity",
            "qualification_preservation",
        ),
    ),
    (
        "uncertainty_calibration",
        "O1",
        (
            "evidence_state_wording_alignment",
            "limitation_wording_alignment",
            "disagreement_wording_alignment",
        ),
    ),
    (
        "summary_body_alignment",
        "O1",
        (
            "title_body_alignment",
            "summary_body_alignment",
            "status_table_body_alignment",
        ),
    ),
    (
        "recommendation_constraint_consistency",
        "O1",
        (
            "recommendation_precondition_consistency",
            "recommendation_status_consistency",
            "recommendation_mutual_consistency",
        ),
    ),
    (
        "brief_requirement_coverage",
        "O2",
        ("must_answer_coverage", "must_include_coverage"),
    ),
    (
        "audience_decision_fit",
        "O2",
        ("audience_need_coverage", "decision_use_coverage"),
    ),
    (
        "explicit_scope_constraint_compliance",
        "O2",
        (
            "must_not_claim_compliance",
            "scope_included_compliance",
            "scope_excluded_compliance",
        ),
    ),
)


@dataclass(frozen=True)
class LoadedProfile:
    profile: EvaluatorProfile
    profile_sha256: str


def validate_exact_profile(profile: EvaluatorProfile) -> None:
    observed = tuple(
        (
            dimension.dimension_id,
            dimension.scope_class,
            tuple(item.sub_aspect_id for item in dimension.sub_aspects),
        )
        for dimension in profile.dimensions
    )
    if observed != EXPECTED_PROFILE_INVENTORY:
        raise SemanticEvaluatorError("profile_invalid")
    if sum(len(item[2]) for item in observed) != 25:
        raise SemanticEvaluatorError("profile_invalid")
    if canonical_model_sha256(profile) != FROZEN_PROFILE_SHA256:
        raise SemanticEvaluatorError("profile_invalid")


def validate_loaded_profile(loaded: LoadedProfile) -> None:
    validate_exact_profile(loaded.profile)
    if loaded.profile_sha256 != canonical_model_sha256(loaded.profile):
        raise SemanticEvaluatorError("profile_invalid")


def strict_loaded_profile_copy(loaded: LoadedProfile) -> LoadedProfile:
    """Detach and strictly revalidate a caller- or package-supplied profile."""

    strict: LoadedProfile | None = None
    invalid = False
    try:
        if not isinstance(loaded, LoadedProfile):
            raise TypeError("profile_invalid")
        profile = EvaluatorProfile.model_validate(strict_model_payload(loaded.profile))
        if type(loaded.profile_sha256) is not str:
            raise TypeError("profile_invalid")
        strict = LoadedProfile(
            profile=profile,
            profile_sha256=loaded.profile_sha256,
        )
        validate_loaded_profile(strict)
    except Exception:
        invalid = True
    if invalid or strict is None:
        raise SemanticEvaluatorError("profile_invalid") from None
    return strict


def load_profile(profile_id: str = PROFILE_ID) -> LoadedProfile:
    if profile_id != PROFILE_ID:
        raise SemanticEvaluatorError("profile_invalid")
    try:
        payload = yaml.safe_load(resource_text("profiles", PROFILE_RESOURCE))
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise SemanticEvaluatorError("profile_invalid") from exc
    if not isinstance(payload, dict):
        raise SemanticEvaluatorError("profile_invalid")
    try:
        profile = EvaluatorProfile.model_validate(payload)
    except ValidationError as exc:
        raise SemanticEvaluatorError("profile_invalid") from exc
    validate_exact_profile(profile)
    loaded = LoadedProfile(
        profile=profile,
        profile_sha256=canonical_model_sha256(profile),
    )
    validate_loaded_profile(loaded)
    return loaded


__all__ = [
    "EXPECTED_PROFILE_INVENTORY",
    "FROZEN_PROFILE_SHA256",
    "LoadedProfile",
    "PROFILE_ID",
    "load_profile",
    "strict_loaded_profile_copy",
    "validate_exact_profile",
    "validate_loaded_profile",
]
