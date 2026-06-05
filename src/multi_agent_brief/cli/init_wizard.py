from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


DEMO_NEWS = {
    "source_url": "https://example.com/synthetic-market-news",
    "published_at": "2026-06-01",
    "items": [
        "A public market tracker reported that manufacturing output continued to expand in selected regions during May 2026.",
        "A policy update indicated that new trade regulations may affect import timelines for selected industrial products.",
        "A competitor announced a major manufacturing capacity expansion plan, with commercial production expected in 2027.",
    ],
}

DEMO_MARKET_DATA = {
    "source_url": "https://example.com/synthetic-market-data",
    "published_at": "2026-06-01",
    "items": [
        "Synthetic commodity price checks showed a 3.5% week-over-week decline in selected spot-market channels.",
        "Synthetic industrial supply quotes remained broadly stable at benchmark levels for project assumptions.",
    ],
}

DEMO_CONFIG = """project:
  name: "Synthetic Market Brief Demo"
  language: "en-US"
  audience: "management"

report:
  date: "2026-06-02"
  max_source_age_days: 14
  fail_on_stale_source: true

input:
  path: "input"

output:
  path: "output"
  filename_template: "{project_name}_{report_date}"
  named_outputs: true
"""

WORKSPACE_GITIGNORE = """.env
.env.*
output/
.rag/
private_inputs/
private_outputs/
logs/
user.md
*.docx
*.pdf
*.xlsx
.DS_Store
"""

INPUT_README_EN = """# Input Folder

Put public or synthetic source files here.

Supported MVP formats:

- `.md`
- `.txt`
- `.json`

Do not place confidential company documents, internal reports, private messages, credentials, raw logs, or material non-public information in this folder unless the folder is local-only and excluded from Git.

Recommended JSON format:

```json
{
  "source_url": "https://example.com/source",
  "published_at": "2026-06-02",
  "source_tier": "industry_media",
  "items": [
    "Example source-backed statement."
  ]
}
```
"""

INPUT_README_ZH = """# 输入文件夹

请把公开或合成来源文件放在这里。

MVP 支持的格式：

- `.md`
- `.txt`
- `.json`

不要把机密公司文件、内部报告、私有消息、凭据、原始日志或重大非公开信息放入此文件夹，除非该文件夹只保存在本地且已从 Git 中排除。

推荐 JSON 格式：

```json
{
  "source_url": "https://example.com/source",
  "published_at": "2026-06-02",
  "source_tier": "industry_media",
  "items": [
    "Example source-backed statement."
  ]
}
```
"""


@dataclass
class InitProfile:
    interface_language: str = "zh-CN"
    output_language: str = "zh-CN"
    source_handling: str = "preserve_original"
    company: str = "Sample Company"
    role: str = "strategy_office"
    industry: str = ""
    industry_text: str = ""  # raw user description, preserved in user.md
    brief_title: str = "Weekly Industry Brief"
    audience: str = "management"
    focus_areas: list[str] = field(default_factory=lambda: ["policy", "competitor", "market", "customer_demand"])
    task_objective: str = ""  # free-text task description
    forbidden_sources: list[str] = field(default_factory=list)
    cadence: str = "weekly"
    max_source_age_days: int = 14
    selector_max_items: int = 8
    retrieval_enabled: bool = False
    retrieval_provider: str = "ollama"
    retrieval_model: str = "nomic-embed-text"
    output_formats: list[str] = field(
        default_factory=lambda: ["markdown", "docx", "claim_ledger", "audit_report", "source_map"]
    )
    source_profile: str = "llm_decide"
    source_decision_mode: str = "agent_decide"
    optional_seed_pack: str = ""  # registered pack key or empty
    tavily_enabled: bool = False


class InitOnboardingRequired(RuntimeError):
    """Raised when init would otherwise create a workspace from hidden defaults."""


_REQUIRED_DIRECT_INIT_ARGS: dict[str, str] = {
    "language": "--language",
    "company": "--company",
    "industry": "--industry",
    "title": "--title",
    "audience": "--audience",
    "cadence": "--cadence",
    "source_profile": "--source-profile",
}


def missing_required_direct_init_args(args: Any) -> list[str]:
    """Return required business fields missing from direct CLI init."""
    missing: list[str] = []
    for attr, flag in _REQUIRED_DIRECT_INIT_ARGS.items():
        if not getattr(args, attr, None):
            missing.append(flag)
    return missing


