"""Frozen prompt resources and deterministic per-dimension assembly."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Protocol

from pydantic import TypeAdapter

from multi_agent_brief.contracts.v2 import ContractId
from multi_agent_brief.semantic_evaluator.contracts import (
    AssessmentPlan,
    BoundedContext,
    DimensionId,
    DimensionProfile,
    DimensionResponse,
    ReaderArtifact,
)
from multi_agent_brief.semantic_evaluator.normalization import (
    replay_reader_artifact,
    verify_bounded_context,
)
from multi_agent_brief.semantic_evaluator.errors import SemanticEvaluatorError
from multi_agent_brief.semantic_evaluator.resources import (
    EvaluatorResourceError,
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
_CONTRACT_ID_ADAPTER = TypeAdapter(ContractId)
_DIMENSION_ID_ADAPTER = TypeAdapter(DimensionId)


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


def _prompt_request_sha256(prompt: FrozenDimensionPrompt) -> str:
    return canonical_sha256(
        {
            "dimension_id": prompt.dimension_id,
            "forbidden_canary_values": list(prompt.forbidden_canary_values),
            "system_text": prompt.system_text,
            "user_text": prompt.user_text,
        }
    )


def _strict_frozen_dimension_prompt(
    prompt: FrozenDimensionPrompt,
) -> FrozenDimensionPrompt:
    strict: FrozenDimensionPrompt | None = None
    try:
        if not isinstance(prompt, FrozenDimensionPrompt):
            raise TypeError("prompt_invalid")
        if any(
            type(value) is not str
            for value in (
                prompt.dimension_id,
                prompt.system_text,
                prompt.user_text,
                prompt.request_sha256,
            )
        ):
            raise TypeError("prompt_invalid")
        dimension_id = _DIMENSION_ID_ADAPTER.validate_python(prompt.dimension_id)
        if type(prompt.forbidden_canary_values) is not tuple:
            raise TypeError("prompt_invalid")
        canaries = tuple(prompt.forbidden_canary_values)
        if (
            not canaries
            or any(type(item) is not str or not item for item in canaries)
            or canaries != tuple(sorted(set(canaries)))
            or _SHA256_RE.fullmatch(prompt.request_sha256) is None
        ):
            raise ValueError("prompt_invalid")
        _CONTRACT_ID_ADAPTER.validate_python(prompt.request_sha256)
        strict = FrozenDimensionPrompt(
            dimension_id=dimension_id,
            system_text=prompt.system_text,
            user_text=prompt.user_text,
            forbidden_canary_values=canaries,
            request_sha256=prompt.request_sha256,
        )
        if strict.request_sha256 != _prompt_request_sha256(strict):
            raise ValueError("prompt_invalid")
    except (AttributeError, TypeError, ValueError):
        strict = None
    if strict is None:
        raise SemanticEvaluatorError("assessment_evidence_mismatch") from None
    return strict


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
    resource_failed = False
    if _resource_snapshot is None:
        try:
            resources = acquire_resource_snapshot()
        except EvaluatorResourceError:
            resource_failed = True
    else:
        resources = _resource_snapshot
    if resource_failed:
        raise SemanticEvaluatorError("instrument_manifest_mismatch") from None
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
        "artifact": reader_artifact.model_dump(mode="json", warnings="error"),
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
                item.model_dump(mode="json", warnings="error")
                for item in bounded_context.requirements
                if item.type in allowed
            ],
        }
    rubric_data = {
        "trial_id": assessment_plan.trial_id,
        "profile_sha256": assessment_plan.profile_sha256,
        "assessment_plan_sha256": assessment_plan.assessment_plan_sha256,
        "dimension": dimension.model_dump(mode="json", warnings="error"),
        "assessment_units": [
            item.model_dump(mode="json", warnings="error") for item in dimension_units
        ],
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
    prompt = FrozenDimensionPrompt(
        dimension_id=dimension.dimension_id,
        system_text=system_text,
        user_text=user_text,
        forbidden_canary_values=forbidden_canary_values,
        request_sha256="0" * 64,
    )
    return FrozenDimensionPrompt(
        dimension_id=prompt.dimension_id,
        system_text=prompt.system_text,
        user_text=prompt.user_text,
        forbidden_canary_values=prompt.forbidden_canary_values,
        request_sha256=_prompt_request_sha256(prompt),
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
