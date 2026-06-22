"""Contract for experimental product-layer PolicyProfile files."""

from __future__ import annotations

import re
from typing import Any, ClassVar

from multi_agent_brief.contracts.base import Contract, SchemaRegistry
from multi_agent_brief.contracts.errors import FieldViolation

POLICY_PROFILE_SCHEMA_VERSION = "briefloop.policy_profile.v1"
POLICY_PROFILE_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")

VALID_GATE_POLICY_LEVELS = {"standard", "strict"}
REQUIRED_SOURCE_POLICY_KEYS = (
    "freshness_days_by_tier",
    "preferred_source_tiers",
    "discouraged_source_tiers",
)
REQUIRED_GATE_POLICY_KEYS = ("freshness", "material_fact", "target_relevance")
POLICY_PROFILE_BOUNDARY = "experimental_policy_profile_only"


@SchemaRegistry.register
class PolicyProfileContract(Contract):
    """Validate product-layer policy profiles.

    PolicyProfile records deterministic product defaults only. It does not
    change runtime stage behavior, adapt gates, judge compliance, or authorize
    release.
    """

    schema_id: ClassVar[str] = "policy_profile"
    schema_version: ClassVar[str] = "v1"

    @classmethod
    def json_schema(cls) -> dict[str, Any]:
        return {
            "type": "object",
            "required": [
                "schema_version",
                "policy_profile_id",
                "industry",
                "source_policy",
                "claim_policy",
                "wording_policy",
                "gate_policy",
                "metadata",
            ],
            "properties": {
                "schema_version": {"type": "string", "enum": [POLICY_PROFILE_SCHEMA_VERSION]},
                "policy_profile_id": {"type": "string", "pattern": POLICY_PROFILE_ID_RE.pattern},
                "industry": {"type": "string"},
                "source_policy": {
                    "type": "object",
                    "required": list(REQUIRED_SOURCE_POLICY_KEYS),
                    "properties": {
                        "freshness_days_by_tier": {
                            "type": "object",
                            "additionalProperties": {"type": "integer", "minimum": 1},
                        },
                        "preferred_source_tiers": {"type": "array", "items": {"type": "string"}},
                        "discouraged_source_tiers": {"type": "array", "items": {"type": "string"}},
                    },
                    "additionalProperties": True,
                },
                "claim_policy": {
                    "type": "object",
                    "required": ["materiality_terms"],
                    "properties": {
                        "materiality_terms": {"type": "array", "items": {"type": "string"}},
                    },
                    "additionalProperties": True,
                },
                "wording_policy": {
                    "type": "object",
                    "required": ["forbidden_phrases"],
                    "properties": {
                        "forbidden_phrases": {"type": "array", "items": {"type": "string"}},
                    },
                    "additionalProperties": True,
                },
                "gate_policy": {
                    "type": "object",
                    "required": list(REQUIRED_GATE_POLICY_KEYS),
                    "properties": {
                        key: {"type": "string", "enum": sorted(VALID_GATE_POLICY_LEVELS)}
                        for key in REQUIRED_GATE_POLICY_KEYS
                    },
                    "additionalProperties": True,
                },
                "metadata": {
                    "type": "object",
                    "required": ["boundary"],
                    "properties": {"boundary": {"type": "string", "enum": [POLICY_PROFILE_BOUNDARY]}},
                    "additionalProperties": True,
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
        if schema_version != POLICY_PROFILE_SCHEMA_VERSION:
            violations.append(
                FieldViolation(field="schema_version", error=f"must be {POLICY_PROFILE_SCHEMA_VERSION}")
            )

        profile_id = data.get("policy_profile_id")
        if not _non_empty_string(profile_id):
            violations.append(FieldViolation(field="policy_profile_id", error="required field is missing or blank"))
        elif not POLICY_PROFILE_ID_RE.match(str(profile_id).strip()):
            violations.append(FieldViolation(field="policy_profile_id", error="must match ^[a-z][a-z0-9_]*$"))

        if not _non_empty_string(data.get("industry")):
            violations.append(FieldViolation(field="industry", error="required field is missing or blank"))

        source_policy = data.get("source_policy")
        if not isinstance(source_policy, dict):
            violations.append(FieldViolation(field="source_policy", error="must be an object"))
        else:
            violations.extend(_validate_source_policy(source_policy))

        claim_policy = data.get("claim_policy")
        if not isinstance(claim_policy, dict):
            violations.append(FieldViolation(field="claim_policy", error="must be an object"))
        else:
            violations.extend(
                _validate_string_list(
                    claim_policy.get("materiality_terms"),
                    field="claim_policy.materiality_terms",
                    allow_empty=False,
                )
            )

        wording_policy = data.get("wording_policy")
        if not isinstance(wording_policy, dict):
            violations.append(FieldViolation(field="wording_policy", error="must be an object"))
        else:
            violations.extend(
                _validate_string_list(
                    wording_policy.get("forbidden_phrases"),
                    field="wording_policy.forbidden_phrases",
                    allow_empty=False,
                )
            )

        gate_policy = data.get("gate_policy")
        if not isinstance(gate_policy, dict):
            violations.append(FieldViolation(field="gate_policy", error="must be an object"))
        else:
            for key in REQUIRED_GATE_POLICY_KEYS:
                value = gate_policy.get(key)
                if value not in VALID_GATE_POLICY_LEVELS:
                    violations.append(
                        FieldViolation(
                            field=f"gate_policy.{key}",
                            error=f"must be one of {', '.join(sorted(VALID_GATE_POLICY_LEVELS))}",
                        )
                    )

        metadata = data.get("metadata")
        if not isinstance(metadata, dict):
            violations.append(FieldViolation(field="metadata", error="must be an object"))
        elif metadata.get("boundary") != POLICY_PROFILE_BOUNDARY:
            violations.append(
                FieldViolation(field="metadata.boundary", error=f"must be {POLICY_PROFILE_BOUNDARY}")
            )

        return violations

    @classmethod
    def migrate(cls, data: dict[str, Any], from_version: str) -> dict[str, Any]:
        return dict(data)


def _validate_source_policy(source_policy: dict[str, Any]) -> list[FieldViolation]:
    violations: list[FieldViolation] = []
    if "tier_weights" in source_policy:
        violations.append(FieldViolation(field="source_policy.tier_weights", error="not supported"))

    freshness = source_policy.get("freshness_days_by_tier")
    if not isinstance(freshness, dict) or not freshness:
        violations.append(FieldViolation(field="source_policy.freshness_days_by_tier", error="must be a non-empty object"))
    else:
        for tier, days in freshness.items():
            if not _non_empty_string(tier) or not POLICY_PROFILE_ID_RE.match(str(tier).strip()):
                violations.append(
                    FieldViolation(field=f"source_policy.freshness_days_by_tier.{tier}", error="tier must be stable id")
                )
            if type(days) is not int or days <= 0:
                violations.append(
                    FieldViolation(field=f"source_policy.freshness_days_by_tier.{tier}", error="days must be positive integer")
                )

    preferred_errors = _validate_string_list(
        source_policy.get("preferred_source_tiers"),
        field="source_policy.preferred_source_tiers",
        allow_empty=False,
    )
    discouraged_errors = _validate_string_list(
        source_policy.get("discouraged_source_tiers"),
        field="source_policy.discouraged_source_tiers",
        allow_empty=True,
    )
    violations.extend(preferred_errors)
    violations.extend(discouraged_errors)

    if not preferred_errors and not discouraged_errors:
        preferred = {item.strip() for item in source_policy.get("preferred_source_tiers", []) if isinstance(item, str)}
        discouraged = {item.strip() for item in source_policy.get("discouraged_source_tiers", []) if isinstance(item, str)}
        overlap = sorted(preferred & discouraged)
        if overlap:
            violations.append(
                FieldViolation(
                    field="source_policy.discouraged_source_tiers",
                    error=f"must not overlap preferred_source_tiers:{','.join(overlap)}",
                )
            )
    return violations


def _validate_string_list(value: Any, *, field: str, allow_empty: bool) -> list[FieldViolation]:
    violations: list[FieldViolation] = []
    if not isinstance(value, list):
        return [FieldViolation(field=field, error="must be a list")]
    if not allow_empty and not value:
        violations.append(FieldViolation(field=field, error="must be non-empty"))
    seen: set[str] = set()
    for idx, item in enumerate(value):
        if not _non_empty_string(item):
            violations.append(FieldViolation(field=f"{field}[{idx}]", error="must be a non-empty string"))
            continue
        normalized = item.strip()
        if normalized in seen:
            violations.append(FieldViolation(field=f"{field}[{idx}]", error=f"duplicate value:{normalized}"))
        seen.add(normalized)
    return violations


def _non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())
