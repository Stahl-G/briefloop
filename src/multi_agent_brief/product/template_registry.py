"""Experimental ReportTemplate registry.

Templates currently define product-layer section order and reader-contract
diagnostics only. They do not render reader text or relax finalize/delivery
gates.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any, Mapping

import yaml

from multi_agent_brief.product.citation_profile import VALID_CITATION_PROFILES, normalize_citation_profile

REPORT_TEMPLATE_SCHEMA_VERSION = "briefloop.report_template.v1"


@dataclass(frozen=True)
class ReportTemplate:
    template_id: str
    report_type: str
    display_name: str
    status: str
    section_order: tuple[str, ...]
    section_aliases: Mapping[str, tuple[str, ...]]
    reader_contract: Mapping[str, Any]
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
        section_aliases = payload.get("section_aliases") if isinstance(payload.get("section_aliases"), dict) else {}
        return cls(
            template_id=str(payload.get("template_id", "")),
            report_type=str(payload.get("report_type", "")),
            display_name=str(payload.get("display_name", "")),
            status=str(payload.get("status", "")),
            section_order=tuple(str(item) for item in section_order),
            section_aliases={
                str(key): tuple(str(item) for item in value)
                for key, value in section_aliases.items()
                if isinstance(value, list)
            },
            reader_contract=_reader_contract_from_payload(payload.get("reader_contract")),
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
            "section_aliases": {
                key: list(value)
                for key, value in self.section_aliases.items()
            },
            "reader_contract": dict(self.reader_contract),
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
    aliases = payload.get("section_aliases")
    if aliases is not None:
        if not isinstance(aliases, dict):
            errors.append({"field": f"{path_name}.section_aliases", "error": "must be an object when present"})
        else:
            known_sections = {str(item) for item in section_order} if isinstance(section_order, list) else set()
            for section_id, values in aliases.items():
                if not isinstance(section_id, str) or not section_id.strip():
                    errors.append({"field": f"{path_name}.section_aliases", "error": "keys must be non-empty strings"})
                    continue
                if known_sections and section_id not in known_sections:
                    errors.append({
                        "field": f"{path_name}.section_aliases.{section_id}",
                        "error": "must reference a section_order entry",
                    })
                if not isinstance(values, list) or any(not isinstance(item, str) or not item.strip() for item in values):
                    errors.append({
                        "field": f"{path_name}.section_aliases.{section_id}",
                        "error": "must be a list of non-empty strings",
                    })
    errors.extend(_reader_contract_errors(payload.get("reader_contract"), path_name=path_name, section_order=section_order))
    return errors


def _reader_contract_from_payload(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    contract: dict[str, Any] = {}
    for key in ("required_blocks", "required_table_sections"):
        raw_items = value.get(key)
        if isinstance(raw_items, list):
            contract[key] = [
                str(item).strip()
                for item in raw_items
                if isinstance(item, str) and item.strip()
            ]
    max_words = value.get("max_executive_summary_words")
    if isinstance(max_words, int) and max_words > 0:
        contract["max_executive_summary_words"] = max_words
    position = value.get("source_appendix_position")
    if isinstance(position, str) and position.strip():
        contract["source_appendix_position"] = position.strip()
    citation_profile = normalize_citation_profile(value.get("citation_profile"))
    if citation_profile:
        contract["citation_profile"] = citation_profile
    return contract


def _reader_contract_errors(
    value: Any,
    *,
    path_name: str,
    section_order: Any,
) -> list[dict[str, str]]:
    if value is None:
        return []
    if not isinstance(value, Mapping):
        return [{"field": f"{path_name}.reader_contract", "error": "must be an object when present"}]
    errors: list[dict[str, str]] = []
    allowed_keys = {
        "required_blocks",
        "required_table_sections",
        "max_executive_summary_words",
        "source_appendix_position",
        "citation_profile",
    }
    for key in value:
        if key not in allowed_keys:
            errors.append({"field": f"{path_name}.reader_contract.{key}", "error": "unknown reader contract field"})
    known_sections = {str(item) for item in section_order} if isinstance(section_order, list) else set()
    for key in ("required_blocks", "required_table_sections"):
        items = value.get(key)
        if items is None:
            continue
        if not isinstance(items, list) or any(not isinstance(item, str) or not item.strip() for item in items):
            errors.append({"field": f"{path_name}.reader_contract.{key}", "error": "must be a list of section ids"})
            continue
        for item in items:
            if known_sections and item not in known_sections:
                errors.append({
                    "field": f"{path_name}.reader_contract.{key}",
                    "error": f"unknown section id:{item}",
                })
    max_words = value.get("max_executive_summary_words")
    if max_words is not None and (not isinstance(max_words, int) or max_words <= 0):
        errors.append({
            "field": f"{path_name}.reader_contract.max_executive_summary_words",
            "error": "must be a positive integer",
        })
    position = value.get("source_appendix_position")
    if position is not None and position not in {"last", "any"}:
        errors.append({
            "field": f"{path_name}.reader_contract.source_appendix_position",
            "error": "must be last or any",
        })
    citation_profile = value.get("citation_profile")
    if citation_profile is not None and normalize_citation_profile(citation_profile) not in VALID_CITATION_PROFILES:
        profiles = ", ".join(sorted(VALID_CITATION_PROFILES))
        errors.append({
            "field": f"{path_name}.reader_contract.citation_profile",
            "error": f"must be one of: {profiles}",
        })
    return errors
