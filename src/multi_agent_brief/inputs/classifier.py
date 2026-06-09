"""Deterministic input file classification."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from multi_agent_brief.inputs.contracts import (
    DIR_ROLES,
    MINERU_EXTRACTABLE_SUFFIXES,
    RECOGNISED_EVIDENCE_SUFFIXES,
    SCANNABLE_SUFFIXES,
    base_item,
    extracted_markdown_path,
)


SUSPICIOUS_FILENAMES: set[str] = {
    "brief.md",
    "audited_brief.md",
    "reader_brief.md",
    "audit_report.json",
    "finalize_report.json",
    "claim_ledger.json",
    "candidate_claims.json",
    "screened_candidates.json",
    "input_classification.json",
    "classification.json",
    "run_manifest.json",
    "source_map.md",
    "source_coverage_report.json",
    "analysis_blocks.json",
    "rendered_output_report.json",
    "final_audit_report.json",
    "final_clean_report.json",
    "editor_draft.md",
    "analyst_draft.md",
    "draft_brief.md",
}

SUSPICIOUS_SUFFIX_PATTERNS: tuple[str, ...] = (
    "_output.md",
    "_final.md",
    "_audit.md",
    "_audited.md",
    "_reviewed.md",
    "_commented.md",
)

SUSPICIOUS_CONTENT_MARKERS: tuple[str, ...] = (
    "[src:",
    "audit_status",
    "claim_id",
    "CLAIM_ID",
    "Reader brief",
    "Internal [src:",
    "Finalized report",
    "Audit report",
    "Evidence ledger",
)

FEEDBACK_KEYWORDS: tuple[str, ...] = (
    "feedback",
    "comment",
    "comments",
    "annotated",
    "review",
    "revision",
    "批注",
    "修改意见",
    "反馈",
)

INSTRUCTION_KEYWORDS: tuple[str, ...] = (
    "instruction",
    "instructions",
    "prompt",
    "requirements",
    "briefing_request",
    "任务要求",
    "写作要求",
)

CONTEXT_KEYWORDS: tuple[str, ...] = (
    "context",
    "background",
    "company_profile",
    "profile",
    "背景",
    "公司介绍",
)


def classify_input_dir(input_dir: Path) -> dict[str, Any]:
    """Scan input_dir and classify every content file by governance role."""
    evidence: list[dict[str, Any]] = []
    feedback: list[dict[str, Any]] = []
    instruction: list[dict[str, Any]] = []
    context: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for entry in sorted(input_dir.iterdir()):
        if entry.name.startswith("."):
            continue

        if entry.is_dir():
            role = DIR_ROLES.get(entry.name)
            if role is None:
                for item in _scan_dir_entries(entry):
                    item["reason"] = "unknown_input_subdir"
                    item["suggested_role"] = "unknown"
                    skipped.append(item)
                continue

            for item in _scan_dir_entries(entry):
                _assign_by_role(role, item, evidence, feedback, instruction, context, skipped)

        elif entry.is_file():
            _classify_root_file(entry, evidence, skipped)

    return {
        "evidence": evidence,
        "feedback": feedback,
        "instruction": instruction,
        "context": context,
        "skipped": skipped,
    }


def _scan_dir_entries(subdir: Path) -> list[dict[str, Any]]:
    """Return metadata dicts for every scannable file one level deep under subdir."""
    items: list[dict[str, Any]] = []
    for f in sorted(subdir.iterdir()):
        if f.name.startswith(".") or f.name.lower() == "readme.md":
            continue
        if f.is_dir():
            items.append({
                "path": str(f),
                "name": f.name + "/",
                "bytes": 0,
                "reason": "nested_directory_not_supported",
            })
            continue
        try:
            size = f.stat().st_size
        except OSError:
            items.append({
                "path": str(f),
                "name": f.name,
                "bytes": 0,
                "reason": "unreadable",
            })
            continue
        items.append({"path": str(f), "name": f.name, "bytes": size})
    return items


def _classify_root_file(entry: Path, evidence: list, skipped: list) -> None:
    """Classify a file in the input/ root."""
    name_lower = entry.name.lower()

    if name_lower in SUSPICIOUS_FILENAMES:
        skipped.append(_skip_item(entry, "suspicious_output_artifact"))
        return

    for pattern in SUSPICIOUS_SUFFIX_PATTERNS:
        if name_lower.endswith(pattern):
            skipped.append(_skip_item(entry, "suspicious_output_artifact"))
            return

    override_role = _keyword_role(entry.name)
    if override_role in ("feedback", "instruction", "context"):
        skipped.append(
            _skip_item(
                entry,
                f"filename_suggests_{override_role}",
                suggested_role=override_role,
            )
        )
        return

    if entry.suffix.lower() not in RECOGNISED_EVIDENCE_SUFFIXES:
        if entry.suffix.lower() in MINERU_EXTRACTABLE_SUFFIXES:
            skipped.append(_document_skip_item(entry, "evidence"))
        else:
            skipped.append(_skip_item(entry, "unsupported_extension"))
        return

    try:
        content = entry.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        skipped.append(_skip_item(entry, "binary_or_unreadable"))
        return

    if _has_suspicious_content(content):
        evidence.append(_content_item(entry, flagged=True))
        skipped.append({
            **base_item(entry),
            "reason": "suspicious_content_markers",
            "note": (
                "File classified as evidence but contains markers of old output "
                "artefacts; review manually."
            ),
        })
        return

    evidence.append(_content_item(entry))


def _assign_by_role(
    role: str,
    item: dict,
    evidence: list,
    feedback: list,
    instruction: list,
    context: list,
    skipped: list,
) -> None:
    """Route an item from a known subdirectory to the right list."""
    if "reason" in item:
        skipped.append(item)
        return

    ext = Path(item["name"]).suffix.lower()
    if ext not in SCANNABLE_SUFFIXES:
        item["reason"] = "unsupported_extension"
        item["suggested_role"] = role
        skipped.append(item)
        return

    if ext not in RECOGNISED_EVIDENCE_SUFFIXES:
        extracted = extracted_markdown_path(Path(item["path"]))
        item["reason"] = "document_extracted" if extracted.exists() else "needs_document_extraction"
        item["suggested_role"] = role
        item["extract_with"] = "multi-agent-brief inputs extract"
        item["extracted_markdown"] = str(extracted) if extracted.exists() else ""
        skipped.append(item)
        return

    name_lower = item["name"].lower()
    if name_lower in SUSPICIOUS_FILENAMES:
        item["reason"] = "suspicious_output_artifact"
        item["suggested_role"] = role
        skipped.append(item)
        return

    if role != "evidence":
        try:
            content = Path(item["path"]).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            item["reason"] = "binary_or_unreadable"
            item["suggested_role"] = role
            skipped.append(item)
            return

        if _has_suspicious_content(content):
            item["reason"] = "suspicious_content_markers"
            item["suggested_role"] = role
            skipped.append(item)
            return

    target = {
        "evidence": evidence,
        "feedback": feedback,
        "instruction": instruction,
        "context": context,
    }.get(role)
    if target is not None:
        target.append(item)


def _content_item(entry: Path, flagged: bool = False) -> dict[str, Any]:
    item = base_item(entry)
    if flagged:
        item["flagged"] = True
    return item


def _skip_item(entry: Path, reason: str, suggested_role: str = "") -> dict[str, Any]:
    item = base_item(entry)
    item["reason"] = reason
    if suggested_role:
        item["suggested_role"] = suggested_role
    return item


def _document_skip_item(entry: Path, suggested_role: str) -> dict[str, Any]:
    item = _skip_item(entry, "needs_document_extraction", suggested_role=suggested_role)
    extracted = extracted_markdown_path(entry)
    if extracted.exists():
        item["reason"] = "document_extracted"
        item["extracted_markdown"] = str(extracted)
    else:
        item["extract_with"] = "multi-agent-brief inputs extract"
    return item


def _keyword_role(filename: str) -> str | None:
    """Detect role from filename keywords."""
    name_lower = filename.lower()
    for kw in FEEDBACK_KEYWORDS:
        if kw in name_lower:
            return "feedback"
    for kw in INSTRUCTION_KEYWORDS:
        if kw in name_lower:
            return "instruction"
    for kw in CONTEXT_KEYWORDS:
        if kw in name_lower:
            return "context"
    return None


def _has_suspicious_content(content: str) -> bool:
    """Check if content contains markers suggesting it is an old output artifact."""
    if not content:
        return False
    head = content[:2000].lower() if len(content) > 2000 else content.lower()
    for marker in SUSPICIOUS_CONTENT_MARKERS:
        if marker.lower() in head:
            return True
    return False

