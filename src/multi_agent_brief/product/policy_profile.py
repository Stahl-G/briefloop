"""Experimental product-layer PolicyProfile config contract."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from multi_agent_brief.contracts.errors import FieldViolation
from multi_agent_brief.contracts.schemas.policy_profile import PolicyProfileContract


@dataclass(frozen=True)
class PolicyProfile:
    profile_id: str
    industry: str
    source_path: str
    payload: Mapping[str, Any]

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any], *, source_path: str | Path) -> "PolicyProfile":
        return cls(
            profile_id=str(payload.get("policy_profile_id", "")),
            industry=str(payload.get("industry", "")),
            source_path=str(source_path),
            payload=payload,
        )

    def to_summary(self) -> dict[str, str]:
        return {
            "policy_profile_id": self.profile_id,
            "industry": self.industry,
        }


def validate_policy_profile_payload(payload: Mapping[str, Any]) -> list[FieldViolation]:
    if not isinstance(payload, dict):
        return [FieldViolation(field="<root>", error="must be an object")]
    return PolicyProfileContract.validate(dict(payload))
