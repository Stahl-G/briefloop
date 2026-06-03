"""Conversational onboarding protocol for multi-agent-brief-workflow."""
from multi_agent_brief.onboarding.schema import OnboardingResult
from multi_agent_brief.onboarding.io import load_onboarding_result, save_onboarding_result
from multi_agent_brief.onboarding.mapper import map_onboarding_to_profile

__all__ = [
    "OnboardingResult",
    "load_onboarding_result",
    "save_onboarding_result",
    "map_onboarding_to_profile",
]
