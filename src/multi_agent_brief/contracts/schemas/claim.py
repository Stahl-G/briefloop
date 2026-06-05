"""Contract for Claim (v1 + v2)."""

from __future__ import annotations

from typing import Any, ClassVar

from multi_agent_brief.contracts.base import Contract, SchemaRegistry
from multi_agent_brief.contracts.errors import FieldViolation

REQUIRED_FIELDS = {"claim_id", "statement", "source_id", "evidence_text"}
KNOWN_FIELDS = REQUIRED_FIELDS | {
    "source_url", "source_type", "claim_type", "confidence",
    "requires_audit", "created_by", "used_in_sections", "metadata",
    "schema_version", "epistemic_type", "evidence_relation",
    "applicability_reason", "limitations",
}

VALID_CLAIM_TYPES = {"fact", "number", "date", "interpretation", "forecast", "risk"}
VALID_CONFIDENCE = {"low", "medium", "high"}
VALID_EPISTEMIC = {"observed", "interpreted", "hypothesis", "action", "analogy"}
VALID_EVIDENCE_RELATION = {"direct", "indirect", "inferred", "analogous"}


@SchemaRegistry.register
class ClaimContract(Contract):
    schema_id: ClassVar[str] = "claim"
    schema_version: ClassVar[str] = "v2"

    @classmethod
    def json_schema(cls) -> dict[str, Any]:
        return {
            "type": "object",
            "required": sorted(REQUIRED_FIELDS),
            "properties": {
                "claim_id": {"type": "string"},
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
                "schema_version": {"type": "string", "enum": ["v1", "v2"]},
                "epistemic_type": {"type": "string", "enum": sorted(VALID_EPISTEMIC)},
                "evidence_relation": {"type": "string", "enum": sorted(VALID_EVIDENCE_RELATION)},
                "applicability_reason": {"type": "string"},
                "limitations": {"type": "array", "items": {"type": "string"}},
            },
            "additionalProperties": True,
        }

    @classmethod
    def validate(cls, data: dict[str, Any]) -> list[FieldViolation]:
        violations: list[FieldViolation] = []

        # Required fields
        for fld in REQUIRED_FIELDS:
            val = data.get(fld)
            if val is None or (isinstance(val, str) and not val.strip()):
                violations.append(FieldViolation(field=fld, error="required field is missing or blank"))

        # Enum validation
        claim_type = data.get("claim_type", "fact")
        if claim_type not in VALID_CLAIM_TYPES:
            violations.append(FieldViolation(
                field="claim_type",
                error=f"invalid claim_type '{claim_type}', must be one of {sorted(VALID_CLAIM_TYPES)}",
            ))

        confidence = data.get("confidence", "medium")
        if confidence not in VALID_CONFIDENCE:
            violations.append(FieldViolation(
                field="confidence",
                error=f"invalid confidence '{confidence}', must be one of {sorted(VALID_CONFIDENCE)}",
            ))

        epistemic = data.get("epistemic_type")
        if epistemic is not None and epistemic not in VALID_EPISTEMIC:
            violations.append(FieldViolation(
                field="epistemic_type",
                error=f"invalid epistemic_type '{epistemic}', must be one of {sorted(VALID_EPISTEMIC)}",
            ))

        evidence_rel = data.get("evidence_relation")
        if evidence_rel is not None and evidence_rel not in VALID_EVIDENCE_RELATION:
            violations.append(FieldViolation(
                field="evidence_relation",
                error=f"invalid evidence_relation '{evidence_rel}', must be one of {sorted(VALID_EVIDENCE_RELATION)}",
            ))

        # Unknown fields as warnings
        unknown = set(data.keys()) - KNOWN_FIELDS
        for field in sorted(unknown):
            violations.append(FieldViolation(field=field, error="unknown field", severity="warning"))

        return violations

    @classmethod
    def migrate(cls, data: dict[str, Any], from_version: str) -> dict[str, Any]:
        if from_version == "v1":
            return cls._migrate_v1_to_v2(data)
        return dict(data)

    @staticmethod
    def _migrate_v1_to_v2(data: dict[str, Any]) -> dict[str, Any]:
        result = dict(data)
        # Map claim_type → epistemic_type
        claim_type = result.get("claim_type", "fact")
        epistemic_map = {
            "interpretation": "interpreted",
            "forecast": "hypothesis",
            "risk": "hypothesis",
        }
        result["epistemic_type"] = epistemic_map.get(claim_type, "observed")
        result["evidence_relation"] = result.get("evidence_relation", "direct")
        result["applicability_reason"] = result.get("applicability_reason", "")
        result["limitations"] = result.get("limitations", [])
        result["schema_version"] = "v2"
        return result
