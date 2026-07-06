from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable

from multi_agent_brief.audience_memory import (
    build_default_audience_profile,
    profile_data_from_object,
)
from multi_agent_brief.workspace.init_profile import InitProfile

try:
    from dotenv import load_dotenv
except ImportError:
    # dotenv is optional; fall back to os.environ only
    def load_dotenv(*args: Any, **kwargs: Any) -> None:
        pass


# Search backend configuration
_SEARCH_BACKENDS = {
    "tavily": {"env_key": "TAVILY_API_KEY", "name": "Tavily", "desc": "AI-powered web search"},
    "exa": {"env_key": "EXA_API_KEY", "name": "Exa", "desc": "Deep research, papers, filings"},
    "brave": {"env_key": "BRAVE_SEARCH_API_KEY", "name": "Brave", "desc": "Independent web index"},
    "firecrawl": {"env_key": "FIRECRAWL_API_KEY", "name": "Firecrawl", "desc": "Search + full-text crawl"},
    "serper": {"env_key": "SERPER_API_KEY", "name": "Serper", "desc": "Google SERP"},
}


def _get_configured_backends(workspace_path: Path | None = None) -> list[str]:
    """Check .env and environment for configured search backend keys."""
    # Try to load .env from workspace or current directory
    if workspace_path:
        env_file = workspace_path / ".env"
        if env_file.exists():
            load_dotenv(env_file)
    else:
        load_dotenv()

    configured = []
    for backend_key, info in _SEARCH_BACKENDS.items():
        if os.environ.get(info["env_key"]):
            configured.append(backend_key)
    return configured


def _build_search_backend_choices(
    language: str, workspace_path: Path | None = None
) -> tuple[dict[str, str], str]:
    """Build search backend choices and return (choices_dict, default_key).

    Returns:
        choices: mapping of choice_key -> backend_value
        default_key: the key to use as default (semantic, not positional)
    """
    configured = _get_configured_backends(workspace_path)
    choices: dict[str, str] = {}
    idx = 1

    # Add all supported external backends. Mark configured ones, but do not hide
    # alternatives; otherwise users think Tavily is the only supported choice.
    configured_set = set(configured)
    for backend_key, info in _SEARCH_BACKENDS.items():
        status = "configured" if backend_key in configured_set else "needs API key"
        if language == "zh-CN":
            choices[str(idx)] = f"{backend_key} ({info['name']} - {info['desc']}；{status})"
        else:
            choices[str(idx)] = f"{backend_key} ({info['desc']}; {status})"
        idx += 1

    # Add runtime-provided websearch
    runtime_key = str(idx)
    if language == "zh-CN":
        choices[runtime_key] = "runtime_websearch (运行时提供的网络搜索工具)"
    else:
        choices[runtime_key] = "runtime_websearch (Runtime-provided web search tool)"
    idx += 1

    # Add "add API key later" option
    later_key = str(idx)
    if language == "zh-CN":
        choices[later_key] = "configure_later (稍后配置 API key)"
    else:
        choices[later_key] = "configure_later (Add API key later)"

    # Default to configure_later so local-first workspaces do not require an API
    # key merely to run doctor or collect local evidence. Tavily remains the
    # recommended backend when users opt into online search.
    return choices, later_key


def _demo_published_at() -> str:
    """Return a dynamic date 1 day before today for demo source freshness."""
    from datetime import date, timedelta
    return (date.today() - timedelta(days=1)).isoformat()


def _build_demo_news() -> dict:
    return {
        "source_url": "https://example.com/synthetic-market-news",
        "published_at": _demo_published_at(),
        "items": [
            "A public market tracker reported that manufacturing output continued to expand in selected regions, with production indices rising 3.2% month-over-month.",
            "A policy update indicated that new trade regulations may affect import timelines for selected industrial products, with tariff adjustments expected next quarter.",
            "A competitor announced a major manufacturing capacity expansion plan, with commercial production expected next year. The expansion represents a $350 million capital investment.",
            "Industry analysts noted that supply chain constraints for key raw materials have eased compared to the prior quarter, with spot prices declining 6% month-over-month.",
            "A regional utility signed a long-term supply agreement with a preferred vendor, marking the largest single procurement in the sector this quarter.",
            "The Department of Commerce released updated guidance on domestic content requirements for manufactured goods, effective next quarter.",
            "Automation adoption in manufacturing facilities reached a record 42% penetration rate, driven by labor cost pressures and quality requirements.",
            "A major logistics provider announced plans to add 15 new distribution centers across North America by 2029, with priority given to industrial zones.",
            "A leading manufacturer reported a 18% year-over-year increase in order backlog, citing strong demand from construction and infrastructure sectors.",
            "Cross-border trade volumes increased 12% compared to the prior year, reflecting improved supply chain normalization.",
        ],
    }


