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


def test_onboarding_mapper_source_style():
    conservative = OnboardingResult(source_style_plain="official filings and announcements")
    assert map_onboarding_to_profile(conservative).source_profile == "conservative"

    research = OnboardingResult(source_style_plain="research")
    assert map_onboarding_to_profile(research).source_profile == "research"

    aggressive = OnboardingResult(source_style_plain="broad radar and social signals")
    assert map_onboarding_to_profile(aggressive).source_profile == "aggressive_signal"

    # Vague source style defaults to llm_decide
    vague = OnboardingResult(source_style_plain="reliable research and sector news")
    assert map_onboarding_to_profile(vague).source_profile == "llm_decide"


def test_onboarding_mapper_bilingual():
    result = OnboardingResult(language_plain="bilingual")
    profile = map_onboarding_to_profile(result)
    assert profile.interface_language == "bilingual"
    assert profile.output_language == "bilingual"


# ── Natural language tolerance tests ───────────────────────────────

def test_onboarding_mapper_natural_language_industry():
    """Substring matching handles registered pack keys."""
    assert normalize_industry("manufacturing sector") == "manufacturing"
    assert normalize_industry("banking regulation") == "banking"
    assert normalize_industry("fund management") == "fund"
    assert normalize_industry("internet platform") == "internet"
    assert normalize_industry("general research") == "general"


def test_onboarding_mapper_industry_returns_empty_for_unknown():
    """Unknown industry text returns empty string, not a guessed slug."""
    assert normalize_industry("technology sector") == ""
    assert normalize_industry("global finance outlook") == ""
    assert normalize_industry("renewable energy") == ""
    assert normalize_industry("some random industry") == ""


def test_onboarding_mapper_preserves_raw_industry_text():
    """Raw industry text is preserved in industry_text field."""
    result = OnboardingResult(industry_or_theme="光伏、HJT、储能、美国政策")
    profile = map_onboarding_to_profile(result)
    assert profile.industry_text == "光伏、HJT、储能、美国政策"
    assert profile.industry == ""  # no registered pack matches


def test_onboarding_mapper_natural_language_audience():
    """Substring matching handles natural-language audience phrases."""
    assert normalize_audience("for executive leadership team") == "management"
    assert normalize_audience("investment portfolio review") == "investment"
    assert normalize_audience("legal and compliance team") == "compliance"


def test_onboarding_mapper_natural_language_cadence():
    """Substring matching handles natural-language cadence phrases."""
    assert normalize_cadence("weekly management update") == "weekly"
    assert normalize_cadence("daily briefing") == "daily"
    assert normalize_cadence("monthly report") == "monthly"


def test_onboarding_mapper_natural_language_source_style():
    """Substring matching handles natural-language source style phrases."""
    assert normalize_source_profile("reliable sources but include sector news") == "llm_decide"
    assert normalize_source_profile("only official filings and announcements") == "conservative"
    assert normalize_source_profile("broad radar including social signals") == "aggressive_signal"
    # Explicit research style still works
    assert normalize_source_profile("research") == "research"


def test_onboarding_mapper_task_objective_preserved():
    """Task objective is preserved in profile."""
    result = OnboardingResult(task_objective="Weekly market intelligence for investment team")
    profile = map_onboarding_to_profile(result)
    assert profile.task_objective == "Weekly market intelligence for investment team"


def test_onboarding_mapper_forbidden_sources_preserved():
    """Forbidden sources are preserved in profile."""
    result = OnboardingResult(forbidden_sources=["internal chat", "customer data"])
    profile = map_onboarding_to_profile(result)
    assert "internal chat" in profile.forbidden_sources
    assert "customer data" in profile.forbidden_sources


def test_onboarding_mapper_optional_seed_pack():
    """When industry matches a registered pack, optional_seed_pack is set."""
    result = OnboardingResult(industry_or_theme="manufacturing")
    profile = map_onboarding_to_profile(result)
    assert profile.optional_seed_pack == "manufacturing"

    result_unknown = OnboardingResult(industry_or_theme="quantum computing")
    profile_unknown = map_onboarding_to_profile(result_unknown)
    assert profile_unknown.optional_seed_pack == ""


def test_onboarding_mapper_output_formats_default():
    """Default output_formats from onboarding must include standard artifacts."""
    result = OnboardingResult()
    profile = map_onboarding_to_profile(result)
    assert "markdown" in profile.output_formats
    assert "claim_ledger" in profile.output_formats
    assert "audit_report" in profile.output_formats
    assert "source_map" in profile.output_formats
    # Should NOT include "json" as a format
    assert "json" not in profile.output_formats


def test_onboarding_mapper_output_formats_docx_on_request():
    """When output_style_plain requests docx, include it in formats."""
    result = OnboardingResult(output_style_plain="executive brief in docx format")
    profile = map_onboarding_to_profile(result)
    assert "docx" in profile.output_formats


def test_language_sentinels_mapper_no_longer_maps_to_defaults():
    """Mapper sentinel handling was removed — sentinels must pass through as-is,
    so Agent workflow fails instead of silently using defaults.
    Init_wizard (interactive) still maps sentinels to en-US for terminal users.
    """
    from multi_agent_brief.cli.init_wizard import normalize_language as iw_normalize

    sentinels = ["default", "unknown", "choose for me", "默认", "不知道", "帮我选"]
    for s in sentinels:
        # Mapper: sentinel passes through — no silent default
        mapper_result = normalize_language(s)
        assert mapper_result != "en-US", f"Mapper should not silently map sentinel '{s}' to en-US"
        assert mapper_result == s, f"Mapper should pass sentinel '{s}' through as-is, got {mapper_result}"

        # Init wizard (interactive): still maps to en-US for terminal fallback
        iw_result = iw_normalize(s)
        assert iw_result == "en-US", f"init_wizard should map sentinel '{s}' to en-US, got {iw_result}"


def test_legacy_onboarding_without_search_backend_does_not_enable_tavily():
    """A legacy onboarding.json without search_backend_plain must not enable Tavily."""
    result = OnboardingResult(
        target="workspace",
        company_or_org="Sample Company",
        industry_or_theme="manufacturing",
    )
    # search_backend_plain defaults to ""; no tavily_enabled flag set
    assert result.search_backend_plain == ""
    assert result.tavily_enabled is False

    profile = map_onboarding_to_profile(result)
    assert profile.tavily_enabled is False


def test_onboarding_explicit_tavily_enables_backend():
    """When user explicitly picks tavily, profile.tavily_enabled should be True."""
    result = OnboardingResult(
        target="workspace",
        company_or_org="Sample Company",
        industry_or_theme="manufacturing",
        search_backend_plain="tavily",
    )
    profile = map_onboarding_to_profile(result)
    assert profile.tavily_enabled is True


def test_onboarding_explicit_none_disables_tavily():
    """When user explicitly picks none, profile.tavily_enabled should be False."""
    result = OnboardingResult(
        target="workspace",
        company_or_org="Sample Company",
        industry_or_theme="manufacturing",
        search_backend_plain="none",
    )
    profile = map_onboarding_to_profile(result)
    assert profile.tavily_enabled is False
