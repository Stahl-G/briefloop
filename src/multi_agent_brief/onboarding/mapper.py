"""Map OnboardingResult to InitProfile.

All mapping is business-language → internal fields.
Users never see source_profile, selector_max_items, etc.

Architecture principle:
- user.md = rich user profile and task context (primary semantic layer)
- config.yaml = operational run settings only
- sources.yaml = approved or discovered sources only
- source.mode = llm_decide by default
- industry packs = optional seed packs, not the source of truth
"""
from __future__ import annotations

from multi_agent_brief.onboarding.schema import OnboardingResult
from multi_agent_brief.sources.industry_packs import INDUSTRY_PACKS
from multi_agent_brief.audience.profiles import map_audience_to_profile
from multi_agent_brief.workspace.init_profile import InitProfile


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
    if not t:
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
    if not t:
        return _DEFAULT_CADENCE
    if t in _CADENCE_MAP:
        return _CADENCE_MAP[t]
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
    if not t:
        return _DEFAULT_AUDIENCE
    if t in _AUDIENCE_MAP:
        return _AUDIENCE_MAP[t]
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
    "conservative": "conservative",
    "official": "conservative",
    "filing": "conservative",
    "announcement": "conservative",
    "官方": "conservative",
    "公告": "conservative",
    "research": "research",
    "reliable research": "research",
    "industry media": "research",
    "研究": "research",
    "行业媒体": "research",
    "aggressive_signal": "aggressive_signal",
    "radar": "aggressive_signal",
    "broad scan": "aggressive_signal",
    "social signals": "aggressive_signal",
    "社媒": "aggressive_signal",
    "信号": "aggressive_signal",
    "llm_decide": "llm_decide",
    "ai decide": "llm_decide",
    "ai决定": "llm_decide",
    "让ai决定": "llm_decide",
}

_DEFAULT_SOURCE_PROFILE = "llm_decide"


def normalize_source_profile(text: str) -> str:
    t = text.strip().lower()
    if not t:
        return _DEFAULT_SOURCE_PROFILE
    if t in _SOURCE_STYLE_MAP:
        return _SOURCE_STYLE_MAP[t]
    if "official" in t or "filing" in t or "announcement" in t or "公告" in t:
        return "conservative"
    if "social" in t or "github" in t or "radar" in t or "broad" in t or "社媒" in t or "信号" in t:
        return "aggressive_signal"
    # Default to llm_decide for vague or unknown source preferences
    return "llm_decide"


# ── search backend mapping ─────────────────────────────────────────

_SEARCH_BACKEND_MAP: dict[str, str] = {
    "tavily": "tavily",
    "exa": "exa",
    "brave": "brave",
    "firecrawl": "firecrawl",
    "serper": "serper",
    "runtime_websearch": "runtime_websearch",
    "configure_later": "configure_later",
    "稍后配置": "configure_later",
    "稍后": "configure_later",
    "none": "none",
    "无": "none",
    "不启用": "none",
}

_DEFAULT_SEARCH_BACKEND = ""


def normalize_search_backend(text: str) -> str:
    t = text.strip().lower()
    if not t:
        return _DEFAULT_SEARCH_BACKEND
    if t in _SEARCH_BACKEND_MAP:
        return _SEARCH_BACKEND_MAP[t]
    # Check for partial matches
    for key, value in _SEARCH_BACKEND_MAP.items():
        if key in t:
            return value
    return _DEFAULT_SEARCH_BACKEND


# ── role mapping ───────────────────────────────────────────────────

