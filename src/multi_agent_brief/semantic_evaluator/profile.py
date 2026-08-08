"""Strict loading of the versioned Semantic Evaluator profile registry."""

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
READER_REVIEW_PROFILE_ID = "management_brief_en_v1"
PROFILE_RESOURCE = "research_design_report_zh_v1.yaml"
READER_REVIEW_PROFILE_RESOURCE = "management_brief_en_v1.yaml"
FROZEN_PROFILE_SHA256 = (
    "2d564f37b1a33692b58df795b57d05251e78ec9e5f891b3e0893a3ad022b4404"
)
# Updated only when the package-owned profile bytes and strict normalized
# contract are intentionally rotated together.
READER_REVIEW_FROZEN_PROFILE_SHA256 = (
    "18fae981cbf5e2df1c33404e3ab8ca03e0441b2bd7b4e32ad4b79f167f82e943"
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

READER_REVIEW_EXPECTED_PROFILE_INVENTORY: tuple[
    tuple[str, str, tuple[str, ...]], ...
] = (
    (
        "cross_section_consistency",
        "O1",
        (
            "summary_body_alignment",
            "entity_time_number_consistency",
            "reasoning_continuity",
            "uncertainty_consistency",
            "recommendation_constraint_consistency",
        ),
    ),
    (
        "brief_requirement_coverage",
        "O2",
        (
            "must_answer_coverage",
            "must_include_coverage",
            "must_not_claim_compliance",
            "audience_need_coverage",
            "decision_use_coverage",
            "scope_included_coverage",
            "scope_excluded_compliance",
        ),
    ),
)

_PROFILE_REGISTRY = {
    PROFILE_ID: (
        PROFILE_RESOURCE,
        FROZEN_PROFILE_SHA256,
        EXPECTED_PROFILE_INVENTORY,
        25,
    ),
    READER_REVIEW_PROFILE_ID: (
        READER_REVIEW_PROFILE_RESOURCE,
        READER_REVIEW_FROZEN_PROFILE_SHA256,
        READER_REVIEW_EXPECTED_PROFILE_INVENTORY,
        12,
    ),
}


@dataclass(frozen=True)
class LoadedProfile:
    profile: EvaluatorProfile
    profile_sha256: str


def profile_ids() -> tuple[str, ...]:
    return tuple(_PROFILE_REGISTRY)


def validate_exact_profile(profile: EvaluatorProfile) -> None:
    registry = _PROFILE_REGISTRY.get(profile.profile_id)
    if registry is None:
        raise SemanticEvaluatorError("profile_invalid")
    _resource, expected_sha256, expected_inventory, expected_unit_count = registry
    observed = tuple(
        (
            dimension.dimension_id,
            dimension.scope_class,
            tuple(item.sub_aspect_id for item in dimension.sub_aspects),
        )
        for dimension in profile.dimensions
    )
    if observed != expected_inventory:
        raise SemanticEvaluatorError("profile_invalid")
    if sum(len(item[2]) for item in observed) != expected_unit_count:
        raise SemanticEvaluatorError("profile_invalid")
    if canonical_model_sha256(profile) != expected_sha256:
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
        profile = EvaluatorProfile.model_validate(
            strict_model_payload(loaded.profile), strict=True
        )
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
    registry = _PROFILE_REGISTRY.get(profile_id)
    if registry is None:
        raise SemanticEvaluatorError("profile_invalid")
    resource, _expected_sha256, _inventory, _unit_count = registry
    try:
        payload = yaml.safe_load(resource_text("profiles", resource))
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise SemanticEvaluatorError("profile_invalid") from exc
    if not isinstance(payload, dict):
        raise SemanticEvaluatorError("profile_invalid")
    try:
        profile = EvaluatorProfile.model_validate(payload, strict=True)
    except ValidationError as exc:
        raise SemanticEvaluatorError("profile_invalid") from exc
    validate_exact_profile(profile)
    loaded = LoadedProfile(
        profile=profile,
        profile_sha256=canonical_model_sha256(profile),
    )
    validate_loaded_profile(loaded)
    return loaded


def load_profile_by_sha256(profile_sha256: str) -> LoadedProfile:
    matches = [
        profile_id
        for profile_id, (
            _resource,
            expected_sha,
            _inventory,
            _count,
        ) in _PROFILE_REGISTRY.items()
        if expected_sha == profile_sha256
    ]
    if len(matches) != 1:
        raise SemanticEvaluatorError("profile_invalid")
    return load_profile(matches[0])


__all__ = [
    "EXPECTED_PROFILE_INVENTORY",
    "FROZEN_PROFILE_SHA256",
    "LoadedProfile",
    "PROFILE_ID",
    "PROFILE_RESOURCE",
    "READER_REVIEW_EXPECTED_PROFILE_INVENTORY",
    "READER_REVIEW_FROZEN_PROFILE_SHA256",
    "READER_REVIEW_PROFILE_ID",
    "READER_REVIEW_PROFILE_RESOURCE",
    "load_profile",
    "load_profile_by_sha256",
    "profile_ids",
    "strict_loaded_profile_copy",
    "validate_exact_profile",
    "validate_loaded_profile",
]
