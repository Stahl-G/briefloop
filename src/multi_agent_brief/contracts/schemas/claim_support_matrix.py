"""Contract for experimental Claim-Support Matrix artifacts."""

from __future__ import annotations

import re
from typing import Any, ClassVar

from multi_agent_brief.contracts.base import Contract, SchemaRegistry
from multi_agent_brief.contracts.errors import FieldViolation

CLAIM_SUPPORT_MATRIX_SCHEMA_VERSION = "mabw.claim_support_matrix.v1"
SUPPORT_ROW_ID_RE = re.compile(r"^CSM-\d{4}$")
ATOM_ID_RE = re.compile(r"^AC-(\d{4})-\d{2}$")
CLAIM_ID_RE = re.compile(r"^CL-(\d{4})$")
EVIDENCE_SPAN_ID_RE = re.compile(r"^ESP-\d{3,4}-\d{2}$")

VALID_SUPPORT_LABELS = {
    "direct_support",
    "partial_support",
    "inferential_support",
    "weak_support",
    "unsupported",
    "contradicted",
    "insufficient_evidence",
    "not_applicable",
}
VALID_SUPPORT_STRENGTHS = {"high", "medium", "low", "none"}
VALID_REQUIRED_ACTIONS = {
    "none",
    "block_release",
    "downgrade_wording",
    "remove_claim",
    "add_evidence_span",
    "human_adjudication",
    "clarify_inference",
    "mark_as_inference",
    "repair_source_pack",
}
VALID_REPAIR_OWNERS = {"none", "analyst", "editor", "auditor", "claim-ledger", "human_review"}
VALID_DECISION_SOURCES = {
    "human",
    "llm_assisted_human",
    "llm_only",
    "deterministic_policy",
    "imported",
    "unknown",
}


