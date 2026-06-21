"""Contract for experimental Semantic Assessment Report artifacts."""

from __future__ import annotations

import re
from typing import Any, ClassVar

from multi_agent_brief.contracts.base import Contract, SchemaRegistry
from multi_agent_brief.contracts.errors import FieldViolation
from multi_agent_brief.contracts.schemas.claim_support_matrix import VALID_SUPPORT_LABELS

SEMANTIC_ASSESSMENT_REPORT_SCHEMA_VERSION = "mabw.semantic_assessment_report.v1"
SEMANTIC_ASSESSMENT_ROW_ID_RE = re.compile(r"^SAR-\d{4}$")
CLAIM_ID_RE = re.compile(r"^CL-(\d{4})$")
ATOM_ID_RE = re.compile(r"^AC-(\d{4})-\d{2}$")
EVIDENCE_SPAN_ID_RE = re.compile(r"^ESP-\d{3,4}-\d{2}$")

VALID_ASSESSMENT_METHODS = {
    "human",
    "llm_assisted_human",
    "llm_only",
    "deterministic_policy",
    "imported",
    "unknown",
}
VALID_UNCERTAINTY_LEVELS = {"low", "medium", "high", "unknown"}
VALID_DISAGREEMENT_LEVELS = {"none", "low", "medium", "high", "unknown"}


@SchemaRegistry.register
class SemanticAssessmentReportContract(Contract):
    """Validate semantic support assessment proposal reports.

    This contract validates report shape, IDs, assessor metadata, and support
    label vocabulary only. It does not judge truth, mutate the Claim-Support
    Matrix, create adjudication queue items, or grant release authority.
    """

    schema_id: ClassVar[str] = "semantic_assessment_report"
    schema_version: ClassVar[str] = "v1"

    @classmethod
    def json_schema(cls) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["schema_version", "assessors", "rows"],
            "properties": {
                "schema_version": {
                    "type": "string",
                    "enum": [SEMANTIC_ASSESSMENT_REPORT_SCHEMA_VERSION],
                },
                "assessors": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "required": ["assessor_id", "assessment_method"],
                        "properties": {
                            "assessor_id": {"type": "string"},
                            "assessment_method": {
                                "type": "string",
                                "enum": sorted(VALID_ASSESSMENT_METHODS),
                            },
                            "label": {"type": "string"},
                            "metadata": {"type": "object"},
                        },
                        "additionalProperties": True,
                    },
                },
                "rows": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "required": [
                            "row_id",
                            "claim_id",
                            "atom_id",
                            "proposed_support_label",
                            "confidence",
                            "uncertainty",
                            "disagreement",
                            "requires_human_adjudication",
                            "assessment_method",
                            "assessor_id",
                            "rationale",
                        ],
                        "properties": {
                            "row_id": {"type": "string", "pattern": SEMANTIC_ASSESSMENT_ROW_ID_RE.pattern},
                            "claim_id": {"type": "string", "pattern": CLAIM_ID_RE.pattern},
                            "atom_id": {"type": "string", "pattern": ATOM_ID_RE.pattern},
                            "evidence_span_id": {"type": "string", "pattern": EVIDENCE_SPAN_ID_RE.pattern},
                            "candidate_evidence_span_ids": {
                                "type": "array",
                                "minItems": 1,
                                "items": {"type": "string", "pattern": EVIDENCE_SPAN_ID_RE.pattern},
                            },
                            "proposed_support_label": {
                                "type": "string",
                                "enum": sorted(VALID_SUPPORT_LABELS),
                            },
                            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                            "uncertainty": {
                                "type": "string",
                                "enum": sorted(VALID_UNCERTAINTY_LEVELS),
                            },
                            "disagreement": {
                                "type": "string",
                                "enum": sorted(VALID_DISAGREEMENT_LEVELS),
                            },
                            "requires_human_adjudication": {"type": "boolean"},
                            "assessment_method": {
                                "type": "string",
                                "enum": sorted(VALID_ASSESSMENT_METHODS),
                            },
                            "assessor_id": {"type": "string"},
                            "rationale": {"type": "string"},
                            "metadata": {"type": "object"},
                        },
                        "additionalProperties": True,
                    },
                },
                "metadata": {"type": "object"},
            },
            "additionalProperties": True,
        }

    @classmethod
    def validate(cls, data: dict[str, Any]) -> list[FieldViolation]:
        if not isinstance(data, dict):
            return [FieldViolation(field="<root>", error="must be an object")]

        violations: list[FieldViolation] = []
        schema_version = data.get("schema_version")
        if not _non_empty_string(schema_version):
            violations.append(FieldViolation(field="schema_version", error="required field is missing"))
        elif schema_version != SEMANTIC_ASSESSMENT_REPORT_SCHEMA_VERSION:
            violations.append(
                FieldViolation(
                    field="schema_version",
                    error=f"must be {SEMANTIC_ASSESSMENT_REPORT_SCHEMA_VERSION}",
                )
            )

        metadata = data.get("metadata")
        if metadata is not None and not isinstance(metadata, dict):
            violations.append(FieldViolation(field="metadata", error="must be an object"))

        declared_assessor_ids: set[str] = set()
        assessors = data.get("assessors")
        if not isinstance(assessors, list):
            violations.append(FieldViolation(field="assessors", error="must be a non-empty list"))
        elif not assessors:
            violations.append(FieldViolation(field="assessors", error="must be a non-empty list"))
        else:
            for idx, assessor in enumerate(assessors):
                violations.extend(
                    _validate_assessor_entry(
                        assessor,
                        idx=idx,
                        seen_assessor_ids=declared_assessor_ids,
                    )
                )

        rows = data.get("rows")
        if not isinstance(rows, list):
            violations.append(FieldViolation(field="rows", error="must be a non-empty list"))
            return violations
        if not rows:
            violations.append(FieldViolation(field="rows", error="must be a non-empty list"))

        seen_row_ids: set[str] = set()
        for idx, row in enumerate(rows):
            violations.extend(
                _validate_row_entry(
                    row,
                    idx=idx,
                    seen_row_ids=seen_row_ids,
                    declared_assessor_ids=declared_assessor_ids,
                )
            )

        return violations

    @classmethod
    def migrate(cls, data: dict[str, Any], from_version: str) -> dict[str, Any]:
        return dict(data)


