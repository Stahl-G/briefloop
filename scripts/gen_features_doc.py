#!/usr/bin/env python3
"""Auto-generate docs/features.md and docs/features.zh-CN.md from capability catalog."""
from __future__ import annotations

from pathlib import Path

from multi_agent_brief.capabilities.catalog import CAPABILITIES

REPO_ROOT = Path(__file__).parent.parent

CATEGORY_LABELS_EN = {
    "source": "Source Providers",
    "processing": "Processing",
    "analysis": "Analysis Modules",
    "output": "Output Formats",
    "integration": "Integration",
}

CATEGORY_LABELS_ZH = {
    "source": "数据源",
    "processing": "处理",
    "analysis": "分析模块",
    "output": "输出格式",
    "integration": "集成",
}

VISIBILITY_ORDER = {"core": 0, "standard": 1, "advanced": 2, "internal": 3}


def generate_en() -> str:
    lines = [
        "# Feature Matrix",
        "",
        "All available features in multi-agent-brief, with configuration hints.",
        "",
        "## Quick Start",
        "",
        "```bash",
        "# See all features and their status",
        "multi-agent-brief features",
        "",
        "# Get recommendations for your task",
        "multi-agent-brief recommend --text \"Track competitors and SEC filings\"",
        "",
        "# Apply recommendations to a workspace",
        "multi-agent-brief setup <workspace>",
        "```",
        "",
        "## Feature Table",
        "",
        "| Goal | Feature | Config |",
        "|------|---------|--------|",
    ]

    goal_map = {
        "manual": ("Add local files to brief", "manual (always enabled)"),
        "rss": ("Monitor RSS feeds", "`rss.enabled: true` + add `rss.feeds`"),
        "web_search": ("Search the web for news/data", "`web_search.enabled: true` + set API key"),
        "api_news": ("Search NewsAPI.org", "`api.enabled: true` + set `NEWSAPI_API_KEY`"),
        "filings": ("Fetch SEC EDGAR filings", "`filings.enabled: true`"),
        "filing_resolver": ("Resolve tickers to XBRL filings", "`filing_resolver.enabled: true` + add tickers"),
        "mineru": ("Parse PDF/DOCX/PPTX/XLSX", "`mineru.enabled: true` + set `MINERU_API_TOKEN`"),
        "feishu": ("Pull docs from Feishu/Lark", "`feishu.enabled: true` + install lark-cli"),
        "cached_package": ("Use pre-downloaded data", "`cached_package.enabled: true`"),
        "mcp": ("Connect MCP servers", "`mcp.enabled: true` + configure servers"),
        "cli_provider": ("Run CLI scrapers", "`cli.enabled: true` + configure scripts"),
        "market_competitor": ("Track competitors", "`modules.market_competitor.enabled: true`"),
        "docx_output": ("Generate Word documents", "`output.formats: [markdown, docx]`"),
        "claim_ledger": ("Source-grounded claims", "Always enabled"),
        "audit_report": ("Deterministic audit", "Always enabled"),
    }

    for cap in CAPABILITIES:
        goal, config_hint = goal_map.get(cap.id, (cap.summary.get("en", ""), "see docs"))
        lines.append(f"| {goal} | {cap.name.get('en', cap.id)} | `{config_hint}` |")

    lines.extend([
        "",
        "## API Keys",
        "",
        "Required only for features you enable:",
        "",
        "| Variable | Feature | Where to get |",
        "|----------|---------|-------------|",
        "| `TAVILY_API_KEY` | Web search (Tavily) | https://tavily.com |",
        "| `EXA_API_KEY` | Web search (Exa) | https://exa.ai |",
        "| `BRAVE_SEARCH_API_KEY` | Web search (Brave) | https://brave.com/search/api |",
        "| `FIRECRAWL_API_KEY` | Web search (Firecrawl) | https://firecrawl.dev |",
        "| `SERPER_API_KEY` | Web search (Serper) | https://serper.dev |",
        "| `NEWSAPI_API_KEY` | News API | https://newsapi.org/register |",
        "| `MINERU_API_TOKEN` | MinerU premium parsing | https://mineru.net |",
        "",
        "No key needed: manual, rss, filings, filing_resolver, feishu, mcp, cli, cached_package.",
        "",
        "Copy `.env.example` to `.env` in your workspace and fill in keys for enabled features.",
        "",
        "## CLI Commands",
        "",
        "| Command | Description |",
        "|---------|-------------|",
        "| `features` | Show all features and their status |",
        "| `features --info <id>` | Show details for a specific feature |",
        "| `features --json` | Machine-readable output |",
        "| `recommend` | Recommend features for your task |",
        "| `setup <workspace>` | Apply recommendations to a workspace |",
        "| `doctor` | Check configuration health |",
        "| `init` | Create a new workspace |",
        "| `prepare` | Deprecated; use `/generate-brief <workspace>` |",
    ])

    return "\n".join(lines) + "\n"


