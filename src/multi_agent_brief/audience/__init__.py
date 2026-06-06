"""Audience Profiles — deterministic configuration for brief structure and quality thresholds.

Each profile defines required sections, banned phrases, quality thresholds,
and default DOCX template. Profiles are mapped conservatively from free-text
onboarding input.
"""
from multi_agent_brief.audience.profiles import (
    AudienceProfile,
    get_profile,
    map_audience_to_profile,
    PROFILES,
)

__all__ = [
    "AudienceProfile",
    "get_profile",
    "map_audience_to_profile",
    "PROFILES",
]
