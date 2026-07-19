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


__all__ = [
    "LEGACY_CONTROL_PATHS",
    "WorkspaceAuthority",
    "classify_workspace_authority",
]
