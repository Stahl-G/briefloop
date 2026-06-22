"""Read-only registry for experimental product ReportPacks."""

from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any

import yaml

from multi_agent_brief.contracts.errors import FieldViolation
from multi_agent_brief.product.policy_registry import PolicyProfileRegistry
from multi_agent_brief.product.report_pack import ReportPack, validate_report_pack_payload


@dataclass(frozen=True)
class ReportPackRegistry:
    config_dir: Path
    packs: tuple[ReportPack, ...]
    validation_errors: tuple[FieldViolation, ...]

    @classmethod
    def from_config_dir(
        cls,
        config_dir: str | Path,
        *,
        known_policy_profiles: set[str] | None = None,
    ) -> "ReportPackRegistry":
        base = Path(config_dir)
        packs: list[ReportPack] = []
        errors: list[FieldViolation] = []
        seen: set[str] = set()
        for path in sorted(base.glob("*.yaml")):
            payload = _load_yaml(path)
            for violation in validate_report_pack_payload(
                payload,
                known_policy_profiles=known_policy_profiles,
            ):
                errors.append(
                    FieldViolation(
                        field=f"{path.name}.{violation.field}",
                        error=violation.error,
                        severity=violation.severity,
                    )
                )
            pack = ReportPack.from_payload(payload, source_path=path)
            if pack.pack_id:
                if pack.pack_id in seen:
                    errors.append(FieldViolation(field=f"{path.name}.pack_id", error=f"duplicate pack_id:{pack.pack_id}"))
                seen.add(pack.pack_id)
            packs.append(pack)
        return cls(config_dir=base, packs=tuple(packs), validation_errors=tuple(errors))

    @classmethod
    def from_package(cls) -> "ReportPackRegistry":
        policy_registry = PolicyProfileRegistry.from_package()
        config_dir = files("multi_agent_brief").joinpath("configs", "report_packs")
        return cls.from_config_dir(
            Path(str(config_dir)),
            known_policy_profiles=policy_registry.profile_ids(),
        )

    def pack_ids(self) -> set[str]:
        return {pack.pack_id for pack in self.packs if pack.pack_id}

    def report_type_by_pack(self) -> dict[str, str]:
        return {pack.pack_id: pack.report_type for pack in self.packs if pack.pack_id}

    def default_policy_profile_by_pack(self) -> dict[str, str]:
        return {
            pack.pack_id: pack.default_policy_profile
            for pack in self.packs
            if pack.pack_id and pack.default_policy_profile
        }

    def get(self, pack_id: str) -> ReportPack | None:
        return next((pack for pack in self.packs if pack.pack_id == pack_id), None)

    def to_list_payload(self) -> dict[str, Any]:
        return {
            "ok": not any(item.severity == "error" for item in self.validation_errors),
            "packs": [pack.to_summary() for pack in self.packs],
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
