"""Detached, package-owned resource snapshots for one evaluator operation."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import Field, ValidationError
import yaml

from multi_agent_brief.contracts.v2 import CleanText, StrictModel
from multi_agent_brief.semantic_evaluator.contracts import DimensionId
from multi_agent_brief.semantic_evaluator.errors import SemanticEvaluatorError
from multi_agent_brief.semantic_evaluator.profile import (
    LoadedProfile,
    load_profile,
    strict_loaded_profile_copy,
)
from multi_agent_brief.semantic_evaluator.resources import (
    EvaluatorResourceError,
    resource_text,
)
from multi_agent_brief.semantic_evaluator.serialization import sha256_text


SYSTEM_PROMPT_RESOURCE = "system_v1.txt"
DIMENSION_PROMPT_RESOURCE = "dimension_v1.txt"
CHECKLIST_RESOURCE = "structured_checklist_zh_v1.yaml"


class _ChecklistTemplateItem(StrictModel):
    dimension_id: DimensionId
    text: CleanText


class _ChecklistTemplate(StrictModel):
    checklist_id: str
    language: str
    items: list[_ChecklistTemplateItem] = Field(min_length=1)


@dataclass(frozen=True)
class PromptResourceSnapshot:
    system_text: str
    dimension_template_text: str
    system_sha256: str
    dimension_sha256: str


@dataclass(frozen=True)
class ChecklistResourceItem:
    dimension_id: str
    text: str


@dataclass(frozen=True)
class ChecklistResourceSnapshot:
    checklist_id: str
    language: str
    items: tuple[ChecklistResourceItem, ...]
    sha256: str


@dataclass(frozen=True)
class EvaluatorResourceSnapshot:
    loaded_profile: LoadedProfile
    prompts: PromptResourceSnapshot
    checklist: ChecklistResourceSnapshot | None


def _current_profile() -> LoadedProfile:
    try:
        return strict_loaded_profile_copy(load_profile())
    except EvaluatorResourceError:
        raise
    except SemanticEvaluatorError:
        raise EvaluatorResourceError("evaluator_resource_unavailable") from None


def _checklist_snapshot(
    *,
    loaded_profile: LoadedProfile,
) -> ChecklistResourceSnapshot:
    try:
        text = resource_text("baselines", CHECKLIST_RESOURCE)
        payload = yaml.safe_load(text)
        template = _ChecklistTemplate.model_validate(payload)
    except EvaluatorResourceError:
        raise
    except (TypeError, ValueError, yaml.YAMLError, ValidationError):
        raise EvaluatorResourceError("evaluator_resource_unavailable") from None
    expected = [item.dimension_id for item in loaded_profile.profile.dimensions]
    if (
        template.checklist_id != "structured_checklist_zh_v1"
        or template.language != "zh-CN"
        or [item.dimension_id for item in template.items] != expected
    ):
        raise EvaluatorResourceError("evaluator_resource_unavailable") from None
    return ChecklistResourceSnapshot(
        checklist_id=template.checklist_id,
        language=template.language,
        items=tuple(
            ChecklistResourceItem(
                dimension_id=item.dimension_id,
                text=item.text,
            )
            for item in template.items
        ),
        sha256=sha256_text(text),
    )


def acquire_resource_snapshot(
    *,
    loaded_profile: LoadedProfile | None = None,
    include_baseline: bool = False,
) -> EvaluatorResourceSnapshot:
    """Acquire each required package resource exactly once for one operation."""

    profile = (
        _current_profile()
        if loaded_profile is None
        else strict_loaded_profile_copy(loaded_profile)
    )
    try:
        system_text = resource_text("prompts", SYSTEM_PROMPT_RESOURCE)
        dimension_text = resource_text("prompts", DIMENSION_PROMPT_RESOURCE)
    except EvaluatorResourceError:
        raise
    prompt_snapshot = PromptResourceSnapshot(
        system_text=system_text,
        dimension_template_text=dimension_text,
        system_sha256=sha256_text(system_text),
        dimension_sha256=sha256_text(dimension_text),
    )
    checklist = (
        _checklist_snapshot(loaded_profile=profile) if include_baseline else None
    )
    return EvaluatorResourceSnapshot(
        loaded_profile=profile,
        prompts=prompt_snapshot,
        checklist=checklist,
    )


__all__ = [
    "CHECKLIST_RESOURCE",
    "ChecklistResourceItem",
    "ChecklistResourceSnapshot",
    "DIMENSION_PROMPT_RESOURCE",
    "EvaluatorResourceSnapshot",
    "PromptResourceSnapshot",
    "SYSTEM_PROMPT_RESOURCE",
    "acquire_resource_snapshot",
]
