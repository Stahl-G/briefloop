"""Claim v1 → v2 migration: adds epistemic fields from claim_type."""

from __future__ import annotations

from typing import Any


# Mapping from old claim_type to new epistemic_type
_CLAIM_TYPE_TO_EPISTEMIC: dict[str, str] = {
    "interpretation": "interpreted",
    "forecast": "hypothesis",
    "risk": "hypothesis",
}

# Types that are direct observations
_OBSERVED_TYPES = {"fact", "number", "date"}


def migrate_claim_v1_to_v2(data: dict[str, Any]) -> dict[str, Any]:
    """Migrate a v1 claim dict to v2 by adding epistemic fields.

    Does not mutate the input dict.
    """
    result = dict(data)

    claim_type = result.get("claim_type", "fact")
    result["epistemic_type"] = _CLAIM_TYPE_TO_EPISTEMIC.get(claim_type, "observed")
    result["evidence_relation"] = result.get("evidence_relation", "direct")
    result["applicability_reason"] = result.get("applicability_reason", "")
    result["limitations"] = result.get("limitations", [])
    result["schema_version"] = "v2"

    return result
