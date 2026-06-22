"""Experimental ReportPack config contract."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from multi_agent_brief.contracts.errors import FieldViolation
from multi_agent_brief.contracts.schemas.report_spec import REPORT_PACK_ID_RE
from multi_agent_brief.product.report_spec import validate_report_spec_payload

REPORT_PACK_SCHEMA_VERSION = "briefloop.report_pack.v1"


@dataclass(frozen=True)
class ReportPack:
    pack_id: str
    report_type: str
    display_name: str
    status: str
    description: str
    source_path: str
    payload: Mapping[str, Any]
    default_report_spec: Mapping[str, Any]

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any], *, source_path: str | Path) -> "ReportPack":
        spec = payload.get("default_report_spec") if isinstance(payload.get("default_report_spec"), dict) else {}
        return cls(
            pack_id=str(payload.get("pack_id", "")),
            report_type=str(payload.get("report_type", "")),
            display_name=str(payload.get("display_name", "")),
            status=str(payload.get("status", "")),
            description=str(payload.get("description", "")),
            source_path=str(source_path),
            payload=payload,
            default_report_spec=spec,
        )

    def to_summary(self) -> dict[str, str]:
        return {
            "pack_id": self.pack_id,
            "report_type": self.report_type,
            "display_name": self.display_name,
            "status": self.status,
            "description": self.description,
        }


def validate_report_pack_payload(payload: Mapping[str, Any]) -> list[FieldViolation]:
    if not isinstance(payload, dict):
        return [FieldViolation(field="<root>", error="must be an object")]

    violations: list[FieldViolation] = []
    schema_version = payload.get("schema_version")
    if schema_version != REPORT_PACK_SCHEMA_VERSION:
        violations.append(FieldViolation(field="schema_version", error=f"must be {REPORT_PACK_SCHEMA_VERSION}"))

    for field in ("pack_id", "report_type", "display_name", "status", "description"):
        value = payload.get(field)
        if not isinstance(value, str) or not value.strip():
            violations.append(FieldViolation(field=field, error="required field is missing or blank"))
        elif field in {"pack_id", "report_type"} and not REPORT_PACK_ID_RE.match(value.strip()):
            violations.append(FieldViolation(field=field, error="must match ^[a-z][a-z0-9_]*$"))

    if payload.get("status") != "experimental":
        violations.append(FieldViolation(field="status", error="must be experimental"))

    default_report_spec = payload.get("default_report_spec")
    if not isinstance(default_report_spec, dict):
        violations.append(FieldViolation(field="default_report_spec", error="must be an object"))
        return violations

    pack_id = str(payload.get("pack_id", "")).strip()
    report_type = str(payload.get("report_type", "")).strip()
    result = validate_report_spec_payload(
        default_report_spec,
        known_report_packs={pack_id} if pack_id else set(),
        report_type_by_pack={pack_id: report_type} if pack_id and report_type else {},
    )
    for violation in result.errors:
        violations.append(
            FieldViolation(
                field=f"default_report_spec.{violation.field}",
                error=violation.error,
                severity=violation.severity,
            )
        )
    return violations
