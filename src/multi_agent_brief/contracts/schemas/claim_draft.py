"""Contract for Claim Draft collections."""

from __future__ import annotations

from typing import Any, ClassVar

from multi_agent_brief.contracts.base import Contract, SchemaRegistry
from multi_agent_brief.contracts.errors import FieldViolation
from multi_agent_brief.contracts.schemas.claim import (
    VALID_CLAIM_TYPES,
    VALID_CONFIDENCE,
    VALID_EPISTEMIC,
    VALID_EVIDENCE_RELATION,
)

TOP_LEVEL_REQUIRED_FIELDS = {"drafts"}
TOP_LEVEL_KNOWN_FIELDS = TOP_LEVEL_REQUIRED_FIELDS | {"schema_version", "metadata"}

DRAFT_REQUIRED_FIELD_ORDER = ("statement", "source_id", "evidence_text")
DRAFT_REQUIRED_FIELDS = set(DRAFT_REQUIRED_FIELD_ORDER)
DRAFT_KNOWN_FIELDS = DRAFT_REQUIRED_FIELDS | {
    "draft_id",
    "candidate_id",
    "source_url",
    "source_type",
    "claim_type",
    "confidence",
    "requires_audit",
    "created_by",
    "used_in_sections",
    "metadata",
    "epistemic_type",
    "evidence_relation",
    "applicability_reason",
    "limitations",
}
DRAFT_OPTIONAL_STRING_FIELDS = {
    "draft_id",
    "candidate_id",
    "source_url",
    "source_type",
    "claim_type",
    "confidence",
    "created_by",
    "epistemic_type",
    "evidence_relation",
    "applicability_reason",
}
DRAFT_OPTIONAL_STRING_LIST_FIELDS = {"used_in_sections", "limitations"}
RESERVED_ID_KEYS = {"claim_id"}


@SchemaRegistry.register
class ClaimDraftContract(Contract):
    """Validate claim drafts before any future ID-allocation freeze step.

    Claim drafts are pre-freeze inputs. They may carry source-grounded claim text,
    but they must not carry stable Claim Ledger IDs.
    """

    schema_id: ClassVar[str] = "claim_drafts"
    schema_version: ClassVar[str] = "v1"

    @classmethod
    def json_schema(cls) -> dict[str, Any]:
        return {
            "type": "object",
            "required": sorted(TOP_LEVEL_REQUIRED_FIELDS),
            "properties": {
                "schema_version": {"type": "string", "enum": ["mabw.claim_drafts.v1"]},
                "drafts": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": sorted(DRAFT_REQUIRED_FIELDS),
                        "properties": {
                            "draft_id": {"type": "string"},
                            "candidate_id": {"type": "string"},
                            "statement": {"type": "string"},
                            "source_id": {"type": "string"},
                            "evidence_text": {"type": "string"},
                            "source_url": {"type": "string"},
                            "source_type": {"type": "string"},
                            "claim_type": {"type": "string", "enum": sorted(VALID_CLAIM_TYPES)},
                            "confidence": {"type": "string", "enum": sorted(VALID_CONFIDENCE)},
                            "requires_audit": {"type": "boolean"},
                            "created_by": {"type": "string"},
                            "used_in_sections": {"type": "array", "items": {"type": "string"}},
                            "metadata": {"type": "object"},
                            "epistemic_type": {"type": "string", "enum": sorted(VALID_EPISTEMIC)},
                            "evidence_relation": {
                                "type": "string",
                                "enum": sorted(VALID_EVIDENCE_RELATION),
                            },
                            "applicability_reason": {"type": "string"},
                            "limitations": {"type": "array", "items": {"type": "string"}},
                        },
                        "not": {"required": ["claim_id"]},
                        "additionalProperties": True,
                    },
                },
                "metadata": {"type": "object"},
            },
            "additionalProperties": True,
        }

    @classmethod
    def validate(cls, data: dict[str, Any]) -> list[FieldViolation]:
        violations: list[FieldViolation] = []
        if not isinstance(data, dict):
            return [FieldViolation(field="<root>", error="must be an object")]

        for field in TOP_LEVEL_REQUIRED_FIELDS:
            if field not in data:
                violations.append(FieldViolation(field=field, error="required field is missing"))

        schema_version = data.get("schema_version")
        if schema_version is not None and not isinstance(schema_version, str):
            violations.append(FieldViolation(field="schema_version", error="must be a string"))
        elif schema_version is not None and schema_version != "mabw.claim_drafts.v1":
            violations.append(
                FieldViolation(
                    field="schema_version",
                    error="must be mabw.claim_drafts.v1",
                )
            )
        metadata = data.get("metadata")
        if metadata is not None and not isinstance(metadata, dict):
            violations.append(FieldViolation(field="metadata", error="must be an object"))

        drafts = data.get("drafts")
        if drafts is None:
            drafts = []
        elif not isinstance(drafts, list):
            violations.append(FieldViolation(field="drafts", error="must be a list"))
            drafts = []

        for idx, draft in enumerate(drafts):
            violations.extend(_validate_draft_entry(draft, idx))

        unknown = set(data.keys()) - TOP_LEVEL_KNOWN_FIELDS
        for field in sorted(unknown):
            violations.append(FieldViolation(field=field, error="unknown field", severity="warning"))

        return violations

    @classmethod
    def migrate(cls, data: dict[str, Any], from_version: str) -> dict[str, Any]:
        return dict(data)


