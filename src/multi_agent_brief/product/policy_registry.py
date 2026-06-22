"""Read-only registry for experimental product PolicyProfiles."""

from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any

import yaml

from multi_agent_brief.contracts.errors import FieldViolation
from multi_agent_brief.product.policy_profile import PolicyProfile, validate_policy_profile_payload


@dataclass(frozen=True)
class PolicyProfileRegistry:
    config_dir: Path
    profiles: tuple[PolicyProfile, ...]
    validation_errors: tuple[FieldViolation, ...]

    @classmethod
    def from_config_dir(cls, config_dir: str | Path) -> "PolicyProfileRegistry":
        base = Path(config_dir)
        profiles: list[PolicyProfile] = []
        errors: list[FieldViolation] = []
        seen: set[str] = set()
        for path in sorted(base.glob("*.yaml")):
            payload = _load_yaml(path)
            for violation in validate_policy_profile_payload(payload):
                errors.append(
                    FieldViolation(
                        field=f"{path.name}.{violation.field}",
                        error=violation.error,
                        severity=violation.severity,
                    )
                )
            profile = PolicyProfile.from_payload(payload, source_path=path)
            if profile.profile_id:
                if profile.profile_id in seen:
                    errors.append(
                        FieldViolation(
                            field=f"{path.name}.policy_profile_id",
                            error=f"duplicate policy_profile_id:{profile.profile_id}",
                        )
                    )
                seen.add(profile.profile_id)
            profiles.append(profile)
        return cls(config_dir=base, profiles=tuple(profiles), validation_errors=tuple(errors))

    @classmethod
    def from_package(cls) -> "PolicyProfileRegistry":
        config_dir = files("multi_agent_brief").joinpath("configs", "policy_profiles")
        return cls.from_config_dir(Path(str(config_dir)))

    def profile_ids(self) -> set[str]:
        return {profile.profile_id for profile in self.profiles if profile.profile_id}

    def get(self, profile_id: str) -> PolicyProfile | None:
        return next((profile for profile in self.profiles if profile.profile_id == profile_id), None)

    def to_list_payload(self) -> dict[str, Any]:
        return {
            "ok": not any(item.severity == "error" for item in self.validation_errors),
            "policy_profiles": [profile.to_summary() for profile in self.profiles],
            "errors": [_violation_to_dict(item) for item in self.validation_errors if item.severity == "error"],
        }


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError:
        return {}
    return data if isinstance(data, dict) else {}


def _violation_to_dict(violation: FieldViolation) -> dict[str, str]:
    return {
        "field": violation.field,
        "error": violation.error,
        "severity": violation.severity,
    }
