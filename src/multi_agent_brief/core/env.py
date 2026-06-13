"""Safe workspace environment helpers."""
from __future__ import annotations

import os
from pathlib import Path


KNOWN_WORKSPACE_ENV_KEYS = frozenset(
    {
        "TAVILY_API_KEY",
        "EXA_API_KEY",
        "BRAVE_SEARCH_API_KEY",
        "FIRECRAWL_API_KEY",
        "SERPER_API_KEY",
        "NEWSAPI_API_KEY",
        "MINERU_API_TOKEN",
    }
)


def _parse_env_line(line: str) -> tuple[str, str] | None:
    text = line.strip()
    if not text or text.startswith("#") or "=" not in text:
        return None
    if text.startswith("export "):
        text = text[len("export "):].lstrip()
    key, value = text.split("=", 1)
    key = key.strip()
    if key not in KNOWN_WORKSPACE_ENV_KEYS:
        return None
    value = value.strip()
    if (
        len(value) >= 2
        and value[0] == value[-1]
        and value[0] in {"'", '"'}
    ):
        value = value[1:-1]
    return key, value


def read_workspace_env_key(workspace_dir: str | Path | None, key: str) -> str:
    """Return a known env key from workspace .env without exporting it."""
    if key not in KNOWN_WORKSPACE_ENV_KEYS or not workspace_dir:
        return ""
    path = Path(workspace_dir) / ".env"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    for line in lines:
        parsed = _parse_env_line(line)
        if not parsed:
            continue
        parsed_key, value = parsed
        if parsed_key == key and value:
            return value
    return ""


def get_known_env_value(key: str, workspace_dir: str | Path | None = None) -> str:
    """Return shell env value first, then known workspace .env value."""
    if key not in KNOWN_WORKSPACE_ENV_KEYS:
        return ""
    return os.environ.get(key, "") or read_workspace_env_key(workspace_dir, key)


def known_env_key_is_set(key: str, workspace_dir: str | Path | None = None) -> bool:
    """Return whether a known key is set without exposing its value."""
    return bool(get_known_env_value(key, workspace_dir))
