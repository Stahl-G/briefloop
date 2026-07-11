"""Authoritative workspace-contained artifact contract path resolution."""

from __future__ import annotations

from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Mapping

from multi_agent_brief.orchestrator.runtime_state.errors import (
    E_TRANSACTION_INTEGRITY,
    RuntimeStateError,
)


def validate_workspace_relative_artifact_path(
    raw_path: str | Path,
    *,
    artifact_id: str,
    binding_source: str,
) -> str:
    """Return one canonical workspace-relative artifact identity."""

    raw = str(raw_path).strip()
    normalized = raw.replace("\\", "/")
    posix_path = PurePosixPath(normalized)
    windows_path = PureWindowsPath(raw)
    canonical = posix_path.as_posix()
    unsafe = (
        not raw
        or canonical == "."
        or raw.startswith("~")
        or Path(raw).is_absolute()
        or posix_path.is_absolute()
        or windows_path.is_absolute()
        or bool(windows_path.drive)
        or ".." in posix_path.parts
        or ".." in windows_path.parts
    )
    if unsafe:
        raise RuntimeStateError(
            "Artifact path must be workspace-relative and contained by the workspace.",
            details={
                "artifact_id": artifact_id,
                "binding_source": binding_source,
                "path": raw,
            },
            error_code=E_TRANSACTION_INTEGRITY,
        )
    return canonical


def workspace_artifact_path(
    workspace: Path,
    raw_path: str | Path,
    *,
    artifact_id: str,
    binding_source: str,
) -> Path:
    """Resolve one artifact path without changing its canonical identity."""

    normalized = validate_workspace_relative_artifact_path(
        raw_path,
        artifact_id=artifact_id,
        binding_source=binding_source,
    )
    workspace_root = workspace.expanduser().resolve(strict=False)
    logical_candidate = workspace_root / normalized
    resolved_candidate = logical_candidate.resolve(strict=False)
    try:
        resolved_candidate.relative_to(workspace_root)
    except ValueError:
        raise RuntimeStateError(
            "Artifact path must be workspace-relative and contained by the workspace.",
            details={
                "artifact_id": artifact_id,
                "binding_source": binding_source,
                "path": normalized,
                "workspace": str(workspace_root),
            },
            error_code=E_TRANSACTION_INTEGRITY,
        ) from None
    if resolved_candidate != logical_candidate:
        raise RuntimeStateError(
            "Artifact path must not change identity through symlink resolution.",
            details={
                "artifact_id": artifact_id,
                "binding_source": binding_source,
                "path": normalized,
                "resolved_path": str(resolved_candidate),
            },
            error_code=E_TRANSACTION_INTEGRITY,
        )
    return logical_candidate


def artifact_path_from_contracts(
    workspace: Path,
    artifacts_by_id: Mapping[str, Mapping[str, Any]],
    *,
    artifact_id: str,
) -> Path:
    """Resolve one required artifact path from the authoritative contract map."""

    artifact = artifacts_by_id.get(artifact_id)
    candidate = artifact.get("path") if isinstance(artifact, Mapping) else None
    if not isinstance(candidate, str) or not candidate.strip():
        raise RuntimeStateError(
            "Artifact contract path must be a non-empty string.",
            details={
                "artifact_id": artifact_id,
                "binding_source": "artifact_contract",
                "path": candidate,
            },
            error_code=E_TRANSACTION_INTEGRITY,
        )
    return workspace_artifact_path(
        workspace,
        candidate,
        artifact_id=artifact_id,
        binding_source="artifact_contract",
    )


def artifact_paths_from_contracts(
    workspace: Path,
    artifacts_by_id: Mapping[str, Mapping[str, Any]],
) -> dict[str, Path]:
    """Resolve the complete authoritative artifact path context once."""

    return {
        artifact_id: artifact_path_from_contracts(
            workspace,
            artifacts_by_id,
            artifact_id=artifact_id,
        )
        for artifact_id in artifacts_by_id
    }
