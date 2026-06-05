# Changelog

All notable changes to the multi-agent-brief-workflow project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] — 2026-06-05

### Added

- **FilingResolverProvider**: New source provider that integrates [disclosure-filing-resolver](https://github.com/Stahl-G/disclosure-filing-resolver) for automatic SEC EDGAR filing acquisition. Fetches 10-K, 10-Q, 8-K, 6-K filings, extracts XBRL financial data (revenue, net income, assets, EPS), and converts them to Claim Ledger entries. 22 tests.
- **filing-resolver source discovery integration**: `sources decide` now generates `filing_sources` candidates when company name is available. `sources decide --merge` enables `filing_resolver` provider and merges tickers into `sources.yaml`. 6 tests.
- **filing_resolver workspace template**: All source profiles (llm_decide, research, conservative, etc.) now include a `filing_resolver` config section in `sources.yaml` — disabled by default, enabled via `sources decide --merge` or manual config.
- **MineruProvider remote API mode**: Two new modes alongside local CLI. "Agent" mode uses MinerU's lightweight cloud API (no token needed, `https://mineru.net/api/v1/agent/parse`). "Premium" mode uses the full API with Bearer token (`https://mineru.net/api/v4/extract`). Both support URL and local file upload paths. All HTTP calls via `urllib.request` — zero extra dependencies.
- **docs/mineru-integration.md**: New section covering remote API setup, agent vs. premium comparison table, configuration examples.
- **Tests**: 6 new remote-mode tests (disabled, no files, validate, agent URL mock, premium URL mock).

### Fixed

- **CI smoke tests**: Replaced broken inline `python -c` blocks in GitHub Actions workflow with standalone `scripts/ci/smoke_pipeline.py` script. Fixes YAML parsing errors introduced by MinerU PR.

## [0.1.2] — 2026-06-04

### Added

- **Feishu bidirectional integration via lark-cli**: New `FeishuProvider` (sources/feishu_provider.py) pulls data from Feishu Docs, Meeting Minutes, Base tables, Spreadsheets, Calendar, and Approval tasks. `FeishuDeliveryConnector` (delivery/feishu.py) sends briefs to Feishu chat, creates Feishu documents, and uploads files to Drive.
- `.env.example` now lists all 5 search backends (Tavily, Exa, Brave, Firecrawl, Serper) with comments — generated on every workspace init, not just when Tavily is enabled.
- **Free-text onboarding**: `audience`, `role`, `industry`, `cadence` all changed from numbered-choice (`ask_choice`) to free-text input (`ask_text`). Users can type "市场团队" or "solar" directly instead of being forced to pick from a numbered menu.
- **New tests**: 13 new tests covering MCP JSON-RPC lifecycle, NewsAPI name filtering, CLI error_type, FeishuProvider validation/collection/delivery.

### Changed

- **Agent onboarding hardening**: Removed "choose sensible defaults" from all agent instructions. All 6 `normalize_*` functions in `onboarding/mapper.py` no longer silently convert sentinel values to defaults. CLI validates company/industry/title after `--from-onboarding`.
- **`multi-agent-brief run` removed**: The deterministic Python pipeline no longer runs via CLI. Users are redirected to `/generate-brief <workspace>` in Claude Code. Pipeline code (`BriefPipeline`, agents, audit) remains for internal testing.
- **doctor error messages** now point to `.env.example` instead of vague "set environment variable".

### Fixed

- **MCP Provider**: Fixed `text=True` + bytes write type error; added `_readline_timeout()` with `select.select()` for real timeout enforcement.
- **NewsAPI validate_config**: Now filters providers by `name == "newsapi"` before checking API key — no longer false-positives when `sec` or other providers share the config section.
- **CLI Provider**: Non-zero exit items now set `metadata.error_type = "CliExecutionError"`, caught by `registry._is_error_or_placeholder()`.
- **Feishu validate_config**: Removed early return when lark-cli is missing (fixes CI). Removed `--format json` from `auth status` calls (flag not supported by lark-cli).

## [0.1.1] — 2026-06-04

First public release. The following entries document the development iterations that led to this release.

### Development iterations

#### Iteration 7 — Interactive onboarding enforcement

- Conversational onboarding: 10-question interactive wizard replaces hidden default profile creation.
- `--from-onboarding onboarding.json` protocol for agent-driven workspace creation.
- Non-interactive environments must use `--from-onboarding`; partial CLI args are rejected.
- All CLI tests updated with `complete_init_args()` helper providing 7 required business fields.
- Doc files updated for interactive-first workflow.

#### Iteration 6 — Profile-driven source discovery

- `user.md` as primary semantic context — generated with company, industry, role, focus areas, task objectives, and forbidden sources.
- Simplified onboarding mapper: unknown industries return empty string instead of guessed slugs; raw user text preserved in `user.md`.
- Default `llm_decide` source mode: agent-driven source discovery generates `source_candidates.yaml` for user review before ingestion.
- Industry packs as optional seeds (no longer used as routing mechanism).
- Tavily opt-in during interactive init; developer-only direct CLI init requires all required business fields.
- Fixed `format_scalar(None)` outputting `"None"` instead of `null`.

#### Iteration 5.1 — Source provider pipeline fixes

- Fixed ScoutAgent unconditionally overwriting `context.sources`.
- Fixed AnalystAgent only rendering 5 topics — expanded to all 10 Screener topics.
- Fixed `merge_candidates_to_sources()` auto-enabling `web_search`.
- Fixed `WebSearchProvider` using `hash()` for unstable `source_id` — switched to `hashlib.sha1`.
- Fixed manual URL placeholders entering Claim Ledger.
- Fixed `collect_all_sources()` silently swallowing provider exceptions.
- Fixed `web_search.py` nested f-string `SyntaxError` on Python 3.9.
- Fixed `init --industry` not writing industry into `source_strategy.industry`.
- Implemented WebSearchProvider domain filtering.
- Removed runtime `MockSearchBackend`: `web_search.enabled=true` without a real backend fails explicitly.

#### Iteration 5 — Three-layer source collection architecture

- Added `SourcePlanner`: generates search plans based on industry, role, and time window.
- Added `industry_packs.py`: industry presets (manufacturing, banking, fund, internet, general) with search tasks.
- `WebSearchProvider` with pluggable backend interface (tavily, serpapi, etc.).
- Added `CachedPackageProvider`: reads pre-collected source package folders.
- Added `search_backends/` module with `SearchBackend` ABC.
- Unified `SourceItem` — eliminated duplicate definitions.
- Pipeline restructured: Source Collection → Scout → Screener → ...
- CLI gained `--industry` and `--days` args.

#### Iteration 4 — Source provider system

- Added `sources/` module with unified `SourceProvider` interface.
- Three source profiles: `conservative`, `research`, `aggressive_signal`.
- Manual provider: loads local `.md`/`.txt`/`.json` files and manual URL entries.
- RSS provider: fetches and parses RSS/Atom feeds with keyword filtering.
- Source normalization, deduplication, and recency filtering.
- `multi-agent-brief doctor`: checks source configuration health.
- Init wizard asks for source profile and generates tailored `sources.yaml`.
- Stub providers for `web_search`, `api`, `mcp`, `cli`.

#### Iteration 3 — Agent config generation

- `configs/agent_roles.yaml` as single source of truth for all agent roles.
- `scripts/generate_agent_configs.py` to generate platform-specific agent configs.
- Generated Codex agents, skills, Claude Code subagents.
- Generated documentation (`docs/agents/`).
- `--check` mode for CI staleness detection.

#### Iteration 2 — Screener agent

- `ScreenerAgent` between `Scout` and `Analyst` in the pipeline.
- Topic-based capacity caps across 10 topic buckets (max 160 claims total).
- Novelty scoring with source tier, claim type, and high-signal term weights.
- Previous report deduplication via text matching and theme-group detection.
- Stale source and low-confidence (T5) source exclusion.
- Pre-push hook and CI check: README must be updated before pushing code changes.

#### Iteration 1 — MVP pipeline

- Workspace initialization.
- User profile and task objective recording.
- Local file input.
- Source discovery and source configuration.
- Claim Ledger.
- Audit and quality checks.
- Markdown / JSON / DOCX output.
- Claude Code / Codex agent configurations.
- Open-source release safety scanning tools.
