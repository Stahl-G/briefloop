"""Experimental ReportTemplate registry.

Templates currently define product-layer section order only. They do not render
reader text or relax finalize/delivery gates.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any, Mapping

import yaml

REPORT_TEMPLATE_SCHEMA_VERSION = "briefloop.report_template.v1"


@dataclass(frozen=True)
class ReportTemplate:
    template_id: str
    report_type: str
    display_name: str
    status: str
    section_order: tuple[str, ...]
    source_path: str
    payload: Mapping[str, Any]

    @classmethod
    def from_payload(
        cls,
        payload: Mapping[str, Any],
        *,
        source_path: str | Path,
    ) -> "ReportTemplate":
        section_order = payload.get("section_order") if isinstance(payload.get("section_order"), list) else []
        return cls(
            template_id=str(payload.get("template_id", "")),
            report_type=str(payload.get("report_type", "")),
            display_name=str(payload.get("display_name", "")),
            status=str(payload.get("status", "")),
            section_order=tuple(str(item) for item in section_order),
            source_path=str(source_path),
            payload=payload,
        )

    def to_summary(self) -> dict[str, Any]:
        return {
            "template_id": self.template_id,
            "report_type": self.report_type,
            "display_name": self.display_name,
            "status": self.status,
            "section_order": list(self.section_order),
        }


@dataclass(frozen=True)
class ReportTemplateRegistry:
    config_dir: Path
    templates: tuple[ReportTemplate, ...]
    validation_errors: tuple[dict[str, str], ...]

    @classmethod
    def from_config_dir(cls, config_dir: str | Path) -> "ReportTemplateRegistry":
        base = Path(config_dir)
        templates: list[ReportTemplate] = []
        errors: list[dict[str, str]] = []
        seen: set[str] = set()
        for path in sorted(base.glob("*.yaml")):
            payload = _load_yaml(path)
            errors.extend(_template_errors(payload, path_name=path.name))
            template = ReportTemplate.from_payload(payload, source_path=path)
            if template.template_id:
                if template.template_id in seen:
                    errors.append({
                        "field": f"{path.name}.template_id",
                        "error": f"duplicate template_id:{template.template_id}",
                    })
                seen.add(template.template_id)
            templates.append(template)
        return cls(config_dir=base, templates=tuple(templates), validation_errors=tuple(errors))

    @classmethod
    def from_package(cls) -> "ReportTemplateRegistry":
        config_dir = files("multi_agent_brief").joinpath("configs", "report_templates")
        return cls.from_config_dir(Path(str(config_dir)))

    def get_by_report_type(self, report_type: str) -> ReportTemplate | None:
        return next((item for item in self.templates if item.report_type == report_type), None)

    def template_ids(self) -> set[str]:
        return {item.template_id for item in self.templates if item.template_id}

    def to_list_payload(self) -> dict[str, Any]:
        return {
            "ok": not self.validation_errors,
            "templates": [item.to_summary() for item in self.templates],
            "errors": list(self.validation_errors),
        }


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError:
        return {}
    return data if isinstance(data, dict) else {}


def _template_errors(payload: Mapping[str, Any], *, path_name: str) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    if payload.get("schema_version") != REPORT_TEMPLATE_SCHEMA_VERSION:
        errors.append({"field": f"{path_name}.schema_version", "error": f"must be {REPORT_TEMPLATE_SCHEMA_VERSION}"})
    for field in ("template_id", "report_type", "display_name", "status"):
        value = payload.get(field)
        if not isinstance(value, str) or not value.strip():
            errors.append({"field": f"{path_name}.{field}", "error": "required field is missing or blank"})
    if payload.get("status") != "experimental":
        errors.append({"field": f"{path_name}.status", "error": "must be experimental"})
    section_order = payload.get("section_order")
    if not isinstance(section_order, list) or not section_order:
        errors.append({"field": f"{path_name}.section_order", "error": "must be a non-empty list"})
    elif any(not isinstance(item, str) or not item.strip() for item in section_order):
        errors.append({"field": f"{path_name}.section_order", "error": "entries must be non-empty strings"})
    return errors
