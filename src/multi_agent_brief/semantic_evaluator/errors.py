"""Stable, value-free error vocabulary for the Semantic Evaluator."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from pydantic import ValidationError

from multi_agent_brief.contracts.errors import FieldViolation, pydantic_error_violations
from multi_agent_brief.semantic_evaluator.serialization import SourceResolutionError


ADMISSION_REASON_CODES = (
    "admission_contract_invalid",
    "input_missing",
    "input_unreadable",
    "input_sha_mismatch",
    "input_not_utf8",
    "unsupported_language",
    "unsupported_data_class",
    "public_data_attestation_required",
    "private_material_forbidden",
    "profile_invalid",
    "instrument_config_invalid",
    "instrument_manifest_mismatch",
    "prompt_sizer_unavailable",
    "input_too_long_for_full_context_instrument",
    "archive_root_unsafe",
    "trial_identity_conflict",
)

PARSER_REASON_CODES = (
    "parser_invalid_utf8",
    "parser_invalid_json",
    "parser_top_level_not_object",
    "parser_schema_invalid",
    "parser_duplicate_member",
    "authority_output_forbidden",
    "tool_or_canary_output_forbidden",
)

VALIDATION_REASON_CODES = (
    "trial_identity_mismatch",
    "dimension_identity_mismatch",
    "raw_response_binding_mismatch",
    "run_binding_mismatch",
    "assessment_unit_set_mismatch",
    "assessment_unit_failure_link_missing",
    "finding_owner_mismatch",
    "finding_id_duplicate",
    "handoff_id_duplicate",
    "span_report_mismatch",
    "span_block_unknown",
    "span_offset_invalid",
    "span_excerpt_hash_mismatch",
    "o1_requirement_binding_forbidden",
    "o2_requirement_binding_required",
    "requirement_reference_unknown",
    "requirement_type_not_eligible",
    "evidence_dependent_finding_forbidden",
    "evidence_dependent_handoff_required",
    "authority_output_forbidden",
    "tool_or_canary_output_forbidden",
    "attempt_reference_incomplete",
    "assessment_evidence_mismatch",
    "baseline_input_binding_mismatch",
    "event_sequence_invalid",
    "run_count_mismatch",
    "composition_record_mismatch",
    "composition_witness_mismatch",
    "instrument_manifest_mismatch",
)


@dataclass(frozen=True)
class EvaluatorFailure:
    reason_code: str
    violations: tuple[FieldViolation, ...] = ()


class SemanticEvaluatorError(Exception):
    """A stable evaluator failure that never renders untrusted input values."""

    def __init__(
        self,
        reason_code: str,
        *,
        violations: Iterable[FieldViolation] = (),
    ) -> None:
        self.reason_code = reason_code
        self.violations = tuple(violations)
        super().__init__(reason_code)


def _is_current_instrument_source_failure(error: BaseException) -> bool:
    """Recognize only explicit current-source failures without exposing values."""

    current: BaseException | None = error
    seen: set[int] = set()
    for _ in range(16):
        if current is None or id(current) in seen:
            return False
        seen.add(id(current))
        if isinstance(current, (SourceResolutionError, OSError)):
            return True
        current = current.__cause__
    return False


def value_free_violations(error: ValidationError) -> tuple[FieldViolation, ...]:
    return tuple(pydantic_error_violations(error))


__all__ = [
    "ADMISSION_REASON_CODES",
    "EvaluatorFailure",
    "PARSER_REASON_CODES",
    "SemanticEvaluatorError",
    "VALIDATION_REASON_CODES",
    "value_free_violations",
]
