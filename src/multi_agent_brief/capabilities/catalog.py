"""Built-in capability registry — single source of truth for all features."""
from __future__ import annotations

from multi_agent_brief.capabilities.models import CapabilityOption, CapabilitySpec

CAPABILITIES: list[CapabilitySpec] = [
    # ── Source providers ──
    CapabilitySpec(
        id="manual",
        name={"en": "Manual Inputs", "zh": "本地输入"},
        summary={"en": "Local markdown/text files in input/", "zh": "本地 markdown/text 文件输入"},
        category="source",
        provider_name="manual",
        visibility="core",
        maturity="stable",
    ),
    CapabilitySpec(
        id="rss",
        name={"en": "RSS Feeds", "zh": "RSS 订阅"},
        summary={"en": "Monitor RSS/Atom feeds for new content", "zh": "监控 RSS/Atom 订阅源"},
        category="source",
        provider_name="rss",
        visibility="standard",
        maturity="stable",
        requirements=["feeds configured in sources.yaml"],
    ),
    CapabilitySpec(
        id="web_search",
        name={"en": "Web Search", "zh": "网络搜索"},
        summary={"en": "Search news and market data via multiple backends", "zh": "通过多种后端搜索新闻和市场数据"},
        category="source",
        provider_name="web_search",
        visibility="standard",
        maturity="stable",
        options=[
            CapabilityOption(id="tavily", name="Tavily", description="Fast AI search — https://tavily.com"),
            CapabilityOption(id="exa", name="Exa", description="Deep research, papers — https://exa.ai"),
            CapabilityOption(id="brave", name="Brave", description="Independent web index — https://brave.com/search/api"),
            CapabilityOption(id="firecrawl", name="Firecrawl", description="Search + full-text crawl — https://firecrawl.dev"),
            CapabilityOption(id="serper", name="Serper", description="Google SERP — https://serper.dev"),
        ],
        requirements=["API key in .env"],
    ),
    CapabilitySpec(
        id="api_news",
        name={"en": "News API", "zh": "新闻 API"},
        summary={"en": "NewsAPI.org article search", "zh": "NewsAPI.org 文章搜索"},
        category="source",
        provider_name="api",
        visibility="standard",
        maturity="stable",
        requirements=["NEWSAPI_API_KEY in .env"],
    ),
    CapabilitySpec(
        id="filings",
        name={"en": "SEC Filings", "zh": "SEC 文件"},
        summary={"en": "Raw SEC EDGAR filings via public API", "zh": "通过公共 API 获取 SEC EDGAR 原始文件"},
        category="source",
        provider_name="filings",
        visibility="advanced",
        maturity="stable",
        requirements=["SEC_USER_AGENT env var recommended"],
    ),
    CapabilitySpec(
        id="filing_resolver",
        name={"en": "Filing Resolver & XBRL", "zh": "文件解析与 XBRL"},
        summary={"en": "Resolve tickers to SEC filings with XBRL extraction", "zh": "将股票代码解析为 SEC 文件并提取 XBRL"},
        category="source",
        provider_name="filing_resolver",
        visibility="advanced",
        maturity="stable",
        requirements=["tickers configured in sources.yaml"],
    ),
    CapabilitySpec(
        id="mineru",
        name={"en": "MinerU", "zh": "MinerU 文档解析"},
        summary={"en": "Parse PDF/DOCX/PPTX/XLSX with high fidelity", "zh": "高精度解析 PDF/DOCX/PPTX/XLSX"},
        category="processing",
        provider_name="mineru",
        visibility="standard",
        maturity="stable",
        options=[
            CapabilityOption(id="local_cli", name="Local CLI", description="Local command-line mode"),
            CapabilityOption(id="agent_api", name="Agent API", description="Lightweight API, no token"),
            CapabilityOption(id="premium_api", name="Premium API", description="Premium API, highest accuracy"),
        ],
        requirements=["MINERU_API_TOKEN in .env (for remote modes)"],
    ),
    CapabilitySpec(
        id="feishu",
        name={"en": "Feishu / Lark", "zh": "飞书"},
        summary={"en": "Pull docs, meetings, and minutes from Feishu", "zh": "从飞书拉取文档、会议和纪要"},
        category="source",
        provider_name="feishu",
        visibility="standard",
        maturity="beta",
        requirements=["lark-cli installed"],
    ),
    CapabilitySpec(
        id="cached_package",
        name={"en": "Cached Package", "zh": "离线数据包"},
        summary={"en": "Pre-downloaded local data files", "zh": "预下载的本地数据文件"},
        category="source",
        provider_name="cached_package",
        visibility="standard",
        maturity="stable",
    ),
    CapabilitySpec(
        id="mcp",
        name={"en": "MCP Server", "zh": "MCP 服务器"},
        summary={"en": "Connect to Model Context Protocol servers", "zh": "连接 MCP 服务器"},
        category="integration",
        provider_name="mcp",
        visibility="advanced",
        maturity="beta",
        requirements=["mcp.servers configured in sources.yaml"],
    ),
    CapabilitySpec(
        id="cli_provider",
        name={"en": "CLI Scraper", "zh": "CLI 爬虫"},
        summary={"en": "Run external CLI scripts and parse output as sources", "zh": "运行外部 CLI 脚本并将输出作为数据源"},
        category="integration",
        provider_name="cli",
        visibility="advanced",
        maturity="beta",
        requirements=["scripts configured in mcp.cli section of sources.yaml"],
    ),
    # ── Analysis modules ──
    CapabilitySpec(
        id="market_competitor",
        name={"en": "Market & Competitor Analysis", "zh": "市场竞争分析"},
        summary={"en": "Competitor tracking, event matrix, coverage report", "zh": "竞争对手追踪、事件矩阵、覆盖报告"},
        category="analysis",
        provider_name="market_competitor",
        visibility="standard",
        maturity="stable",
        requirements=["modules.market_competitor.enabled: true in config.yaml"],
    ),
    # ── Output formats ──
    CapabilitySpec(
        id="docx_output",
        name={"en": "DOCX Output", "zh": "DOCX 输出"},
        summary={"en": "Generate Word documents from briefs", "zh": "从简报生成 Word 文档"},
        category="output",
        provider_name="docx",
        visibility="standard",
        maturity="stable",
        requirements=["python-docx installed, output.formats includes docx"],
    ),
    CapabilitySpec(
        id="claim_ledger",
        name={"en": "Claim Ledger", "zh": "索赔账本"},
        summary={"en": "Source-grounded claim tracking with stable IDs", "zh": "基于来源的索赔追踪"},
        category="output",
        provider_name="claim_ledger",
        visibility="core",
        maturity="stable",
    ),
    CapabilitySpec(
        id="audit_report",
        name={"en": "Audit Report", "zh": "审计报告"},
        summary={"en": "Deterministic source/freshness audit", "zh": "确定性来源/时效审计"},
        category="output",
        provider_name="audit",
        visibility="core",
        maturity="stable",
    ),
]

# Fast lookup by ID
_CAPABILITY_MAP: dict[str, CapabilitySpec] = {c.id: c for c in CAPABILITIES}


def get_capability(capability_id: str) -> CapabilitySpec | None:
    """Look up a capability by its stable ID."""
    return _CAPABILITY_MAP.get(capability_id)


def list_capabilities(category: str | None = None, visibility: str | None = None) -> list[CapabilitySpec]:
    """List capabilities, optionally filtered by category or visibility."""
    result = CAPABILITIES
    if category:
        result = [c for c in result if c.category == category]
    if visibility:
        result = [c for c in result if c.visibility == visibility]
    return result