def create_demo_workspace(target: Path, *, force: bool = False) -> None:
    input_dir = target / "input"
    files = {
        target / "config.yaml": DEMO_CONFIG,
        input_dir / "news.json": json.dumps(DEMO_NEWS, indent=2),
        input_dir / "market_data.json": json.dumps(DEMO_MARKET_DATA, indent=2),
    }
    _write_files(files, force=force)


def create_workspace(target: Path, profile: InitProfile, *, force: bool = False) -> None:
    # Set decision mode based on source profile
    if profile.source_profile == "llm_decide":
        profile.source_decision_mode = "agent_decide"

    input_dir = target / "input"
    output_dir = target / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    input_dir.mkdir(parents=True, exist_ok=True)

    files = {
        target / "config.yaml": to_yaml(build_config(profile)),
        target / "profile.yaml": to_yaml(build_profile(profile)),
        target / "sources.yaml": to_yaml(build_sources(profile)),
        target / "competitor_universe.yaml": to_yaml(_build_competitor_universe(profile)),
        target / "user.md": build_user_md(profile),
        target / ".gitignore": WORKSPACE_GITIGNORE,
        target / ".env.example": _build_env_example(),
        input_dir / "README.md": build_input_readme(profile.interface_language),
    }
    _write_files(files, force=force)

    # Create state directory for cross-period tracking (runtime, not committed)
    state_dir = target / "state" / "market_competitor"
    state_dir.mkdir(parents=True, exist_ok=True)


def _is_interactive() -> bool:
    """Check if stdin is connected to a real terminal (not a pipe or agent Bash tool)."""
    import sys
    return sys.stdin.isatty() if hasattr(sys.stdin, "isatty") else False


def build_profile_from_args(args: Any, *, input_func: Callable[[str], str] | None = None) -> InitProfile:
    input_func = input if input_func is None else input_func
    profile = InitProfile()
    if has_direct_init_args(args):
        profile.interface_language = normalize_language(args.language or profile.interface_language)
        profile.output_language = normalize_language(args.output_language or args.language or profile.output_language)
        profile.company = args.company or profile.company
        profile.role = args.role or profile.role
        profile.industry = args.industry or profile.industry
        profile.brief_title = args.title or profile.brief_title
        profile.audience = args.audience or profile.audience
        profile.focus_areas = parse_list_arg(args.focus_areas) or profile.focus_areas
        profile.cadence = args.cadence or profile.cadence
        profile.selector_max_items = args.selector_max_items or profile.selector_max_items
        apply_rag_args(profile, args.rag, args.retrieval_provider)
        profile.output_formats = parse_list_arg(args.output_formats) or profile.output_formats
        profile.source_profile = getattr(args, "source_profile", None) or profile.source_profile
        profile.tavily_enabled = getattr(args, "tavily", False) or profile.tavily_enabled
        return profile
    # No CLI args → try interactive prompt
    if _is_interactive():
        try:
            return prompt_for_profile(input_func=input_func)
        except (EOFError, KeyboardInterrupt):
            raise InitOnboardingRequired(
                "Interactive init was interrupted before onboarding completed."
            )
    raise InitOnboardingRequired(
        "Non-interactive init without onboarding answers is not allowed."
    )


def has_direct_init_args(args: Any) -> bool:
    fields = [
        "language",
        "output_language",
        "company",
        "role",
        "industry",
        "title",
        "audience",
        "focus_areas",
        "cadence",
        "rag",
        "retrieval_provider",
        "selector_max_items",
        "output_formats",
        "source_profile",
    ]
    # Check string/list fields for non-None
    if any(getattr(args, field, None) is not None for field in fields):
        return True
    # Check boolean flags — tavily is store_true, default False
    if getattr(args, "tavily", False):
        return True
    return False