def _build_demo_market_data() -> dict:
    return {
        "source_url": "https://example.com/synthetic-market-data",
        "published_at": _demo_published_at(),
        "items": [
            "Synthetic commodity price checks showed a 3.5% week-over-week decline in selected spot-market channels, bringing benchmark prices to $1.42 per unit.",
            "Synthetic industrial supply quotes remained broadly stable at benchmark levels for project assumptions, with steel plate at $890 per ton.",
            "Copper futures traded at $4.15/lb, up 1.8% week-over-week, reflecting tight mine supply and strong industrial demand.",
            "Industrial equipment lead times averaged 14 weeks, down from 18 weeks earlier, indicating improving supply conditions.",
            "Freight index for domestic trucking rose 2.3% month-over-month to 142.5, driven by seasonal demand and fuel cost pass-through.",
        ],
    }

DEMO_SOURCES = """source_strategy:
  profile: "conservative"
  enabled_providers:
    - "manual"

manual:
  enabled: true
  sources: []
"""

DEMO_USER_MD = """# User Context

## Company

Synthetic Corp

## Industry

Manufacturing & Industrial

## Role

Strategy Office

## Focus Areas

- Manufacturing output and capacity trends
- Trade regulation and policy impacts
- Competitor capacity announcements
- Commodity pricing and supply chain conditions

## Cadence

Weekly

## Notes

This is a synthetic demo workspace for validating the reference workflow.
All data is public-safe and fabricated for testing purposes.
"""

DEMO_CONFIG = """project:
  name: "Synthetic Market Brief Demo"
  language: "en-US"
  audience: "management"

report:
  date: "auto"
  max_source_age_days: 14
  fail_on_stale_source: true

input:
  path: "input"

output:
  path: "output"
  formats:
    - "markdown"
    - "docx"
  filename_template: "{project_name}_{report_date}"
  named_outputs: true

# Relaxed quality thresholds for synthetic demo workspace.
# Production weekly briefs should NOT set these overrides.
brief_quality:
  quiet_week: true
  allow_quiet_week_exception: true
  expected_summary_bullets: null
  required_metadata_labels: []
"""

WORKSPACE_GITIGNORE = """.env
.env.*
output/
.rag/
private_inputs/
private_outputs/
logs/
user.md
audience_profile.md
*.docx
*.pdf
*.xlsx
.DS_Store
"""

