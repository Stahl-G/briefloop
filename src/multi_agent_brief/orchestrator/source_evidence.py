"""Shared durable source-evidence path rules for runtime controls."""

from __future__ import annotations

from pathlib import Path


NON_EVIDENCE_INPUT_SUBDIRS = {"context", "feedback", "instructions"}
NON_EVIDENCE_INPUT_FILENAMES = {
    ".ds_store",
    ".gitkeep",
    ".keep",
    "readme",
    "readme.md",
    "readme.txt",
}
NON_EVIDENCE_INPUT_FILENAME_PARTS = ("placeholder", "template")


def is_evidence_input_path(path: Path, workspace: Path) -> bool:
    """Return whether an input path can count as durable evidence."""
    input_dir = (workspace / "input").resolve()
    resolved = path.resolve()
    try:
        relative = resolved.relative_to(input_dir)
    except ValueError:
        return True
    if relative.parts and relative.parts[0] in NON_EVIDENCE_INPUT_SUBDIRS:
        return False
    if any(part.startswith(".") for part in relative.parts):
        return False
    name = path.name.lower()
    if name in NON_EVIDENCE_INPUT_FILENAMES:
        return False
    if any(part in name for part in NON_EVIDENCE_INPUT_FILENAME_PARTS):
        return False
    if path.is_file():
        try:
            if path.stat().st_size == 0:
                return False
        except OSError:
            return False
    return True
