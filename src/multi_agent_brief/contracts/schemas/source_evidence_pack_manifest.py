"""Contract for durable source evidence pack manifests."""

from __future__ import annotations

import re
from typing import Any, ClassVar

from multi_agent_brief.contracts.base import Contract, SchemaRegistry
from multi_agent_brief.contracts.errors import FieldViolation
from multi_agent_brief.contracts.source_metadata import source_category_error

SOURCE_EVIDENCE_PACK_MANIFEST_SCHEMA_VERSION = "mabw.source_evidence_pack_manifest.v1"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@SchemaRegistry.register
class SourceEvidencePackManifestContract(Contract):
    """Validate source evidence pack manifest shape.

    This contract validates deterministic source-pack bookkeeping only. It does
    not judge whether a source supports a claim.
    """

    schema_id: ClassVar[str] = "source_evidence_pack_manifest"
    schema_version: ClassVar[str] = "v1"

    @classmethod
    def json_schema(cls) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["schema_version", "records", "pack_sha256"],
            "properties": {
                "schema_version": {
                    "type": "string",
                    "enum": [SOURCE_EVIDENCE_PACK_MANIFEST_SCHEMA_VERSION],
                },
                "source": {"type": "string"},
                "source_config_path": {"type": "string"},
                "record_count": {"type": "integer", "minimum": 1},
                "error_count": {"type": "integer", "minimum": 0},
                "pack_sha256": {"type": "string", "pattern": SHA256_RE.pattern},
                "durable_provider_names": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "records": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "required": ["source_id", "path", "sha256", "size_bytes"],
                        "properties": {
                            "source_id": {"type": "string"},
                            "path": {"type": "string"},
                            "sha256": {"type": "string", "pattern": SHA256_RE.pattern},
                            "size_bytes": {"type": "integer", "minimum": 1},
                            "source_url": {"type": "string"},
                            "source_title": {"type": "string"},
                            "publisher": {"type": "string"},
                            "source_type": {"type": "string"},
                            "source_category": {"type": "string"},
                            "retrieval_source_type": {"type": "string"},
                            "underlying_evidence_type": {"type": "string"},
                        },
                        "additionalProperties": True,
                    },
                },
                "provider_errors": {
                    "type": "array",
                    "items": {"type": "object"},
                },
                "non_goals": {
                    "type": "array",
                    "items": {"type": "string"},
                },
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
        elif schema_version != SOURCE_EVIDENCE_PACK_MANIFEST_SCHEMA_VERSION:
            violations.append(
                FieldViolation(
                    field="schema_version",
                    error=f"must be {SOURCE_EVIDENCE_PACK_MANIFEST_SCHEMA_VERSION}",
                )
            )

        records = data.get("records")
        if not isinstance(records, list) or not records:
            violations.append(FieldViolation(field="records", error="must be a non-empty list"))
        else:
            seen_paths: set[str] = set()
            seen_source_ids: set[str] = set()
            for idx, record in enumerate(records):
                violations.extend(
                    _validate_record(
                        record,
                        idx=idx,
                        seen_paths=seen_paths,
                        seen_source_ids=seen_source_ids,
                    )
                )
            record_count = data.get("record_count")
            if record_count is not None and record_count != len(records):
                violations.append(
                    FieldViolation(
                        field="record_count",
                        error=f"must equal records length:{len(records)}",
                    )
                )

        pack_sha = data.get("pack_sha256")
        if not _valid_sha256(pack_sha):
            violations.append(FieldViolation(field="pack_sha256", error="must be a lowercase sha256 hex digest"))

        for field in ("record_count", "error_count"):
            value = data.get(field)
            if value is not None and (type(value) is not int or value < (1 if field == "record_count" else 0)):
                violations.append(FieldViolation(field=field, error="must be a non-negative integer"))

        provider_errors = data.get("provider_errors")
        if provider_errors is not None and not isinstance(provider_errors, list):
            violations.append(FieldViolation(field="provider_errors", error="must be a list"))
        elif provider_errors is not None:
            error_count = data.get("error_count")
            if error_count is not None and error_count != len(provider_errors):
                violations.append(
                    FieldViolation(
                        field="error_count",
                        error=f"must equal provider_errors length:{len(provider_errors)}",
                    )
                )

        return violations

    @classmethod
    def migrate(cls, data: dict[str, Any], from_version: str) -> dict[str, Any]:
        return dict(data)


def _validate_record(
    record: Any,
    *,
    idx: int,
    seen_paths: set[str],
    seen_source_ids: set[str],
) -> list[FieldViolation]:
    prefix = f"records[{idx}]"
    if not isinstance(record, dict):
        return [FieldViolation(field=prefix, error="must be an object")]

    violations: list[FieldViolation] = []
    source_id = record.get("source_id")
    if not _non_empty_string(source_id):
        violations.append(FieldViolation(field=f"{prefix}.source_id", error="must be a non-empty string"))
    else:
        normalized_source_id = str(source_id).strip()
        if normalized_source_id in seen_source_ids:
            violations.append(
                FieldViolation(field=f"{prefix}.source_id", error=f"duplicate source_id:{normalized_source_id}")
            )
        seen_source_ids.add(normalized_source_id)

    path = record.get("path")
    if not _non_empty_string(path):
        violations.append(FieldViolation(field=f"{prefix}.path", error="must be a non-empty string"))
    else:
        normalized_path = str(path).strip()
        if normalized_path in seen_paths:
            violations.append(FieldViolation(field=f"{prefix}.path", error=f"duplicate path:{normalized_path}"))
        seen_paths.add(normalized_path)

    if not _valid_sha256(record.get("sha256")):
        violations.append(FieldViolation(field=f"{prefix}.sha256", error="must be a lowercase sha256 hex digest"))

    size = record.get("size_bytes")
    if type(size) is not int or size <= 0:
        violations.append(FieldViolation(field=f"{prefix}.size_bytes", error="must be a positive integer"))

    for field in (
        "source_title",
        "publisher",
        "source_type",
        "retrieval_source_type",
        "underlying_evidence_type",
    ):
        value = record.get(field)
        if value is not None and not _non_empty_string(value):
            violations.append(FieldViolation(field=f"{prefix}.{field}", error="must be a non-empty string"))
    category_error = source_category_error(record.get("source_category"))
    if category_error:
        violations.append(FieldViolation(field=f"{prefix}.source_category", error=category_error))

    return violations


def _valid_sha256(value: Any) -> bool:
    return isinstance(value, str) and bool(SHA256_RE.match(value.strip()))


def _non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())
