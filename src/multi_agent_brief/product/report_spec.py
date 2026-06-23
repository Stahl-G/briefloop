"""Helpers for loading and validating product-layer ReportSpec files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from multi_agent_brief.contracts.errors import FieldViolation
from multi_agent_brief.contracts.schemas.report_spec import ReportSpecContract


@dataclass(frozen=True)
class ReportSpecValidationResult:
    ok: bool
    report_pack: str | None
    policy_profile: str | None
    resolved_policy_profile: str | None
    policy_profile_source: str | None
    policy_profile_resolution: dict[str, Any] | None
    report_type: str | None
    errors: tuple[FieldViolation, ...]
    warnings: tuple[FieldViolation, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "report_pack": self.report_pack,
            "policy_profile": self.policy_profile,
            "resolved_policy_profile": self.resolved_policy_profile,
            "policy_profile_source": self.policy_profile_source,
            "policy_profile_resolution": self.policy_profile_resolution,
            "report_type": self.report_type,
            "errors": [_violation_to_dict(item) for item in self.errors],
            "warnings": [_violation_to_dict(item) for item in self.warnings],
        }


class ReportSpecLoadError(ValueError):
    """Controlled error for unreadable or malformed ReportSpec payloads."""

    def __init__(self, *, path: Path, message: str) -> None:
        super().__init__(message)
        self.path = path
        self.message = message


def load_report_spec(path: str | Path) -> dict[str, Any]:
    spec_path = Path(path)
    text = spec_path.read_text(encoding="utf-8")
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ReportSpecLoadError(path=spec_path, message=f"invalid YAML: {exc}") from exc
    if not isinstance(data, dict):
        return {}
    return data


def validate_report_spec_payload(
    payload: dict[str, Any],
    *,
    known_report_packs: set[str] | None = None,
    report_type_by_pack: dict[str, str] | None = None,
    known_policy_profiles: set[str] | None = None,
    default_policy_profile_by_pack: dict[str, str] | None = None,
) -> ReportSpecValidationResult:
    violations = list(ReportSpecContract.validate(payload))
    report_pack = _text(payload.get("report_pack"))
    policy_profile = _text(payload.get("policy_profile"))
    report_type = _text(payload.get("report_type"))
    resolved_policy_profile = policy_profile
    policy_profile_resolution = _resolution_payload(payload.get("policy_profile_resolution"))
    resolution_source = _text(policy_profile_resolution.get("source")) if policy_profile_resolution else ""
    policy_profile_source = ""
    pack_default_policy_profile = ""

    if known_report_packs is not None:
        if not report_pack or report_pack not in known_report_packs:
            violations.append(FieldViolation(field="report_pack", error=f"unknown report_pack:{report_pack or '<missing>'}"))

    if report_type_by_pack is not None and report_pack in report_type_by_pack:
        expected = report_type_by_pack[report_pack]
        if report_type != expected:
            violations.append(
                FieldViolation(field="report_type", error=f"must match report pack type:{expected}")
            )

    if not resolved_policy_profile and default_policy_profile_by_pack is not None and report_pack:
        pack_default_policy_profile = _text(default_policy_profile_by_pack.get(report_pack))
        resolved_policy_profile = pack_default_policy_profile
        if not policy_profile_source and resolved_policy_profile:
            policy_profile_source = "report_pack.default_policy_profile"
        if known_report_packs is None or report_pack in known_report_packs:
            if not resolved_policy_profile:
                violations.append(
                    FieldViolation(
                        field="policy_profile",
                        error=f"missing default policy profile for report_pack:{report_pack}",
                    )
                )

    if known_policy_profiles is not None:
        if resolved_policy_profile and resolved_policy_profile not in known_policy_profiles:
            violations.append(
                FieldViolation(
                    field="policy_profile",
                    error=f"unknown policy_profile:{resolved_policy_profile}",
                )
            )

    if not pack_default_policy_profile and default_policy_profile_by_pack is not None and report_pack:
        pack_default_policy_profile = _text(default_policy_profile_by_pack.get(report_pack))

    if policy_profile_resolution:
        resolution_profile = _text(policy_profile_resolution.get("policy_profile"))
        if resolution_profile and resolved_policy_profile and resolution_profile != resolved_policy_profile:
            violations.append(
                FieldViolation(
                    field="policy_profile_resolution.policy_profile",
                    error=f"must match resolved policy_profile:{resolved_policy_profile}",
                )
            )
        if not policy_profile and resolution_source and resolution_source != "report_pack.default_policy_profile":
            violations.append(
                FieldViolation(
                    field="policy_profile_resolution.source",
                    error="requires explicit policy_profile unless source is report_pack.default_policy_profile",
                )
            )
        if (
            resolution_source == "report_pack.default_policy_profile"
            and pack_default_policy_profile
            and resolved_policy_profile
            and resolved_policy_profile != pack_default_policy_profile
        ):
            violations.append(
                FieldViolation(
                    field="policy_profile_resolution.source",
                    error=f"report_pack.default_policy_profile must resolve to pack default:{pack_default_policy_profile}",
                )
            )

    if policy_profile:
        policy_profile_source = resolution_source or "report_spec.policy_profile"
    elif resolved_policy_profile:
        policy_profile_source = "report_pack.default_policy_profile"

    errors = tuple(item for item in violations if item.severity == "error")
    warnings = tuple(item for item in violations if item.severity != "error")
    return ReportSpecValidationResult(
        ok=not errors,
        report_pack=report_pack or None,
        policy_profile=policy_profile or None,
        resolved_policy_profile=resolved_policy_profile or None,
        policy_profile_source=policy_profile_source or None,
        policy_profile_resolution=policy_profile_resolution or None,
        report_type=report_type or None,
        errors=errors,
        warnings=warnings,
    )


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _resolution_payload(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return dict(value)


def _violation_to_dict(violation: FieldViolation) -> dict[str, str]:
    return {
        "field": violation.field,
        "error": violation.error,
        "severity": violation.severity,
    }