INPUT_README_EN = """# Input Folder

Use the subfolders below to keep evidence, instructions, feedback, and
background references separate.

| Folder | Use for | Enters Claim Ledger? |
|---|---|---|
| `sources/` | Current-period factual evidence: news, filings, market data, reports, RSS exports | Yes |
| `context/` | Background reference: prior briefs, example report style, company background, historical notes | No |
| `instructions/` | Task requirements, structure requirements, prompt drafts, writing rules | No |
| `feedback/` | Human comments, revision notes, previous-version feedback | No |

For prior weekly reports or example briefs, place a Markdown version in
`input/context/`. These files are style and structure references only; they are
not current-period evidence.

PDF, DOCX, PPTX, XLSX, and image inputs are supported through MinerU extraction.
Place the original file in the correct subfolder first, then run:

```bash
multi-agent-brief inputs extract --config config.yaml
multi-agent-brief inputs classify --config config.yaml
```

The extraction command writes an adjacent `.mineru.md` file. Directory role is
preserved: extracted Markdown under `input/sources/` may enter the Claim Ledger;
extracted Markdown under `input/context/`, `input/instructions/`, or
`input/feedback/` remains non-evidence.

Do not place confidential company documents, internal reports, private messages,
credentials, raw logs, or material non-public information in this folder unless
the workspace is local-only and excluded from Git.

Backward compatibility: files placed directly in `input/` may be treated as
evidence. Prefer using the subfolders above.

Recommended JSON format for `input/sources/`:

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

请使用下面的子目录区分事实来源、任务要求、修改反馈和背景参考。

| 目录 | 用途 | 是否进入 Claim Ledger |
|---|---|---|
| `sources/` | 本期事实证据：新闻、公告、市场数据、行业报告、RSS 导出等 | 是 |
| `context/` | 背景参考：往期简报、简报示例、公司背景、历史资料 | 否 |
| `instructions/` | 任务要求、结构要求、prompt 草稿、写作规则 | 否 |
| `feedback/` | 人类批注、修改意见、上一版反馈 | 否 |

请在 `input/context/` 里加入你的简报示例 Markdown 文件，例如往期周报。
这些文件只作为结构、口吻和表格风格参考，不作为本期事实来源。

PDF、DOCX、PPTX、XLSX 和图片输入通过 MinerU 抽取支持。请先把原始文件放到正确子目录，再运行：

```bash
multi-agent-brief inputs extract --config config.yaml
multi-agent-brief inputs classify --config config.yaml
```

抽取命令会在原文件旁边写入 `.mineru.md`。目录角色保持不变：
`input/sources/` 下抽取出的 Markdown 可以进入 Claim Ledger；`input/context/`、
`input/instructions/`、`input/feedback/` 下抽取出的 Markdown 仍然是非证据材料。

不要把机密公司文件、内部报告、私有消息、凭据、原始日志或重大非公开信息放入此文件夹，除非该工作区只保存在本地且已从 Git 中排除。

向后兼容：直接放在 `input/` 根目录下的文件可能被视为 evidence。
新项目请优先使用上面的子目录。

`input/sources/` 推荐 JSON 格式：

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
    sources_dir = input_dir / "sources"
    output_dir = target / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    sources_dir.mkdir(parents=True, exist_ok=True)
    # Create non-evidence subdirectories (empty, for user organization)
    for subdir in ("feedback", "instructions", "context"):
        (input_dir / subdir).mkdir(parents=True, exist_ok=True)
    files = {
        target / "config.yaml": DEMO_CONFIG,
        target / "sources.yaml": DEMO_SOURCES,
        target / "user.md": DEMO_USER_MD,
        target / "audience_profile.md": build_default_audience_profile(
            {
                "company": "Synthetic Corp",
                "industry_text": "Manufacturing & Industrial",
                "role": "strategy_office",
                "audience": "management",
                "audience_profile": "management",
                "brief_title": "Synthetic Market Brief Demo",
                "task_objective": "Validate the public-safe reference workflow with synthetic data.",
                "interface_language": "en-US",
                "output_language": "en-US",
                "cadence": "weekly",
                "source_profile": "conservative",
                "focus_areas": [
                    "Manufacturing output and capacity trends",
                    "Trade regulation and policy impacts",
                    "Competitor capacity announcements",
                    "Commodity pricing and supply chain conditions",
                ],
                "forbidden_sources": [
                    "confidential company documents",
                    "private messages",
                    "credentials",
                    "material non-public information",
                ],
                "output_formats": ["markdown", "docx"],
            }
        ),
        target / ".gitignore": WORKSPACE_GITIGNORE,
        target / ".env.example": _build_env_example(),
        input_dir / "README.md": build_input_readme("en-US"),
        sources_dir / "README.md": _build_sources_readme("en-US"),
        input_dir / "feedback" / "README.md": _build_feedback_readme("en-US"),
        input_dir / "instructions" / "README.md": _build_instructions_readme("en-US"),
        input_dir / "context" / "README.md": _build_context_readme("en-US"),
        sources_dir / "news.json": json.dumps(_build_demo_news(), indent=2),
        sources_dir / "market_data.json": json.dumps(_build_demo_market_data(), indent=2),
    }
    _write_files(files, force=force)


def create_workspace(target: Path, profile: InitProfile, *, force: bool = False) -> None:
    # Set decision mode based on source profile
    if profile.source_profile == "llm_decide":
        profile.source_decision_mode = "agent_decide"

    input_dir = target / "input"
    sources_dir = input_dir / "sources"
    output_dir = target / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    sources_dir.mkdir(parents=True, exist_ok=True)
    # Create non-evidence subdirectories with README
    for subdir in ("feedback", "instructions", "context"):
        (input_dir / subdir).mkdir(parents=True, exist_ok=True)

    lang = profile.interface_language
    files = {
        target / "config.yaml": to_yaml(build_config(profile)),
        target / "profile.yaml": to_yaml(build_profile(profile)),
        target / "sources.yaml": to_yaml(build_sources(profile)),
        target / "competitor_universe.yaml": to_yaml(_build_competitor_universe(profile)),
        target / "user.md": build_user_md(profile),
        target / "audience_profile.md": build_default_audience_profile(
            profile_data_from_object(profile)
        ),
        target / ".gitignore": WORKSPACE_GITIGNORE,
        target / ".env.example": _build_env_example(),
        input_dir / "README.md": build_input_readme(lang),
        sources_dir / "README.md": _build_sources_readme(lang),
        input_dir / "feedback" / "README.md": _build_feedback_readme(lang),
        input_dir / "instructions" / "README.md": _build_instructions_readme(lang),
        input_dir / "context" / "README.md": _build_context_readme(lang),
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
        if args.selector_max_items is not None:
            profile.selector_max_items = args.selector_max_items
        apply_rag_args(profile, args.rag, args.retrieval_provider)
        profile.output_formats = parse_list_arg(args.output_formats) or profile.output_formats
        profile.source_profile = getattr(args, "source_profile", None) or profile.source_profile
        if getattr(args, "web_search_mode", None):
            profile.web_search_mode = args.web_search_mode
            profile.web_search_enabled = args.web_search_mode != "disabled"
            if args.web_search_mode == "disabled":
                profile.tavily_enabled = False
                profile.search_backend = ""
            elif args.web_search_mode == "external_api" and not profile.search_backend:
                profile.tavily_enabled = True
                profile.search_backend = "tavily"
            elif args.web_search_mode in {"runtime_tool", "configure_later"}:
                profile.tavily_enabled = False
                profile.search_backend = ""
        if getattr(args, "search_backend", None):
            profile.search_backend = args.search_backend
            profile.web_search_mode = "external_api"
            profile.web_search_enabled = True
            profile.tavily_enabled = args.search_backend == "tavily"
        if getattr(args, "tavily", False):
            profile.tavily_enabled = True
            profile.web_search_enabled = True
            profile.web_search_mode = "external_api"
            profile.search_backend = "tavily"
        if getattr(args, "initial_news_backfill", False):
            profile.initial_news_backfill_enabled = True
        if getattr(args, "initial_news_backfill_days", None):
            profile.initial_news_backfill_days = parse_int(
                str(args.initial_news_backfill_days),
                profile.initial_news_backfill_days,
            )
        if getattr(args, "initial_news_backfill_daily_max_results", None):
            profile.initial_news_backfill_daily_max_results = parse_int(
                str(args.initial_news_backfill_daily_max_results),
                profile.initial_news_backfill_daily_max_results,
            )
        preferred_domains = parse_list_arg(getattr(args, "preferred_news_domains", None))
        if preferred_domains:
            profile.preferred_news_domains = preferred_domains
        excluded_domains = parse_list_arg(getattr(args, "excluded_news_domains", None))
        if excluded_domains:
            profile.excluded_news_domains = excluded_domains
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
        "web_search_mode",
        "search_backend",
        "initial_news_backfill",
        "initial_news_backfill_days",
        "initial_news_backfill_daily_max_results",
        "preferred_news_domains",
        "excluded_news_domains",
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

    # Web search backend selection
    web_search_enabled = ask_yes_no(input_func, prompts["web_search"], default=True)
    if web_search_enabled:
        search_choices, default_key = _build_search_backend_choices(language_choice)
        selected_display = ask_choice(
            input_func, prompts["search_backend"], search_choices, default_key
        )

        # Parse the backend value from the display string
        # Format: "backend_key (description)" or "backend_key (中文描述)"
        if "(" in selected_display:
            selected_backend = selected_display.split("(")[0].strip()
        else:
            selected_backend = selected_display.strip()

        # Map backend value to mode and set profile fields
        if selected_backend == "runtime_websearch":
            profile.web_search_enabled = True
            profile.web_search_mode = "runtime_tool"
            profile.search_backend = ""
            profile.tavily_enabled = False
        elif selected_backend == "configure_later":
            profile.web_search_enabled = True
            profile.web_search_mode = "configure_later"
            profile.search_backend = ""
            profile.tavily_enabled = False
        elif selected_backend in _SEARCH_BACKENDS:
            profile.web_search_enabled = True
            profile.web_search_mode = "external_api"
            profile.search_backend = selected_backend
            profile.tavily_enabled = selected_backend == "tavily"
        else:
            # Unknown backend, treat as configure_later
            profile.web_search_enabled = True
            profile.web_search_mode = "configure_later"
            profile.search_backend = ""
            profile.tavily_enabled = False
    else:
        profile.web_search_enabled = False
        profile.web_search_mode = "disabled"
        profile.search_backend = ""
        profile.tavily_enabled = False

    if profile.web_search_enabled and profile.source_profile == "llm_decide":
        profile.initial_news_backfill_enabled = ask_yes_no(
            input_func,
            prompts["initial_news_backfill"],
            default=False,
        )
    if profile.web_search_enabled:
        profile.preferred_news_domains = parse_list_arg(
            ask_text(input_func, prompts["preferred_news_domains"], "")
        )
        profile.excluded_news_domains = parse_list_arg(
            ask_text(input_func, prompts["excluded_news_domains"], "")
        )

    # Competitor monitoring
    competitor_enabled = ask_yes_no(input_func, prompts["competitor_module"], default=False)
    profile.competitor_module_enabled = competitor_enabled
    if competitor_enabled:
        names_raw = ask_text(input_func, prompts["competitor_names"], "")
        if names_raw.strip():
            profile.competitor_names = [n.strip() for n in names_raw.split(",") if n.strip()]

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
            "selector_max_items": "How many items should be selected for each brief? Default [20]: ",
            "rag": "Enable historical retrieval / RAG? [y/N]: ",
            "retrieval_provider": "Choose retrieval provider:\n1. Ollama local\n2. Gemini API\nDefault [1]: ",
            "outputs": "Output formats, comma-separated: ",
            "max_age": "Maximum source age in days: ",
            "source_profile": "Source profile:\n1. Conservative: official and high-confidence sources only\n2. Research: balanced official, industry, market, and research sources\n3. Aggressive signal: broader signal discovery, more noise allowed\n4. Custom: user will manually edit sources.yaml\n5. Let LLM decide: generate an agent-readable source discovery policy\nDefault [2]: ",
            "source_profile_options": {"1": "conservative", "2": "research", "3": "aggressive_signal", "4": "custom", "5": "llm_decide"},
            "web_search": (
                "Enable online search? Strongly recommended for fresh reports; "
                "if enabled, add a Tavily API key. [Y/n]: "
            ),
            "search_backend": "How should live web search be provided? ",
            "initial_news_backfill": (
                "Run initial last-7-days news discovery? This searches 20"
                " relevant news items per day. [y/N]: "
            ),
            "preferred_news_domains": (
                "Optional preferred news domains, comma-separated"
                " (leave blank for user-need-based general search): "
            ),
            "excluded_news_domains": (
                "Optional domains to exclude from discovered news, comma-separated: "
            ),
            "competitor_module": "Enable competitor monitoring? [y/N]: ",
            "competitor_names": "Enter competitor names, comma-separated (e.g. Acme Corp, Globex Inc): ",
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
                "selector_max_items": "How many items / 每期筛选多少条？Default [20]: ",
                "rag": "Enable historical retrieval / RAG? 是否启用历史检索？[y/N]: ",
                "outputs": "Output formats / 输出格式，comma-separated / 逗号分隔: ",
                "max_age": "Maximum source age in days / 最大来源天数: ",
                "source_profile": "Source profile / 信息来源策略:\n1. Conservative / 保守：仅官方和高置信来源\n2. Research / 研究：官方、行业、市场、研究来源平衡\n3. Aggressive signal / 激进信号：扩大发现范围\n4. Custom / 自定义\n5. Let LLM decide / 让 LLM 自动决定来源\nDefault [2]: ",
                "source_profile_options": {"1": "conservative", "2": "research", "3": "aggressive_signal", "4": "custom", "5": "llm_decide"},
                "web_search": (
                    "Enable online search? / 是否打开在线搜索？"
                    " If enabled, strongly add a Tavily API key. / "
                    "如打开，强烈建议添加 Tavily API。[Y/n]: "
                ),
                "search_backend": "How should live web search be provided? / 如何提供实时网络搜索？ ",
                "initial_news_backfill": (
                    "Run initial last-7-days news discovery? / 是否运行过去七天新闻查找？"
                    " 将会搜索过去七天每日二十条相关新闻。[y/N]: "
                ),
                "preferred_news_domains": (
                    "Optional preferred news domains / 可选：偏好的新闻网站域名，"
                    "comma-separated / 逗号分隔（留空则按用户需求通用搜索）: "
                ),
                "excluded_news_domains": (
                    "Optional excluded news domains / 可选：排除的新闻网站域名，"
                    "comma-separated / 逗号分隔: "
                ),
                "competitor_module": "Enable competitor monitoring? / 启用竞对监测？[y/N]: ",
                "competitor_names": "Enter competitor names / 输入竞对公司名称，comma-separated / 逗号分隔: ",
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
        "selector_max_items": "每期筛选多少条？默认 [20]：",
        "rag": "是否启用历史检索 / RAG？[y/N]：",
        "retrieval_provider": "请选择检索 provider：\n1. Ollama 本地\n2. Gemini API\n默认 [1]：",
        "outputs": "请输入输出格式，逗号分隔：",
        "max_age": "请输入最大来源天数：",
        "source_profile": "请选择信息来源策略：\n1. 保守：只使用官方和高置信来源\n2. 研究：官方、行业媒体、市场数据、研究来源平衡\n3. 激进信号：扩大信号发现范围，允许更多噪音\n4. 自定义：用户后续手动编辑 sources.yaml\n5. 让 LLM 自动决定：生成 agent 可读的来源发现策略\n默认 [2]：",
        "source_profile_options": {"1": "conservative", "2": "research", "3": "aggressive_signal", "4": "custom", "5": "llm_decide"},
        "web_search": "是否打开在线搜索？如打开，强烈建议添加 Tavily API。[Y/n]：",
        "search_backend": "如何提供实时网络搜索？ ",
        "initial_news_backfill": "是否运行过去七天新闻查找？将会搜索过去七天每日二十条相关新闻。[y/N]：",
        "preferred_news_domains": "可选：偏好的新闻网站域名，逗号分隔（留空则按用户需求通用搜索）：",
        "excluded_news_domains": "可选：排除的新闻网站域名，逗号分隔：",
        "competitor_module": "是否启用竞对监测？[y/N]：",
        "competitor_names": "请输入竞对公司名称，逗号分隔（如：XX科技、YY集团）：",
    }


def build_config(profile: InitProfile) -> dict[str, Any]:
    cfg: dict[str, Any] = {
        "project": {
            "name": profile.brief_title,
            "company": profile.company,
            "industry": profile.industry,
            "role": profile.role,
            "audience": profile.audience,
        },
        "audience_profile": {
            "id": profile.audience_profile or "default",
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
    if profile.competitor_module_enabled:
        cfg["modules"] = {
            "market_competitor": {
                "enabled": True,
                "mode": "weekly_monitor",
                "max_events": 20,
                "max_events_per_entity": 4,
                "universe_path": "competitor_universe.yaml",
            },
        }
    return cfg


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
            "name": "Local Evidence Sources",
            "path": "input/sources/",
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

    # web_search: add to enabled_providers if web search is enabled
    web_search_mode = getattr(profile, "web_search_mode", "disabled")
    if web_search_mode != "disabled":
        if "web_search" not in enabled:
            enabled.append("web_search")

    # Build web_search config using the unified function
    web_search_config = _build_web_search_config(profile)

    # Add seed_tasks if available and backend is configured
    if seed_tasks and web_search_config.get("mode") == "external_api":
        web_search_config["search_tasks"] = seed_tasks

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


def _build_web_search_config(profile: InitProfile) -> dict[str, Any]:
    """Build web_search config based on web_search_mode and backend.

    Three distinct states:
    - disabled: user chose not to enable web search
    - runtime_tool: use runtime-provided search (e.g., Claude Code, Codex)
    - external_api: use configured backend (tavily, exa, brave, etc.)
    - configure_later: enabled but no backend configured yet
    """
    # Get mode and backend from profile, with fallback to legacy tavily_enabled
    mode = getattr(profile, "web_search_mode", "disabled")
    backend = getattr(profile, "search_backend", "")

    # Legacy compatibility: if tavily_enabled is True but no external backend is
    # selected yet, override to external_api with Tavily.
    if mode in {"disabled", "configure_later"} and getattr(profile, "tavily_enabled", False):
        mode = "external_api"
        if not backend:
            backend = "tavily"

    if mode == "disabled":
        return {
            "enabled": False,
            "mode": "disabled",
        }
    elif mode == "runtime_tool":
        return {
            "enabled": True,
            "mode": "runtime_tool",
            "required_capability": "web_search",
            "max_results": 10,
            "recency_days": 7,
            "initial_news_backfill": _build_initial_news_backfill_config(profile),
            "news_source_domains": _build_news_source_domain_config(profile),
            "note": "Requires the execution runtime to provide a web search tool.",
        }
    elif mode == "external_api" and backend:
        # Configured backend (tavily, exa, brave, firecrawl, serper)
        backend_info = _SEARCH_BACKENDS.get(backend, {})
        config: dict[str, Any] = {
            "enabled": True,
            "mode": "external_api",
            "backend": backend,
            "api_key_env": backend_info.get("env_key", ""),
            "max_results": 5,
            "recency_days": 7,
            "initial_news_backfill": _build_initial_news_backfill_config(profile),
            "news_source_domains": _build_news_source_domain_config(profile),
        }
        # Add backend-specific options (only Tavily supports these)
        if backend == "tavily":
            config["topic"] = "news"
            config["search_depth"] = "basic"
        return config
    else:
        # configure_later: enabled but no backend configured
        return {
            "enabled": True,
            "mode": "configure_later",
            "status": "unconfigured",
            "max_results": 20,
            "recency_days": 7,
            "initial_news_backfill": _build_initial_news_backfill_config(profile),
            "news_source_domains": _build_news_source_domain_config(profile),
            "note": "Configure a search backend before running live retrieval.",
        }


def _build_initial_news_backfill_config(profile: InitProfile) -> dict[str, Any]:
    return {
        "enabled": bool(getattr(profile, "initial_news_backfill_enabled", False)),
        "days": int(getattr(profile, "initial_news_backfill_days", 7) or 7),
        "daily_max_results": int(
            getattr(profile, "initial_news_backfill_daily_max_results", 20) or 20
        ),
        "mode": "daily_news_windows",
        "note": (
            "When enabled, sources decide --search expands source discovery into"
            " one user-need-customized news query per day."
        ),
    }


def _build_news_source_domain_config(profile: InitProfile) -> dict[str, Any]:
    return {
        "preferred_domains": list(getattr(profile, "preferred_news_domains", []) or []),
        "excluded_domains": list(getattr(profile, "excluded_news_domains", []) or []),
        "mode": "user_configured_domains",
        "note": (
            "Optional per-workspace news source preferences. Leave empty to let"
            " the search backend discover sources from company, industry,"
            " focus areas, task objective, and audience."
        ),
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
            "search_customization": {
                "derive_queries_from": [
                    "company",
                    "industry",
                    "task_objective",
                    "focus_areas",
                    "audience",
                    "cadence",
                ],
                "avoid_fixed_personal_or_vendor_source_lists": True,
                "daily_backfill_uses_user_need_terms": True,
            },
            "news_source_selection": {
                "selection_mode": "user_need_customized",
                "preferred_domains": list(profile.preferred_news_domains),
                "excluded_domains": list(profile.excluded_news_domains),
                "allow_backend_general_search_when_empty": True,
                "derive_domains_from": [
                    "user_provided_domains",
                    "industry",
                    "focus_areas",
                    "source_profile",
                ],
                "do_not_use_fixed_personal_domain_list": True,
            },
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
        "web_search": _build_web_search_config(profile),
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


def _build_sources_readme(language: str) -> str:
    if language == "zh-CN":
        return (
            "# 外部证据来源\n\n"
            "将外部事实来源文件放在此目录：新闻、行业报告、SEC 文件、RSS 导出等。\n\n"
            "支持格式：`.json`（推荐）、`.md`、`.txt`\n\n"
            "此目录中的文件会被 Scout 提取为 Claim Ledger 中的事实声明。\n"
        )
    return (
        "# External Evidence Sources\n\n"
        "Place external factual source files here: news, industry reports, SEC filings, RSS exports, etc.\n\n"
        "Supported formats: `.json` (recommended), `.md`, `.txt`\n\n"
        "Files in this directory are extracted by Scout into the Claim Ledger as factual claims.\n"
    )


def _build_feedback_readme(language: str) -> str:
    if language == "zh-CN":
        return (
            "# 用户批注与反馈\n\n"
            "将批注、修改意见、上一版反馈放在此目录。\n\n"
            "此目录中的文件**不会**被当作事实来源进入 Claim Ledger，\n"
            "只会作为 Editor 修改方向的参考。\n"
        )
    return (
        "# User Feedback & Annotations\n\n"
        "Place annotations, revision notes, and previous-version feedback here.\n\n"
        "Files in this directory are **NOT** treated as factual evidence for the Claim Ledger.\n"
        "They are only used as editorial direction for the Editor.\n"
    )


def _build_instructions_readme(language: str) -> str:
    if language == "zh-CN":
        return (
            "# 用户任务要求\n\n"
            "将任务说明、需求文档、prompt 草稿放在此目录。\n\n"
            "此目录中的文件**不会**被当作事实来源进入 Claim Ledger，\n"
            "只会影响任务配置和报告方向。\n"
        )
    return (
        "# User Instructions\n\n"
        "Place task requirements, briefing notes, and prompt drafts here.\n\n"
        "Files in this directory are **NOT** treated as factual evidence for the Claim Ledger.\n"
        "They only influence task configuration and report direction.\n"
    )


def _build_context_readme(language: str) -> str:
    if language == "zh-CN":
        return (
            "# 背景资料\n\n"
            "将背景资料、参考材料、历史数据放在此目录。\n\n"
            "如果你有往期周报、简报样例或目标版式，请优先放入 Markdown 版本，\n"
            "例如：`input/context/previous_weekly_reference.md`。\n\n"
            "此目录中的文件**不会**被当作事实来源进入 Claim Ledger，\n"
            "可作为 Analyst / Editor 写作时的结构、口吻和背景参考。\n"
        )
    return (
        "# Background Context\n\n"
        "Place background materials, reference documents, and historical data here.\n\n"
        "If you have prior weekly reports, example briefs, or target layout references,\n"
        "prefer adding a Markdown version, for example:\n"
        "`input/context/previous_weekly_reference.md`.\n\n"
        "Files in this directory are **NOT** treated as factual evidence for the Claim Ledger.\n"
        "They serve as structure, tone, and background reference for the Analyst / Editor.\n"
    )


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
    entities = []
    for name in profile.competitor_names:
        entities.append({
            "entity_id": name.lower().replace(" ", "_"),
            "name": name,
            "aliases": [],
            "relation": "direct_competitor",
            "priority": "secondary",
            "geographies": [],
            "technologies": [],
        })
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
        "entities": entities,
        "mode": "weekly_monitor",
        "enabled": profile.competitor_module_enabled,
    }


def _build_env_example() -> str:
    """Build a .env.example file listing all supported web search backend env vars."""
    return (
        "# Web search API keys\n"
        "# Copy this file to .env and fill in the key for your chosen backend.\n"
        "# Only the backend configured in sources.yaml requires its key.\n"
        "# Never commit .env to version control.\n"
        "\n"
        "# Tavily — fast AI search (recommended)\n"
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
