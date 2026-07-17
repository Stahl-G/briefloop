"""Frozen prompt resources and deterministic per-dimension assembly."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Protocol

from multi_agent_brief.semantic_evaluator.contracts import (
    AssessmentPlan,
    BoundedContext,
    DimensionProfile,
    DimensionResponse,
    ReaderArtifact,
)
from multi_agent_brief.semantic_evaluator.normalization import (
    replay_reader_artifact,
    verify_bounded_context,
)
from multi_agent_brief.semantic_evaluator.resources import (
    resource_sha256,
    resource_text,
)
from multi_agent_brief.semantic_evaluator.serialization import (
    canonical_json_text,
    canonical_sha256,
)
from multi_agent_brief.semantic_evaluator.snapshot import (
    DIMENSION_PROMPT_RESOURCE,
    SYSTEM_PROMPT_RESOURCE,
    EvaluatorResourceSnapshot,
    acquire_resource_snapshot,
)
from multi_agent_brief.semantic_evaluator.unit_planner import (
    validate_frozen_assessment_plan,
)


PROMPT_ASSEMBLER_VERSION = "dimension_prompt_assembler_v2"
CANARY_DERIVATION_VERSION = "semantic_evaluator_canary_v1"


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_DIMENSION_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class PromptSizer(Protocol):
    sizer_id: str
    sizer_version: str

    def count_tokens(self, *, system_text: str, user_text: str) -> int: ...


@dataclass(frozen=True)
class FrozenDimensionPrompt:
    dimension_id: str
    system_text: str
    user_text: str
    forbidden_canary_values: tuple[str, ...]
    request_sha256: str


def derive_forbidden_canary_values(
    *,
    assessment_plan_sha256: str,
    bounded_context_sha256: str,
    dimension_id: str,
) -> tuple[str, ...]:
    """Derive the one prompt-owned non-secret sentinel for a dimension."""

    if (
        not isinstance(assessment_plan_sha256, str)
        or _SHA256_RE.fullmatch(assessment_plan_sha256) is None
        or not isinstance(bounded_context_sha256, str)
        or _SHA256_RE.fullmatch(bounded_context_sha256) is None
        or not isinstance(dimension_id, str)
        or _DIMENSION_ID_RE.fullmatch(dimension_id) is None
    ):
        raise ValueError("canary_derivation_input_invalid")
    digest = canonical_sha256(
        [
            CANARY_DERIVATION_VERSION,
            assessment_plan_sha256,
            bounded_context_sha256,
            dimension_id,
        ]
    )
    return (f"BLSE_CANARY_V1_{digest}",)


def system_prompt_text() -> str:
    return resource_text("prompts", SYSTEM_PROMPT_RESOURCE)


def dimension_template_text() -> str:
    return resource_text("prompts", DIMENSION_PROMPT_RESOURCE)


def system_prompt_sha256() -> str:
    return resource_sha256("prompts", SYSTEM_PROMPT_RESOURCE)


def dimension_prompt_sha256() -> str:
    return resource_sha256("prompts", DIMENSION_PROMPT_RESOURCE)


def build_dimension_prompt(
    *,
    reader_artifact: ReaderArtifact,
    normalized_text: str,
    bounded_context: BoundedContext,
    dimension: DimensionProfile,
    assessment_plan: AssessmentPlan,
    _resource_snapshot: EvaluatorResourceSnapshot | None = None,
) -> FrozenDimensionPrompt:
    resources = _resource_snapshot or acquire_resource_snapshot()
    replay_reader_artifact(reader_artifact, normalized_text)
    bounded_context = verify_bounded_context(bounded_context)
    validate_frozen_assessment_plan(
        assessment_plan,
        loaded_profile=resources.loaded_profile,
    )
    dimension_units = [
        item
        for item in assessment_plan.units
        if item.dimension_id == dimension.dimension_id
    ]
    if (
        assessment_plan.report_sha256 != reader_artifact.report_sha256
        or [item.sub_aspect_id for item in dimension_units]
        != [item.sub_aspect_id for item in dimension.sub_aspects]
        or any(item.scope_class != dimension.scope_class for item in dimension_units)
    ):
        raise ValueError("dimension_prompt_plan_binding_invalid")
    report_data = {
        "artifact": reader_artifact.model_dump(mode="json"),
        "normalized_text": normalized_text,
    }
    if dimension.scope_class == "O1":
        context_data = {
            "availability": "unavailable_non_evidentiary",
            "context_sha256": bounded_context.context_sha256,
            "requirements": [],
        }
    else:
        allowed = set(dimension.eligible_requirement_types)
        context_data = {
            "availability": "bounded_requirements_only",
            "context_sha256": bounded_context.context_sha256,
            "language": bounded_context.language,
            "data_class": bounded_context.data_class,
            "requirements": [
                item.model_dump(mode="json")
                for item in bounded_context.requirements
                if item.type in allowed
            ],
        }
    rubric_data = {
        "trial_id": assessment_plan.trial_id,
        "profile_sha256": assessment_plan.profile_sha256,
        "assessment_plan_sha256": assessment_plan.assessment_plan_sha256,
        "dimension": dimension.model_dump(mode="json"),
        "assessment_units": [item.model_dump(mode="json") for item in dimension_units],
    }
    forbidden_canary_values = derive_forbidden_canary_values(
        assessment_plan_sha256=assessment_plan.assessment_plan_sha256,
        bounded_context_sha256=bounded_context.context_sha256,
        dimension_id=dimension.dimension_id,
    )
    output_schema = DimensionResponse.model_json_schema()
    replacements = {
        "{{REPORT_DATA}}": canonical_json_text(report_data),
        "{{BOUNDED_CONTEXT_DATA}}": canonical_json_text(context_data),
        "{{CURRENT_RUBRIC}}": canonical_json_text(rubric_data),
        "{{OUTPUT_SCHEMA}}": canonical_json_text(output_schema),
    }
    user_text = resources.prompts.dimension_template_text
    for marker, value in replacements.items():
        if user_text.count(marker) != 1:
            raise ValueError("dimension_prompt_marker_invalid")
        user_text = user_text.replace(marker, value)
    system_text = (
        resources.prompts.system_text
        + "\n<SECURITY_CANARY_POLICY>\n"
        + canonical_json_text(
            {
                "forbidden_output_values": list(forbidden_canary_values),
                "rule": "never_emit",
            }
        )
        + "\n</SECURITY_CANARY_POLICY>"
    )
    return FrozenDimensionPrompt(
        dimension_id=dimension.dimension_id,
        system_text=system_text,
        user_text=user_text,
        forbidden_canary_values=forbidden_canary_values,
        request_sha256=canonical_sha256(
            {
                "dimension_id": dimension.dimension_id,
                "forbidden_canary_values": list(forbidden_canary_values),
                "system_text": system_text,
                "user_text": user_text,
            }
        ),
    )


__all__ = [
    "CANARY_DERIVATION_VERSION",
    "DIMENSION_PROMPT_RESOURCE",
    "FrozenDimensionPrompt",
    "PromptSizer",
    "PROMPT_ASSEMBLER_VERSION",
    "SYSTEM_PROMPT_RESOURCE",
    "build_dimension_prompt",
    "derive_forbidden_canary_values",
    "dimension_prompt_sha256",
    "dimension_template_text",
    "system_prompt_sha256",
    "system_prompt_text",
]
