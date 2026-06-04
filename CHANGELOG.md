# Changelog

All notable changes to the multi-agent-brief-workflow project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Interactive onboarding now enforces required business fields — no more silent defaults for agent/CI environments.
- `InitOnboardingRequired` exception and `missing_required_direct_init_args()` validation ensure all 7 business fields (language, company, industry, title, audience, cadence, source_profile) must be explicit in developer-only direct CLI init.
- New test coverage for non-interactive init rejection, partial-arg failure, and `--tavily`-alone validation.

### Changed

- `has_noninteractive_profile_args()` renamed to `has_direct_init_args()` for clarity.
- Quick-start guides in README and docs updated: init command is now `multi-agent-brief init <workspace>` (no flags) for interactive mode.
- All CLI tests updated to pass explicit required fields via `complete_init_args()` helper.

## [0.7.0] — 2025-07-17

### Added

- Conversational onboarding: 10-question interactive wizard replaces hidden default profile creation.
- `--from-onboarding onboarding.json` protocol for agent-driven workspace creation.
- `user.md` as primary semantic context — generated with company, industry, role, focus areas, task objectives, and forbidden sources.
- Simplified onboarding mapper: unknown industries return empty string instead of guessed slugs; raw user text preserved in `user.md`.
- Default `llm_decide` source mode: agent-driven source discovery generates `source_candidates.yaml` for user review before ingestion.
- Industry packs as optional search task accelerators (no longer used as routing mechanism).
- Tavily opt-in during interactive init; developer-only direct CLI init must provide all required business fields.
- `missing_required_direct_init_args()` validation for developer-only CLI path.
- `InitOnboardingRequired` exception to prevent silent default workspace creation.

### Changed

- Enforced interactive onboarding — non-interactive environments must use `--from-onboarding`.
- `has_noninteractive_profile_args()` renamed to `has_direct_init_args()`.
- All CLI tests updated with `complete_init_args()` helper providing 7 required business fields.
- Quick-start guides updated to interactive-first workflow.

### Fixed

- `format_scalar(None)` outputting `"None"` instead of `null`.

## [0.6.0] — 2025-07-14

### Added

- `user.md` as primary semantic context for agent-driven configuration.
- Default `llm_decide` source mode that generates `source_candidates.yaml` for user review.
- Industry packs as optional seeds (manufacturing, banking, fund, internet, general).
- Tavily opt-in during interactive init.

### Changed

- Simplified onboarding mapper — removed long keyword mapping tables; unknown industries return empty string.
- Agent prompts now instruct agents to read `user.md` first.

## [0.5.1] — 2025-07-10

### Fixed

- ScoutAgent unconditionally overwriting `context.sources` — now uses provider-collected sources when available.
- AnalystAgent only rendering 5 topics — expanded to all 10 Screener topics (compliance, demand, rates, capital, technology).
- `merge_candidates_to_sources()` auto-enabling `web_search` — merge no longer implicitly enables web search.
- `WebSearchProvider` using `hash()` for unstable `source_id` — switched to `hashlib.sha1` for cross-process consistency.
- Manual URL placeholders entering Claim Ledger — placeholder sources now carry `requires_fetch` metadata and Scout skips them automatically.
- `collect_all_sources()` silently swallowing provider exceptions — errors are now captured and included in pipeline artifacts as `collection_errors`.
- `web_search.py` nested f-string `SyntaxError` on Python 3.9 — refactored to intermediate variables.
- CI now runs `compileall` before tests to catch syntax compatibility issues early.
- `init --industry` not writing industry into `source_strategy.industry`.
- `WebSearchProvider.collect()` silently swallowing backend exceptions — errors now propagate to registry errors.
- Implemented WebSearchProvider domain filtering: `config.search_tasks` supports `domains` field, passed through to `backend.search()`.
- `doctor.py` accurate warning when `web_search` uses mock backend instead of stale Phase 1 message.
- Removed runtime `MockSearchBackend`: `web_search.enabled=true` without a real backend now fails explicitly via registry errors.
- All init profiles default to `web_search` disabled; users must configure a real backend.

## [0.5.0] — 2025-07-03

### Added

- `SourcePlanner`: generates search plans based on industry, role, and time window.
- `industry_packs.py`: industry presets (manufacturing, banking, fund, internet, general) with search tasks.
- `WebSearchProvider` with pluggable backend interface (tavily, serpapi, etc.) — no runtime mock backend shipped.
- `CachedPackageProvider`: reads pre-collected source package folders (supports OpenClaw-style workflows).
- `search_backends/` module with `SearchBackend` ABC.

### Changed

- Unified `SourceItem` — eliminated duplicate definitions in `core/schemas.py` and `sources/base.py`.
- Pipeline restructured: Source Collection → Scout → Screener → ...; Scout now reads from Provider system.
- CLI gained `--industry` and `--days` args for industry-aware automatic collection.
- Backward compatible: without source_config, still reads local files from `input_dir`.

## [0.4.0] — 2025-06-25

### Added

- `sources/` module with unified `SourceProvider` interface.
- Three source profiles: `conservative`, `research`, `aggressive_signal`.
- Manual provider: loads local `.md`/`.txt`/`.json` files and manual URL entries.
- RSS provider: fetches and parses RSS/Atom feeds with keyword filtering.
- Source normalization, deduplication, and recency filtering.
- `multi-agent-brief doctor`: checks source configuration health.
- Init wizard now asks for source profile and generates tailored `sources.yaml`.

### Added (stubs)

- Stub providers for `web_search`, `api`, `mcp`, `cli` (Phase 1 placeholders).

## [0.3.0] — 2025-06-18

### Added

- `configs/agent_roles.yaml` as single source of truth for all agent roles.
- `scripts/generate_agent_configs.py` to generate platform-specific agent configs.
- Generated Codex agents (`.codex/agents/*.toml`), skills (`.agents/skills/*/SKILL.md`).
- Generated Claude Code subagents (`.claude/agents/*.md`).
- Generated documentation (`docs/agents/`).
- `--check` mode for CI staleness detection.

## [0.2.0] — 2025-06-10

### Added

- `ScreenerAgent` between `Scout` and `Analyst` in the pipeline.
- Topic-based capacity caps across 10 topic buckets (max 160 claims total).
- Novelty scoring with source tier, claim type, and high-signal term weights.
- Previous report deduplication via text matching and theme-group detection.
- Stale source and low-confidence (T5) source exclusion.
- Previous report loader supporting `.md`, `.txt`, and `.docx` formats.
- Pre-push hook and CI check: README must be updated before pushing code changes.

## [0.1.0] — 2025-06-01

### Added

- Workspace initialization.
- User profile and task objective recording.
- Local file input.
- Source discovery and source configuration.
- Claim Ledger.
- Audit and quality checks.
- Markdown / JSON / DOCX output.
- Claude Code / Codex agent configurations.
- Open-source release safety scanning tools.

<!--
Versions above track the actual release history.
The versioning scheme follows SemVer: MAJOR.MINOR.PATCH.
-->
