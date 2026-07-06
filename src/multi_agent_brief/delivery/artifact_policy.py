"""Reader delivery artifact policy shared by finalize and delivery consumers."""

from __future__ import annotations

from pathlib import Path


def reader_delivery_artifact_kind(path: str | Path) -> str:
    """Return the supported reader delivery kind, or an empty string."""

    artifact = Path(path)
    if artifact.name == "brief.md":
        return "reader_markdown"
    if artifact.suffix.lower() == ".docx":
        return "reader_docx"
    return ""


def reader_delivery_artifact_policy_text() -> str:
    return "reader delivery artifacts must be output/delivery/brief.md or .docx files"