_ROLE_MAP: dict[str, str] = {
    "strategy": "strategy_office",
    "strategy office": "strategy_office",
    "president office": "strategy_office",
    "总裁办": "strategy_office",
    "战略": "strategy_office",
    "战略研究": "strategy_office",
    "research": "research_analyst",
    "research analyst": "research_analyst",
    "行业研究": "research_analyst",
    "研究员": "research_analyst",
    "ir": "investor_relations",
    "investor relations": "investor_relations",
    "投资者关系": "investor_relations",
    "投关": "investor_relations",
    "policy": "policy_analyst",
    "policy analyst": "policy_analyst",
    "政策研究": "policy_analyst",
    "政策": "policy_analyst",
    "management": "management_support",
    "management support": "management_support",
    "管理层支持": "management_support",
    "管理": "management_support",
    "marketing": "management_support",
    "市场": "management_support",
    "市场部": "management_support",
}

_DEFAULT_ROLE = "strategy_office"


def normalize_role(text: str) -> str:
    t = text.strip().lower()
    if not t:
        return _DEFAULT_ROLE
    if t in _ROLE_MAP:
        return _ROLE_MAP[t]
    # Check for partial matches
    for key, value in _ROLE_MAP.items():
        if key in t:
            return value
    return _DEFAULT_ROLE


# ── audit strictness mapping ───────────────────────────────────────

_AUDIT_STRICTNESS_MAP: dict[str, str] = {
    "standard": "standard",
    "strict": "strict",
    "lenient": "lenient",
    "标准": "standard",
    "严格": "strict",
    "宽松": "lenient",
}

_DEFAULT_AUDIT_STRICTNESS = "standard"


def normalize_audit_strictness(text: str) -> str:
    t = text.strip().lower()
    if not t:
        return _DEFAULT_AUDIT_STRICTNESS
    if t in _AUDIT_STRICTNESS_MAP:
        return _AUDIT_STRICTNESS_MAP[t]
    if "strict" in t or "严格" in t:
        return "strict"
    if "lenient" in t or "宽松" in t:
        return "lenient"
    return _DEFAULT_AUDIT_STRICTNESS


# ── industry matching (optional seed pack only) ────────────────────

_REGISTERED_PACK_KEYS = set(INDUSTRY_PACKS.keys())


def _try_match_seed_pack(text: str) -> str:
    """Try to match user text to a registered industry pack key.

    Returns the pack key if clearly matched, empty string otherwise.
    Does NOT guess or invent unsupported slugs.
    """
    t = text.strip().lower()
    if not t:
        return ""
    # Exact match against registered pack keys
    if t in _REGISTERED_PACK_KEYS:
        return t
    # Substring match against registered pack keys
    for key in _REGISTERED_PACK_KEYS:
        if key in t:
            return key
    return ""


def normalize_industry(text: str) -> str:
    """Normalize industry text to a registered pack key or empty string.

    Returns a registered pack key if clearly matched, empty string otherwise.
    Does NOT guess or invent unsupported slugs.
    """
    return _try_match_seed_pack(text)


# ── selector_max_items ─────────────────────────────────────────────

_SELECTOR_MAP: dict[str, int] = {
    "conservative": 20,
    "research": 20,
    "aggressive_signal": 28,
    "llm_decide": 20,
}


# ── main mapper ────────────────────────────────────────────────────