def prompt_for_profile(*, input_func: Callable[[str], str] | None = None) -> InitProfile:
    input_func = input if input_func is None else input_func
    language_choice = ask_choice(
        input_func,
        "Select language / 选择语言:\n1. English\n2. 简体中文\n3. Bilingual / 双语\nDefault [2]: ",
        {"1": "en-US", "2": "zh-CN", "3": "bilingual"},
        "2",
    )
    profile = InitProfile(interface_language=language_choice, output_language=language_choice)
    prompts = prompt_labels(language_choice)

    profile.company = ask_text(input_func, prompts["company"], profile.company)
    profile.role = ask_text(input_func, prompts["role"], profile.role)
    profile.industry = ask_text(input_func, prompts["industry"], profile.industry)
    profile.brief_title = ask_text(input_func, prompts["title"], profile.brief_title)
    profile.audience = ask_text(input_func, prompts["audience"], profile.audience)
    profile.focus_areas = parse_list_arg(ask_text(input_func, prompts["focus"], ",".join(profile.focus_areas)))
    profile.cadence = ask_text(input_func, prompts["cadence"], profile.cadence)
    max_items = ask_text(input_func, prompts["selector_max_items"], str(profile.selector_max_items))
    profile.selector_max_items = parse_int(max_items, profile.selector_max_items)
    rag_enabled = ask_yes_no(input_func, prompts["rag"], default=False)
    profile.retrieval_enabled = rag_enabled
    if rag_enabled:
        profile.retrieval_provider = ask_choice(input_func, prompts["retrieval_provider"], {"1": "ollama", "2": "gemini"}, "1")
        profile.retrieval_model = retrieval_model_for_provider(profile.retrieval_provider)
    profile.output_formats = parse_list_arg(ask_text(input_func, prompts["outputs"], ",".join(profile.output_formats)))
    profile.max_source_age_days = parse_int(ask_text(input_func, prompts["max_age"], str(profile.max_source_age_days)), 14)
    profile.source_profile = ask_choice(input_func, prompts["source_profile"], prompts["source_profile_options"], "2")
    profile.tavily_enabled = ask_yes_no(input_func, prompts["tavily"], default=False)
    return profile