def _validate_draft_entry(data: Any, idx: int) -> list[FieldViolation]:
    prefix = f"drafts[{idx}]"
    violations: list[FieldViolation] = []
    if not isinstance(data, dict):
        return [FieldViolation(field=prefix, error="must be an object")]

    if "claim_id" in data:
        violations.append(
            FieldViolation(
                field=f"{prefix}.claim_id",
                error="claim drafts must not contain claim_id; Python assigns IDs during freeze",
            )
        )
    for path in _reserved_id_paths(data, prefix):
        if path == f"{prefix}.claim_id":
            continue
        violations.append(
            FieldViolation(
                field=path,
                error="claim drafts must not contain claim_id; Python assigns IDs during freeze",
            )
        )

    for field in DRAFT_REQUIRED_FIELD_ORDER:
        value = data.get(field)
        if not isinstance(value, str) or not value.strip():
            violations.append(
                FieldViolation(field=f"{prefix}.{field}", error="must be a non-empty string")
            )

    for field in sorted(DRAFT_OPTIONAL_STRING_FIELDS):
        value = data.get(field)
        if value is not None and not isinstance(value, str):
            violations.append(FieldViolation(field=f"{prefix}.{field}", error="must be a string"))

    for field in sorted(DRAFT_OPTIONAL_STRING_LIST_FIELDS):
        value = data.get(field)
        if value is None:
            continue
        if not isinstance(value, list):
            violations.append(FieldViolation(field=f"{prefix}.{field}", error="must be a list"))
            continue
        for item_idx, item in enumerate(value):
            if not isinstance(item, str):
                violations.append(
                    FieldViolation(field=f"{prefix}.{field}[{item_idx}]", error="must be a string")
                )

    metadata = data.get("metadata")
    if metadata is not None and not isinstance(metadata, dict):
        violations.append(FieldViolation(field=f"{prefix}.metadata", error="must be an object"))

    requires_audit = data.get("requires_audit")
    if requires_audit is not None and not isinstance(requires_audit, bool):
        violations.append(FieldViolation(field=f"{prefix}.requires_audit", error="must be a boolean"))

    claim_type = data.get("claim_type")
    if isinstance(claim_type, str) and claim_type not in VALID_CLAIM_TYPES:
        violations.append(
            FieldViolation(
                field=f"{prefix}.claim_type",
                error=f"invalid claim_type '{claim_type}', must be one of {sorted(VALID_CLAIM_TYPES)}",
            )
        )

    confidence = data.get("confidence")
    if isinstance(confidence, str) and confidence not in VALID_CONFIDENCE:
        violations.append(
            FieldViolation(
                field=f"{prefix}.confidence",
                error=f"invalid confidence '{confidence}', must be one of {sorted(VALID_CONFIDENCE)}",
            )
        )

    epistemic = data.get("epistemic_type")
    if isinstance(epistemic, str) and epistemic not in VALID_EPISTEMIC:
        violations.append(
            FieldViolation(
                field=f"{prefix}.epistemic_type",
                error=f"invalid epistemic_type '{epistemic}', must be one of {sorted(VALID_EPISTEMIC)}",
            )
        )

    evidence_rel = data.get("evidence_relation")
    if isinstance(evidence_rel, str) and evidence_rel not in VALID_EVIDENCE_RELATION:
        violations.append(
            FieldViolation(
                field=f"{prefix}.evidence_relation",
                error=(
                    f"invalid evidence_relation '{evidence_rel}', "
                    f"must be one of {sorted(VALID_EVIDENCE_RELATION)}"
                ),
            )
        )

    unknown = set(data.keys()) - DRAFT_KNOWN_FIELDS - {"claim_id"}
    for field in sorted(unknown):
        violations.append(
            FieldViolation(field=f"{prefix}.{field}", error="unknown field", severity="warning")
        )
    return violations


def _reserved_id_paths(value: Any, prefix: str) -> list[str]:
    paths: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            path = f"{prefix}.{key}"
            if key in RESERVED_ID_KEYS:
                paths.append(path)
            paths.extend(_reserved_id_paths(item, path))
    elif isinstance(value, list):
        for idx, item in enumerate(value):
            paths.extend(_reserved_id_paths(item, f"{prefix}[{idx}]"))
    return paths
