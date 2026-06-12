# Search Backends

This document describes the runtime search option and pluggable external search backend architecture.

## User-Facing Modes

MABW supports two search paths:

1. `mode: runtime_tool` — use the current Orchestrator runtime's built-in web
   search tool. This is the zero-API-key path. MABW records the resulting
   evidence through the normal Claim Ledger and gates, but Python does not call
   a search API.
2. `mode: external_api` — use a configured Python search backend such as
   Tavily, Exa, Brave, Firecrawl, or Serper. This is better for reproducible
   source collection and reference runs, and requires the matching API key.

Example runtime-tool config:

```yaml
web_search:
  enabled: true
  mode: runtime_tool
  required_capability: web_search
```

Example external API config:

```yaml
web_search:
  enabled: true
  mode: external_api
  backend: tavily
  api_key_env: TAVILY_API_KEY
```

## Architecture

```text
WebSearchProvider
  → SearchBackend.search() → list[SearchResult]
  → SourceItem (normalized)
  → Scout → Claim Ledger
```

All backends implement `SearchBackend` (from `sources/search_backends/base.py`):
- `search(query, max_results, *, domains, **kwargs) → list[SearchResult]`
- `is_available() → bool`
- `capabilities() → SearchBackendCapabilities`

`SearchResult` carries: title, url, snippet, published_at, source_name, metadata.

`WebSearchProvider` converts `SearchResult` into `SourceItem` with standardized metadata keys.

## External API Providers

### 1. Tavily

| Field | Value |
|-------|-------|
| Role | Default AI-agent search |
| Best for | Fast public web/news search |
| API key env | `TAVILY_API_KEY` |
| Strengths | Agent-friendly snippets, simple API, news/general modes |
| Weaknesses | published_at may be missing, snippets need filtering |
| Status | **Supported** |

### 2. Exa

| Field | Value |
|-------|-------|
| Role | Semantic/deep research search |
| Best for | Company pages, research papers, financial reports, technical research, deep source discovery |
| API key env | `EXA_API_KEY` |
| Strengths | Semantic search, publishedDate, text/highlights/summary |
| Weaknesses | More complex configuration and cost control |
| Priority | **High** |

### 3. Brave Search API

| Field | Value |
|-------|-------|
| Role | Independent web index |
| Best for | General web/news search with non-Google index |
| API key env | `BRAVE_SEARCH_API_KEY` |
| Strengths | Independent index, web/news/images, useful for open-source projects |
| Weaknesses | Still mostly search-result oriented; may need extraction |
| Priority | **High** |

### 4. Firecrawl

| Field | Value |
|-------|-------|
| Role | Search + scrape/extract |
| Best for | Turning URLs/search results into clean markdown/evidence text |
| API key env | `FIRECRAWL_API_KEY` |
| Strengths | Search plus full-page markdown extraction |
| Weaknesses | Scraping cost and rate limits; must respect site terms |
| Priority | **High** |

### 5. Serper

| Field | Value |
|-------|-------|
| Role | Google SERP/News/Scholar/Patents style coverage |
| Best for | Google-like coverage with verticals |
| API key env | `SERPER_API_KEY` |
| Strengths | Google-like coverage, verticals (search, news, scholar, patents) |
| Weaknesses | Snippets often require downstream extraction |
| Priority | **Medium-high** |

### 6. SerpApi

| Field | Value |
|-------|-------|
| Role | Broad SERP provider |
| Best for | Google News, Scholar, Patents, Trends, Finance, many verticals |
| API key env | `SERPAPI_API_KEY` |
| Strengths | Many verticals, structured data |
| Weaknesses | Paid SERP parsing; can be overkill |
| Priority | **Medium** |

### 7. DataForSEO / SearchAPI.io

| Field | Value |
|-------|-------|
| Role | Enterprise SERP alternatives |
| Priority | **Later** |

### 8. Google Custom Search / Bing Search API

| Field | Value |
|-------|-------|
| Role | Legacy or not recommended as default |
| Reason | Google Custom Search is no longer ideal for new projects; Bing route is not stable |
| Priority | **Low / not default** |

## Recommendation Matrix

| Use Case | Recommended Backend |
|----------|---------------------|
| Default fast search | Tavily |
| Deep research/evidence | Exa |
| Independent web/news | Brave |
| Full-text extraction | Firecrawl |
| Google verticals | Serper or SerpApi |
| Patent/Scholar expansion | Serper/SerpApi first, later dedicated connectors |

## Configuration

### Current (single backend)

```yaml
web_search:
  enabled: true
  backend: tavily
  api_key_env: TAVILY_API_KEY
  max_results: 5
  recency_days: 7
  search_tasks:
    - query: "automotive regulation tariffs safety recalls"
      domains:
        - reuters.com
```

### Example: Exa

```yaml
web_search:
  enabled: true
  backend: exa
  api_key_env: EXA_API_KEY
  max_results: 5
  recency_days: 7
  search_tasks:
    - query: "autonomous driving L4 technology 2026"
```

### Example: Brave

```yaml
web_search:
  enabled: true
  backend: brave
  api_key_env: BRAVE_SEARCH_API_KEY
  max_results: 10
  recency_days: 7
  search_tasks:
    - query: "EV battery supply chain 2026"
```

### Example: Firecrawl

```yaml
web_search:
  enabled: true
  backend: firecrawl
  api_key_env: FIRECRAWL_API_KEY
  max_results: 3
  search_tasks:
    - query: "quarterly earnings report analysis"
```

### Example: Serper

```yaml
web_search:
  enabled: true
  backend: serper
  api_key_env: SERPER_API_KEY
  max_results: 10
  recency_days: 7
  search_tasks:
    - query: "patent filing electric vehicle battery"
```

### Future: multi-backend orchestration (not yet implemented)

```yaml
web_search:
  enabled: true
  backends:
    - name: tavily
      role: fast_agent_search
      api_key_env: TAVILY_API_KEY
      max_results: 5
    - name: exa
      role: semantic_research
      api_key_env: EXA_API_KEY
      max_results: 5
    - name: firecrawl
      role: full_text_extraction
      api_key_env: FIRECRAWL_API_KEY
      max_results: 3
```

## Metadata Standards

All backends should populate these metadata keys:

| Key | Values | Description |
|-----|--------|-------------|
| `backend` | string | Backend name (e.g. "tavily", "exa") |
| `query` | string | The search query used |
| `date_status` | "published_at_present" \| "missing_published_at" | Whether published_at was available |
| `source_temporality` | "published" \| "retrieved_only" | Whether the source has a publication date |
| `evidence_quality` | "snippet" \| "highlight" \| "full_text" | Quality of the evidence text |
| `vertical` | string (optional) | Search vertical (e.g. "news", "scholar", "patents") |
| `raw_score` | float (optional) | Backend-specific relevance score |

## Important Notes

- Web search backends differ in freshness, date quality, and evidence quality.
- Search API results are not automatically verified facts.
- Time-sensitive claims require manual verification.
- API keys must be environment variables — never stored in config, README, examples, logs, or tests.
- Tavily remains the default backend.
- Exa/Brave/Firecrawl/Serper are optional enhancements.