def prompt_labels(language: str) -> dict[str, Any]:
    if language == "en-US":
        return {
            "company": "Company name: ",
            "role": "Select your role:\n1. Strategy / President Office\n2. Investor Relations\n3. Research Analyst\n4. Policy Analyst\n5. Management Support\n6. Other\nDefault [1]: ",
            "role_options": {
                "1": "strategy_office",
                "2": "investor_relations",
                "3": "research_analyst",
                "4": "policy_analyst",
                "5": "management_support",
                "6": "other",
            },
            "industry": "Select industry:\n1. Manufacturing\n2. Banking\n3. Fund / Asset Management\n4. Internet / Technology\n5. General Research\nDefault [1]: ",
            "industry_options": {
                "1": "manufacturing",
                "2": "banking",
                "3": "fund",
                "4": "internet",
                "5": "general",
            },
            "title": "Brief title: ",
            "audience": "Audience (e.g. management, strategy, research, investor relations, marketing, etc.): ",
            "focus": "Focus areas, comma-separated: ",
            "cadence": "Reporting cadence:\n1. Weekly\n2. Biweekly\n3. Monthly\n4. Ad hoc\nDefault [1]: ",
            "cadence_options": {"1": "weekly", "2": "biweekly", "3": "monthly", "4": "ad_hoc"},
            "selector_max_items": "How many news items should be selected for each brief? Default [8]: ",
            "rag": "Enable historical retrieval / RAG? [y/N]: ",
            "retrieval_provider": "Choose retrieval provider:\n1. Ollama local\n2. Gemini API\nDefault [1]: ",
            "outputs": "Output formats, comma-separated: ",
            "max_age": "Maximum source age in days: ",
            "source_profile": "Source profile:\n1. Conservative: official and high-confidence sources only\n2. Research: balanced official, industry, market, and research sources\n3. Aggressive signal: broader signal discovery, more noise allowed\n4. Custom: user will manually edit sources.yaml\n5. Let LLM decide: generate an agent-readable source discovery policy\nDefault [2]: ",
            "source_profile_options": {"1": "conservative", "2": "research", "3": "aggressive_signal", "4": "custom", "5": "llm_decide"},
            "tavily": "Enable live web search with Tavily? [y/N]: ",
        }
    if language == "bilingual":
        labels = prompt_labels("en-US")
        labels.update(
            {
                "company": "Company name / 公司名称: ",
                "role": "Select your role / 请选择岗位:\n1. Strategy / President Office / 总裁办・战略研究\n2. Investor Relations / 投资者关系\n3. Research Analyst / 行业研究\n4. Policy Analyst / 政策研究\n5. Management Support / 管理层支持\n6. Other / 其他\nDefault [1]: ",
                "industry": "Select industry / 请选择行业:\n1. Manufacturing / 制造业\n2. Banking / 银行\n3. Fund / 基金\n4. Internet / 互联网\n5. General Research / 通用研究\nDefault [1]: ",
                "title": "Brief title / 简报标题: ",
                "audience": "Audience / 阅读对象 (e.g. management/管理层, strategy/战略, research/研究, IR/投关, marketing/市场, etc.): ",
                "focus": "Focus areas / 关注领域，comma-separated / 逗号分隔: ",
                "cadence": "Reporting cadence / 简报频率:\n1. Weekly / 每周\n2. Biweekly / 双周\n3. Monthly / 每月\n4. Ad hoc / 不定期\nDefault [1]: ",
                "selector_max_items": "How many news items / 每期筛选多少条新闻？Default [8]: ",
                "rag": "Enable historical retrieval / RAG? 是否启用历史检索？[y/N]: ",
                "outputs": "Output formats / 输出格式，comma-separated / 逗号分隔: ",
                "max_age": "Maximum source age in days / 最大来源天数: ",
                "source_profile": "Source profile / 信息来源策略:\n1. Conservative / 保守：仅官方和高置信来源\n2. Research / 研究：官方、行业、市场、研究来源平衡\n3. Aggressive signal / 激进信号：扩大发现范围\n4. Custom / 自定义\n5. Let LLM decide / 让 LLM 自动决定来源\nDefault [2]: ",
                "source_profile_options": {"1": "conservative", "2": "research", "3": "aggressive_signal", "4": "custom", "5": "llm_decide"},
                "tavily": "Enable live web search with Tavily? / 启用 Tavily 实时搜索？[y/N]: ",
            }
        )
        return labels
    return {
        "company": "请输入公司名称：",
        "role": "请选择你的岗位：\n1. 总裁办 / 战略研究\n2. 投资者关系\n3. 行业研究\n4. 政策研究\n5. 管理层支持\n6. 其他\n默认 [1]：",
        "role_options": {
            "1": "strategy_office",
            "2": "investor_relations",
            "3": "research_analyst",
            "4": "policy_analyst",
            "5": "management_support",
            "6": "other",
        },
        "industry": "请选择行业：\n1. 制造业\n2. 银行\n3. 基金\n4. 互联网\n5. 通用研究\n默认 [1]：",
        "industry_options": {
            "1": "manufacturing",
            "2": "banking",
            "3": "fund",
            "4": "internet",
            "5": "general",
        },
        "title": "请输入简报标题：",
        "audience": "请输入阅读对象（例如：管理层、战略团队、研究团队、投资者关系、市场团队 等）：",
        "focus": "请输入关注领域，逗号分隔：",
        "cadence": "请选择简报频率：\n1. 每周\n2. 双周\n3. 每月\n4. 不定期\n默认 [1]：",
        "cadence_options": {"1": "weekly", "2": "biweekly", "3": "monthly", "4": "ad_hoc"},
        "selector_max_items": "每期筛选多少条新闻？默认 [8]：",
        "rag": "是否启用历史检索 / RAG？[y/N]：",
        "retrieval_provider": "请选择检索 provider：\n1. Ollama 本地\n2. Gemini API\n默认 [1]：",
        "outputs": "请输入输出格式，逗号分隔：",
        "max_age": "请输入最大来源天数：",
        "source_profile": "请选择信息来源策略：\n1. 保守：只使用官方和高置信来源\n2. 研究：官方、行业媒体、市场数据、研究来源平衡\n3. 激进信号：扩大信号发现范围，允许更多噪音\n4. 自定义：用户后续手动编辑 sources.yaml\n5. 让 LLM 自动决定：生成 agent 可读的来源发现策略\n默认 [2]：",
        "source_profile_options": {"1": "conservative", "2": "research", "3": "aggressive_signal", "4": "custom", "5": "llm_decide"},
        "tavily": "是否启用 Tavily 实时搜索？[y/N]：",
    }


