"""Isolated deterministic Semantic Evaluator research contracts.

This package is not a normal BriefLoop workflow stage and exposes no provider,
CLI, archive writer, ControlStore, gate, finalize, or delivery integration.
"""

from multi_agent_brief.semantic_evaluator.admission import (
    AdmissionDecision,
    admit_inputs,
)
from multi_agent_brief.semantic_evaluator.baseline import build_baseline
from multi_agent_brief.semantic_evaluator.composition import (
    build_presentation,
    compose_actual_laj,
    compose_matched_non_llm,
    verify_additive_baseline,
)
from multi_agent_brief.semantic_evaluator.contracts import (
    SEMANTIC_EVALUATOR_CONTRACT_IDS,
    SEMANTIC_EVALUATOR_CONTRACT_MODELS,
)
from multi_agent_brief.semantic_evaluator.instrument import build_instrument_manifest
from multi_agent_brief.semantic_evaluator.normalization import (
    freeze_bounded_context,
    normalize_markdown,
)
from multi_agent_brief.semantic_evaluator.parser import parse_dimension_response
from multi_agent_brief.semantic_evaluator.profile import load_profile
from multi_agent_brief.semantic_evaluator.unit_planner import build_assessment_plan
from multi_agent_brief.semantic_evaluator.validator import (
    assemble_semantic_assessment_run,
    make_semantic_evaluator_event,
    validate_dimension_response,
)

__all__ = [
    "AdmissionDecision",
    "SEMANTIC_EVALUATOR_CONTRACT_IDS",
    "SEMANTIC_EVALUATOR_CONTRACT_MODELS",
    "admit_inputs",
    "assemble_semantic_assessment_run",
    "build_assessment_plan",
    "build_baseline",
    "build_instrument_manifest",
    "build_presentation",
    "compose_actual_laj",
    "compose_matched_non_llm",
    "freeze_bounded_context",
    "load_profile",
    "make_semantic_evaluator_event",
    "normalize_markdown",
    "parse_dimension_response",
    "validate_dimension_response",
    "verify_additive_baseline",
]
