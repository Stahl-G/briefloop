"""Authoritative workspace-contained artifact contract path resolution."""

from __future__ import annotations

from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Mapping

from multi_agent_brief.contracts.agent_artifact_intake import (
    AGENT_ARTIFACT_IDS,
    AgentArtifactId,
)
from multi_agent_brief.orchestrator.runtime_state.errors import (
    E_TRANSACTION_INTEGRITY,
    RuntimeStateError,
)


def workspace_artifact_path(
    workspace: Path,
    raw_path: str | Path,
    *,
    artifact_id: str,
    binding_source: str,
) -> Path:
    """Resolve one artifact path without allowing workspace escape."""

    normalized = validate_workspace_relative_artifact_path(
        raw_path,
        artifact_id=artifact_id,
        binding_source=binding_source,
    )
    raw = str(raw_path).strip()
    workspace_root = workspace.expanduser().resolve(strict=False)
    candidate = (workspace_root / normalized).resolve(strict=False)
    try:
        candidate.relative_to(workspace_root)
    except ValueError:
        raise _unsafe_artifact_path_error(
            artifact_id=artifact_id,
            binding_source=binding_source,
            raw_path=raw,
            workspace=workspace_root,
        ) from None
    return candidate


def validate_workspace_relative_artifact_path(
    raw_path: str | Path,
    *,
    artifact_id: str,
    binding_source: str,
) -> str:
    """Validate the lexical workspace-relative artifact path contract."""

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
        raise _unsafe_artifact_path_error(
            artifact_id=artifact_id,
            binding_source=binding_source,
            raw_path=raw,
        )
    return canonical


def _unsafe_artifact_path_error(
    *,
    artifact_id: str,
    binding_source: str,
    raw_path: str,
    workspace: Path | None = None,
) -> RuntimeStateError:
    details = {
        "artifact_id": artifact_id,
        "binding_source": binding_source,
        "path": raw_path,
    }
    if workspace is not None:
        details["workspace"] = str(workspace)
    return RuntimeStateError(
        "Artifact path must be workspace-relative and contained by the workspace.",
        details=details,
        error_code=E_TRANSACTION_INTEGRITY,
    )


def artifact_path_from_contracts(
    workspace: Path,
    artifacts_by_id: Mapping[str, Mapping[str, Any]],
    *,
    artifact_id: str,
    default_path: Path | None = None,
) -> Path | None:
    """Resolve one artifact path from the authoritative contract map."""

    artifact = artifacts_by_id.get(artifact_id)
    raw_path: str | Path | None = None
    if isinstance(artifact, Mapping):
        candidate = artifact.get("path")
        if "path" in artifact and (
            not isinstance(candidate, str) or not candidate.strip()
        ):
            raise RuntimeStateError(
                "Artifact contract path must be a non-empty string.",
                details={
                    "artifact_id": artifact_id,
                    "binding_source": "artifact_contract",
                    "path": candidate,
                },
                error_code=E_TRANSACTION_INTEGRITY,
            )
        if isinstance(candidate, str):
            raw_path = candidate
    if raw_path is None:
        raw_path = default_path
    if raw_path is None:
        return None
    return workspace_artifact_path(
        workspace,
        raw_path,
        artifact_id=artifact_id,
        binding_source="artifact_contract",
    )


def agent_artifact_paths_from_contracts(
    workspace: Path,
    artifacts_by_id: Mapping[str, Mapping[str, Any]],
) -> dict[AgentArtifactId, Path]:
    """Resolve the one contract-bound path map used by intake consumers."""

    paths: dict[AgentArtifactId, Path] = {}
    for artifact_id in AGENT_ARTIFACT_IDS:
        path = artifact_path_from_contracts(
            workspace,
            artifacts_by_id,
            artifact_id=artifact_id,
        )
        if path is not None:
            paths[artifact_id] = path
    return paths
