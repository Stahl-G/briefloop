"""Central workspace authority classification for active SQLite commands."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import stat
from typing import Literal


LEGACY_CONTROL_PATHS = (
    "output/intermediate/runtime_manifest.json",
    "output/intermediate/workflow_state.json",
    "output/intermediate/artifact_registry.json",
    "output/intermediate/event_log.jsonl",
    "output/intermediate/finalize_report.json",
)

SQLITE_ACTIVE_COMMANDS = frozenset(
    {
        "run",
        "runtime",
        "status",
        "quality",
        "core-v2",
        "intake-v2",
    }
)


@dataclass(frozen=True)
class WorkspaceAuthority:
    kind: Literal["fresh", "sqlite", "legacy", "invalid_sqlite"]
    database_path: Path


def classify_workspace_authority(workspace: Path) -> WorkspaceAuthority:
    database = workspace / "briefloop.db"
    try:
        mode = database.lstat().st_mode
    except FileNotFoundError:
        mode = None
    except OSError:
        return WorkspaceAuthority("invalid_sqlite", database)
    if mode is not None:
        if not stat.S_ISREG(mode):
            return WorkspaceAuthority("invalid_sqlite", database)
        return WorkspaceAuthority("sqlite", database)
    for relative in LEGACY_CONTROL_PATHS:
        try:
            (workspace / relative).lstat()
        except FileNotFoundError:
            continue
        except OSError:
            return WorkspaceAuthority("legacy", database)
        return WorkspaceAuthority("legacy", database)
    return WorkspaceAuthority("fresh", database)


def active_command_authority_error(
    workspace: Path,
    command: str,
) -> str | None:
    """Fail closed before dispatch when a workspace has the wrong authority."""

    authority = classify_workspace_authority(workspace)
    if authority.kind == "legacy":
        return "legacy_workspace_unsupported"
    if authority.kind == "invalid_sqlite":
        return "control_store_integrity_invalid"
    if authority.kind == "sqlite" and command not in SQLITE_ACTIVE_COMMANDS:
        return "runtime_command_unsupported"
    return None


__all__ = [
    "LEGACY_CONTROL_PATHS",
    "SQLITE_ACTIVE_COMMANDS",
    "WorkspaceAuthority",
    "active_command_authority_error",
    "classify_workspace_authority",
]