def build_config(profile: InitProfile) -> dict[str, Any]:
    return {
        "project": {
            "name": profile.brief_title,
            "company": profile.company,
            "industry": profile.industry,
            "role": profile.role,
            "audience": profile.audience,
        },
        "language": {
            "interface": profile.interface_language,
            "output": profile.output_language,
            "source_handling": profile.source_handling,
        },
        "report": {
            "cadence": profile.cadence,
            "date": "auto",
            "max_source_age_days": profile.max_source_age_days,
            "fail_on_stale_source": True,
        },
        "input": {"path": "input"},
        "output": {
            "path": "output",
            "formats": profile.output_formats,
            "filename_template": "{project_name}_{report_date}",
            "named_outputs": True,
            "footer": "Confidential — Internal Use Only",
        },
        "source": {
            "mode": profile.source_profile,
            "optional_seed_pack": profile.optional_seed_pack or None,
        },
        "pipeline": {
            "steps": [
                "source_collection",
                "scout",
                "screener",
                "analyst",
                "editor",
                "auditor",
                "formatter",
            ]
        },
        "brief_quality": {
            "min_items": 20,
            "min_zh_chars": 3000,
            "require_dates": True,
            "allow_quiet_week_exception": False,
        },
        "selector": {
            "enabled": True,
            "max_items": profile.selector_max_items,
            "require_fresh_source": True,
            "topic_diversity": True,
        },
        "retrieval": {
            "enabled": profile.retrieval_enabled,
            "provider": profile.retrieval_provider,
            "model": profile.retrieval_model,
            "chroma_dir": ".rag/chroma",
            "top_k": 5,
            "lookback_days": 365,
        },
        "audit": {
            "fail_on_missing_source": True,
            "fail_on_stale_source": True,
            "redaction_scan": True,
            "require_claim_citations": True,
        },
        "safety": {
            "no_investment_advice": True,
            "no_legal_advice": True,
            "no_trading_signals": True,
            "require_human_review": True,
        },
    }


def build_profile(profile: InitProfile) -> dict[str, Any]:
    return {
        "company": profile.company,
        "industry": profile.industry,
        "role": profile.role,
        "audience": profile.audience,
        "brief_title": profile.brief_title,
        "language": {
            "interface": profile.interface_language,
            "output": profile.output_language,
            "source_handling": profile.source_handling,
        },
        "focus": {"areas": profile.focus_areas},
    }


def build_sources(profile: InitProfile) -> dict[str, Any]:
    """Generate sources.yaml content based on the selected source profile."""
    sp = profile.source_profile

    if sp == "llm_decide":
        return _build_llm_decide_sources(profile)

    # Base: always include manual local inputs
    manual_sources = [
        {
            "name": "Local Input Directory",
            "path": "input/",
            "category": "local_files",
            "language": profile.output_language.split("-")[0] if "-" in profile.output_language else profile.output_language,
            "enabled": True,
        }
    ]

    # Profile-specific provider enables
    if sp == "conservative":
        enabled = ["manual"]
        rss_enabled = False
    elif sp == "aggressive_signal":
        enabled = ["manual", "rss"]
        rss_enabled = True
    else:  # research or custom
        enabled = ["manual", "rss"]
        rss_enabled = True

    # Seed pack: use optional_seed_pack if set and registered
    from multi_agent_brief.sources.industry_packs import get_industry_pack
    seed_tasks = []
    if profile.optional_seed_pack:
        pack = get_industry_pack(profile.optional_seed_pack)
        if pack:
            seed_tasks = pack.get("search_tasks", [])

    # web_search: always enabled as a capability; backend may be unconfigured
    if "web_search" not in enabled:
        enabled.append("web_search")
    if profile.tavily_enabled:
        web_search_config: dict[str, Any] = {
            "enabled": True,
            "backend": "tavily",
            "api_key_env": "TAVILY_API_KEY",
            "topic": "news",
            "search_depth": "basic",
            "max_results": 5,
            "recency_days": 7,
        }
        if seed_tasks:
            web_search_config["search_tasks"] = seed_tasks
    else:
        web_search_config = {
            "enabled": True,
            "backend": "",
            "max_results": 20,
            "recency_days": 7,
            "note": "No search backend configured. Set TAVILY_API_KEY and run init with --tavily to enable live search.",
        }

    return {
        "source_strategy": {
            "profile": sp,
            "industry": profile.industry_text or profile.industry,
            "enabled_providers": enabled,
        },
        "manual": {
            "enabled": True,
            "sources": manual_sources,
        },
        "rss": {
            "enabled": rss_enabled,
            "feeds": [],
        },
        "web_search": web_search_config,
        "api": {
            "enabled": False,
            "providers": [],
        },
        "mcp": {
            "enabled": False,
            "servers": [],
        },
        "filing_resolver": {
            "enabled": False,
            "tickers": [],
            "filing_types": ["10-K", "10-Q", "8-K"],
            "xbrl": True,
            "note": "Disabled by default. Set enabled: true and add tickers to activate SEC filing resolution.",
        },
    }


