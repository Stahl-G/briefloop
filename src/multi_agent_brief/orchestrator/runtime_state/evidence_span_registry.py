"""Runtime source-pack validation helpers for Evidence Span Registry artifacts."""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
from typing import Any

from multi_agent_brief.orchestrator.source_evidence import is_evidence_input_path


EVIDENCE_SPAN_REGISTRY_VALIDATION_PREFIX = "evidence_span_registry_validation_error"


def validate_evidence_span_registry_against_source_pack(
    *,
    registry_payload: dict[str, Any],
    workspace: Path,
) -> str | None:
    """Return the first deterministic source-pack validation reason, if any.

    This helper validates only machine-checkable source byte binding. It does
    not judge whether a source span supports a claim or atom.
    """

    for source in registry_payload.get("sources", []):
        if not isinstance(source, dict):
            continue
        source_id = _source_id(source)
        source_path = source.get("source_path")
        if not isinstance(source_path, str) or not source_path.strip():
            return f"source_path_missing:{source_id}"

        source_file, reason = _source_file_for_path(
            workspace=workspace,
            source_path=source_path,
            source_id=source_id,
        )
        if reason:
            return reason
        assert source_file is not None

        source_text, source_payload, reason = _read_source_text(source_file=source_file, source_id=source_id)
        if reason:
            return reason

        embedded_source_id = source_payload.get("source_id") if isinstance(source_payload, dict) else None
        if isinstance(embedded_source_id, str) and embedded_source_id.strip() and embedded_source_id.strip() != source_id:
            return f"source_id_mismatch:{source_id}"

        for span in source.get("spans", []):
            if not isinstance(span, dict):
                continue
            reason = _validate_span_against_text(span=span, source_text=source_text, source_id=source_id)
            if reason:
                return reason

    return None


def _source_id(source: dict[str, Any]) -> str:
    source_id = source.get("source_id")
    if isinstance(source_id, str) and source_id.strip():
        return source_id.strip()
    return "<unknown_source>"


def _source_file_for_path(
    *,
    workspace: Path,
    source_path: str,
    source_id: str,
) -> tuple[Path | None, str | None]:
    normalized = source_path.strip()
    if "\\" in normalized:
        return None, f"source_path_unsafe:{source_id}"
    posix_path = PurePosixPath(normalized)
    if posix_path.is_absolute() or ".." in posix_path.parts:
        return None, f"source_path_unsafe:{source_id}"
    if not posix_path.parts[:2] == ("input", "sources"):
        return None, f"source_path_unsafe:{source_id}"

    ws = workspace.expanduser().resolve()
    source_root = (ws / "input" / "sources").resolve()
    candidate = ws.joinpath(*posix_path.parts)
    if not candidate.exists():
        return None, f"source_file_missing:{source_id}"
    resolved = candidate.resolve()
    try:
        resolved.relative_to(source_root)
    except ValueError:
        return None, f"source_path_unsafe:{source_id}"
    if not candidate.is_file():
        return None, f"source_file_missing:{source_id}"
    if not is_evidence_input_path(candidate, ws):
        return None, f"source_path_not_evidence:{source_id}"
    return candidate, None


def _read_source_text(
    *,
    source_file: Path,
    source_id: str,
) -> tuple[str, dict[str, Any] | None, str | None]:
    try:
        raw_text = source_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return "", None, f"source_file_unreadable:{source_id}"

    if source_file.suffix.lower() == ".json":
        try:
            payload = json.loads(raw_text)
        except json.JSONDecodeError:
            return raw_text, None, None
        if isinstance(payload, dict) and isinstance(payload.get("content"), str):
            return payload["content"], payload, None
        return raw_text, payload if isinstance(payload, dict) else None, None

    return raw_text, None, None


def _validate_span_against_text(
    *,
    span: dict[str, Any],
    source_text: str,
    source_id: str,
) -> str | None:
    span_id = span.get("span_id")
    stable_span_id = span_id.strip() if isinstance(span_id, str) and span_id.strip() else f"{source_id}:<unknown_span>"
    raw_excerpt = span.get("raw_excerpt")
    if not isinstance(raw_excerpt, str):
        return f"span_excerpt_not_found:{stable_span_id}"
    if raw_excerpt not in source_text:
        return f"span_excerpt_not_found:{stable_span_id}"

    has_start = "char_start" in span
    has_end = "char_end" in span
    if has_start != has_end:
        return f"span_offset_incomplete:{stable_span_id}"
    if has_start and has_end:
        char_start = span.get("char_start")
        char_end = span.get("char_end")
        if not isinstance(char_start, int) or not isinstance(char_end, int):
            return f"span_offset_mismatch:{stable_span_id}"
        if source_text[char_start:char_end] != raw_excerpt:
            return f"span_offset_mismatch:{stable_span_id}"

    return None