def _validate_assessor_entry(
    assessor: Any,
    *,
    idx: int,
    seen_assessor_ids: set[str],
) -> list[FieldViolation]:
    prefix = f"assessors[{idx}]"
    violations: list[FieldViolation] = []
    if not isinstance(assessor, dict):
        return [FieldViolation(field=prefix, error="must be an object")]

    assessor_id = assessor.get("assessor_id")
    if not _non_empty_string(assessor_id):
        violations.append(FieldViolation(field=f"{prefix}.assessor_id", error="must be a non-empty string"))
    else:
        normalized = str(assessor_id).strip()
        if normalized in seen_assessor_ids:
            violations.append(FieldViolation(field=f"{prefix}.assessor_id", error=f"duplicate assessor_id:{normalized}"))
        seen_assessor_ids.add(normalized)

    _validate_enum_field(
        assessor,
        field="assessment_method",
        prefix=prefix,
        allowed=VALID_ASSESSMENT_METHODS,
        violations=violations,
    )

    metadata = assessor.get("metadata")
    if metadata is not None and not isinstance(metadata, dict):
        violations.append(FieldViolation(field=f"{prefix}.metadata", error="must be an object"))

    return violations


def _validate_row_entry(
    row: Any,
    *,
    idx: int,
    seen_row_ids: set[str],
    declared_assessor_ids: set[str],
) -> list[FieldViolation]:
    prefix = f"rows[{idx}]"
    violations: list[FieldViolation] = []
    if not isinstance(row, dict):
        return [FieldViolation(field=prefix, error="must be an object")]

    row_id = row.get("row_id")
    if not _non_empty_string(row_id) or not SEMANTIC_ASSESSMENT_ROW_ID_RE.match(str(row_id).strip()):
        violations.append(FieldViolation(field=f"{prefix}.row_id", error="must match SAR-####"))
    else:
        normalized_row_id = str(row_id).strip()
        if normalized_row_id in seen_row_ids:
            violations.append(FieldViolation(field=f"{prefix}.row_id", error=f"duplicate row_id:{normalized_row_id}"))
        seen_row_ids.add(normalized_row_id)

    atom_id = row.get("atom_id")
    atom_match = ATOM_ID_RE.match(atom_id.strip()) if _non_empty_string(atom_id) else None
    if not atom_match:
        violations.append(FieldViolation(field=f"{prefix}.atom_id", error="must match AC-####-##"))

    claim_id = row.get("claim_id")
    claim_match = CLAIM_ID_RE.match(claim_id.strip()) if _non_empty_string(claim_id) else None
    if not claim_match:
        violations.append(FieldViolation(field=f"{prefix}.claim_id", error="must match CL-####"))
    elif atom_match and atom_match.group(1) != claim_match.group(1):
        violations.append(
            FieldViolation(
                field=f"{prefix}.atom_id",
                error=f"must use AC-{claim_match.group(1)}-## for matching claim_id",
            )
        )

    violations.extend(_validate_evidence_span_binding(row, prefix=prefix))

    _validate_enum_field(
        row,
        field="proposed_support_label",
        prefix=prefix,
        allowed=VALID_SUPPORT_LABELS,
        violations=violations,
    )
    _validate_number_field(row, field="confidence", prefix=prefix, violations=violations)
    _validate_enum_field(
        row,
        field="uncertainty",
        prefix=prefix,
        allowed=VALID_UNCERTAINTY_LEVELS,
        violations=violations,
    )
    _validate_enum_field(
        row,
        field="disagreement",
        prefix=prefix,
        allowed=VALID_DISAGREEMENT_LEVELS,
        violations=violations,
    )
    _validate_enum_field(
        row,
        field="assessment_method",
        prefix=prefix,
        allowed=VALID_ASSESSMENT_METHODS,
        violations=violations,
    )

    assessor_id = row.get("assessor_id")
    if not _non_empty_string(assessor_id):
        violations.append(FieldViolation(field=f"{prefix}.assessor_id", error="must be a non-empty string"))
    else:
        normalized_assessor_id = str(assessor_id).strip()
        if normalized_assessor_id not in declared_assessor_ids:
            violations.append(
                FieldViolation(
                    field=f"{prefix}.assessor_id",
                    error=f"unknown assessor_id:{normalized_assessor_id}",
                )
            )

    if not isinstance(row.get("requires_human_adjudication"), bool):
        violations.append(FieldViolation(field=f"{prefix}.requires_human_adjudication", error="must be a boolean flag"))
    if not _non_empty_string(row.get("rationale")):
        violations.append(FieldViolation(field=f"{prefix}.rationale", error="must be a non-empty string"))

    metadata = row.get("metadata")
    if metadata is not None and not isinstance(metadata, dict):
        violations.append(FieldViolation(field=f"{prefix}.metadata", error="must be an object"))

    return violations


