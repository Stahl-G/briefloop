"""Contract for AuditReport."""

from __future__ import annotations

from typing import Any, ClassVar

from multi_agent_brief.contracts.base import Contract, SchemaRegistry
from multi_agent_brief.contracts.errors import FieldViolation

REQUIRED_FIELDS = {"audit_status", "audit_score"}
KNOWN_FIELDS = REQUIRED_FIELDS | {"findings", "metadata"}
VALID_STATUSES = {"pass", "warning", "fail"}


@SchemaRegistry.register
class AuditReportContract(Contract):
    schema_id: ClassVar[str] = "audit_report"
    schema_version: ClassVar[str] = "v1"

    @classmethod
    def json_schema(cls) -> dict[str, Any]:
        return {
            "type": "object",
            "required": sorted(REQUIRED_FIELDS),
            "properties": {
                "audit_status": {"type": "string", "enum": sorted(VALID_STATUSES)},
                "audit_score": {"type": "integer", "minimum": 0, "maximum": 100},
                "findings": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["finding_id", "severity", "finding_type", "description"],
                        "properties": {
                            "finding_id": {"type": "string"},
                            "severity": {"type": "string", "enum": ["low", "medium", "high"]},
                            "finding_type": {"type": "string"},
                            "description": {"type": "string"},
                            "recommendation": {"type": "string"},
                            "related_claim_id": {"type": "string"},
                            "line_number": {"type": ["integer", "null"]},
                            "evidence": {"type": "string"},
                        },
                    },
                },
                "metadata": {"type": "object"},
            },
            "additionalProperties": True,
        }

    @classmethod
    def validate(cls, data: dict[str, Any]) -> list[FieldViolation]:
        violations: list[FieldViolation] = []

        for fld in REQUIRED_FIELDS:
            if fld not in data:
                violations.append(FieldViolation(field=fld, error="required field is missing"))

        status = data.get("audit_status", "")
        if status and status not in VALID_STATUSES:
            violations.append(FieldViolation(
                field="audit_status",
                error=f"invalid audit_status '{status}', must be one of {sorted(VALID_STATUSES)}",
            ))

        score = data.get("audit_score")
        if score is not None:
            if not isinstance(score, (int, float)):
                violations.append(FieldViolation(field="audit_score", error="must be a number"))
            elif not (0 <= score <= 100):
                violations.append(FieldViolation(field="audit_score", error=f"score {score} out of range [0, 100]"))

        unknown = set(data.keys()) - KNOWN_FIELDS
        for field in sorted(unknown):
            violations.append(FieldViolation(field=field, error="unknown field", severity="warning"))

        return violations

    @classmethod
    def migrate(cls, data: dict[str, Any], from_version: str) -> dict[str, Any]:
        return dict(data)
