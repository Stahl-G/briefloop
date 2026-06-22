"""secrets — deterministic workspace secret import helpers."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path

from multi_agent_brief.core.env import KNOWN_WORKSPACE_ENV_KEYS, _parse_env_line


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


def handle(args: argparse.Namespace) -> int:
    """Dispatch secrets subcommands."""
    if args.secrets_action == "import":
        return _handle_import(args)
    return 1


def _handle_import(args: argparse.Namespace) -> int:
    try:
        result = import_workspace_secrets(
            workspace=Path(args.workspace).expanduser(),
            source=Path(args.from_path).expanduser(),
            keys=list(args.keys),
        )
    except SecretImportError as exc:
        payload = {"ok": False, "error": str(exc)}
        if getattr(args, "json", False):
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        else:
            print(f"[secrets] error: {exc}")
        return 1

    if getattr(args, "json", False):
        print(json.dumps(
            {
                "ok": True,
                "workspace_env": result["workspace_env"],
                "keys": result["keys"],
            },
            ensure_ascii=False,
            sort_keys=True,
        ))
        return 0

    for item in result["keys"]:
        status = item["status"]
        prefix = item.get("sha256_prefix", "")
        prefix_part = f" sha256_prefix={prefix}" if prefix else ""
        print(f"{item['key']}={status}{prefix_part}")
    return 0


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


def _require_existing_workspace(workspace: Path) -> None:
    if not workspace.exists():
        raise SecretImportError(
            f"workspace not found: {workspace}. Run briefloop new or multi-agent-brief init first."
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
    if path.exists():
        try:
            existing_lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise SecretImportError(f"unable to read workspace env file: {path}") from exc

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

    try:
        path.write_text("\n".join(updated_lines).rstrip() + "\n", encoding="utf-8")
        os.chmod(path, 0o600)
    except OSError as exc:
        raise SecretImportError(f"unable to write workspace env file: {path}") from exc


def _quote_env_value(value: str) -> str:
    if not value:
        return ""
    if re.search(r"\s|#|['\"]", value):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value
