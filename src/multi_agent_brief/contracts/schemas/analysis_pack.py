"""Contract for AnalysisPack (market_competitor schemas)."""

from __future__ import annotations

from typing import Any, ClassVar

from multi_agent_brief.contracts.base import Contract, SchemaRegistry
from multi_agent_brief.contracts.errors import FieldViolation

VALID_EVENT_TYPES = {
    "product_launch", "partnership", "acquisition", "funding",
    "regulatory", "leadership", "market_shift", "technology", "other",
}
VALID_FINDING_TYPES = {
    "competitive_threat", "opportunity", "market_trend",
    "regulatory_risk", "technology_shift", "other",
}
VALID_CONFIDENCE = {"low", "medium", "high"}
VALID_MATERIALITY = {"low", "medium", "high"}


class MarketEventContract(Contract):
    """Contract for MarketEvent (used by market_competitor module)."""

    schema_id: ClassVar[str] = "market_event"
    schema_version: ClassVar[str] = "v1"

    @classmethod
    def json_schema(cls) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["event_id", "entity_ids", "event_type"],
            "properties": {
                "event_id": {"type": "string"},
                "entity_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                "event_type": {"type": "string", "enum": sorted(VALID_EVENT_TYPES)},
                "dimension": {"type": "string"},
                "status": {"type": "string"},
                "geography": {"type": "string"},
                "event_date": {"type": "string"},
                "summary": {"type": "string"},
                "supporting_claim_ids": {"type": "array", "items": {"type": "string"}},
                "source_count": {"type": "integer"},
                "confidence": {"type": "string", "enum": sorted(VALID_CONFIDENCE)},
                "materiality": {"type": "string", "enum": sorted(VALID_MATERIALITY)},
                "change_status": {"type": "string"},
            },
        }

    @classmethod
    def validate(cls, data: dict[str, Any]) -> list[FieldViolation]:
        violations: list[FieldViolation] = []

        for fld in ("event_id", "entity_ids", "event_type"):
            if fld not in data:
                violations.append(FieldViolation(field=fld, error="required field is missing"))

        entity_ids = data.get("entity_ids", [])
        if isinstance(entity_ids, list) and len(entity_ids) == 0:
            violations.append(FieldViolation(field="entity_ids", error="must have at least one entity"))

        event_type = data.get("event_type", "")
        if event_type and event_type not in VALID_EVENT_TYPES:
            violations.append(FieldViolation(
                field="event_type",
                error=f"invalid event_type '{event_type}'",
            ))

        confidence = data.get("confidence")
        if confidence is not None and confidence not in VALID_CONFIDENCE:
            violations.append(FieldViolation(
                field="confidence",
                error=f"invalid confidence '{confidence}'",
            ))

        return violations

    @classmethod
    def migrate(cls, data: dict[str, Any], from_version: str) -> dict[str, Any]:
        return dict(data)


class AnalysisCardContract(Contract):
    """Contract for AnalysisCard (used by market_competitor module)."""

    schema_id: ClassVar[str] = "analysis_card"
    schema_version: ClassVar[str] = "v1"

    @classmethod
    def json_schema(cls) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["analysis_id", "finding_type", "headline", "observation"],
            "properties": {
                "analysis_id": {"type": "string"},
                "finding_type": {"type": "string", "enum": sorted(VALID_FINDING_TYPES)},
                "headline": {"type": "string"},
                "observation": {"type": "string"},
                "implication_for_target": {"type": "string"},
                "time_horizon": {"type": "string"},
                "confidence": {"type": "string", "enum": sorted(VALID_CONFIDENCE)},
                "supporting_claim_ids": {"type": "array", "items": {"type": "string"}},
                "counterevidence_claim_ids": {"type": "array", "items": {"type": "string"}},
                "evidence_gaps": {"type": "array", "items": {"type": "string"}},
                "follow_up_questions": {"type": "array", "items": {"type": "string"}},
            },
        }

    @classmethod
    def validate(cls, data: dict[str, Any]) -> list[FieldViolation]:
        violations: list[FieldViolation] = []

        for fld in ("analysis_id", "finding_type", "headline", "observation"):
            if fld not in data:
                violations.append(FieldViolation(field=fld, error="required field is missing"))

        finding_type = data.get("finding_type", "")
        if finding_type and finding_type not in VALID_FINDING_TYPES:
            violations.append(FieldViolation(
                field="finding_type",
                error=f"invalid finding_type '{finding_type}'",
            ))

        confidence = data.get("confidence")
        if confidence is not None and confidence not in VALID_CONFIDENCE:
            violations.append(FieldViolation(
                field="confidence",
                error=f"invalid confidence '{confidence}'",
            ))

        return violations

    @classmethod
    def migrate(cls, data: dict[str, Any], from_version: str) -> dict[str, Any]:
        return dict(data)