def generate_zh() -> str:
    lines = [
        "# 功能矩阵",
        "",
        "multi-agent-brief 所有可用功能及配置说明。",
        "",
        "## 快速开始",
        "",
        "```bash",
        "# 查看所有功能及状态",
        "multi-agent-brief features",
        "",
        "# 根据任务获取推荐",
        "multi-agent-brief recommend --text \"追踪竞争对手和 SEC 文件\"",
        "",
        "# 将推荐应用到工作区",
        "multi-agent-brief setup <workspace>",
        "```",
        "",
        "## 功能表",
        "",
        "| 目标 | 功能 | 配置 |",
        "|------|------|------|",
    ]

    goal_map = {
        "manual": ("添加本地文件", "manual（默认启用）"),
        "rss": ("监控 RSS 订阅", "`rss.enabled: true` + 添加 `rss.feeds`"),
        "web_search": ("搜索新闻和数据", "`web_search.enabled: true` + 设置 API key"),
        "api_news": ("搜索 NewsAPI.org", "`api.enabled: true` + 设置 `NEWSAPI_API_KEY`"),
        "filings": ("获取 SEC EDGAR 文件", "`filings.enabled: true`"),
        "filing_resolver": ("将股票代码解析为 XBRL", "`filing_resolver.enabled: true` + 添加 tickers"),
        "mineru": ("解析 PDF/DOCX/PPTX/XLSX", "`mineru.enabled: true` + 设置 `MINERU_API_TOKEN`"),
        "feishu": ("从飞书拉取文档", "`feishu.enabled: true` + 安装 lark-cli"),
        "cached_package": ("使用预下载数据", "`cached_package.enabled: true`"),
        "mcp": ("连接 MCP 服务器", "`mcp.enabled: true` + 配置 servers"),
        "cli_provider": ("运行 CLI 爬虫", "`cli.enabled: true` + 配置 scripts"),
        "market_competitor": ("竞争对手追踪", "`modules.market_competitor.enabled: true`"),
        "docx_output": ("生成 Word 文档", "`output.formats: [markdown, docx]`"),
        "claim_ledger": ("基于来源的索赔追踪", "默认启用"),
        "audit_report": ("确定性审计", "默认启用"),
    }

    for cap in CAPABILITIES:
        goal, config_hint = goal_map.get(cap.id, (cap.summary.get("zh", ""), "见文档"))
        lines.append(f"| {goal} | {cap.name.get('zh', cap.id)} | `{config_hint}` |")

    lines.extend([
        "",
        "## API 密钥",
        "",
        "仅为启用的功能需要：",
        "",
        "| 变量 | 功能 | 获取地址 |",
        "|------|------|---------|",
        "| `TAVILY_API_KEY` | 网络搜索 (Tavily) | https://tavily.com |",
        "| `EXA_API_KEY` | 网络搜索 (Exa) | https://exa.ai |",
        "| `BRAVE_SEARCH_API_KEY` | 网络搜索 (Brave) | https://brave.com/search/api |",
        "| `FIRECRAWL_API_KEY` | 网络搜索 (Firecrawl) | https://firecrawl.dev |",
        "| `SERPER_API_KEY` | 网络搜索 (Serper) | https://serper.dev |",
        "| `NEWSAPI_API_KEY` | 新闻 API | https://newsapi.org/register |",
        "| `MINERU_API_TOKEN` | MinerU 高级解析 | https://mineru.net |",
        "",
        "无需密钥：manual, rss, filings, filing_resolver, feishu, mcp, cli, cached_package。",
        "",
        "将 `.env.example` 复制为 `.env` 并填入启用功能的密钥。",
        "",
        "## CLI 命令",
        "",
        "| 命令 | 说明 |",
        "|------|------|",
        "| `features` | 查看所有功能及状态 |",
        "| `features --info <id>` | 查看单个功能详情 |",
        "| `features --json` | 机器可读输出 |",
        "| `recommend` | 根据任务推荐功能 |",
        "| `setup <workspace>` | 将推荐应用到工作区 |",
        "| `doctor` | 检查配置健康状态 |",
        "| `init` | 创建新工作区 |",
        "| `prepare` | 已废弃；请使用 `/generate-brief <workspace>` |",
    ])

    return "\n".join(lines) + "\n"


def main() -> int:
    en_path = REPO_ROOT / "docs" / "features.md"
    zh_path = REPO_ROOT / "docs" / "features.zh-CN.md"

    en_path.write_text(generate_en(), encoding="utf-8")
    zh_path.write_text(generate_zh(), encoding="utf-8")

    print(f"[gen_features_doc] Generated {en_path}")
    print(f"[gen_features_doc] Generated {zh_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
