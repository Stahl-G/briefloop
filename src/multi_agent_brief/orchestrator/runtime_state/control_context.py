"""Fail-closed loaders for runtime control records."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from multi_agent_brief.orchestrator.runtime_state.errors import (
    E_TRANSACTION_INTEGRITY,
    RuntimeStateError,
)


def load_control_object(
    path: str | Path,
    *,
    expected_schema: str | None = None,
    required: bool = True,
) -> dict[str, Any] | None:
    """Load one JSON control object and validate its optional schema."""

    control_path = Path(path)
    if not control_path.exists():
        if not required:
            return None
        raise RuntimeStateError(
            f"Required control file is missing: {control_path}",
            details={"path": str(control_path), "reason_code": "control_file_missing"},
            error_code=E_TRANSACTION_INTEGRITY,
        )
    try:
        payload = json.loads(control_path.read_text(encoding="utf-8"))
    except UnicodeDecodeError as exc:
        raise RuntimeStateError(
            f"Control file is not valid UTF-8: {control_path}",
            details={"path": str(control_path), "reason": str(exc)},
            error_code=E_TRANSACTION_INTEGRITY,
        ) from exc
    except json.JSONDecodeError as exc:
        raise RuntimeStateError(
            f"Control file is not valid JSON: {control_path}",
            details={"path": str(control_path), "reason": str(exc)},
            error_code=E_TRANSACTION_INTEGRITY,
        ) from exc
    except OSError as exc:
        raise RuntimeStateError(
            f"Control file could not be read: {control_path}",
            details={"path": str(control_path), "reason": str(exc)},
            error_code=E_TRANSACTION_INTEGRITY,
        ) from exc
    if not isinstance(payload, dict):
        raise RuntimeStateError(
            f"Control file must contain an object: {control_path}",
            details={"path": str(control_path), "reason_code": "control_file_not_object"},
            error_code=E_TRANSACTION_INTEGRITY,
        )
    if expected_schema is not None and payload.get("schema_version") != expected_schema:
        raise RuntimeStateError(
            f"Control file has an unsupported schema: {control_path}",
            details={
                "path": str(control_path),
                "expected_schema": expected_schema,
                "schema_version": payload.get("schema_version"),
            },
            error_code=E_TRANSACTION_INTEGRITY,
        )
    return payload
