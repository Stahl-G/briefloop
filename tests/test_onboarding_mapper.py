"""Tests for OnboardingResult → InitProfile mapping."""
from __future__ import annotations

from multi_agent_brief.onboarding.schema import OnboardingResult
from multi_agent_brief.onboarding.mapper import map_onboarding_to_profile


def test_onboarding_mapper_management_weekly_en():
    result = OnboardingResult(
        target="exampleco-weekly",
        company_or_org="ExampleCo",
        industry_or_theme="manufacturing",
        audience_plain="management team",
        source_style_plain="reliable, but include sector news",
        language_plain="English",
        cadence_plain="weekly",
        must_watch=["ExampleCo", "policy", "competitors", "risk events"],
    )
    profile = map_onboarding_to_profile(result)
    assert profile.company == "ExampleCo"
    assert profile.industry == "manufacturing"
    assert profile.industry_text == "manufacturing"
    assert profile.audience == "management"
    assert profile.source_profile == "llm_decide"
    assert profile.interface_language == "en-US"
    assert profile.output_language == "en-US"
    assert profile.cadence == "weekly"


def test_onboarding_mapper_defaults():
    result = OnboardingResult(
        audience_plain="",
        source_style_plain="",
        language_plain="",
        cadence_plain="",
    )
    profile = map_onboarding_to_profile(result)
    assert profile.audience == "management"
    assert profile.source_profile == "llm_decide"
    assert profile.cadence == "weekly"
    assert profile.interface_language == "en-US"
