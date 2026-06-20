"""Contract for experimental Evidence Span Registry artifacts."""

from __future__ import annotations

import hashlib
import re
from typing import Any, ClassVar

from multi_agent_brief.contracts.base import Contract, SchemaRegistry
from multi_agent_brief.contracts.errors import FieldViolation

EVIDENCE_SPAN_REGISTRY_SCHEMA_VERSION = "mabw.evidence_span_registry.v1"
SOURCE_ID_RE = re.compile(r"^SRC-(\d{3,4})$")
EVIDENCE_SPAN_ID_RE = re.compile(r"^ESP-(\d{3,4})-\d{2}$")
SHA256_RE = re.compile(r"^sha256:[0-9a-fA-F]{64}$")

VALID_EVIDENCE_SPAN_ROLES = {
    "direct_statement",
    "numeric_observation",
    "date_or_timing",
    "context",
    "limitation",
    "contradiction",
    "methodology",
    "background",
}


@SchemaRegistry.register
class EvidenceSpanRegistryContract(Contract):
    """Validate deterministic Evidence Span Registry structure.

    This contract validates source/span identity, machine-checkable metadata,
    span roles, and raw excerpt hashes only. It does not judge whether a span
    semantically supports a claim or atom.
    """

    schema_id: ClassVar[str] = "evidence_span_registry"
    schema_version: ClassVar[str] = "v1"

    @classmethod
    def json_schema(cls) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["schema_version", "sources"],
            "properties": {
                "schema_version": {
                    "type": "string",
                    "enum": [EVIDENCE_SPAN_REGISTRY_SCHEMA_VERSION],
                },
                "sources": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "required": ["source_id", "source_type", "source_tier", "spans"],
                        "properties": {
                            "source_id": {"type": "string"},
                            "source_type": {"type": "string"},
                            "source_tier": {"type": "string"},
                            "url": {"type": "string"},
                            "source_path": {"type": "string"},
                            "published_at": {"type": "string"},
                            "retrieved_at": {"type": "string"},
                            "spans": {
                                "type": "array",
                                "minItems": 1,
                                "items": {
                                    "type": "object",
                                    "required": ["span_id", "raw_excerpt", "hash", "span_role"],
                                    "properties": {
                                        "span_id": {"type": "string", "pattern": EVIDENCE_SPAN_ID_RE.pattern},
                                        "raw_excerpt": {"type": "string"},
                                        "hash": {"type": "string", "pattern": SHA256_RE.pattern},
                                        "span_role": {
                                            "type": "string",
                                            "enum": sorted(VALID_EVIDENCE_SPAN_ROLES),
                                        },
                                        "char_start": {"type": "integer", "minimum": 0},
                                        "char_end": {"type": "integer", "minimum": 0},
                                    },
                                    "additionalProperties": True,
                                },
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
        elif schema_version != EVIDENCE_SPAN_REGISTRY_SCHEMA_VERSION:
            violations.append(
                FieldViolation(
                    field="schema_version",
                    error=f"must be {EVIDENCE_SPAN_REGISTRY_SCHEMA_VERSION}",
                )
            )

        metadata = data.get("metadata")
        if metadata is not None and not isinstance(metadata, dict):
            violations.append(FieldViolation(field="metadata", error="must be an object"))

        sources = data.get("sources")
        if not isinstance(sources, list):
            violations.append(FieldViolation(field="sources", error="must be a non-empty list"))
            return violations
        if not sources:
            violations.append(FieldViolation(field="sources", error="must be a non-empty list"))

        seen_source_ids: set[str] = set()
        seen_span_ids: set[str] = set()
        for source_idx, source in enumerate(sources):
            violations.extend(
                _validate_source_entry(
                    source,
                    idx=source_idx,
                    seen_source_ids=seen_source_ids,
                    seen_span_ids=seen_span_ids,
                )
            )

        return violations

    @classmethod
    def migrate(cls, data: dict[str, Any], from_version: str) -> dict[str, Any]:
        return dict(data)


def _validate_source_entry(
    source: Any,
    *,
    idx: int,
    seen_source_ids: set[str],
    seen_span_ids: set[str],
) -> list[FieldViolation]:
    prefix = f"sources[{idx}]"
    violations: list[FieldViolation] = []
    if not isinstance(source, dict):
        return [FieldViolation(field=prefix, error="must be an object")]

    source_id = source.get("source_id")
    canonical_source_match: re.Match[str] | None = None
    if not _non_empty_string(source_id):
        violations.append(FieldViolation(field=f"{prefix}.source_id", error="must be a non-empty string"))
    else:
        normalized_source_id = str(source_id).strip()
        canonical_source_match = SOURCE_ID_RE.match(normalized_source_id)
        if normalized_source_id in seen_source_ids:
            violations.append(
                FieldViolation(field=f"{prefix}.source_id", error=f"duplicate source_id:{normalized_source_id}")
            )
        seen_source_ids.add(normalized_source_id)

    for field in ("source_type", "source_tier"):
        if not _non_empty_string(source.get(field)):
            violations.append(FieldViolation(field=f"{prefix}.{field}", error="must be a non-empty string"))

    if not (_non_empty_string(source.get("url")) or _non_empty_string(source.get("source_path"))):
        violations.append(FieldViolation(field=f"{prefix}.source_identity", error="requires url or source_path"))
    if not (_non_empty_string(source.get("published_at")) or _non_empty_string(source.get("retrieved_at"))):
        violations.append(FieldViolation(field=f"{prefix}.source_date", error="requires published_at or retrieved_at"))

    metadata = source.get("metadata")
    if metadata is not None and not isinstance(metadata, dict):
        violations.append(FieldViolation(field=f"{prefix}.metadata", error="must be an object"))

    spans = source.get("spans")
    if not isinstance(spans, list):
        violations.append(FieldViolation(field=f"{prefix}.spans", error="must be a non-empty list"))
        return violations
    if not spans:
        violations.append(FieldViolation(field=f"{prefix}.spans", error="must be a non-empty list"))

    expected_span_prefix = canonical_source_match.group(1) if canonical_source_match else None
    for span_idx, span in enumerate(spans):
        violations.extend(
            _validate_span_entry(
                span,
                source_prefix=prefix,
                span_idx=span_idx,
                expected_span_prefix=expected_span_prefix,
                seen_span_ids=seen_span_ids,
            )
        )

    return violations


def _validate_span_entry(
    span: Any,
    *,
    source_prefix: str,
    span_idx: int,
    expected_span_prefix: str | None,
    seen_span_ids: set[str],
) -> list[FieldViolation]:
    prefix = f"{source_prefix}.spans[{span_idx}]"
    violations: list[FieldViolation] = []
    if not isinstance(span, dict):
        return [FieldViolation(field=prefix, error="must be an object")]

    span_id = span.get("span_id")
    span_match = EVIDENCE_SPAN_ID_RE.match(span_id.strip()) if _non_empty_string(span_id) else None
    if not span_match:
        violations.append(FieldViolation(field=f"{prefix}.span_id", error="must match ESP-###-##"))
    else:
        normalized_span_id = str(span_id).strip()
        if normalized_span_id in seen_span_ids:
            violations.append(FieldViolation(field=f"{prefix}.span_id", error=f"duplicate span_id:{normalized_span_id}"))
        seen_span_ids.add(normalized_span_id)
        if expected_span_prefix is not None and span_match.group(1) != expected_span_prefix:
            violations.append(
                FieldViolation(
                    field=f"{prefix}.span_id",
                    error=f"must use ESP-{expected_span_prefix}-## for matching source_id",
                )
            )

    raw_excerpt = span.get("raw_excerpt")
    if not _non_empty_string(raw_excerpt):
        violations.append(FieldViolation(field=f"{prefix}.raw_excerpt", error="must be a non-empty string"))

    declared_hash = span.get("hash")
    if not _non_empty_string(declared_hash):
        violations.append(FieldViolation(field=f"{prefix}.hash", error="must be a non-empty string"))
    elif not SHA256_RE.match(str(declared_hash).strip()):
        violations.append(FieldViolation(field=f"{prefix}.hash", error="must match sha256:<64 hex chars>"))
    elif _non_empty_string(raw_excerpt):
        expected_hash = "sha256:" + hashlib.sha256(str(raw_excerpt).encode("utf-8")).hexdigest()
        if str(declared_hash).strip().lower() != expected_hash:
            violations.append(FieldViolation(field=f"{prefix}.hash", error="must match sha256(raw_excerpt)"))

    span_role = span.get("span_role")
    if not _non_empty_string(span_role):
        violations.append(FieldViolation(field=f"{prefix}.span_role", error="must be a non-empty string"))
    elif span_role not in VALID_EVIDENCE_SPAN_ROLES:
        violations.append(
            FieldViolation(
                field=f"{prefix}.span_role",
                error=f"invalid span_role '{span_role}', must be one of {sorted(VALID_EVIDENCE_SPAN_ROLES)}",
            )
        )

    char_start = span.get("char_start")
    char_end = span.get("char_end")
    if char_start is not None and (not isinstance(char_start, int) or char_start < 0):
        violations.append(FieldViolation(field=f"{prefix}.char_start", error="must be a non-negative integer"))
    if char_end is not None and (not isinstance(char_end, int) or char_end < 0):
        violations.append(FieldViolation(field=f"{prefix}.char_end", error="must be a non-negative integer"))
    if isinstance(char_start, int) and isinstance(char_end, int) and char_end < char_start:
        violations.append(FieldViolation(field=f"{prefix}.char_end", error="must be greater than or equal to char_start"))

    return violations


def _non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())
