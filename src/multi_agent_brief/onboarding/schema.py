"""OnboardingResult schema: business-language fields for conversational onboarding."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class OnboardingResult:
    """Business-language onboarding answers collected from the user.

    All fields have defaults so missing fields never block init.
    The mapper translates these into the internal InitProfile.
    """

    target: str = "brief-workspace"

    company_or_org: str = ""
    industry_or_theme: str = ""
    task_objective: str = ""  # free-text task description
    forbidden_sources: list[str] = field(default_factory=list)

    audience_plain: str = "management team"
    source_style_plain: str = "reliable research"
    output_style_plain: str = "executive brief, conclusion-first"
    language_plain: str = "English"
    cadence_plain: str = "weekly"

    must_watch: list[str] = field(default_factory=list)

    # New fields for extended onboarding
    focus_areas_plain: str = ""  # user description of focus areas
    search_backend_plain: str = "tavily"  # tavily/exa/brave/firecrawl/serper/serpapi
    max_items_per_brief: int = 8  # max items per brief
    source_age_days: int = 14  # max source age in days
    audit_strictness: str = "standard"  # standard/strict/lenient

    tavily_enabled: bool = False

    confidence: str = "medium"
    missing: list[str] = field(default_factory=list)
