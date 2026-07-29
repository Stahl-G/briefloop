"""Safe workspace environment helpers."""

from __future__ import annotations

import os
from pathlib import Path
import stat


MAX_WORKSPACE_ENV_BYTES = 1024 * 1024


class WorkspaceEnvError(Exception):
    """Value-free failure for an unsafe workspace credential file."""


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
        text = text[len("export ") :].lstrip()
    key, value = text.split("=", 1)
    key = key.strip()
    if key not in KNOWN_WORKSPACE_ENV_KEYS:
        return None
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    return key, value


def read_workspace_env_key(workspace_dir: str | Path | None, key: str) -> str:
    """Return a known env key from workspace .env without exporting it."""
    if key not in KNOWN_WORKSPACE_ENV_KEYS or not workspace_dir:
        return ""
    try:
        payload = _read_workspace_env_bytes(Path(workspace_dir))
    except FileNotFoundError:
        return ""
    try:
        lines = payload.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise WorkspaceEnvError("workspace_secret_unsafe") from exc
    for line in lines:
        parsed = _parse_env_line(line)
        if not parsed:
            continue
        parsed_key, value = parsed
        if parsed_key == key and value:
            return value
    return ""


def _read_workspace_env_bytes(workspace: Path) -> bytes:
    """Read one bounded regular single-link .env through its verified descriptor."""

    path = workspace / ".env"
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise WorkspaceEnvError("workspace_secret_unsafe") from exc
    if (
        path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_size > MAX_WORKSPACE_ENV_BYTES
    ):
        raise WorkspaceEnvError("workspace_secret_unsafe")
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
        )
        opened = os.fstat(descriptor)
        if (
            (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino)
            or not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or opened.st_size > MAX_WORKSPACE_ENV_BYTES
        ):
            raise WorkspaceEnvError("workspace_secret_unsafe")
        chunks: list[bytes] = []
        remaining = MAX_WORKSPACE_ENV_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) > MAX_WORKSPACE_ENV_BYTES:
            raise WorkspaceEnvError("workspace_secret_unsafe")
        return payload
    except WorkspaceEnvError:
        raise
    except OSError as exc:
        raise WorkspaceEnvError("workspace_secret_unsafe") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def get_known_env_value(key: str, workspace_dir: str | Path | None = None) -> str:
    """Return shell env value first, then known workspace .env value."""
    if key not in KNOWN_WORKSPACE_ENV_KEYS:
        return ""
    return os.environ.get(key, "") or read_workspace_env_key(workspace_dir, key)


def known_env_key_is_set(key: str, workspace_dir: str | Path | None = None) -> bool:
    """Return whether a known key is set without exposing its value."""
    return bool(get_known_env_value(key, workspace_dir))