def _validate_evidence_span_binding(row: dict[str, Any], *, prefix: str) -> list[FieldViolation]:
    violations: list[FieldViolation] = []
    has_single = "evidence_span_id" in row and row.get("evidence_span_id") is not None
    has_candidates = "candidate_evidence_span_ids" in row

    if not has_single and not has_candidates:
        return [
            FieldViolation(
                field=f"{prefix}.evidence_span_binding",
                error="requires evidence_span_id or candidate_evidence_span_ids",
            )
        ]

    if "evidence_span_id" in row:
        value = row.get("evidence_span_id")
        if value is not None:
            if not _non_empty_string(value):
                violations.append(FieldViolation(field=f"{prefix}.evidence_span_id", error="must be a non-empty string"))
            elif not EVIDENCE_SPAN_ID_RE.match(str(value).strip()):
                violations.append(FieldViolation(field=f"{prefix}.evidence_span_id", error="must match ESP-###-##"))

    if has_candidates:
        candidates = row.get("candidate_evidence_span_ids")
        if not isinstance(candidates, list) or not candidates:
            violations.append(
                FieldViolation(field=f"{prefix}.candidate_evidence_span_ids", error="must be a non-empty list")
            )
        else:
            seen_candidates: set[str] = set()
            for idx, candidate in enumerate(candidates):
                field = f"{prefix}.candidate_evidence_span_ids[{idx}]"
                if not _non_empty_string(candidate):
                    violations.append(FieldViolation(field=field, error="must be a non-empty string"))
                    continue
                normalized = str(candidate).strip()
                if not EVIDENCE_SPAN_ID_RE.match(normalized):
                    violations.append(FieldViolation(field=field, error="must match ESP-###-##"))
                if normalized in seen_candidates:
                    violations.append(FieldViolation(field=field, error=f"duplicate evidence_span_id:{normalized}"))
                seen_candidates.add(normalized)

    return violations


def _validate_number_field(
    row: dict[str, Any],
    *,
    field: str,
    prefix: str,
    violations: list[FieldViolation],
) -> None:
    value = row.get(field)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        violations.append(FieldViolation(field=f"{prefix}.{field}", error="must be a number from 0 to 1"))
    elif value < 0 or value > 1:
        violations.append(FieldViolation(field=f"{prefix}.{field}", error="must be between 0 and 1"))


def _validate_enum_field(
    row: dict[str, Any],
    *,
    field: str,
    prefix: str,
    allowed: set[str],
    violations: list[FieldViolation],
) -> None:
    value = row.get(field)
    if not _non_empty_string(value):
        violations.append(FieldViolation(field=f"{prefix}.{field}", error="must be a non-empty string"))
    elif value not in allowed:
        violations.append(
            FieldViolation(
                field=f"{prefix}.{field}",
                error=f"invalid {field} '{value}', must be one of {sorted(allowed)}",
            )
        )


def _non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())
