"""Shared contracts for workspace input governance."""

from __future__ import annotations

from pathlib import Path
from typing import Any


DIR_ROLES: dict[str, str] = {
    "sources": "evidence",
    "feedback": "feedback",
    "instructions": "instruction",
    "context": "context",
}

NON_EVIDENCE_SUBDIRS = {"feedback", "instructions", "context"}

RECOGNISED_EVIDENCE_SUFFIXES = {".md", ".txt", ".json", ".yaml", ".yml", ".csv"}

MINERU_EXTRACTABLE_SUFFIXES = {
    ".pdf",
    ".docx",
    ".doc",
    ".pptx",
    ".xlsx",
    ".jpg",
    ".jpeg",
    ".png",
    ".tif",
    ".tiff",
    ".bmp",
    ".webp",
}

SCANNABLE_SUFFIXES = RECOGNISED_EVIDENCE_SUFFIXES | MINERU_EXTRACTABLE_SUFFIXES


def extracted_markdown_path(path: Path) -> Path:
    """Return the adjacent Markdown path for a MinerU-extracted input file."""
    suffix_tag = path.suffix.lower().replace(".", "_")
    return path.with_name(f"{path.stem}{suffix_tag}.mineru.md")


def safe_rel(path: Path, base: Path) -> str:
    """Return path relative to base for reports, falling back to the name."""
    try:
        return path.resolve().relative_to(base.resolve()).as_posix()
    except ValueError:
        return path.name


def base_item(entry: Path) -> dict[str, Any]:
    """Return common file metadata for classification reports."""
    try:
        size = entry.stat().st_size
    except OSError:
        size = 0
    return {"path": str(entry), "name": entry.name, "bytes": size}