def map_onboarding_to_profile(result: OnboardingResult) -> InitProfile:
    """Convert business-language OnboardingResult into an InitProfile.

    Preserves raw user text in user.md-facing fields.
    Only maps to registered pack keys when clearly matched.
    Defaults to llm_decide mode for source discovery.
    """
    profile = InitProfile()

    language = normalize_language(result.language_plain)
    profile.interface_language = language
    profile.output_language = language

    profile.company = result.company_or_org.strip()

    # Role mapping
    role_raw = getattr(result, "role_plain", "").strip()
    if role_raw:
        profile.role = normalize_role(role_raw)

    # Preserve raw industry text for user.md
    industry_raw = result.industry_or_theme.strip()
    profile.industry_text = industry_raw
    # Only set industry slug if clearly matches a registered pack
    profile.industry = _try_match_seed_pack(industry_raw)
    profile.optional_seed_pack = profile.industry  # same as industry if matched

    profile.audience = normalize_audience(result.audience_plain)
    profile.audience_profile = map_audience_to_profile(result.audience_plain)
    profile.cadence = normalize_cadence(result.cadence_plain)
    profile.source_profile = normalize_source_profile(result.source_style_plain)
    profile.selector_max_items = _SELECTOR_MAP.get(profile.source_profile, 20)

    # Brief title: user-specified overrides auto-generation.
    # task_objective is descriptive context, not a title or filename stem.
    user_title = ""
    if hasattr(result, "brief_title") and result.brief_title.strip():
        user_title = result.brief_title.strip()

    if user_title:
        profile.brief_title = user_title
    else:
        industry_display = industry_raw or "Industry"
        company = profile.company
        cadence_word = profile.cadence.capitalize()
        if language == "zh-CN" and company and company != "Sample Company":
            profile.brief_title = f"{company} {industry_display}周报"
        elif company and company != "Sample Company":
            profile.brief_title = f"{company} {cadence_word} Brief"
        else:
            profile.brief_title = "Multi-Agent Brief"

    # Focus areas: preserve raw must_watch items
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

    # Store task objective and forbidden sources
    if hasattr(result, "task_objective") and result.task_objective:
        profile.task_objective = result.task_objective
    if hasattr(result, "forbidden_sources") and result.forbidden_sources:
        profile.forbidden_sources = list(result.forbidden_sources)

    # Output formats: standard artifact set
    profile.output_formats = ["markdown", "docx", "claim_ledger", "audit_report", "source_appendix"]

    # If output_style_plain requests docx/word, include it
    style_lower = (getattr(result, "output_style_plain", "") or "").lower()
    if "docx" not in profile.output_formats and any(
        k in style_lower for k in ("docx", "word", "docx格式", "word格式")
    ):
        profile.output_formats.append("docx")

    # New fields from extended onboarding
    if hasattr(result, "max_items_per_brief") and result.max_items_per_brief:
        profile.selector_max_items = result.max_items_per_brief
    
    if hasattr(result, "source_age_days") and result.source_age_days:
        profile.max_source_age_days = result.source_age_days

    # Web search: handle search backend selection
    search_backend = getattr(result, "search_backend_plain", "").strip()
    if search_backend:
        search_backend = normalize_search_backend(search_backend)
        if search_backend == "tavily":
            profile.tavily_enabled = True
            profile.web_search_enabled = True
            profile.web_search_mode = "external_api"
            profile.search_backend = "tavily"
        elif search_backend == "none":
            profile.tavily_enabled = False
            profile.web_search_enabled = False
            profile.web_search_mode = "disabled"
            profile.search_backend = ""
        elif search_backend == "runtime_websearch":
            profile.tavily_enabled = False
            profile.web_search_enabled = True
            profile.web_search_mode = "runtime_tool"
            profile.search_backend = ""
        elif search_backend == "configure_later":
            profile.tavily_enabled = False
            profile.web_search_enabled = True
            profile.web_search_mode = "configure_later"
            profile.search_backend = ""
        else:
            # Other backends (exa, brave, firecrawl, serper)
            profile.tavily_enabled = False
            profile.web_search_enabled = True
            profile.web_search_mode = "external_api"
            profile.search_backend = search_backend

    # Also check the legacy tavily_enabled field
    if getattr(result, "tavily_enabled", False):
        profile.tavily_enabled = True
        profile.web_search_enabled = True
        if not profile.web_search_mode or profile.web_search_mode == "disabled":
            profile.web_search_mode = "external_api"
        if not profile.search_backend:
            profile.search_backend = "tavily"

    # Competitor monitoring
    comp_prefs = getattr(result, "competitor_preferences", None) or {}
    if comp_prefs.get("enabled"):
        profile.competitor_module_enabled = True
        names = comp_prefs.get("names", [])
        if isinstance(names, list):
            profile.competitor_names = [n.strip() for n in names if isinstance(n, str) and n.strip()]

    return profile
