"""Read-only ReportTemplate section conformance projection."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from multi_agent_brief.product.template_projection import project_workspace_report_template

REPORT_TEMPLATE_CONFORMANCE_BOUNDARY = "product_report_template_conformance_projection_only"

_MARKDOWN_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_LEADING_NUMBERING_RE = re.compile(r"^\s*(?:\d+(?:\.\d+)*[.)、：:]?|\([0-9]+\)|[IVXLC]+[.)、：:]?)\s+", re.IGNORECASE)
_NON_LABEL_RE = re.compile(r"[^a-z0-9\u4e00-\u9fff]+")


def project_workspace_report_template_conformance(workspace: str | Path) -> dict[str, Any]:
    """Compare existing reader/audited markdown headings to the resolved template.

    This is a diagnostic projection only. It does not render, rewrite, gate, or
    finalize any artifact.
    """

    ws = Path(workspace)
    template = project_workspace_report_template(ws)
    base = {
        "boundary": REPORT_TEMPLATE_CONFORMANCE_BOUNDARY,
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
            "targets": [],
            "summary_counts": _summary_counts([]),
        }

    expected_sections = [
        str(item).strip()
        for item in template.get("section_order", [])
        if isinstance(item, str) and item.strip()
    ]
    section_aliases = _section_aliases(template.get("section_aliases"), expected_sections)
    targets = [
        _project_target(ws, "output/intermediate/audited_brief.md", expected_sections, section_aliases),
        _project_target(ws, "output/brief.md", expected_sections, section_aliases),
        _project_target(ws, "output/delivery/brief.md", expected_sections, section_aliases),
    ]
    present_targets = [item for item in targets if item.get("status") != "missing"]
    if not present_targets:
        return {
            **base,
            "status": "no_targets",
            "section_order": expected_sections,
            "targets": targets,
            "summary_counts": _summary_counts(targets),
        }
    status = "pass" if all(item.get("status") == "pass" for item in present_targets) else "warning"
    return {
        **base,
        "status": status,
        "section_order": expected_sections,
        "targets": targets,
        "summary_counts": _summary_counts(targets),
    }


def _project_target(
    workspace: Path,
    rel_path: str,
    expected_sections: list[str],
    section_aliases: dict[str, list[str]],
) -> dict[str, Any]:
    path = workspace / rel_path
    if not path.exists():
        return {
            "target_artifact": rel_path,
            "status": "missing",
            "heading_count": 0,
            "matched_sections": [],
            "missing_sections": list(expected_sections),
            "out_of_order_sections": [],
            "extra_headings": [],
        }
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return {
            "target_artifact": rel_path,
            "status": "unreadable",
            "error": str(exc),
            "heading_count": 0,
            "matched_sections": [],
            "missing_sections": list(expected_sections),
            "out_of_order_sections": [],
            "extra_headings": [],
        }

    headings = _markdown_headings(text)
    matched_sections: list[str] = []
    matched_indices: list[int] = []
    extra_headings: list[str] = []
    nested_headings: list[str] = []
    for heading in headings:
        section = _match_heading_to_section(heading["text"], expected_sections, section_aliases)
        if section is None:
            if heading["level"] == 2:
                extra_headings.append(heading["text"])
            elif heading["level"] > 2:
                nested_headings.append(heading["text"])
            continue
        if section not in matched_sections:
            matched_sections.append(section)
            matched_indices.append(expected_sections.index(section))

    missing_sections = [section for section in expected_sections if section not in matched_sections]
    out_of_order_sections = _out_of_order_sections(matched_sections, matched_indices)
    status = (
        "pass"
        if not missing_sections and not out_of_order_sections and not extra_headings
        else "warning"
    )
    return {
        "target_artifact": rel_path,
        "status": status,
        "heading_count": len(headings),
        "matched_sections": matched_sections,
        "missing_sections": missing_sections,
        "out_of_order_sections": out_of_order_sections,
        "extra_headings": extra_headings[:20],
        "extra_heading_count": len(extra_headings),
        "nested_heading_count": len(nested_headings),
    }


def _markdown_headings(text: str) -> list[dict[str, Any]]:
    headings: list[dict[str, Any]] = []
    for idx, line in enumerate(text.splitlines(), start=1):
        match = _MARKDOWN_HEADING_RE.match(line)
        if not match:
            continue
        headings.append({
            "line": idx,
            "level": len(match.group(1)),
            "text": match.group(2).strip(),
        })
    return headings


def _match_heading_to_section(
    heading: str,
    expected_sections: list[str],
    section_aliases: dict[str, list[str]],
) -> str | None:
    normalized = _normalize_label(heading)
    for section in expected_sections:
        labels = [section.replace("_", " "), *section_aliases.get(section, [])]
        for label in labels:
            expected = _normalize_label(label)
            if expected and (normalized == expected or normalized.endswith(f" {expected}")):
                return section
    return None


def _normalize_label(value: str) -> str:
    stripped = _LEADING_NUMBERING_RE.sub("", value.strip().lower())
    normalized = _NON_LABEL_RE.sub(" ", stripped).strip()
    return " ".join(normalized.split())


def _section_aliases(value: Any, expected_sections: list[str]) -> dict[str, list[str]]:
    if not isinstance(value, dict):
        return {}
    expected = set(expected_sections)
    aliases: dict[str, list[str]] = {}
    for section_id, labels in value.items():
        if not isinstance(section_id, str) or section_id not in expected or not isinstance(labels, list):
            continue
        aliases[section_id] = [
            str(label).strip()
            for label in labels
            if isinstance(label, str) and label.strip()
        ]
    return aliases


def _out_of_order_sections(sections: list[str], indices: list[int]) -> list[str]:
    out: list[str] = []
    max_seen = -1
    for section, index in zip(sections, indices):
        if index < max_seen:
            out.append(section)
        max_seen = max(max_seen, index)
    return out


def _summary_counts(targets: list[dict[str, Any]]) -> dict[str, int]:
    present = [item for item in targets if item.get("status") != "missing"]
    return {
        "target_count": len(targets),
        "present_target_count": len(present),
        "warning_target_count": sum(1 for item in present if item.get("status") == "warning"),
        "missing_section_count": sum(len(item.get("missing_sections") or []) for item in present),
        "out_of_order_section_count": sum(len(item.get("out_of_order_sections") or []) for item in present),
        "extra_heading_count": sum(int(item.get("extra_heading_count") or 0) for item in present),
    }
