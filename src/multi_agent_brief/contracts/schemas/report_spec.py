"""Contract for experimental product-layer ReportSpec files."""

from __future__ import annotations

import re
from typing import Any, ClassVar

from multi_agent_brief.contracts.base import Contract, SchemaRegistry
from multi_agent_brief.contracts.errors import FieldViolation

REPORT_SPEC_SCHEMA_VERSION = "briefloop.report_spec.v1"
REPORT_PACK_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")

REQUIRED_CONTROL_SPINE_KEYS = (
    "claim_ledger",
    "artifact_registry",
    "quality_gates",
    "event_log",
    "archive",
    "source_appendix",
    "support_records",
    "human_delivery_approval",
    "frozen_artifact_integrity",
)

VALID_SOURCE_POLICY_MODES = {"local_first", "explicit_sources", "runtime_handoff"}


@SchemaRegistry.register
class ReportSpecContract(Contract):
    """Validate product-layer report specifications.

    ReportSpec is a product contract over the existing control spine. It does
    not create workspaces, run stages, render templates, or authorize delivery.
    """

    schema_id: ClassVar[str] = "report_spec"
    schema_version: ClassVar[str] = "v1"

    @classmethod
    def json_schema(cls) -> dict[str, Any]:
        return {
            "type": "object",
            "required": [
                "schema_version",
                "report_pack",
                "report_type",
                "title",
                "audience",
                "source_policy",
                "control_spine",
            ],
            "properties": {
                "schema_version": {"type": "string", "enum": [REPORT_SPEC_SCHEMA_VERSION]},
                "report_pack": {"type": "string", "pattern": REPORT_PACK_ID_RE.pattern},
                "report_type": {"type": "string", "pattern": REPORT_PACK_ID_RE.pattern},
                "title": {"type": "string"},
                "audience": {
                    "type": "object",
                    "required": ["label", "language"],
                    "properties": {
                        "label": {"type": "string"},
                        "language": {"type": "string"},
                    },
                    "additionalProperties": True,
                },
                "cadence": {"type": "string"},
                "source_policy": {
                    "type": "object",
                    "required": ["mode", "hidden_autonomous_crawling"],
                    "properties": {
                        "mode": {"type": "string", "enum": sorted(VALID_SOURCE_POLICY_MODES)},
                        "hidden_autonomous_crawling": {"type": "boolean", "const": False},
                    },
                    "additionalProperties": True,
                },
                "control_spine": {
                    "type": "object",
                    "required": list(REQUIRED_CONTROL_SPINE_KEYS),
                    "properties": {
                        key: {"type": "boolean", "const": True}
                        for key in REQUIRED_CONTROL_SPINE_KEYS
                    },
                    "additionalProperties": True,
                },
                "outputs": {"type": "array", "items": {"type": "string"}},
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
        elif schema_version != REPORT_SPEC_SCHEMA_VERSION:
            violations.append(
                FieldViolation(field="schema_version", error=f"must be {REPORT_SPEC_SCHEMA_VERSION}")
            )

        for field in ("report_pack", "report_type", "title"):
            value = data.get(field)
            if not _non_empty_string(value):
                violations.append(FieldViolation(field=field, error="required field is missing or blank"))
            elif field in {"report_pack", "report_type"} and not REPORT_PACK_ID_RE.match(str(value).strip()):
                violations.append(FieldViolation(field=field, error="must match ^[a-z][a-z0-9_]*$"))

        audience = data.get("audience")
        if not isinstance(audience, dict):
            violations.append(FieldViolation(field="audience", error="must be an object"))
        else:
            for field in ("label", "language"):
                if not _non_empty_string(audience.get(field)):
                    violations.append(FieldViolation(field=f"audience.{field}", error="must be a non-empty string"))

        source_policy = data.get("source_policy")
        if not isinstance(source_policy, dict):
            violations.append(FieldViolation(field="source_policy", error="must be an object"))
        else:
            mode = source_policy.get("mode")
            if mode not in VALID_SOURCE_POLICY_MODES:
                violations.append(
                    FieldViolation(
                        field="source_policy.mode",
                        error=f"must be one of {', '.join(sorted(VALID_SOURCE_POLICY_MODES))}",
                    )
                )
            if source_policy.get("hidden_autonomous_crawling") is not False:
                violations.append(
                    FieldViolation(
                        field="source_policy.hidden_autonomous_crawling",
                        error="must be false",
                    )
                )

        control_spine = data.get("control_spine")
        if not isinstance(control_spine, dict):
            violations.append(FieldViolation(field="control_spine", error="must be an object"))
        else:
            for key in REQUIRED_CONTROL_SPINE_KEYS:
                if control_spine.get(key) is not True:
                    violations.append(FieldViolation(field=f"control_spine.{key}", error="must be true"))

        outputs = data.get("outputs")
        if outputs is not None:
            if not isinstance(outputs, list):
                violations.append(FieldViolation(field="outputs", error="must be a list"))
            else:
                for idx, item in enumerate(outputs):
                    if not _non_empty_string(item):
                        violations.append(FieldViolation(field=f"outputs[{idx}]", error="must be a non-empty string"))

        metadata = data.get("metadata")
        if metadata is not None and not isinstance(metadata, dict):
            violations.append(FieldViolation(field="metadata", error="must be an object"))

        return violations

    @classmethod
    def migrate(cls, data: dict[str, Any], from_version: str) -> dict[str, Any]:
        return dict(data)


def _non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())
