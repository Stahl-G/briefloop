"""Map OnboardingResult to InitProfile.

All mapping is business-language → internal fields.
Users never see source_profile, selector_max_items, etc.
"""
from __future__ import annotations

import re

from multi_agent_brief.cli.init_wizard import InitProfile
from multi_agent_brief.onboarding.schema import OnboardingResult


# ── language mapping ────────────────────────────────────────────────

_LANG_MAP: dict[str, str] = {
    "zh": "zh-CN",
    "zh-cn": "zh-CN",
    "zh_cn": "zh-CN",
    "中文": "zh-CN",
    "chinese": "zh-CN",
    "en": "en-US",
    "en-us": "en-US",
    "en_us": "en-US",
    "english": "en-US",
    "英文": "en-US",
    "ja": "ja-JP",
    "ja-jp": "ja-JP",
    "ja_jp": "ja-JP",
    "japanese": "ja-JP",
    "日文": "ja-JP",
    "bilingual": "bilingual",
    "dual language": "bilingual",
    "中英": "bilingual",
    "双语": "bilingual",
}

_DEFAULT_LANG = "en-US"


def normalize_language(text: str) -> str:
    t = text.strip().lower()
    if not t or t in ("default", "unknown", "choose for me", "默认", "不知道", "帮我选"):
        return _DEFAULT_LANG
    return _LANG_MAP.get(t, t)


# ── cadence mapping ────────────────────────────────────────────────

_CADENCE_MAP: dict[str, str] = {
    "daily": "daily",
    "day": "daily",
    "每日": "daily",
    "weekly": "weekly",
    "week": "weekly",
    "weekly brief": "weekly",
    "周报": "weekly",
    "每周": "weekly",
    "monthly": "monthly",
    "month": "monthly",
    "monthly brief": "monthly",
    "月报": "monthly",
    "每月": "monthly",
}

_DEFAULT_CADENCE = "weekly"


def normalize_cadence(text: str) -> str:
    t = text.strip().lower()
    if not t or t in ("default", "unknown", "choose for me", "默认", "不知道", "帮我选"):
        return _DEFAULT_CADENCE
    # Exact match first
    if t in _CADENCE_MAP:
        return _CADENCE_MAP[t]
    # Substring/keyword matching
    if any(k in t for k in ("daily", "每日", "every day")):
        return "daily"
    if any(k in t for k in ("monthly", "月报", "每月", "every month")):
        return "monthly"
    if any(k in t for k in ("weekly", "week", "周报", "每周", "every week")):
        return "weekly"
    return t


# ── audience mapping ───────────────────────────────────────────────

_AUDIENCE_MAP: dict[str, str] = {
    "management": "management",
    "executive": "management",
    "ceo office": "management",
    "ceo": "management",
    "leadership": "management",
    "管理层": "management",
    "management team": "management",
    "总裁办": "management",
    "boss": "management",
    "investment": "investment",
    "portfolio": "investment",
    "fund": "investment",
    "investor": "investment",
    "投资": "investment",
    "持仓": "investment",
    "基金": "investment",
    "ir": "investor_relations",
    "investor relations": "investor_relations",
    "disclosure": "investor_relations",
    "投关": "investor_relations",
    "披露": "investor_relations",
    "research": "research",
    "analyst": "research",
    "研究员": "research",
    "legal": "compliance",
    "compliance": "compliance",
    "法务": "compliance",
    "合规": "compliance",
    "business": "business",
    "operations": "business",
    "sales": "business",
    "业务": "business",
}

_DEFAULT_AUDIENCE = "management"


def normalize_audience(text: str) -> str:
    t = text.strip().lower()
    if not t or t in ("default", "unknown", "choose for me", "默认", "不知道", "帮我选"):
        return _DEFAULT_AUDIENCE
    # Exact match first
    if t in _AUDIENCE_MAP:
        return _AUDIENCE_MAP[t]
    # Substring/keyword matching for natural-language phrases
    if any(k in t for k in ("investor relations", "ir", "投关", "披露")):
        return "investor_relations"
    if any(k in t for k in ("investment", "portfolio", "fund", "investor", "投资", "持仓", "基金")):
        return "investment"
    if any(k in t for k in ("management", "executive", "ceo", "leadership", "管理层", "总裁", "boss")):
        return "management"
    if any(k in t for k in ("research", "analyst", "研究员")):
        return "research"
    if any(k in t for k in ("legal", "compliance", "法务", "合规")):
        return "compliance"
    if any(k in t for k in ("business", "operations", "sales", "业务")):
        return "business"
    return t


# ── source_profile mapping ─────────────────────────────────────────

_SOURCE_STYLE_MAP: dict[str, str] = {
    "official": "conservative",
    "filing": "conservative",
    "announcement": "conservative",
    "authoritative": "conservative",
    "conservative": "conservative",
    "公告": "conservative",
    "官网": "conservative",
    "权威": "conservative",
    "reliable research": "research",
    "industry media": "research",
    "sector news": "research",
    "research": "research",
    "稳健": "research",
    "研究": "research",
    "行业媒体": "research",
    "产业新闻": "research",
    "radar": "aggressive_signal",
    "broad scan": "aggressive_signal",
    "social media": "aggressive_signal",
    "github": "aggressive_signal",
    "signals": "aggressive_signal",
    "aggressive": "aggressive_signal",
    "雷达": "aggressive_signal",
    "广泛": "aggressive_signal",
    "社媒": "aggressive_signal",
    "信号": "aggressive_signal",
}

