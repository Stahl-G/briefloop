"""Re-export shim for the relocated artifact path resolver (LD2-2b).

The definitions now live in :mod:`multi_agent_brief.contracts.artifact_paths`.
This shim keeps legacy runtime_state imports working until LD2-3 deletes the
stack. It must not define anything of its own.
"""

from __future__ import annotations

from multi_agent_brief.contracts.artifact_paths import *  # noqa: F401,F403
from multi_agent_brief.contracts.artifact_paths import (  # noqa: F401
    agent_artifact_paths_from_contracts,
    artifact_path_from_contracts,
    artifact_paths_from_contracts,
    validate_workspace_relative_artifact_path,
    workspace_artifact_path,
)
