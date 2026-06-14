"""Runtime-state identity and version helpers."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from multi_agent_brief import __version__
from multi_agent_brief.orchestrator.runtime_state.errors import RuntimeStateError


MAX_RUN_ID_LENGTH = 200
_RUN_ID_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_RUN_ID_WINDOWS_ABSOLUTE_RE = re.compile(r"\b[A-Za-z]:[\\/]")
_RUN_ID_TOKEN_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"ghp_[A-Za-z0-9_]{16,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"\b[A-Za-z0-9_-]{40,}\b"),
]
_RUN_ID_FORBIDDEN_PATH_FRAGMENTS = ("/Users/", "/home/", "/var/", "file://")
_RUN_ID_INJECTION_PHRASES = ("system:", "developer:", "assistant:", "ignore previous", "ignore all previous")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def new_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"mabw-{stamp}-{uuid.uuid4().hex[:8]}"


def _validate_runtime_run_id(value: Any, *, path: Path | None = None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RuntimeStateError(
            "runtime run_id is required.",
            details={"path": str(path) if path is not None else None},
        )
    text = value.strip()
    if _unsafe_runtime_run_id(text):
        raise RuntimeStateError(
            "runtime run_id is unsafe.",
            details={"path": str(path) if path is not None else None},
        )
    return text


def _safe_previous_run_id(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if _unsafe_runtime_run_id(text):
        return "unsafe-run-id"
    return text


def _unsafe_runtime_run_id(text: str) -> bool:
    lower = text.lower()
    return (
        len(text) > MAX_RUN_ID_LENGTH
        or "\n" in text
        or "\r" in text
        or "/" in text
        or "\\" in text
        or text.lstrip().startswith("#")
        or "```" in text
        or "~~~" in text
        or "<!--" in text
        or "-->" in text
        or bool(_RUN_ID_CONTROL_CHAR_RE.search(text))
        or bool(_RUN_ID_WINDOWS_ABSOLUTE_RE.search(text))
        or any(fragment.lower() in lower for fragment in _RUN_ID_FORBIDDEN_PATH_FRAGMENTS)
        or any(pattern.search(text) for pattern in _RUN_ID_TOKEN_PATTERNS)
        or any(phrase in lower for phrase in _RUN_ID_INJECTION_PHRASES)
    )


def _source_or_package_version() -> str:
    for parent in Path(__file__).resolve().parents:
        version_file = parent / "VERSION"
        if version_file.exists():
            text = version_file.read_text(encoding="utf-8").strip()
            if text:
                return text
    return __version__