@SchemaRegistry.register
class ClaimSupportMatrixContract(Contract):
    """Validate experimental atom-to-evidence-span support rows.

    This contract validates shape, IDs, and vocabulary only. It does not judge
    semantic support, enforce blocking policy, route repairs, or verify that
    referenced atoms/spans exist in sibling artifacts.
    """

    schema_id: ClassVar[str] = "claim_support_matrix"
    schema_version: ClassVar[str] = "v1"

    @classmethod
    def json_schema(cls) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["schema_version", "rows"],
            "properties": {
                "schema_version": {
                    "type": "string",
                    "enum": [CLAIM_SUPPORT_MATRIX_SCHEMA_VERSION],
                },
                "rows": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "required": [
                            "row_id",
                            "atom_id",
                            "claim_id",
                            "evidence_span_id",
                            "support_label",
                            "support_strength",
                            "support_reason",
                            "required_action",
                            "repair_owner",
                            "decision_source",
                        ],
                        "properties": {
                            "row_id": {"type": "string", "pattern": SUPPORT_ROW_ID_RE.pattern},
                            "atom_id": {"type": "string", "pattern": ATOM_ID_RE.pattern},
                            "claim_id": {"type": "string", "pattern": CLAIM_ID_RE.pattern},
                            "evidence_span_id": {
                                "type": ["string", "null"],
                                "pattern": EVIDENCE_SPAN_ID_RE.pattern,
                            },
                            "support_label": {
                                "type": "string",
                                "enum": sorted(VALID_SUPPORT_LABELS),
                            },
                            "support_strength": {
                                "type": "string",
                                "enum": sorted(VALID_SUPPORT_STRENGTHS),
                            },
                            "support_reason": {"type": "string"},
                            "required_action": {
                                "type": "string",
                                "enum": sorted(VALID_REQUIRED_ACTIONS),
                            },
                            "repair_owner": {
                                "type": "string",
                                "enum": sorted(VALID_REPAIR_OWNERS),
                            },
                            "decision_source": {
                                "type": "string",
                                "enum": sorted(VALID_DECISION_SOURCES),
                            },
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
        elif schema_version != CLAIM_SUPPORT_MATRIX_SCHEMA_VERSION:
            violations.append(
                FieldViolation(
                    field="schema_version",
                    error=f"must be {CLAIM_SUPPORT_MATRIX_SCHEMA_VERSION}",
                )
            )

        metadata = data.get("metadata")
        if metadata is not None and not isinstance(metadata, dict):
            violations.append(FieldViolation(field="metadata", error="must be an object"))

        rows = data.get("rows")
        if not isinstance(rows, list):
            violations.append(FieldViolation(field="rows", error="must be a non-empty list"))
            return violations
        if not rows:
            violations.append(FieldViolation(field="rows", error="must be a non-empty list"))

        seen_row_ids: set[str] = set()
        for row_idx, row in enumerate(rows):
            violations.extend(_validate_row_entry(row, idx=row_idx, seen_row_ids=seen_row_ids))

        return violations

    @classmethod
    def migrate(cls, data: dict[str, Any], from_version: str) -> dict[str, Any]:
        return dict(data)


def _validate_row_entry(
    row: Any,
    *,
    idx: int,
    seen_row_ids: set[str],
) -> list[FieldViolation]:
    prefix = f"rows[{idx}]"
    violations: list[FieldViolation] = []
    if not isinstance(row, dict):
        return [FieldViolation(field=prefix, error="must be an object")]

    row_id = row.get("row_id")
    if not _non_empty_string(row_id) or not SUPPORT_ROW_ID_RE.match(str(row_id).strip()):
        violations.append(FieldViolation(field=f"{prefix}.row_id", error="must match CSM-####"))
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

    support_label = row.get("support_label")
    if not _non_empty_string(support_label):
        violations.append(FieldViolation(field=f"{prefix}.support_label", error="must be a non-empty string"))
    elif support_label not in VALID_SUPPORT_LABELS:
        violations.append(
            FieldViolation(
                field=f"{prefix}.support_label",
                error=f"invalid support_label '{support_label}', must be one of {sorted(VALID_SUPPORT_LABELS)}",
            )
        )

    if "evidence_span_id" not in row:
        violations.append(FieldViolation(field=f"{prefix}.evidence_span_id", error="required field is missing"))
    else:
        violations.extend(_validate_evidence_span_id(row.get("evidence_span_id"), prefix=prefix))

    _validate_enum_field(
        row,
        field="support_strength",
        prefix=prefix,
        allowed=VALID_SUPPORT_STRENGTHS,
        violations=violations,
    )
    _validate_enum_field(
        row,
        field="required_action",
        prefix=prefix,
        allowed=VALID_REQUIRED_ACTIONS,
        violations=violations,
    )
    _validate_enum_field(
        row,
        field="repair_owner",
        prefix=prefix,
        allowed=VALID_REPAIR_OWNERS,
        violations=violations,
    )
    _validate_enum_field(
        row,
        field="decision_source",
        prefix=prefix,
        allowed=VALID_DECISION_SOURCES,
        violations=violations,
    )

    if not _non_empty_string(row.get("support_reason")):
        violations.append(FieldViolation(field=f"{prefix}.support_reason", error="must be a non-empty string"))

    metadata = row.get("metadata")
    if metadata is not None and not isinstance(metadata, dict):
        violations.append(FieldViolation(field=f"{prefix}.metadata", error="must be an object"))

    return violations


def _validate_evidence_span_id(
    value: Any,
    *,
    prefix: str,
) -> list[FieldViolation]:
    if value is None:
        return []
    if not _non_empty_string(value):
        return [FieldViolation(field=f"{prefix}.evidence_span_id", error="must be a string or null")]
    if not EVIDENCE_SPAN_ID_RE.match(str(value).strip()):
        return [FieldViolation(field=f"{prefix}.evidence_span_id", error="must match ESP-###-## or be null")]
    return []


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
