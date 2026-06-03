"""Tests for OnboardingResult → InitProfile mapping."""
from __future__ import annotations

from multi_agent_brief.onboarding.schema import OnboardingResult
from multi_agent_brief.onboarding.mapper import (
    map_onboarding_to_profile,
    normalize_industry,
    normalize_language,
    normalize_cadence,
    normalize_audience,
    normalize_source_profile,
)


def test_onboarding_mapper_management_weekly_en():
    result = OnboardingResult(
        target="exampleco-weekly",
        company_or_org="ExampleCo",
        industry_or_theme="renewable energy",
        audience_plain="management team",
        source_style_plain="reliable, but include sector news",
        language_plain="English",
        cadence_plain="weekly",
        must_watch=["ExampleCo", "policy", "competitors", "risk events"],
    )
    profile = map_onboarding_to_profile(result)
    assert profile.company == "ExampleCo"
    assert profile.industry == "energy"
    assert profile.audience == "management"
    assert profile.source_profile == "research"
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
    assert profile.source_profile == "research"
    assert profile.cadence == "weekly"
    assert profile.interface_language == "en-US"


def test_onboarding_mapper_source_style():
    conservative = OnboardingResult(source_style_plain="official filings and announcements")
    assert map_onboarding_to_profile(conservative).source_profile == "conservative"

    research = OnboardingResult(source_style_plain="reliable research and sector news")
    assert map_onboarding_to_profile(research).source_profile == "research"

    aggressive = OnboardingResult(source_style_plain="broad radar and social signals")
    assert map_onboarding_to_profile(aggressive).source_profile == "aggressive_signal"


def test_onboarding_mapper_bilingual():
    result = OnboardingResult(language_plain="bilingual")
    profile = map_onboarding_to_profile(result)
    assert profile.interface_language == "bilingual"
    assert profile.output_language == "bilingual"
