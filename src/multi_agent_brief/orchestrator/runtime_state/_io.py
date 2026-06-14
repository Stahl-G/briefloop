"""Runtime-state low-level IO helpers."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Any

import yaml

from multi_agent_brief.orchestrator.runtime_state.errors import (
    E_TRANSACTION_PARTIAL_WRITE,
    RuntimeStateError,
)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeStateError(
            f"Invalid JSON state file: {path}",
            details={"path": str(path), "reason": str(exc)},
        ) from exc
    except OSError as exc:
        raise RuntimeStateError(
            f"Failed to read state file: {path}",
            details={"path": str(path), "reason": str(exc)},
        ) from exc
    if not isinstance(data, dict):
        raise RuntimeStateError(
            f"State file must contain a JSON object: {path}",
            details={"path": str(path)},
        )
    return data


def _read_json_if_exists(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return _read_json(path)


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    text += "\n"
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise RuntimeStateError(
            f"Failed to write state file: {path}",
            details={"path": str(path), "reason": str(exc)},
        ) from exc


def _read_state_bytes(path: Path) -> bytes | None:
    try:
        if not path.exists():
            return None
        return path.read_bytes()
    except OSError as exc:
        raise RuntimeStateError(
            f"Failed to snapshot state file: {path}",
            details={"path": str(path), "reason": str(exc)},
        ) from exc


def _restore_state_bytes(path: Path, data: bytes | None) -> None:
    try:
        if data is None:
            path.unlink(missing_ok=True)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.rollback.tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)
    except OSError as exc:
        raise RuntimeStateError(
            f"Failed to restore state file after partial write: {path}",
            details={"path": str(path), "reason": str(exc)},
            error_code=E_TRANSACTION_PARTIAL_WRITE,
        ) from exc


def _snapshot_state_files(paths: dict[str, Path], keys: tuple[str, ...]) -> dict[str, bytes | None]:
    return {key: _read_state_bytes(paths[key]) for key in keys}


def _restore_state_files(paths: dict[str, Path], snapshots: dict[str, bytes | None]) -> None:
    rollback_errors: list[dict[str, str]] = []
    for key, data in snapshots.items():
        try:
            _restore_state_bytes(paths[key], data)
        except RuntimeStateError as exc:
            rollback_errors.append({
                "key": key,
                "path": str(paths[key]),
                "reason": str(exc),
            })
    if rollback_errors:
        raise RuntimeStateError(
            "Runtime state rollback failed after partial write.",
            details={"rollback_errors": rollback_errors},
            error_code=E_TRANSACTION_PARTIAL_WRITE,
        )


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n"
    try:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line)
    except OSError as exc:
        raise RuntimeStateError(
            f"Failed to append event log: {path}",
            details={"path": str(path), "reason": str(exc)},
        ) from exc


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise RuntimeStateError(
            f"Invalid YAML contract file: {path}",
            details={"path": str(path), "reason": str(exc)},
        ) from exc
    except OSError as exc:
        raise RuntimeStateError(
            f"Failed to read contract file: {path}",
            details={"path": str(path), "reason": str(exc)},
        ) from exc
    if not isinstance(data, dict):
        raise RuntimeStateError(
            f"Contract file must contain a mapping: {path}",
            details={"path": str(path)},
        )
    return data


def _load_workspace_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}
