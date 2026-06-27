"""Read-only ReportTemplate render-plan projection.

This module projects what a future template renderer would consume and emit.
It does not render, rewrite, finalize, gate, or write workspace files.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from multi_agent_brief.core.config import build_run_settings, load_config
from multi_agent_brief.outputs.naming import render_output_stem
from multi_agent_brief.product.template_conformance import (
    project_workspace_report_template_conformance,
)
from multi_agent_brief.product.template_projection import project_workspace_report_template

REPORT_TEMPLATE_RENDER_PLAN_BOUNDARY = "product_report_template_render_plan_projection_only"

_PRIMARY_RENDER_SOURCE = "output/intermediate/audited_brief.md"
_SOURCE_CANDIDATES = (
    (_PRIMARY_RENDER_SOURCE, "primary_render_source"),
    ("output/brief.md", "existing_reader_markdown"),
    ("output/delivery/brief.md", "existing_delivery_markdown"),
)


def project_workspace_report_template_render_plan(workspace: str | Path) -> dict[str, Any]:
    """Return a deterministic, read-only render plan for an existing workspace."""

    ws = Path(workspace)
    template = project_workspace_report_template(ws)
    base = {
        "boundary": REPORT_TEMPLATE_RENDER_PLAN_BOUNDARY,
        "runtime_effect": "none",
        "template_status": template.get("status"),
        "template_id": template.get("template_id"),
        "report_type": template.get("report_type"),
    }
    if template.get("status") != "resolved":
        return {
            **base,
            "status": "not_available",
            "reason": f"report_template_{template.get('status') or 'missing'}",
            "source_artifact_candidates": [],
            "selected_source_artifact": None,
            "section_plan": [],
            "unresolved_sections": [],
            "planned_delivery_targets": [],
            "summary_counts": _summary_counts([], []),
        }

    conformance = project_workspace_report_template_conformance(ws)
    targets = conformance.get("targets") if isinstance(conformance.get("targets"), list) else []
    target_by_artifact = {
        str(item.get("target_artifact")): item
        for item in targets
        if isinstance(item, dict) and item.get("target_artifact")
    }
    source_candidates = _source_artifact_candidates(target_by_artifact)
    selected_source = (
        _PRIMARY_RENDER_SOURCE
        if _artifact_status(target_by_artifact.get(_PRIMARY_RENDER_SOURCE)) != "missing"
        else None
    )
    selected_target = target_by_artifact.get(selected_source or "")
    expected_sections = [
        str(item).strip()
        for item in template.get("section_order", [])
        if isinstance(item, str) and item.strip()
    ]
    section_plan = _section_plan(
        expected_sections=expected_sections,
        selected_target=selected_target if isinstance(selected_target, dict) else None,
    )
    unresolved_sections = [
        item
        for item in section_plan
        if item.get("status") in {"missing", "out_of_order", "source_missing", "source_unreadable"}
    ]
    planned_targets = _planned_delivery_targets(ws, _report_spec_outputs(ws))
    status = _render_plan_status(selected_source, selected_target, unresolved_sections)

    return {
        **base,
        "status": status,
        "report_pack": template.get("report_pack"),
        "report_title": template.get("report_title") or "",
        "section_order": expected_sections,
        "source_artifact_candidates": source_candidates,
        "selected_source_artifact": selected_source,
        "section_plan": section_plan,
        "unresolved_sections": unresolved_sections,
        "planned_delivery_targets": planned_targets,
        "summary_counts": _summary_counts(section_plan, planned_targets),
    }


def _source_artifact_candidates(target_by_artifact: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for artifact, role in _SOURCE_CANDIDATES:
        target = target_by_artifact.get(artifact)
        artifact_status = _artifact_status(target)
        candidates.append({
            "artifact": artifact,
            "role": role,
            "status": artifact_status,
            "selected": artifact == _PRIMARY_RENDER_SOURCE and artifact_status != "missing",
            "conformance_status": target.get("status") if isinstance(target, dict) else "missing",
            "missing_sections": list(target.get("missing_sections") or []) if isinstance(target, dict) else [],
            "out_of_order_sections": list(target.get("out_of_order_sections") or []) if isinstance(target, dict) else [],
            "extra_heading_count": int(target.get("extra_heading_count") or 0) if isinstance(target, dict) else 0,
        })
    return candidates


def _artifact_status(target: dict[str, Any] | None) -> str:
    if not isinstance(target, dict):
        return "missing"
    status = target.get("status")
    if status == "missing":
        return "missing"
    if status == "unreadable":
        return "unreadable"
    return "present"


def _section_plan(
    *,
    expected_sections: list[str],
    selected_target: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if not selected_target:
        return [
            {
                "section": section,
                "order": idx,
                "status": "source_missing",
                "matched_heading": None,
                "line": None,
                "level": None,
            }
            for idx, section in enumerate(expected_sections, start=1)
        ]
    if selected_target.get("status") == "unreadable":
        return [
            {
                "section": section,
                "order": idx,
                "status": "source_unreadable",
                "matched_heading": None,
                "line": None,
                "level": None,
            }
            for idx, section in enumerate(expected_sections, start=1)
        ]

    heading_map = selected_target.get("section_heading_map")
    heading_map = heading_map if isinstance(heading_map, dict) else {}
    missing = set(str(item) for item in (selected_target.get("missing_sections") or []))
    out_of_order = set(str(item) for item in (selected_target.get("out_of_order_sections") or []))
    plan: list[dict[str, Any]] = []
    for idx, section in enumerate(expected_sections, start=1):
        match = heading_map.get(section)
        match = match if isinstance(match, dict) else {}
        if section in missing:
            status = "missing"
        elif section in out_of_order:
            status = "out_of_order"
        else:
            status = "matched"
        plan.append({
            "section": section,
            "order": idx,
            "status": status,
            "matched_heading": match.get("heading"),
            "line": match.get("line"),
            "level": match.get("level"),
        })
    return plan


def _report_spec_outputs(workspace: Path) -> list[str]:
    path = workspace / "report_spec.yaml"
    if not path.exists():
        return []
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return []
    if not isinstance(payload, dict):
        return []
    outputs = payload.get("outputs")
    if not isinstance(outputs, list):
        return []
    return [
        str(item).strip().lower()
        for item in outputs
        if isinstance(item, str) and item.strip()
    ]


def _planned_delivery_targets(workspace: Path, outputs: list[str]) -> list[dict[str, str]]:
    formats = set(outputs or ["markdown"])
    output_dir = _workspace_relative_output_dir(workspace)
    delivery_dir = f"{output_dir}/delivery"
    targets: list[dict[str, str]] = []
    if "markdown" in formats:
        targets.extend([
            {"artifact": f"{output_dir}/brief.md", "kind": "reader_markdown", "concrete": "true"},
            {"artifact": f"{delivery_dir}/brief.md", "kind": "delivery_markdown", "concrete": "true"},
        ])
    if "docx" in formats:
        delivery_docx_target = _planned_delivery_docx_target(workspace, output_dir=output_dir)
        targets.extend([
            {"artifact": f"{output_dir}/brief.docx", "kind": "reader_docx", "concrete": "true"},
            delivery_docx_target,
        ])
    if "source_appendix" in formats:
        targets.append({
            "artifact": f"{output_dir}/source_appendix.md",
            "kind": "source_appendix_markdown",
            "concrete": "true",
        })
    return targets


def _workspace_relative_output_dir(workspace: Path) -> str:
    settings = _workspace_run_settings(workspace)
    output_dir = settings.get("output_dir") if isinstance(settings, dict) else None
    if isinstance(output_dir, str) and output_dir.strip():
        path = Path(output_dir)
        if path.is_absolute():
            try:
                return path.resolve().relative_to(workspace.resolve()).as_posix()
            except ValueError:
                return "output"
        return path.as_posix().strip("/") or "output"
    return "output"


def _planned_delivery_docx_target(workspace: Path, *, output_dir: str) -> dict[str, str]:
    settings = _workspace_run_settings(workspace)
    if settings:
        named_outputs = bool(settings.get("output_named_outputs", True))
        if named_outputs:
            tokens = dict(settings.get("output_filename_tokens") or {})
            project_name = str(settings.get("project_name") or "BriefLoop Report")
            tokens.setdefault("project_name", project_name)
            tokens.setdefault("title", project_name)
            stem = render_output_stem(str(settings.get("output_filename_template") or ""), tokens)
            artifact_name = f"{stem}.docx" if stem and stem != "brief" else "brief.docx"
            return {
                "artifact": f"{output_dir}/delivery/{artifact_name}",
                "kind": "delivery_docx",
                "concrete": "true",
                "filename_source": "named_output",
            }
        return {
            "artifact": f"{output_dir}/delivery/brief.docx",
            "kind": "delivery_docx",
            "concrete": "true",
            "filename_source": "unnamed_output",
        }
    return {
        "artifact": f"{output_dir}/delivery/<named-output>.docx",
        "artifact_pattern": f"{output_dir}/delivery/<named-output>.docx",
        "kind": "delivery_docx",
        "concrete": "false",
        "filename_source": "unknown_without_config",
    }


def _workspace_run_settings(workspace: Path) -> dict[str, Any]:
    config_path = workspace / "config.yaml"
    if not config_path.exists():
        return {}
    try:
        config = load_config(config_path)
        return build_run_settings(
            config=config,
            input_dir=None,
            output_dir=None,
            name=None,
            language=None,
            audience=None,
        )
    except (OSError, ValueError, yaml.YAMLError):
        return {}


def _render_plan_status(
    selected_source: str | None,
    selected_target: dict[str, Any] | None,
    unresolved_sections: list[dict[str, Any]],
) -> str:
    if not selected_source:
        return "source_missing"
    if isinstance(selected_target, dict) and selected_target.get("status") == "unreadable":
        return "source_unreadable"
    if unresolved_sections:
        return "diagnostic_warning"
    return "planned"


def _summary_counts(section_plan: list[dict[str, Any]], planned_targets: list[dict[str, str]]) -> dict[str, int]:
    return {
        "section_count": len(section_plan),
        "matched_section_count": sum(1 for item in section_plan if item.get("status") == "matched"),
        "unresolved_section_count": sum(1 for item in section_plan if item.get("status") != "matched"),
        "planned_delivery_target_count": len(planned_targets),
    }