_DEFAULT_SOURCE_PROFILE = "research"


def normalize_source_profile(text: str) -> str:
    t = text.strip().lower()
    if not t or t in ("default", "unknown", "choose for me", "默认", "不知道", "帮我选"):
        return _DEFAULT_SOURCE_PROFILE
    # Direct match
    if t in _SOURCE_STYLE_MAP:
        return _SOURCE_STYLE_MAP[t]
    # Compound phrases
    if "official" in t or "filing" in t or "announcement" in t or "公告" in t:
        return "conservative"
    if "social" in t or "github" in t or "radar" in t or "broad" in t or "社媒" in t or "信号" in t:
        return "aggressive_signal"
    # Default to research for vague "reliable", "industry", etc.
    return "research"


# ── industry mapping ───────────────────────────────────────────────

_INDUSTRY_MAP: dict[str, str] = {
    "manufacturing": "manufacturing",
    "industrial": "manufacturing",
    "制造业": "manufacturing",
    "banking": "banking",
    "bank": "banking",
    "银行": "banking",
    "fund": "fund",
    "asset management": "fund",
    "基金": "fund",
    "technology": "internet",
    "tech": "internet",
    "ai": "internet",
    "software": "internet",
    "internet": "internet",
    "互联网": "internet",
    "科技": "internet",
    "finance": "banking",
    "securities": "banking",
    "investment": "fund",
    "金融": "banking",
    "general": "general",
    "通用": "general",
}


def normalize_industry(text: str) -> str:
    t = text.strip().lower()
    if not t or t in ("default", "unknown", "choose for me", "默认", "不知道", "帮我选", "general"):
        return "general"
    # Exact match first
    if t in _INDUSTRY_MAP:
        return _INDUSTRY_MAP[t]
    # Substring/keyword matching for natural-language phrases
    if any(k in t for k in ("manufacturing", "industrial", "制造业", "factory", "production")):
        return "manufacturing"
    if any(k in t for k in ("banking", "bank", "银行", "finance", "securities", "金融")):
        return "banking"
    if any(k in t for k in ("fund", "asset management", "基金", "investment", "私募")):
        return "fund"
    if any(k in t for k in ("technology", "tech", "ai", "software", "internet", "互联网", "科技")):
        return "internet"
    if any(k in t for k in ("general", "通用", "研究")):
        return "general"
    # Fallback: lowercase, spaces/hyphens -> underscores
    return re.sub(r"[\s\-]+", "_", t)

# ── industry label for titles ──────────────────────────────────────

_INDUSTRY_LABELS: dict[str, str] = {
    "manufacturing": ("Manufacturing", "manufacturing"),
    "banking": ("Banking", "banking"),
    "fund": ("Fund / Asset Management", "fund"),
    "internet": ("Internet / Technology", "internet"),
    "general": ("General Research", "general"),
}


def _industry_label(industry: str) -> tuple[str, str]:
    """Return (English label, description) for an industry slug."""
    return _INDUSTRY_LABELS.get(industry, (industry.replace("_", " ").title(), industry))


# ── selector_max_items ─────────────────────────────────────────────

_SELECTOR_MAP: dict[str, int] = {
    "conservative": 8,
    "research": 12,
    "aggressive_signal": 20,
}


# ── main mapper ────────────────────────────────────────────────────

def map_onboarding_to_profile(result: OnboardingResult) -> InitProfile:
    """Convert business-language OnboardingResult into an InitProfile."""
    profile = InitProfile()

    language = normalize_language(result.language_plain)
    profile.interface_language = language
    profile.output_language = language

    profile.company = result.company_or_org.strip() or "Sample Company"

    industry = normalize_industry(result.industry_or_theme)
    profile.industry = industry

    profile.audience = normalize_audience(result.audience_plain)
    profile.cadence = normalize_cadence(result.cadence_plain)
    profile.source_profile = normalize_source_profile(result.source_style_plain)
    profile.selector_max_items = _SELECTOR_MAP.get(profile.source_profile, 12)

    # Brief title
    en_label, _ = _industry_label(industry)
    company = profile.company
    cadence_word = profile.cadence.capitalize()
    if language == "zh-CN" and company and company != "Sample Company":
        profile.brief_title = f"{company} {en_label}周报"
    elif company and company != "Sample Company":
        profile.brief_title = f"{company} {cadence_word} {en_label} Brief"
    elif en_label and en_label != "Industry":
        profile.brief_title = f"{cadence_word} {en_label} Brief"
    else:
        profile.brief_title = "Multi-Agent Brief"

    # Focus areas
    base_focus = ["company", "industry", "policy", "competitors", "risk_events"]
    seen: set[str] = set()
    focus: list[str] = []
    for item in base_focus:
        if item not in seen:
            focus.append(item)
            seen.add(item)
    for item in result.must_watch:
        key = item.strip()
        if key and key.lower() not in seen:
            focus.append(key)
            seen.add(key.lower())
    profile.focus_areas = focus

    # Output formats: default markdown + json (user never asked)
    profile.output_formats = ["markdown", "json"]

    # Web search: never enable by default
    # (build_sources() handles this; we don't override here)

    return profile
