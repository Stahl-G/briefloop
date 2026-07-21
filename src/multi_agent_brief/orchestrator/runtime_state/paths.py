"""Runtime-state workspace path helpers."""

from __future__ import annotations

from pathlib import Path

from multi_agent_brief.orchestrator.runtime_state.errors import RuntimeStateError


# RUNTIME_STATE_FILES is owned by contracts.runtime_contracts (LD2-2b).
from multi_agent_brief.contracts.runtime_contracts import (  # noqa: E402
    RUNTIME_STATE_FILES,
)


def runtime_state_paths(workspace: str | Path) -> dict[str, Path]:
    ws = Path(workspace).expanduser().resolve()
    return {key: ws / rel_path for key, rel_path in RUNTIME_STATE_FILES.items()}


def _require_workspace(workspace: str | Path) -> Path:
    ws = Path(workspace).expanduser().resolve()
    if not (ws / "config.yaml").exists():
        raise RuntimeStateError(
            f"Workspace config.yaml not found: {ws / 'config.yaml'}",
            details={"workspace": str(ws)},
        )
    return ws


def _workspace_relative(workspace: Path, path: Path) -> str:
    resolved = path.expanduser().resolve()
    try:
        return resolved.relative_to(workspace).as_posix()
    except ValueError:
        return path.as_posix()