def _build_llm_decide_sources(profile: InitProfile) -> dict[str, Any]:
    """Generate sources.yaml for llm_decide profile: agent-readable discovery policy."""
    lang = profile.output_language.split("-")[0] if "-" in profile.output_language else profile.output_language

    # Build enabled_providers: always include manual and web_search
    enabled_providers = ["manual", "web_search"]
    # filing_resolver is available but disabled by default; enable via sources decide --merge

    return {
        "source_strategy": {
            "profile": "llm_decide",
            "decision_mode": "agent_decide",
            "requires_agent_resolution": True,
            "optional_seed_pack": profile.optional_seed_pack or None,
            "enabled_providers": enabled_providers,
        },
        "source_discovery": {
            "instruction": (
                "Read user.md first for full user context (company, industry, role, "
                "task objective, focus areas, forbidden sources). "
                "Propose search intents and candidate sources based on user.md. "
                "Write source_candidates.yaml for user review before first ingestion."
            ),
            "company": profile.company,
            "industry": profile.industry_text or profile.industry,
            "role": profile.role,
            "task_objective": profile.task_objective,
            "audience": profile.audience,
            "focus_areas": profile.focus_areas,
            "cadence": profile.cadence,
            "max_source_age_days": profile.max_source_age_days,
            "language": lang,
            "selection_goals": [
                "official company and peer company updates",
                "regulator and policy sources",
                "industry media",
                "market data",
                "customer or demand signals",
                "competitor movements",
            ],
            "source_requirements": [
                "prefer public, citable, timestamped sources",
                "prefer sources with stable URLs or RSS feeds",
                "avoid paywalled-only sources unless user provides access",
                "avoid private, confidential, internal, or material non-public information",
                "preserve source URL, source name, source tier, and published date",
            ],
            "forbidden_sources": [
                "credentials",
                "private emails",
                "private chat logs",
                "internal reports",
                "customer names",
                "confidential files",
                "material non-public information",
            ] + [s for s in profile.forbidden_sources if s],
            "review_policy": {
                "require_user_confirmation_before_first_live_ingestion": True,
                "write_candidate_sources_to": "source_candidates.yaml",
            },
        },
        "manual": {
            "enabled": True,
            "sources": [
                {
                    "name": "Local Input Directory",
                    "path": "input/",
                    "category": "local_files",
                    "language": lang,
                    "enabled": True,
                }
            ],
        },
        "rss": {"enabled": False, "feeds": []},
        "web_search": (
            {
                "enabled": True,
                "backend": "tavily",
                "api_key_env": "TAVILY_API_KEY",
                "topic": "news",
                "search_depth": "basic",
                "max_results": 5,
                "recency_days": 7,
            }
            if profile.tavily_enabled
            else {
                "enabled": True,
                "backend": "",
                "max_results": 20,
                "recency_days": 7,
                "note": "No search backend configured. Set TAVILY_API_KEY and run init with --tavily to enable live search.",
            }
        ),
        "api": {"enabled": False, "providers": []},
        "mcp": {"enabled": False, "servers": []},
        "filing_resolver": {
            "enabled": False,
            "tickers": [],
            "filing_types": ["10-K", "10-Q", "8-K"],
            "xbrl": True,
            "note": "Disabled by default. Enable via 'multi-agent-brief sources decide --merge' after reviewing source_candidates.yaml filing_sources.",
        },
    }


def build_input_readme(language: str) -> str:
    if language == "zh-CN":
        return INPUT_README_ZH
    if language == "bilingual":
        return INPUT_README_ZH + "\n---\n\n" + INPUT_README_EN
    return INPUT_README_EN


def build_user_md(profile: InitProfile) -> str:
    """Generate user.md: agent-readable briefing context, NOT source evidence."""
    lang = profile.interface_language
    focus = "\n".join(f"- {f}" for f in profile.focus_areas)

    if lang == "zh-CN":
        return _user_md_zh(profile, focus)
    if lang == "bilingual":
        return _user_md_en(profile, focus) + "\n---\n\n" + _user_md_zh(profile, focus)
    return _user_md_en(profile, focus)


