# Feature Matrix

All available features in multi-agent-brief, with configuration hints.

## Quick Start

```bash
# See all features and their status
multi-agent-brief features

# Get recommendations for your task
multi-agent-brief recommend --text "Track competitors and SEC filings"

# Apply recommendations to a workspace
multi-agent-brief setup <workspace>
```

## Feature Table

| Goal | Feature | Config |
|------|---------|--------|
| Add local files to brief | Manual Inputs | `manual (always enabled)` |
| Monitor RSS feeds | RSS Feeds | ``rss.enabled: true` + add `rss.feeds`` |
| Search the web for news/data | Web Search | ``web_search.enabled: true` + set API key` |
| Search NewsAPI.org | News API | ``api.enabled: true` + set `NEWSAPI_API_KEY`` |
| Fetch SEC EDGAR filings | SEC Filings | ``filings.enabled: true`` |
| Resolve tickers to XBRL filings | Filing Resolver & XBRL | ``filing_resolver.enabled: true` + add tickers` |
| Parse PDF/DOCX/PPTX/XLSX | MinerU | ``mineru.enabled: true` + set `MINERU_API_TOKEN`` |
| Pull docs from Feishu/Lark | Feishu / Lark | ``feishu.enabled: true` + install lark-cli` |
| Use pre-downloaded data | Cached Package | ``cached_package.enabled: true`` |
| Connect MCP servers | MCP Server | ``mcp.enabled: true` + configure servers` |
| Run CLI scrapers | CLI Scraper | ``cli.enabled: true` + configure scripts` |
| Track competitors | Market & Competitor Analysis | ``modules.market_competitor.enabled: true`` |
| Generate Word documents | DOCX Output | ``output.formats: [markdown, docx]`` |
| Source-grounded claims | Claim Ledger | `Always enabled` |
| Deterministic audit | Audit Report | `Always enabled` |

## API Keys

Required only for features you enable:

| Variable | Feature | Where to get |
|----------|---------|-------------|
| `TAVILY_API_KEY` | Web search (Tavily) | https://tavily.com |
| `EXA_API_KEY` | Web search (Exa) | https://exa.ai |
| `BRAVE_SEARCH_API_KEY` | Web search (Brave) | https://brave.com/search/api |
| `FIRECRAWL_API_KEY` | Web search (Firecrawl) | https://firecrawl.dev |
| `SERPER_API_KEY` | Web search (Serper) | https://serper.dev |
| `NEWSAPI_API_KEY` | News API | https://newsapi.org/register |
| `MINERU_API_TOKEN` | MinerU premium parsing | https://mineru.net |

No key needed: manual, rss, filings, filing_resolver, feishu, mcp, cli, cached_package.

Copy `.env.example` to `.env` in your workspace and fill in keys for enabled features.

## CLI Commands

| Command | Description |
|---------|-------------|
| `features` | Show all features and their status |
| `features --info <id>` | Show details for a specific feature |
| `features --json` | Machine-readable output |
| `recommend` | Recommend features for your task |
| `setup <workspace>` | Apply recommendations to a workspace |
| `doctor` | Check configuration health |
| `init` | Create a new workspace |
| `prepare` | Deprecated; use `/generate-brief <workspace>` |
