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

## [0.1.0] — 2026-06-04

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
