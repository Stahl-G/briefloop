"""Workspace-local audience taste memory runtime surface."""

from multi_agent_brief.audience_memory.profile import (
    AUDIENCE_MEMORY_FILES,
    AudienceProfileResult,
    AudienceSnapshotResult,
    build_default_audience_profile,
    create_audience_profile_snapshot,
    ensure_audience_profile,
    profile_data_from_object,
    profile_data_from_workspace_config,
)

__all__ = [
    "AUDIENCE_MEMORY_FILES",
    "AudienceProfileResult",
    "AudienceSnapshotResult",
    "build_default_audience_profile",
    "create_audience_profile_snapshot",
    "ensure_audience_profile",
    "profile_data_from_object",
    "profile_data_from_workspace_config",
]