def _user_md_zh(profile: InitProfile, focus: str) -> str:
    forbidden = "\n".join(f"- {s}" for s in profile.forbidden_sources) if profile.forbidden_sources else ""
    forbidden_section = f"\n## 禁止来源\n\n{forbidden}\n" if forbidden else ""
    task_section = f"\n## 任务目标\n\n{profile.task_objective}\n" if profile.task_objective else ""
    industry_raw = profile.industry_text or profile.industry
    return (
        "# 用户简报画像\n\n"
        "本文件用于帮助 agent 理解用户的简报需求。\n"
        "它不是新闻来源、不是证据来源，不应被 Scout 当作 source ingestion 输入。\n\n"
        "## 基本信息\n\n"
        f"- 公司：{profile.company}\n"
        f"- 行业：{industry_raw}\n"
        f"- 岗位：{profile.role}\n"
        f"- 阅读对象：{profile.audience}\n"
        f"- 简报标题：{profile.brief_title}\n"
        f"- 简报频率：{profile.cadence}\n"
        f"- 最大来源天数：{profile.max_source_age_days}\n"
        f"- 每期筛选条数：{profile.selector_max_items}\n"
        f"- 来源模式：{profile.source_profile}\n"
        f"- 种子包：{profile.optional_seed_pack or '无'}\n"
        f"{task_section}\n"
        "## 关注领域\n\n"
        f"{focus}\n"
        f"{forbidden_section}\n"
        "## 来源发现策略\n\n"
        "如果 source mode = llm_decide，请根据以下原则发现来源：\n\n"
        "1. 先读取本文件（user.md）理解用户的行业、岗位、任务目标和关注领域。\n"
        "2. 根据用户描述的行业和关注领域，生成搜索意图和候选来源。\n"
        "3. 优先使用公开、可引用、有发布时间的来源。\n"
        "4. 不要使用私有邮件、内部聊天记录、机密报告、客户名称、凭据、token 或重大非公开信息。\n"
        "5. 对第一次自动发现的来源，应先写入 source_candidates.yaml，等待用户确认后再进入正式 sources.yaml。\n"
        "6. 所有进入简报的事实仍必须经过 Claim Ledger 和 Auditor。\n\n"
        "## Safety\n\n"
        "This project is not investment advice, legal advice, tax advice, trading signal generation, or a replacement for human review.\n"
    )


def _user_md_en(profile: InitProfile, focus: str) -> str:
    forbidden = "\n".join(f"- {s}" for s in profile.forbidden_sources) if profile.forbidden_sources else ""
    forbidden_section = f"\n## Forbidden Sources\n\n{forbidden}\n" if forbidden else ""
    task_section = f"\n## Task Objective\n\n{profile.task_objective}\n" if profile.task_objective else ""
    industry_raw = profile.industry_text or profile.industry
    return (
        "# User Briefing Profile\n\n"
        "This file describes the user/workspace context for agents.\n"
        "It is not source evidence and must not be ingested as a report source.\n\n"
        "## Basic Information\n\n"
        f"- Company: {profile.company}\n"
        f"- Industry: {industry_raw}\n"
        f"- Role: {profile.role}\n"
        f"- Audience: {profile.audience}\n"
        f"- Brief title: {profile.brief_title}\n"
        f"- Cadence: {profile.cadence}\n"
        f"- Max source age: {profile.max_source_age_days} days\n"
        f"- Max items per brief: {profile.selector_max_items}\n"
        f"- Source mode: {profile.source_profile}\n"
        f"- Seed pack: {profile.optional_seed_pack or 'none'}\n"
        f"{task_section}\n"
        "## Focus Areas\n\n"
        f"{focus}\n"
        f"{forbidden_section}\n"
        "## Source Discovery Policy\n\n"
        "If source mode = llm_decide, use these principles:\n\n"
        "1. Read this file (user.md) first to understand the user's industry, role, task objective, and focus areas.\n"
        "2. Generate search intents and candidate sources based on the user's description.\n"
        "3. Prefer public, citable, timestamped sources.\n"
        "4. Do not use private emails, chat logs, internal reports, customer names, credentials, tokens, or MNPI.\n"
        "5. Write first-discovered sources to source_candidates.yaml for user confirmation before adding to sources.yaml.\n"
        "6. All facts entering the brief must pass through Claim Ledger and Auditor.\n\n"
        "## Safety\n\n"
        "This project is not investment advice, legal advice, tax advice, trading signal generation, or a replacement for human review.\n"
    )


