"""Read-only ReportTemplate section conformance projection."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from multi_agent_brief.product.template_projection import project_workspace_report_template

REPORT_TEMPLATE_CONFORMANCE_BOUNDARY = "product_report_template_conformance_projection_only"

_MARKDOWN_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_LEADING_NUMBERING_RE = re.compile(r"^\s*(?:\d+(?:\.\d+)*[.)、：:]?|\([0-9]+\)|[IVXLC]+[.)、：:]?)\s+", re.IGNORECASE)
_NON_LABEL_RE = re.compile(r"[^a-z0-9\u4e00-\u9fff]+")
_WORDISH_RE = re.compile(r"[A-Za-z0-9]+(?:[-'][A-Za-z0-9]+)*|[\u4e00-\u9fff]")
_MARKDOWN_TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$")

_ALLOWED_STATUSES = {"pass", "warning", "not_available", "no_targets", "unreadable"}
_ALLOWED_READER_WARNING_TYPES = {
    "required_block_missing",
    "executive_summary_too_long",
    "missing_table_slot",
    "source_appendix_not_last",
}


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
    reader_contract = _reader_contract(template.get("reader_contract"), expected_sections)
    section_aliases = _section_aliases(template.get("section_aliases"), expected_sections)
    _add_report_title_alias(section_aliases, template.get("report_title"))
    _add_workspace_company_aliases(section_aliases, _workspace_company(ws))
    report_title = template.get("report_title") if isinstance(template.get("report_title"), str) else ""
    targets = [
        _project_target(
            ws,
            "output/intermediate/audited_brief.md",
            expected_sections,
            section_aliases,
            reader_contract,
            report_title,
        ),
        _project_target(ws, "output/brief.md", expected_sections, section_aliases, reader_contract, report_title),
        _project_target(
            ws,
            "output/delivery/brief.md",
            expected_sections,
            section_aliases,
            reader_contract,
            report_title,
        ),
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
        "reader_contract": reader_contract,
        "targets": targets,
        "summary_counts": _summary_counts(targets),
    }


def _project_target(
    workspace: Path,
    rel_path: str,
    expected_sections: list[str],
    section_aliases: dict[str, list[str]],
    reader_contract: dict[str, Any],
    report_title: str = "",
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
    section_heading_map: dict[str, dict[str, Any]] = {}
    extra_headings: list[str] = []
    nested_headings: list[str] = []
    first_h1_seen = False
    active_h1_section: str | None = None
    for heading in headings:
        if heading["level"] > 2:
            nested_headings.append(heading["text"])
            continue
        if heading["level"] == 2 and active_h1_section and active_h1_section != "cover":
            nested_headings.append(heading["text"])
            continue
        is_first_h1 = False
        if heading["level"] == 1 and not first_h1_seen:
            first_h1_seen = True
            is_first_h1 = True
        if heading["level"] == 1:
            active_h1_section = None
        section = _match_heading_to_section(heading["text"], expected_sections, section_aliases)
        if section is None:
            if is_first_h1 and "cover" in expected_sections:
                section = "cover"
            elif is_first_h1 and _label_matches(heading["text"], report_title):
                continue
            else:
                if heading["level"] in {1, 2}:
                    extra_headings.append(heading["text"])
                continue
        if section not in matched_sections:
            matched_sections.append(section)
            matched_indices.append(expected_sections.index(section))
            section_heading_map[section] = {
                "heading": heading["text"],
                "level": heading["level"],
                "line": heading["line"],
            }
        if heading["level"] == 1:
            active_h1_section = section

    missing_sections = [section for section in expected_sections if section not in matched_sections]
    out_of_order_sections = _out_of_order_sections(matched_sections, matched_indices)
    reader_contract_applied = rel_path in {"output/brief.md", "output/delivery/brief.md"}
    reader_block_warnings = (
        _reader_contract_warnings(
            text=text,
            headings=headings,
            expected_sections=expected_sections,
            section_heading_map=section_heading_map,
            matched_sections=matched_sections,
            missing_sections=missing_sections,
            reader_contract=reader_contract,
        )
        if reader_contract_applied
        else []
    )
    status = (
        "pass"
        if not missing_sections and not out_of_order_sections and not extra_headings and not reader_block_warnings
        else "warning"
    )
    return {
        "target_artifact": rel_path,
        "status": status,
        "heading_count": len(headings),
        "matched_sections": matched_sections,
        "section_heading_map": section_heading_map,
        "missing_sections": missing_sections,
        "out_of_order_sections": out_of_order_sections,
        "extra_headings": extra_headings[:20],
        "extra_heading_count": len(extra_headings),
        "nested_heading_count": len(nested_headings),
        "reader_contract_applied": reader_contract_applied,
        "reader_block_warnings": reader_block_warnings,
        "reader_block_warning_count": len(reader_block_warnings),
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


def _label_matches(value: str, expected: str) -> bool:
    normalized = _normalize_label(value)
    normalized_expected = _normalize_label(expected)
    return bool(normalized_expected and normalized == normalized_expected)


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


def _add_report_title_alias(section_aliases: dict[str, list[str]], title: Any) -> None:
    if not isinstance(title, str) or not title.strip():
        return
    labels = section_aliases.setdefault("cover", [])
    title_text = title.strip()
    if title_text not in labels:
        labels.append(title_text)


def _add_workspace_company_aliases(section_aliases: dict[str, list[str]], company: str) -> None:
    if not company:
        return
    labels = section_aliases.setdefault("company_implications", [])
    for label in (
        f"Company Implications for {company}",
        f"{company} Implications",
        f"对{company}的启示",
        f"对 {company} 的启示",
    ):
        if label not in labels:
            labels.append(label)


def _workspace_company(workspace: Path) -> str:
    config_path = workspace / "config.yaml"
    if not config_path.exists():
        return ""
    try:
        payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return ""
    if not isinstance(payload, dict):
        return ""
    project = payload.get("project")
    if not isinstance(project, dict):
        return ""
    for key in ("company", "organization"):
        value = project.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _reader_contract(value: Any, expected_sections: list[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    expected = set(expected_sections)
    contract: dict[str, Any] = {}
    for key in ("required_blocks", "required_table_sections"):
        raw_items = value.get(key)
        if not isinstance(raw_items, list):
            continue
        items = [
            str(item).strip()
            for item in raw_items
            if isinstance(item, str) and item.strip() in expected
        ]
        if items:
            contract[key] = items
    max_words = value.get("max_executive_summary_words")
    if isinstance(max_words, int) and max_words > 0:
        contract["max_executive_summary_words"] = max_words
    position = value.get("source_appendix_position")
    if position in {"last", "any"}:
        contract["source_appendix_position"] = position
    return contract


def _reader_contract_warnings(
    *,
    text: str,
    headings: list[dict[str, Any]],
    expected_sections: list[str],
    section_heading_map: dict[str, dict[str, Any]],
    matched_sections: list[str],
    missing_sections: list[str],
    reader_contract: dict[str, Any],
) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    for section_id in reader_contract.get("required_blocks") or []:
        if section_id in missing_sections:
            warnings.append({
                "type": "required_block_missing",
                "section_id": section_id,
                "message": f"Required reader block is missing: {section_id}",
            })

    max_words = reader_contract.get("max_executive_summary_words")
    if isinstance(max_words, int) and max_words > 0 and "executive_summary" in section_heading_map:
        body = _section_body(text, headings, section_heading_map["executive_summary"])
        word_count = _reader_word_count(body)
        if word_count > max_words:
            warnings.append({
                "type": "executive_summary_too_long",
                "section_id": "executive_summary",
                "limit": max_words,
                "actual": word_count,
                "message": f"Executive summary has {word_count} word-equivalent tokens; limit is {max_words}.",
            })

    for section_id in reader_contract.get("required_table_sections") or []:
        if section_id not in section_heading_map:
            continue
        body = _section_body(text, headings, section_heading_map[section_id])
        if not _has_markdown_table(body):
            warnings.append({
                "type": "missing_table_slot",
                "section_id": section_id,
                "message": f"Reader contract expects a Markdown table in section: {section_id}",
            })

    if reader_contract.get("source_appendix_position") == "last" and "source_appendix" in section_heading_map:
        if matched_sections and matched_sections[-1] != "source_appendix":
            warnings.append({
                "type": "source_appendix_not_last",
                "section_id": "source_appendix",
                "message": "Source appendix is not the last matched template section.",
            })
        elif _has_peer_heading_after_source_appendix(headings, section_heading_map["source_appendix"]):
            warnings.append({
                "type": "source_appendix_not_last",
                "section_id": "source_appendix",
                "message": "A peer report heading appears after Source Appendix.",
            })
    return warnings


def _section_body(text: str, headings: list[dict[str, Any]], heading: dict[str, Any]) -> str:
    lines = text.splitlines()
    start_line = int(heading.get("line") or 0)
    level = int(heading.get("level") or 1)
    end_line = len(lines) + 1
    for candidate in headings:
        candidate_line = int(candidate.get("line") or 0)
        candidate_level = int(candidate.get("level") or 0)
        if candidate_line <= start_line:
            continue
        if candidate_level <= level:
            end_line = candidate_line
            break
    return "\n".join(lines[start_line:end_line - 1])


def _reader_word_count(text: str) -> int:
    return len(_WORDISH_RE.findall(text))


def _has_markdown_table(text: str) -> bool:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if "|" not in line:
            continue
        next_lines = lines[index + 1:index + 3]
        if any(_MARKDOWN_TABLE_SEPARATOR_RE.match(item) for item in next_lines):
            return True
    return False


def _has_peer_heading_after_source_appendix(
    headings: list[dict[str, Any]],
    source_heading: dict[str, Any],
) -> bool:
    source_line = int(source_heading.get("line") or 0)
    source_level = int(source_heading.get("level") or 1)
    for heading in headings:
        line = int(heading.get("line") or 0)
        if line <= source_line:
            continue
        level = int(heading.get("level") or 0)
        if level <= source_level:
            return True
    return False


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
    reader_warnings = [
        warning
        for item in present
        for warning in (item.get("reader_block_warnings") or [])
        if isinstance(warning, dict)
    ]
    return {
        "target_count": len(targets),
        "present_target_count": len(present),
        "warning_target_count": sum(1 for item in present if item.get("status") == "warning"),
        "missing_section_count": sum(len(item.get("missing_sections") or []) for item in present),
        "out_of_order_section_count": sum(len(item.get("out_of_order_sections") or []) for item in present),
        "extra_heading_count": sum(int(item.get("extra_heading_count") or 0) for item in present),
        "reader_block_warning_count": len(reader_warnings),
        "missing_table_slot_count": sum(
            1 for item in reader_warnings if item.get("type") == "missing_table_slot"
        ),
        "overlong_executive_summary_count": sum(
            1 for item in reader_warnings if item.get("type") == "executive_summary_too_long"
        ),
        "source_appendix_position_warning_count": sum(
            1 for item in reader_warnings if item.get("type") == "source_appendix_not_last"
        ),
    }


def validate_report_template_conformance_payload(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return "report_template_conformance_schema_error:not_object"
    if payload.get("boundary") != REPORT_TEMPLATE_CONFORMANCE_BOUNDARY:
        return "report_template_conformance_schema_error:boundary"
    if payload.get("runtime_effect") != "none":
        return "report_template_conformance_schema_error:runtime_effect"
    if payload.get("status") not in _ALLOWED_STATUSES:
        return "report_template_conformance_schema_error:status"
    targets = payload.get("targets")
    if not isinstance(targets, list):
        return "report_template_conformance_schema_error:targets"
    counts = payload.get("summary_counts")
    if not isinstance(counts, dict):
        return "report_template_conformance_schema_error:summary_counts"
    for target in targets:
        if not isinstance(target, dict):
            return "report_template_conformance_schema_error:targets"
        if target.get("status") not in {"pass", "warning", "missing", "unreadable"}:
            return "report_template_conformance_schema_error:target.status"
        if target.get("reader_block_warnings") is None:
            continue
        warnings = target.get("reader_block_warnings")
        if not isinstance(warnings, list):
            return "report_template_conformance_schema_error:reader_block_warnings"
        for warning in warnings:
            if not isinstance(warning, dict):
                return "report_template_conformance_schema_error:reader_block_warnings"
            if warning.get("type") not in _ALLOWED_READER_WARNING_TYPES:
                return "report_template_conformance_schema_error:reader_block_warnings.type"
    return None
