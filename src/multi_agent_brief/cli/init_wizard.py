from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


DEMO_NEWS = {
    "source_url": "https://example.com/synthetic-market-news",
    "published_at": "2026-06-01",
    "items": [
        "A public market tracker reported that utility-scale storage demand continued to expand in the Southwest during May 2026.",
        "A policy update indicated that new interconnection queue reforms may shorten approval timelines for selected renewable projects.",
        "A competitor announced a 2 GW manufacturing capacity expansion plan, with commercial production expected in 2027.",
    ],
}

DEMO_MARKET_DATA = {
    "source_url": "https://example.com/synthetic-market-data",
    "published_at": "2026-06-01",
    "items": [
        "Synthetic module price checks showed a 3.5% week-over-week decline in selected spot-market channels.",
        "Synthetic battery storage system quotes remained broadly stable at $140 per kWh for benchmark project assumptions.",
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
"""

WORKSPACE_GITIGNORE = """.env
.env.*
output/
.rag/
private_inputs/
private_outputs/
logs/
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
    industry: str = "finance"
    brief_title: str = "Weekly Industry Brief"
    audience: str = "management"
    focus_areas: list[str] = field(default_factory=lambda: ["policy", "competitor", "market", "customer_demand"])
    cadence: str = "weekly"
    max_source_age_days: int = 14
    selector_max_items: int = 8
    retrieval_enabled: bool = False
    retrieval_provider: str = "ollama"
    retrieval_model: str = "nomic-embed-text"
    output_formats: list[str] = field(
        default_factory=lambda: ["markdown", "claim_ledger", "audit_report", "source_map"]
    )
    source_profile: str = "research"
    source_decision_mode: str = "static"


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
        target / "user.md": build_user_md(profile),
        target / ".gitignore": WORKSPACE_GITIGNORE,
        input_dir / "README.md": build_input_readme(profile.interface_language),
    }
    _write_files(files, force=force)


def _is_interactive() -> bool:
    """Check if stdin is connected to a real terminal (not a pipe or agent Bash tool)."""
    import sys
    return sys.stdin.isatty() if hasattr(sys.stdin, "isatty") else False


def build_profile_from_args(args: Any, *, input_func: Callable[[str], str] | None = None) -> InitProfile:
    input_func = input if input_func is None else input_func
    profile = InitProfile()
    if has_noninteractive_profile_args(args):
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
        return profile
    # No CLI args → try interactive prompt
    if _is_interactive():
        try:
            return prompt_for_profile(input_func=input_func)
        except (EOFError, KeyboardInterrupt):
            print("\n[init] Interactive input interrupted. Using defaults.")
            return profile
    # Non-interactive (agent Bash tool, pipe, CI) → use defaults with a clear message
    print("[init] Non-interactive environment detected. Using default settings.")
    print("[init] To customize, run with CLI args:")
    print("  multi-agent-brief init <target> --language zh-CN --company \"Name\" --industry finance --title \"Brief\" --audience management --source-profile research")
    print("[init] Or run interactively in a real terminal: multi-agent-brief init <target>")
    return profile


def has_noninteractive_profile_args(args: Any) -> bool:
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
    return any(getattr(args, field, None) is not None for field in fields)


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
    profile.role = ask_choice(input_func, prompts["role"], prompts["role_options"], "1")
    profile.industry = ask_choice(input_func, prompts["industry"], prompts["industry_options"], "1")
    profile.brief_title = ask_text(input_func, prompts["title"], profile.brief_title)
    profile.audience = ask_choice(input_func, prompts["audience"], prompts["audience_options"], "1")
    profile.focus_areas = parse_list_arg(ask_text(input_func, prompts["focus"], ",".join(profile.focus_areas)))
    profile.cadence = ask_choice(input_func, prompts["cadence"], prompts["cadence_options"], "1")
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
            "industry": "Select industry:\n1. Finance\n2. Internet / Technology\n3. Manufacturing\nDefault [1]: ",
            "industry_options": {
                "1": "finance",
                "2": "technology",
                "3": "manufacturing",
            },
            "title": "Brief title: ",
            "audience": "Select audience:\n1. Management\n2. Strategy team\n3. Research team\n4. Investor relations\n5. Other\nDefault [1]: ",
            "audience_options": {
                "1": "management",
                "2": "strategy",
                "3": "research",
                "4": "investor_relations",
                "5": "other",
            },
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
        }
    if language == "bilingual":
        labels = prompt_labels("en-US")
        labels.update(
            {
                "company": "Company name / 公司名称: ",
                "role": "Select your role / 请选择岗位:\n1. Strategy / President Office / 总裁办・战略研究\n2. Investor Relations / 投资者关系\n3. Research Analyst / 行业研究\n4. Policy Analyst / 政策研究\n5. Management Support / 管理层支持\n6. Other / 其他\nDefault [1]: ",
                "industry": "Select industry / 请选择行业:\n1. Finance / 金融\n2. Internet / Technology / 互联网・科技\n3. Manufacturing / 制造业\nDefault [1]: ",
                "title": "Brief title / 简报标题: ",
                "audience": "Select audience / 请选择阅读对象:\n1. Management / 管理层\n2. Strategy team / 战略团队\n3. Research team / 研究团队\n4. Investor relations / 投资者关系\n5. Other / 其他\nDefault [1]: ",
                "focus": "Focus areas / 关注领域，comma-separated / 逗号分隔: ",
                "cadence": "Reporting cadence / 简报频率:\n1. Weekly / 每周\n2. Biweekly / 双周\n3. Monthly / 每月\n4. Ad hoc / 不定期\nDefault [1]: ",
                "selector_max_items": "How many news items / 每期筛选多少条新闻？Default [8]: ",
                "rag": "Enable historical retrieval / RAG? 是否启用历史检索？[y/N]: ",
                "outputs": "Output formats / 输出格式，comma-separated / 逗号分隔: ",
                "max_age": "Maximum source age in days / 最大来源天数: ",
                "source_profile": "Source profile / 信息来源策略:\n1. Conservative / 保守：仅官方和高置信来源\n2. Research / 研究：官方、行业、市场、研究来源平衡\n3. Aggressive signal / 激进信号：扩大发现范围\n4. Custom / 自定义\n5. Let LLM decide / 让 LLM 自动决定来源\nDefault [2]: ",
                "source_profile_options": {"1": "conservative", "2": "research", "3": "aggressive_signal", "4": "custom", "5": "llm_decide"},
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
        "industry": "请选择行业：\n1. 金融\n2. 互联网 / 科技\n3. 制造业\n默认 [1]：",
        "industry_options": {
            "1": "finance",
            "2": "technology",
            "3": "manufacturing",
        },
        "title": "请输入简报标题：",
        "audience": "请选择阅读对象：\n1. 管理层\n2. 战略团队\n3. 研究团队\n4. 投资者关系\n5. 其他\n默认 [1]：",
        "audience_options": {
            "1": "management",
            "2": "strategy",
            "3": "research",
            "4": "investor_relations",
            "5": "other",
        },
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
        "output": {"path": "output", "formats": profile.output_formats},
        "source": {
            "profile": profile.source_profile,
            "decision_mode": profile.source_decision_mode,
        },
        "pipeline": {
            "steps": [
                "scout",
                "selector",
                "retrieval",
                "claim_ledger",
                "analyst",
                "auditor",
                "editor",
                "formatter",
            ]
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
        web_search_enabled = False
    elif sp == "aggressive_signal":
        enabled = ["manual", "rss", "web_search"]
        rss_enabled = True
        web_search_enabled = True
    else:  # research or custom
        enabled = ["manual", "rss"]
        rss_enabled = True
        web_search_enabled = sp == "custom"

    return {
        "source_strategy": {
            "profile": sp,
            "industry": profile.industry,
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
        "web_search": {
            "enabled": web_search_enabled,
            "max_results": 20,
            "recency_days": 7,
        },
        "api": {
            "enabled": False,
            "providers": [],
        },
        "mcp": {
            "enabled": False,
            "servers": [],
        },
    }


def _build_llm_decide_sources(profile: InitProfile) -> dict[str, Any]:
    """Generate sources.yaml for llm_decide profile: agent-readable discovery policy."""
    lang = profile.output_language.split("-")[0] if "-" in profile.output_language else profile.output_language
    return {
        "source_strategy": {
            "profile": "llm_decide",
            "decision_mode": "agent_decide",
            "requires_agent_resolution": True,
        },
        "source_discovery": {
            "instruction": (
                "Let an LLM or external agent decide which sources to use based on "
                "company, industry, role, audience, focus areas, cadence, source age limit, "
                "and safety constraints. The agent must propose sources before ingestion."
            ),
            "company": profile.company,
            "industry": profile.industry,
            "role": profile.role,
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
            ],
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
        "web_search": {"enabled": False},
        "api": {"enabled": False, "providers": []},
        "mcp": {"enabled": False, "servers": []},
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
    return (
        "# 用户简报画像\n\n"
        "本文件用于帮助 Codex、Claude Code、OpenCode 或其他 agent 理解用户的简报需求。\n"
        "它不是新闻来源、不是证据来源，不应被 Scout 当作 source ingestion 输入。\n\n"
        "## 基本信息\n\n"
        f"- 公司：{profile.company}\n"
        f"- 行业：{profile.industry}\n"
        f"- 岗位：{profile.role}\n"
        f"- 阅读对象：{profile.audience}\n"
        f"- 简报标题：{profile.brief_title}\n"
        f"- 简报频率：{profile.cadence}\n"
        f"- 最大来源天数：{profile.max_source_age_days}\n"
        f"- 每期筛选条数：{profile.selector_max_items}\n"
        f"- 信息来源策略：{profile.source_profile}\n\n"
        "## 关注领域\n\n"
        f"{focus}\n\n"
        "## 来源选择偏好\n\n"
        "如果 source_profile = llm_decide，请根据以下原则选择来源：\n\n"
        "1. 优先使用公开、可引用、有发布时间的来源。\n"
        "2. 优先覆盖公司官方、同行公司、监管政策、行业媒体、市场数据、客户需求和竞争动态。\n"
        "3. 不要使用私有邮件、内部聊天记录、机密报告、客户名称、凭据、token 或重大非公开信息。\n"
        "4. 对第一次自动发现的来源，应先写入 source_candidates.yaml，等待用户确认后再进入正式 sources.yaml。\n"
        "5. 所有进入简报的事实仍必须经过 Claim Ledger 和 Auditor。\n\n"
        "## Safety\n\n"
        "This project is not investment advice, legal advice, tax advice, trading signal generation, or a replacement for human review.\n"
    )


def _user_md_en(profile: InitProfile, focus: str) -> str:
    goals = "\n".join(f"- {g}" for g in [
        "official company and peer company updates",
        "regulator and policy sources",
        "industry media",
        "market data",
        "customer or demand signals",
        "competitor movements",
    ])
    return (
        "# User Briefing Profile\n\n"
        "This file describes the user/workspace context for agents.\n"
        "It is not source evidence and must not be ingested as a report source.\n\n"
        "## Basic Information\n\n"
        f"- Company: {profile.company}\n"
        f"- Industry: {profile.industry}\n"
        f"- Role: {profile.role}\n"
        f"- Audience: {profile.audience}\n"
        f"- Brief title: {profile.brief_title}\n"
        f"- Cadence: {profile.cadence}\n"
        f"- Max source age: {profile.max_source_age_days} days\n"
        f"- Max items per brief: {profile.selector_max_items}\n"
        f"- Source profile: {profile.source_profile}\n\n"
        "## Focus Areas\n\n"
        f"{focus}\n\n"
        "## Source Selection Policy\n\n"
        "If source_profile = llm_decide, use these principles:\n\n"
        "1. Prefer public, citable, timestamped sources.\n"
        "2. Cover official company, peers, regulators, industry media, market data, demand signals, competitor moves.\n"
        "3. Do not use private emails, chat logs, internal reports, customer names, credentials, tokens, or MNPI.\n"
        "4. Write first-discovered sources to source_candidates.yaml for user confirmation before adding to sources.yaml.\n"
        "5. All facts entering the brief must pass through Claim Ledger and Auditor.\n\n"
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
    aliases = {"en": "en-US", "english": "en-US", "zh": "zh-CN", "cn": "zh-CN", "chinese": "zh-CN"}
    return aliases.get(value, value)


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
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    text = str(value)
    if text == "auto" or not text or any(char in text for char in [":", "#", "[", "]", "{", "}", ","]):
        return json.dumps(text, ensure_ascii=False)
    return json.dumps(text, ensure_ascii=False)


def _write_files(files: dict[Path, str], *, force: bool) -> None:
    for path in files:
        if path.exists() and not force:
            raise FileExistsError(f"Refusing to overwrite existing file: {path}. Use --force to overwrite.")
    for path, content in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