def apply_rag_args(profile: InitProfile, rag: str | None, retrieval_provider: str | None) -> None:
    if rag:
        profile.retrieval_enabled = rag.lower() in {"on", "true", "yes", "y", "1"}
    if retrieval_provider:
        profile.retrieval_provider = retrieval_provider
        profile.retrieval_enabled = True
    profile.retrieval_model = retrieval_model_for_provider(profile.retrieval_provider)


def retrieval_model_for_provider(provider: str) -> str:
    if provider == "gemini":
        return "gemini-embedding-001"
    return "nomic-embed-text"


def normalize_language(value: str) -> str:
    t = value.strip().lower()
    if not t or t in ("default", "unknown", "choose for me", "默认", "不知道", "帮我选"):
        return "en-US"
    aliases = {
        "en": "en-US", "en-us": "en-US", "en_us": "en-US", "english": "en-US",
        "zh": "zh-CN", "zh-cn": "zh-CN", "zh_cn": "zh-CN", "cn": "zh-CN", "chinese": "zh-CN",
        "bilingual": "bilingual", "dual language": "bilingual",
    }
    return aliases.get(t, t)


def ask_choice(
    input_func: Callable[[str], str],
    prompt: str,
    choices: dict[str, str],
    default_key: str,
) -> str:
    answer = input_func(prompt).strip()
    return choices.get(answer or default_key, choices[default_key])


def ask_text(input_func: Callable[[str], str], prompt: str, default: str) -> str:
    answer = input_func(prompt).strip()
    return answer or default


def ask_yes_no(input_func: Callable[[str], str], prompt: str, *, default: bool) -> bool:
    answer = input_func(prompt).strip().lower()
    if not answer:
        return default
    return answer in {"y", "yes", "true", "1", "on"}


def parse_list_arg(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def parse_int(value: str | int | None, default: int) -> int:
    try:
        return int(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def to_yaml(data: Any, indent: int = 0) -> str:
    lines: list[str] = []
    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, (dict, list)):
                lines.append(" " * indent + f"{key}:")
                lines.append(to_yaml(value, indent + 2).rstrip())
            else:
                lines.append(" " * indent + f"{key}: {format_scalar(value)}")
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                lines.append(" " * indent + "-")
                lines.append(to_yaml(item, indent + 2).rstrip())
            else:
                lines.append(" " * indent + f"- {format_scalar(item)}")
    else:
        lines.append(" " * indent + format_scalar(data))
    return "\n".join(lines) + "\n"


def format_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    text = str(value)
    if text == "auto" or not text or any(char in text for char in [":", "#", "[", "]", "{", "}", ","]):
        return json.dumps(text, ensure_ascii=False)
    return json.dumps(text, ensure_ascii=False)


def _build_competitor_universe(profile: InitProfile) -> dict:
    """Generate default competitor_universe.yaml content."""
    return {
        "target": {
            "entity_id": "",
            "name": profile.company or "",
            "aliases": [],
            "relation": "direct_competitor",
            "priority": "primary",
            "geographies": [],
            "technologies": [],
        },
        "market_scope": {
            "geographies": [],
            "products": [],
            "customer_segments": [],
            "value_chain_positions": [],
        },
        "entities": [],
        "mode": "weekly_monitor",
        "enabled": False,
    }


def _build_env_example() -> str:
    """Build a .env.example file listing all supported web search backend env vars."""
    return (
        "# Web search API keys\n"
        "# Copy this file to .env and fill in the key for your chosen backend.\n"
        "# Only the backend configured in sources.yaml requires its key.\n"
        "# Never commit .env to version control.\n"
        "\n"
        "# Tavily — fast AI search (default)\n"
        "TAVILY_API_KEY=\n"
        "# Exa — deep research, papers, filings\n"
        "EXA_API_KEY=\n"
        "# Brave — independent web index\n"
        "BRAVE_SEARCH_API_KEY=\n"
        "# Firecrawl — search + full-text crawl\n"
        "FIRECRAWL_API_KEY=\n"
        "# Serper — Google SERP\n"
        "SERPER_API_KEY=\n"
    )


def _write_files(files: dict[Path, str], *, force: bool) -> None:
    for path in files:
        if path.exists() and not force:
            raise FileExistsError(f"Refusing to overwrite existing file: {path}. Use --force to overwrite.")
    for path, content in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
