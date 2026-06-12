# 功能矩阵

multi-agent-brief 所有可用功能及配置说明。

## 快速开始

```bash
# 查看所有功能及状态
multi-agent-brief features

# 根据任务获取推荐
multi-agent-brief recommend --text "追踪竞争对手和 SEC 文件"

# 将推荐应用到工作区
multi-agent-brief setup <workspace>
```

## 功能表

| 目标 | 功能 | 配置 |
|------|------|------|
| 添加本地文件 | 本地输入 | `manual（默认启用）` |
| 监控 RSS 订阅 | RSS 订阅 | ``rss.enabled: true` + 添加 `rss.feeds`` |
| 搜索新闻和数据 | 网络搜索 | ``web_search.enabled: true` + `mode: runtime_tool`，或配置外部 API 后端` |
| 搜索 NewsAPI.org | 新闻 API | ``api.enabled: true` + 设置 `NEWSAPI_API_KEY`` |
| 获取 SEC EDGAR 文件 | SEC 文件 | ``filings.enabled: true`` |
| 将股票代码解析为 XBRL | 文件解析与 XBRL | ``filing_resolver.enabled: true` + 添加 tickers` |
| 解析 PDF/DOCX/PPTX/XLSX | MinerU 文档解析 | ``mineru.enabled: true` + 设置 `MINERU_API_TOKEN`` |
| 从飞书拉取文档 | 飞书 | ``feishu.enabled: true` + 安装 lark-cli` |
| 使用预下载数据 | 离线数据包 | ``cached_package.enabled: true`` |
| 连接 MCP 服务器 | MCP 服务器 | ``mcp.enabled: true` + 配置 servers` |
| 运行 CLI 爬虫 | CLI 爬虫 | ``cli.enabled: true` + 配置 scripts` |
| 竞争对手追踪 | 市场竞争分析 | ``modules.market_competitor.enabled: true`` |
| 生成 Word 文档 | DOCX 输出 | ``output.formats: [markdown, docx]`` |
| 基于来源的事实追踪 | 事实账本 | `默认启用` |
| 确定性审计 | 审计报告 | `默认启用` |

## API 密钥

仅外部 API 功能需要。使用 runtime 自带搜索时，MABW 不需要 API key。

| 变量 | 功能 | 获取地址 |
|------|------|---------|
| `TAVILY_API_KEY` | 网络搜索 (Tavily) | https://tavily.com |
| `EXA_API_KEY` | 网络搜索 (Exa) | https://exa.ai |
| `BRAVE_SEARCH_API_KEY` | 网络搜索 (Brave) | https://brave.com/search/api |
| `FIRECRAWL_API_KEY` | 网络搜索 (Firecrawl) | https://firecrawl.dev |
| `SERPER_API_KEY` | 网络搜索 (Serper) | https://serper.dev |
| `NEWSAPI_API_KEY` | 新闻 API | https://newsapi.org/register |
| `MINERU_API_TOKEN` | MinerU 高级解析 | https://mineru.net |

无需密钥：manual, rss, filings, filing_resolver, feishu, mcp, cli, cached_package，以及 `mode: runtime_tool` 的 web_search。

将 `.env.example` 复制为 `.env` 并填入启用功能的密钥。

## CLI 命令

| 命令 | 说明 |
|------|------|
| `features` | 查看所有功能及状态 |
| `features --info <id>` | 查看单个功能详情 |
| `features --json` | 机器可读输出 |
| `recommend` | 根据任务推荐功能 |
| `setup <workspace>` | 将推荐应用到工作区 |
| `doctor` | 检查配置健康状态 |
| `init` | 创建新工作区 |
