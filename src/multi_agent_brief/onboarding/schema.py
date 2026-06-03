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

    audience_plain: str = "management team"
    source_style_plain: str = "reliable research"
    output_style_plain: str = "executive brief, conclusion-first"
    language_plain: str = "English"
    cadence_plain: str = "weekly"

    must_watch: list[str] = field(default_factory=list)

    confidence: str = "medium"
    missing: list[str] = field(default_factory=list)
