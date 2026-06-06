"""Audience Profile definitions and mapping logic.

Profiles are public-safe and industry-neutral. They define:
- Required and optional sections
- Banned phrases and required disclaimers
- Quality thresholds (min chars, min sections, etc.)
- Citation and summary bullet policies
- Default DOCX template
- Final quality and editorial governance thresholds
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AudienceProfile:
    """Deterministic configuration for a specific audience type."""

    profile_id: str
    display_name: str

    # Section requirements
    required_sections: list[str] = field(default_factory=list)
    optional_sections: list[str] = field(default_factory=list)

    # Content restrictions
    banned_phrases: list[str] = field(default_factory=list)
    required_disclaimers: list[str] = field(default_factory=list)

    # Quality thresholds
    min_markdown_chars: int = 8000
    min_main_sections: int = 3
    min_zh_chars: int = 0  # 0 = not enforced
    min_en_words: int = 0  # 0 = not enforced

    # Policy settings
    summary_bullet_policy: str = "required"  # required / optional / none
    citation_policy: str = "required"  # required / optional / none

    # DOCX template default
    docx_template: str = "executive_brief"

    # Threshold overrides for final quality gate
    final_quality_thresholds: dict = field(default_factory=dict)

    # Threshold overrides for editorial governance
    editorial_governance_thresholds: dict = field(default_factory=dict)


# ── Profile Registry ──────────────────────────────────────────────────────────

PROFILES: dict[str, AudienceProfile] = {
    "management": AudienceProfile(
        profile_id="management",
        display_name="Management / Executive",
        required_sections=["Executive Summary", "Key Developments", "Outlook"],
        optional_sections=["Risk Assessment", "Competitive Landscape"],
        banned_phrases=[
            "investment recommendation",
            "buy",
            "sell",
            "target price",
            "strong buy",
            "strong sell",
        ],
        required_disclaimers=[],
        min_markdown_chars=8000,
        min_main_sections=3,
        min_zh_chars=3000,
        min_en_words=1800,
        summary_bullet_policy="required",
        citation_policy="required",
        docx_template="executive_brief",
        final_quality_thresholds={
            "expected_summary_bullets": 5,
            "min_selected_claims": 20,
        },
        editorial_governance_thresholds={
            "min_factual_density": 0.3,
        },
    ),
    "research": AudienceProfile(
        profile_id="research",
        display_name="Research / Analyst",
        required_sections=["Executive Summary", "Methodology", "Findings", "Data Sources"],
        optional_sections=["Limitations", "Appendix"],
        banned_phrases=[],
        required_disclaimers=[],
        min_markdown_chars=12000,
        min_main_sections=4,
        min_zh_chars=4000,
        min_en_words=2500,
        summary_bullet_policy="required",
        citation_policy="required",
        docx_template="research_note",
        final_quality_thresholds={
            "expected_summary_bullets": 7,
            "min_selected_claims": 30,
        },
        editorial_governance_thresholds={
            "min_factual_density": 0.4,
        },
    ),
    "ir": AudienceProfile(
        profile_id="ir",
        display_name="Investor Relations / Disclosure",
        required_sections=["Executive Summary", "Key Metrics", "Outlook"],
        optional_sections=["Risk Factors", "Regulatory Considerations"],
        banned_phrases=[
            "investment recommendation",
            "buy",
            "sell",
            "target price",
        ],
        required_disclaimers=[
            "This document is for informational purposes only and does not constitute investment advice.",
        ],
        min_markdown_chars=8000,
        min_main_sections=3,
        min_zh_chars=3000,
        min_en_words=1800,
        summary_bullet_policy="required",
        citation_policy="required",
        docx_template="formal_internal_report",
        final_quality_thresholds={
            "expected_summary_bullets": 5,
            "min_selected_claims": 20,
        },
        editorial_governance_thresholds={
            "min_factual_density": 0.35,
        },
    ),
    "legal_compliance": AudienceProfile(
        profile_id="legal_compliance",
        display_name="Legal / Compliance / Regulatory",
        required_sections=["Executive Summary", "Regulatory Changes", "Compliance Impact"],
        optional_sections=["Risk Assessment", "Action Items"],
        banned_phrases=[
            "investment recommendation",
            "buy",
            "sell",
            "target price",
        ],
        required_disclaimers=[
            "This document is for informational purposes only and does not constitute legal advice.",
        ],
        min_markdown_chars=10000,
        min_main_sections=3,
        min_zh_chars=3500,
        min_en_words=2000,
        summary_bullet_policy="required",
        citation_policy="required",
        docx_template="formal_internal_report",
        final_quality_thresholds={
            "expected_summary_bullets": 5,
            "min_selected_claims": 25,
        },
        editorial_governance_thresholds={
            "min_factual_density": 0.4,
        },
    ),
    "default": AudienceProfile(
        profile_id="default",
        display_name="General / Default",
        required_sections=["Executive Summary", "Key Developments"],
        optional_sections=["Outlook", "Risk Assessment"],
        banned_phrases=[],
        required_disclaimers=[],
        min_markdown_chars=8000,
        min_main_sections=3,
        min_zh_chars=3000,
        min_en_words=1800,
        summary_bullet_policy="required",
        citation_policy="required",
        docx_template="executive_brief",
        final_quality_thresholds={
            "expected_summary_bullets": 5,
            "min_selected_claims": 20,
        },
        editorial_governance_thresholds={
            "min_factual_density": 0.3,
        },
    ),
}


def get_profile(profile_id: str) -> AudienceProfile:
    """Get an AudienceProfile by ID.

    Returns the 'default' profile if the ID is not found.
    """
    return PROFILES.get(profile_id, PROFILES["default"])


def map_audience_to_profile(audience_text: str) -> str:
    """Conservatively map free-text audience to a profile ID.

    This mapping is intentionally conservative — unknown audience types
    map to 'default' rather than guessing.

    Args:
        audience_text: Free-text audience description from onboarding.

    Returns:
        Profile ID string (management, research, ir, legal_compliance, or default).
    """
    t = audience_text.strip().lower()
    if not t:
        return "default"

    # Management / Executive mappings
    management_keywords = [
        "management", "executive", "ceo", "cfo", "cto", "board",
        "leadership", "c-suite", "management team", "executive team",
        "管理层", "总裁办", "董事会", "高管", "领导层", "boss",
    ]
    if any(kw in t for kw in management_keywords):
        return "management"

    # Research / Analyst mappings
    research_keywords = [
        "research", "analyst", "industry research", "research analyst",
        "研究员", "分析员", "行业研究", "研究团队",
    ]
    if any(kw in t for kw in research_keywords):
        return "research"

    # Investor Relations mappings
    ir_keywords = [
        "investor relations", "ir", "disclosure", "investor",
        "投关", "披露", "投资者关系", "ir部门",
    ]
    if any(kw in t for kw in ir_keywords):
        return "ir"

    # Legal / Compliance mappings
    legal_keywords = [
        "legal", "compliance", "regulatory", "law", "legal compliance",
        "法务", "合规", "监管", "法律", "法规",
    ]
    if any(kw in t for kw in legal_keywords):
        return "legal_compliance"

    # Unknown audience → default
    return "default"
