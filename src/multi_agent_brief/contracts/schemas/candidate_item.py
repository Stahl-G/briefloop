"""Contract for CandidateItem."""

from __future__ import annotations

from typing import Any, ClassVar

from multi_agent_brief.contracts.base import Contract, SchemaRegistry
from multi_agent_brief.contracts.errors import FieldViolation

REQUIRED_FIELDS = {"item_id", "title", "summary", "source_id"}
OPTIONAL_FIELDS = {"topic", "importance", "reason_for_inclusion"}
ALL_FIELDS = REQUIRED_FIELDS | OPTIONAL_FIELDS


@SchemaRegistry.register
class CandidateItemContract(Contract):
    schema_id: ClassVar[str] = "candidate_item"
    schema_version: ClassVar[str] = "v1"

    @classmethod
    def json_schema(cls) -> dict[str, Any]:
        return {
            "type": "object",
            "required": sorted(REQUIRED_FIELDS),
            "properties": {
                "item_id": {"type": "string"},
                "title": {"type": "string"},
                "summary": {"type": "string"},
                "source_id": {"type": "string"},
                "topic": {"type": "string"},
                "importance": {"type": "string", "enum": ["low", "medium", "high"]},
                "reason_for_inclusion": {"type": "string"},
            },
            "additionalProperties": True,
        }

    @classmethod
    def validate(cls, data: dict[str, Any]) -> list[FieldViolation]:
        violations: list[FieldViolation] = []
        for fld in REQUIRED_FIELDS:
            val = data.get(fld)
            if val is None or (isinstance(val, str) and not val.strip()):
                violations.append(FieldViolation(field=fld, error="required field is missing or blank"))
        unknown = set(data.keys()) - ALL_FIELDS
        for field in sorted(unknown):
            violations.append(FieldViolation(field=field, error="unknown field", severity="warning"))
        return violations

    @classmethod
    def migrate(cls, data: dict[str, Any], from_version: str) -> dict[str, Any]:
        return dict(data)
