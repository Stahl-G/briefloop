"""secrets — deterministic workspace secret import helpers."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import stat
import tempfile

from multi_agent_brief.core.env import (
    KNOWN_WORKSPACE_ENV_KEYS,
    MAX_WORKSPACE_ENV_BYTES,
    WorkspaceEnvError,
    _parse_env_line,
    _read_workspace_env_bytes,
)


_SAFE_ENV_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


class SecretImportError(Exception):
    """Raised when workspace secret import cannot complete safely."""


def register(subparsers: argparse._SubParsersAction) -> None:
    """Register the secrets subcommand group."""
    secrets_parser = subparsers.add_parser(
        "secrets",
        help="Safely import allowlisted secrets into a workspace .env file.",
    )
    secrets_sub = secrets_parser.add_subparsers(
        dest="secrets_action",
        required=True,
    )

    import_parser = secrets_sub.add_parser(
        "import",
        help="Import allowlisted keys from a private env file into workspace .env.",
    )
    import_parser.add_argument(
        "--workspace",
        required=True,
        help="Workspace directory whose .env file should be written.",
    )
    import_parser.add_argument(
        "--from",
        dest="from_path",
        required=True,
        help="Private env file to read, for example ~/.env.",
    )
    import_parser.add_argument(
        "--keys",
        nargs="+",
        required=True,
        help="Allowlisted env keys to import, for example TAVILY_API_KEY.",
    )
    import_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit redacted machine-readable import results.",
    )


def _normalize_keys(keys: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in keys:
        key = str(raw or "").strip()
        if not key:
            continue
        if not _SAFE_ENV_KEY_RE.match(key):
            raise SecretImportError(f"invalid secret key name: {key}")
        if key not in seen:
            normalized.append(key)
            seen.add(key)
    return normalized


def _read_known_env_values(path: Path) -> dict[str, str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise SecretImportError(f"unable to read source env file: {path}") from exc
    values: dict[str, str] = {}
    for line in lines:
        parsed = _parse_env_line(line)
        if not parsed:
            continue
        key, value = parsed
        if value:
            values[key] = value
    return values


def _write_workspace_env(path: Path, values: dict[str, str]) -> None:
    existing_lines: list[str] = []
    try:
        existing = _read_workspace_env_bytes(path.parent)
    except FileNotFoundError:
        existing = None
    except WorkspaceEnvError as exc:
        raise SecretImportError("workspace secret target is unsafe") from exc
    if existing is not None:
        try:
            existing_lines = existing.decode("utf-8").splitlines()
        except UnicodeDecodeError as exc:
            raise SecretImportError("workspace secret target is unsafe") from exc

    updated_lines: list[str] = []
    replaced: set[str] = set()
    for line in existing_lines:
        parsed = _parse_env_line(line)
        if parsed and parsed[0] in values:
            key = parsed[0]
            updated_lines.append(f"{key}={_quote_env_value(values[key])}")
            replaced.add(key)
        else:
            updated_lines.append(line)

    for key in values:
        if key not in replaced:
            updated_lines.append(f"{key}={_quote_env_value(values[key])}")

    payload = ("\n".join(updated_lines).rstrip() + "\n").encode("utf-8")
    if not payload or len(payload) > MAX_WORKSPACE_ENV_BYTES:
        raise SecretImportError("workspace secret target is unsafe")
    _replace_workspace_env(path, payload)


def _replace_workspace_env(path: Path, payload: bytes) -> None:
    descriptor = -1
    temporary: Path | None = None
    directory_descriptor = -1
    try:
        parent_metadata = path.parent.lstat()
        if path.parent.is_symlink() or not stat.S_ISDIR(parent_metadata.st_mode):
            raise SecretImportError("workspace secret target is unsafe")
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".briefloop-env-",
            dir=path.parent,
        )
        temporary = Path(temporary_name)
        if os.name != "nt":
            os.fchmod(descriptor, 0o600)
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
            raise SecretImportError("workspace secret target is unsafe")
        offset = 0
        while offset < len(payload):
            count = os.write(descriptor, payload[offset:])
            if count <= 0:
                raise SecretImportError("workspace secret target is unsafe")
            offset += count
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, path)
        temporary = None
        if os.name != "nt":
            os.chmod(path, 0o600)
            directory_descriptor = os.open(
                path.parent,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
            )
            os.fsync(directory_descriptor)
    except SecretImportError:
        raise
    except OSError as exc:
        raise SecretImportError("workspace secret target is unsafe") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if directory_descriptor >= 0:
            os.close(directory_descriptor)
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass


def import_workspace_secrets(
    *,
    workspace: Path,
    source: Path,
    keys: list[str],
) -> dict[str, object]:
    """Import requested known keys from source env into workspace .env."""
    requested = _normalize_keys(keys)
    if not requested:
        raise SecretImportError("at least one key is required")
    unknown = [key for key in requested if key not in KNOWN_WORKSPACE_ENV_KEYS]
    if unknown:
        raise SecretImportError(f"unsupported secret key(s): {', '.join(unknown)}")

    if not source.exists():
        raise SecretImportError(f"source env file not found: {source}")
    if not source.is_file():
        raise SecretImportError(f"source env path is not a file: {source}")

    source_values = _read_known_env_values(source)
    missing = [key for key in requested if not source_values.get(key)]
    if missing:
        raise SecretImportError(
            f"requested key(s) missing from source env: {', '.join(missing)}"
        )

    _require_existing_workspace(workspace)
    target = workspace / ".env"
    _write_workspace_env(target, {key: source_values[key] for key in requested})

    return {
        "workspace_env": str(target),
        "keys": [
            {
                "key": key,
                "status": "present",
                "sha256_prefix": hashlib.sha256(
                    source_values[key].encode("utf-8")
                ).hexdigest()[:12],
            }
            for key in requested
        ],
    }


def store_workspace_secret(
    *,
    workspace: Path,
    key: str,
    value: str,
) -> None:
    """Store one allowlisted secret without returning or hashing its value."""

    requested = _normalize_keys([key])
    if requested != [key] or key not in KNOWN_WORKSPACE_ENV_KEYS:
        raise SecretImportError("unsupported secret key")
    if (
        not isinstance(value, str)
        or not value
        or any(character.isspace() for character in value)
    ):
        raise SecretImportError("secret value is invalid")
    _require_existing_workspace(workspace)
    _write_workspace_env(workspace / ".env", {key: value})


def _require_existing_workspace(workspace: Path) -> None:
    if not workspace.exists():
        raise SecretImportError(
            f"workspace not found: {workspace}. Run briefloop new or briefloop init first."
        )
    if not workspace.is_dir():
        raise SecretImportError(f"workspace path is not a directory: {workspace}")
    markers = [
        workspace / "config.yaml",
        workspace / "output" / "intermediate" / "runtime_manifest.json",
    ]
    if not any(marker.exists() for marker in markers):
        raise SecretImportError(
            f"not a BriefLoop workspace: {workspace}. Expected config.yaml or runtime manifest."
        )


def _quote_env_value(value: str) -> str:
    if not value:
        return ""
    if re.search(r"\s|#|['\"]", value):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value


def handle(args: argparse.Namespace) -> int:
    """Fail-closed stub for the retired public CLI surface.

    The parser registration is retained so the authority guard can return
    the typed rejection for workspace invocations; any no-workspace bypass
    lands here instead of executing legacy code.
    """

    print("runtime_command_unsupported")
    return 1


# NOTE: the public command surface of this module is retired. The
# SQLite ControlStore is the sole runtime authority; only the parser
# registration (typed rejections) and the stub below remain.
