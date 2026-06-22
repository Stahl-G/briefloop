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
    report_type: str | None
    errors: tuple[FieldViolation, ...]
    warnings: tuple[FieldViolation, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "report_pack": self.report_pack,
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
) -> ReportSpecValidationResult:
    violations = list(ReportSpecContract.validate(payload))
    report_pack = _text(payload.get("report_pack"))
    report_type = _text(payload.get("report_type"))

    if known_report_packs is not None:
        if not report_pack or report_pack not in known_report_packs:
            violations.append(FieldViolation(field="report_pack", error=f"unknown report_pack:{report_pack or '<missing>'}"))

    if report_type_by_pack is not None and report_pack in report_type_by_pack:
        expected = report_type_by_pack[report_pack]
        if report_type != expected:
            violations.append(
                FieldViolation(field="report_type", error=f"must match report pack type:{expected}")
            )

    errors = tuple(item for item in violations if item.severity == "error")
    warnings = tuple(item for item in violations if item.severity != "error")
    return ReportSpecValidationResult(
        ok=not errors,
        report_pack=report_pack or None,
        report_type=report_type or None,
        errors=errors,
        warnings=warnings,
    )


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _violation_to_dict(violation: FieldViolation) -> dict[str, str]:
    return {
        "field": violation.field,
        "error": violation.error,
        "severity": violation.severity,
    }
