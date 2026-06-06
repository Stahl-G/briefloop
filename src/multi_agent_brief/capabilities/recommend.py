"""Deterministic recommendation engine — keyword rules → capability suggestions."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from multi_agent_brief.capabilities.catalog import CAPABILITIES, get_capability
from multi_agent_brief.capabilities.models import Recommendation


# Rule: (keywords/conditions, capability_id, reason, trigger_rule)
RULES: list[tuple[list[str], str, str, str]] = [
    # Competitor / market tracking
    (
        ["竞争对手", "竞品", "competitor", "peer", "market share", "市场份额"],
        "market_competitor",
        "Task mentions competitors or market tracking",
        "keywords: competitor/market",
    ),
    # SEC filings / public company
    (
        ["财报", "上市公司", "IR", "earnings", "10-K", "10-Q", "SEC", "filing", "XBRL"],
        "filing_resolver",
        "Task mentions public company filings or earnings",
        "keywords: earnings/SEC",
    ),
    # PDF parsing
    (
        ["pdf", "PDF", "docx", "DOCX", "pptx", "PPTX", "xlsx", "XLSX"],
        "mineru",
        "Input files include PDF/DOCX/PPTX/XLSX that benefit from high-fidelity parsing",
        "file extensions in input",
    ),
    # DOCX output for management
    (
        ["管理层", "董事会", "management", "board", "executive", "汇报"],
        "docx_output",
        "Audience is management/board — DOCX output recommended",
        "keywords: management/board",
    ),
    # Feishu
    (
        ["飞书", "妙记", "会议纪要", "feishu", "lark", "会议"],
        "feishu",
        "Task mentions Feishu/Lark documents or meetings",
        "keywords: feishu/meeting",
    ),
    # RSS
    (
        ["rss", "订阅", "feed", "monitor", "追踪", "track"],
        "rss",
        "Task mentions feed monitoring or tracking",
        "keywords: rss/track",
    ),
    # Web search
    (
        [
            "搜索", "新闻", "search", "news", "market data", "市场数据", "舆情",
            "周报", "日报", "月报", "行业动态",
            "趋势", "trend", "动态", "快讯", "速报",
            "OpenAI", "Anthropic", "Agent", "AI", "大模型", "LLM",
        ],
        "web_search",
        "Task requires web search for news, industry intelligence, or market data",
        "keywords: search/news/industry/AI",
    ),
    # News API
    (
        ["newsapi", "news api", "新闻api"],
        "api_news",
        "Task mentions NewsAPI specifically",
        "keywords: newsapi",
    ),
]


def recommend_from_text(
    text: str,
    enabled_providers: set[str] | None = None,
) -> list[Recommendation]:
    """Scan text for keywords and return capability recommendations.

    Args:
        text: User-provided text (brief title, task objective, focus areas).
        enabled_providers: Already-enabled providers (to skip recommendations).

    Returns:
        List of Recommendation, deduplicated by capability_id.
    """
    text_lower = text.lower()
    seen: set[str] = set()
    results: list[Recommendation] = []

    for keywords, cap_id, reason, trigger in RULES:
        if cap_id in seen:
            continue
        if enabled_providers and cap_id in enabled_providers:
            continue
        for kw in keywords:
            if kw.lower() in text_lower:
                results.append(Recommendation(
                    capability_id=cap_id,
                    reason=reason,
                    trigger_rule=trigger,
                ))
                seen.add(cap_id)
                break

    return results


def recommend_from_input_dir(
    input_dir: str | Path,
    enabled_providers: set[str] | None = None,
) -> list[Recommendation]:
    """Scan input directory for file types and recommend capabilities."""
    input_path = Path(input_dir)
    if not input_path.exists():
        return []

    seen: set[str] = set()
    results: list[Recommendation] = []
    extensions = set()

    for f in input_path.rglob("*"):
        if f.is_file():
            extensions.add(f.suffix.lower())

    # MinerU for document parsing
    doc_exts = {".pdf", ".docx", ".pptx", ".xlsx", ".doc", ".ppt", ".xls"}
    if extensions & doc_exts:
        if "mineru" not in (enabled_providers or set()):
            results.append(Recommendation(
                capability_id="mineru",
                reason=f"Found documents with extensions: {', '.join(sorted(extensions & doc_exts))}",
                trigger_rule="file extensions in input directory",
            ))
            seen.add("mineru")

    return results


def recommend_from_config(
    config: dict[str, Any],
    enabled_providers: set[str] | None = None,
) -> list[Recommendation]:
    """Recommend capabilities based on config.yaml content."""
    results: list[Recommendation] = []
    seen: set[str] = set()

    # Check if market_competitor is mentioned but not enabled
    modules = config.get("modules", {})
    mc = modules.get("market_competitor", {})
    if mc.get("enabled") and "market_competitor" not in (enabled_providers or set()):
        pass  # already enabled, skip

    return results


def generate_setup_plan(
    recommendations: list[Recommendation],
    workspace_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Generate a setup plan from recommendations.

    Returns a dict suitable for writing to setup-plan.json.
    """
    plan: dict[str, Any] = {
        "capabilities": [],
    }

    for rec in recommendations:
        cap = get_capability(rec.capability_id)
        entry: dict[str, Any] = {
            "id": rec.capability_id,
            "name": cap.name.get("en", rec.capability_id) if cap else rec.capability_id,
            "reason": rec.reason,
            "trigger_rule": rec.trigger_rule,
            "enabled": False,
        }

        # Add config hints based on capability
        if rec.capability_id == "web_search":
            entry["config_hint"] = (
                "Choose one search backend: tavily/TAVILY_API_KEY, exa/EXA_API_KEY, "
                "brave/BRAVE_SEARCH_API_KEY, firecrawl/FIRECRAWL_API_KEY, or serper/SERPER_API_KEY; "
                "then enable web_search in sources.yaml"
            )
        elif rec.capability_id == "mineru":
            entry["config_hint"] = "Set MINERU_API_TOKEN in .env and enable mineru in sources.yaml"
        elif rec.capability_id == "filing_resolver":
            entry["config_hint"] = "Add tickers to filing_resolver in sources.yaml"
        elif rec.capability_id == "feishu":
            entry["config_hint"] = "Install lark-cli and enable feishu in sources.yaml"
        elif rec.capability_id == "market_competitor":
            entry["config_hint"] = "Set modules.market_competitor.enabled: true in config.yaml"
        elif rec.capability_id == "docx_output":
            entry["config_hint"] = "Add 'docx' to output.formats in config.yaml"
        elif rec.capability_id == "rss":
            entry["config_hint"] = "Add RSS feed URLs to rss.feeds in sources.yaml"
        elif rec.capability_id == "api_news":
            entry["config_hint"] = "Set NEWSAPI_API_KEY in .env and enable api in sources.yaml"

        plan["capabilities"].append(entry)

    return plan
