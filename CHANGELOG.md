# Changelog

All notable changes to the multi-agent-brief-workflow project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.5.8] — 2026-06-07

### Changed

- **版本号 0.5.7 → 0.5.8**：上游 `check_release_consistency.py` 要求版号与 tag 一致；0.5.7 从未打 tag，本次统一发布。
- **README 清理**：移除尚不可用的 CLI-only curl 安装路径和 Homebrew 引用（打包工作推迟到 v0.7）。
- **旧 `prepare` 叙事清理**：删除五份遗留 impl-plan 文档（`v0.4.0`、`v0.5.0`、`v0.5.1-*`、`v0.5.5-hermes-adapter`）和 `v1-pre-mas-refactor-roadmap.zh-CN.md`——旧执行计划和引用全部移除。最新路线图见 `docs/roadmap.zh-CN.md`。

### Added

- **`docs/support-matrix.md`**：建表明确所有能力的 Supported / Experimental / Interface Only / CLI-only / Deprecated 状态。
- **Issue [#49](https://github.com/Stahl-G/multi-agent-brief-workflow/issues/49) 边界明确化**：README 安装文档澄清 — agent assets（`.agents/`、`.claude/` 等）需 source clone 才能使用子智能体工作流。pip-only 安装仅提供确定性 CLI 命令。正式打包推迟到 v0.7。

## [0.5.7] — 2026-06-07

### Added

- **`inputs classify` CLI 命令**：`multi-agent-brief inputs classify --config <path>` 扫描 `input/` 各子目录，按角色（evidence / feedback / instruction / context）分类输出 `input_classification.json`，作为 Scout 之前的输入治理门禁。
- **Scout 技能合约收紧**：Scout 限定只从 `input/sources/`（和 `input/` 根目录，向后兼容）提取声明。`feedback/`、`instructions/`、`context/` 中的文件被显式排除——它们作为编辑指导、任务要求和背景参考路由给 Editor/Analyst，不进入 Claim Ledger。
- **SourceItem `input_subdir` 元数据**：`ManualProvider._load_local_path()` 写入 `metadata["input_subdir"]`（值如 `"sources"`、`"root"`、`"feedback"`），标记文件所属输入子目录。
- **Hermes adapter / start_commands / docs**：`inputs classify` 的 "(if available)" 后缀已移除，命令现已正式可用。

## [0.5.6] — 2026-06-07

### Changed

- **Thin CLI router**: `main.py` reduced from 1512 to 134 lines. Every command group owns its subparser registration and handler in a dedicated `cli/*_commands.py` module. No user-visible behavior changes.
- **Generator scope**: `scripts/generate_agent_configs.py` now generates only platform adapters (`codex`, `claude`, `docs`, `opencode`). `agents_md` and `skills` targets removed. `--allow-prompt-overwrite` flag removed.
- **Anthropic Skills convergence**: All 17 `.agents/skills/*/SKILL.md` rewritten as short capability contracts with `Scope / Purpose / Use When / Inputs / Outputs / Work / Handoff` structure. Frontmatter descriptions are concrete routing instructions with artifact paths and pipeline ordering.
- **Hermes progressive disclosure**: Hermes skill SKILL.md kept short (~60 lines). Detailed `delegate_task` templates, cron patterns, and source cache contract moved to `references/`.
- **Formatter role updated**: `configs/agent_roles.yaml` output_contract replaced with actual pipeline artifacts. Formatter role description updated to reader-facing finalize semantics from the old "preparation artifacts" contract.
- **Examples workspace**: `examples/workspaces/weekly-brief-zh/` added as a concrete MABW workspace reference.

### Added

- `.agents/AGENTS.md` — skill routing doc
- `.agents/hermes-skills/multi-agent-brief-hermes/references/` — 3 progressive-disclosure reference files
- `tests/test_skill_contracts.py` — validates SKILL.md structure
- `tests/test_generator_boundaries.py` — confirms generator only touches platform adapters

## [0.5.5] — 2026-06-07

### Changed

- **Subagent-first runtime**: Python `BriefPipeline` and `multi-agent-brief prepare` removed. Brief generation is now exclusively the external subagent workflow: scout → screener → claim-ledger → analyst → editor → auditor → finalize.
- **Prompt hygiene**: all agent role Hard Rules converted to positive Guardrails language in `configs/agent_roles.yaml` and all generated agent configs.
- **Hermes delegate_task native workflow**: Hermes adapter rewritten to use `delegate_task` subagents as the native runtime. Parent agent orchestrates; children run scout, screener, claim-ledger, analyst, editor, and auditor tasks. Cron handles scheduling; `delegate_task` handles per-run child dispatch. No longer routes users to Claude Code.
- **Init wizard layout**: new workspaces create `input/sources/README.md` instead of `input/README.md`.

### Added

- `tests/test_subagent_first_contract.py`: anti-regression tests enforcing no `prepare` in user-facing docs, no `ScoutAgent`/`AnalystAgent` class names in source, and Python-commands-are-support-tools contract.

### Removed

- `src/multi_agent_brief/agents/` directory (Python fake agent runtime).
- `src/multi_agent_brief/inputs/` directory (stale empty package).

## [0.5.3] — 2026-06-06

### Fixed

- **Selector/quality gate conflict**: `selector.max_items` default raised from 8 to 20, matching `min_selected_claims` in audience profiles. Mapper defaults also aligned.
- **Epistemic blocks no longer replace reader-facing brief**: `analysis_blocks.json` and `epistemic_draft` are now intermediate governance artifacts. The reader-facing `brief.md` / `brief.docx` uses the legacy prose format with Executive Summary.
- **Confidence label**: changed from `100%` percentage (triggered audit `number_without_source` false positive) to qualitative `高/中/低` (High/Medium/Low).

### Added

- **Epistemic Presentation Layer** (PR ac0cefa): AnalysisBlock builder, renderer, limitation hygiene audit, case applicability check. Intermediate artifacts: `analysis_blocks.json`, `limitation_hygiene_report.json`.
- **Version bump to 0.5.3**: pyproject.toml, __init__.py, README, CHANGELOG.

## [0.5.2] — 2026-06-06

### Fixed

- **Dynamic dates in demo config**: `report.date` changed from hardcoded `"2026-06-02"` to `"auto"` in demo workspace and `examples/basic_market_brief`. Demo input files now use dynamic dates (`_demo_published_at()`) so sources never become stale.
- **DOCX default in demo**: demo config now includes `docx` in `output.formats` by default. No more CI patching needed for DOCX smoke.

### Added

- **Finalize delivery gate** (PR #48): deterministic `finalize_reader_outputs()` strips `[src:CLAIM_ID]` from `audited_brief.md` before writing reader-facing `brief.md` / named md / docx. CLI subcommand: `multi-agent-brief finalize --config <workspace>/config.yaml`.
- **Golden smoke test** (CI): new `golden-smoke` job verifies all demos (reference, basic, onboarding) produce non-empty, auditable, renderable output with at least 1 claim.
- **Finalize workflow documentation** in README: clarifies finalize is an optional post-pipeline step for agent-assisted workflows, not part of the core deterministic pipeline.

### Changed

- **CI: CLI smoke input date refresh**: example input dates are dynamically patched to yesterday before running CLI smoke test.
- **`init_wizard.py`**: `DEMO_NEWS` and `DEMO_MARKET_DATA` converted from constants to functions (`_build_demo_news()`, `_build_demo_market_data()`) with dynamic `published_at` dates.

## [0.5.1] — 2026-06-06

### Added

- **Local Signal Discovery** (Issue #44): deterministic support for non-English market and local consumer signal discovery. The system can now generate local-language search tasks, produce `collector_tasks.json` for manual/OpenCLI collection, parse `local_signal_samples.jsonl`, and generate `local_signal_report.json` with signals found and data gaps.
- **`local_signal_planner.py`**: core module with `MARKET_PLATFORM_HINTS` (9 markets: Vietnam, Japan, China, Indonesia, Thailand, Brazil, Mexico, Germany, Korea), `build_local_signal_tasks()`, `parse_local_signal_samples()`, `generate_local_signal_report()`.
- **`opencli_local_signal_adapter.py`**: local evidence processor for screenshots, audio, and text exports. OpenCLI is optional — pipeline works without it.
- **`collector_tasks.json`**: execution plan for manual/browser/OpenCLI collection with privacy rules and instructions.
- **`local_signal_report.json`**: intermediate artifact recording signals found and data gaps per market/language/platform.
- **`build_search_tasks_with_metadata()`**: new function in `decider.py` that preserves search task metadata (topic, market, language, platform_group, signal_type) through pipeline injection.
- **3 new audit rules**:
  - `LOCAL_SIGNAL_CLAIM_001`: consumer pain-point claims require consumer-discussion or platform-data evidence.
  - `LOCAL_SIGNAL_PROVENANCE_001`: local signal claims require sample metadata (platform, market, collected_at, access_level, sample_type, collector).
  - `LOCAL_SIGNAL_PRIVACY_001`: personal data from local signal samples must not enter final brief.
- **47 new tests** covering task generation, market hints, collector tasks, source candidates, search queries, sample parsing, report generation, and audit rules.

### Changed

- **`sources/decider.py`**: `build_search_queries()` now appends local-language queries from `local_signal_planner`. `generate_source_candidates()` includes `local_social_listening_tasks`. `merge_candidates_to_sources()` injects local tasks into `web_search.search_tasks` with metadata.
- **`core/pipeline.py`**: search task injection uses `build_search_tasks_with_metadata()` for metadata preservation. Generates `collector_tasks.json` and `local_signal_report.json` when `local_signal_discovery` is enabled.
- **`agents/formatter.py`**: persists `local_signal_report.json` to `output/intermediate/`.
- **`audit/rule_packs.py`**: registered 3 new local signal finding types.

### Non-goals (explicitly excluded)

- No RAG / vector database / embedding-based retrieval.
- No browser automation or platform crawling.
- No login-wall bypass or unauthorized scraping.
- No OpenCLI MCP server integration — OpenCLI is treated as local evidence processor only.

## [0.5.0] — 2026-06-06

### Added

- **Official Workflow Harness**: reference workflow demo with synthetic data, smoke tests, and artifact contract.
- **Final Clean Gate**: clears internal markers from reader-facing output.
- **Audience Profiles**: different brief structures and audit thresholds for management, research, IR, policy, support audiences.
- **DOCX Templates**: executive_brief, research_note, formal_internal_report templates with rendered-output validation.
- **Source Coverage Report**: configurable coverage dimensions with research gaps separation.
- **Policy & Regulatory Risk Module**: second analysis module with policy events, risk register, applicability questions.
- **Minimal HistoryStore**: file-backed storage for previous briefs and claim ledgers with repeat/novelty tracking.
- **Editorial Governance Rule Packs**: quality checks for factual density, business advice, comparable cases, historical analogies, must-preserve facts.
- **Effort Budgets**: deterministic runtime limits with budget levels (low, medium, high, xhigh).
- **Pipeline Exit Codes**: structured exit codes (0/1/2) for runtime/config fatal and quality gate failures.
- **Manifest Stage Status**: trustworthy stage status detection from artifacts and summary text.
- **Final Quality Gate**: FinalQualityAuditAgent wired into production pipeline with audience profile thresholds.
- **CI Gate Scripts**: release consistency, capabilities, and reference workflow smoke checks integrated into CI.

### Fixed

- **Test Warnings**: resolved ResourceWarning and UserWarning in test suite.
- **Search Backend Selection**: improved multi-backend support with proper state machine (disabled/runtime_tool/external_api/configure_later).
- **Source Coverage Recency**: use report date instead of current time for recency calculation.
- **SourceConfig Validation**: validate enabled_providers must be list[str].
- **0 Sources Coverage**: return 0% coverage instead of 100% when no sources collected.
- **Final Clean Metadata**: write final_clean_status to audit_report.metadata.

## [0.4.0] — 2026-06-05

### Added

- **Claim Schema v2**: new epistemic fields on `Claim` — `schema_version`, `epistemic_type` (observed/interpreted/hypothesis/action/analogy), `evidence_relation` (direct/indirect/inferred/analogous), `applicability_reason`, `limitations`.
- **Epistemic audit gates**: deterministic auditor now checks hypothesis-high-confidence misuse, action-without-basis, analogy-without-limitations, and analogy-direct-relation.
- **Contracts package**: new `src/multi_agent_brief/contracts/` with `Contract` base class, `SchemaRegistry`, and contracts for `SourceItem`, `CandidateItem`, `Claim` (v1+v2), `AuditReport`, `MarketEvent`, `AnalysisCard`. Includes `FieldViolation`, `ContractError`, and claim v1→v2 migration.
- **Backward-compatible migration**: `Claim.from_dict()` auto-fills v2 fields from `claim_type` for v1 ledger data.
- **Run Manifest**: every `prepare` run now writes `output/intermediate/run_manifest.json` with run_id, config_hash, provider/module status, source/claim counts, audit status, artifact paths and SHA-256 hashes, and pipeline stage results.
- **Semantic audit status**: `NoOpSemanticAuditAgent` now returns `not_configured` instead of faking a pass. `CompositeAuditAgent` tracks `semantic_status` in metadata. Manifest includes `semantic_status` field.
- **Audit Finding Taxonomy**: `AuditFinding` gains `blocking_level` (editor_fixable/analyst_blocking/source_blocking/configuration_error/rendering_error/safety_blocking) and `repair_owner` (editor/analyst/source/configuration/rendering/safety). All 25+ finding types tagged via `rule_packs.py`.
- **Release Consistency Gate**: `scripts/check_release_consistency.py` verifies pyproject.toml, __init__.py, README.md, README_en.md, CHANGELOG.md, and generated agent configs are version-synced. Integrated into CI.

## [0.3.5] — 2026-06-05

### Added

- Init wizard auto-recommends capabilities based on focus areas after workspace creation.

## [0.3.4] — 2026-06-05

### Added

- **Capability Center**: new `src/multi_agent_brief/capabilities/` package with registry, readiness detection, and recommendation engine.
- **`multi-agent-brief features`**: categorized feature catalog with status symbols (✓/!/○/—). Supports `--info <id>`, `--json`, and `<workspace>` arguments.
- **`multi-agent-brief recommend`**: deterministic keyword→capability recommendation rules. Supports `--text`, `--json`, and `<workspace>` arguments.
- **`multi-agent-brief setup`**: apply capability recommendations to a workspace with safe YAML merge. Supports `--dry-run` and `--from-plan` arguments.
- **Doctor enhancements**: now shows capability status summary and input-based recommendations.
- **`.env.example` updated**: lists all 7 API keys (Tavily, Exa, Brave, Firecrawl, Serper, NewsAPI, MinerU) with section headers and provider URLs.
- **Auto-generated feature docs**: `docs/features.md` and `docs/features.zh-CN.md` generated from capability catalog.
- **CI gate**: `scripts/check_capabilities.py` ensures every user-facing provider has a CapabilitySpec registered.

### Changed

- **Root `.env.example`** replaced legacy model-provider keys with current API key list matching wizard-generated output.

## [0.3.2] — 2026-06-05

### Added

- **`/propose-competitors` slash command** (`.claude/commands/propose-competitors.md`):
  invokes `market-competitor-planner` subagent to recommend competitor candidates
  based on `user.md` context.  Writes `competitor_candidates.yaml` for user review.
- **`prepare` CLI integration test**: verifies end-to-end output of `brief.md`,
  `claim_ledger.json`, and `audit_report.json` via real CLI invocation.

### Fixed

- **Analysis module failures are no longer silently swallowed**: `_run_analysis_modules`
  now records failures as `AgentOutput` with `status: failed` and error details.
  Specialist auditor failures are logged with `logger.warning` and recorded in
  `analysis_packs` metadata — the system no longer silently falls back to default
  audit without indication.
- **README**: `run` command wording changed from "已移除" to "已弃用，仅保留迁移提示"
  to match actual CLI behaviour.
- **`docs/claude-code-workflow.md`**: CLI command list updated to include
  `prepare` and `competitors init/list/merge`.

## [0.3.1] — 2026-06-05

### Added

- **`multi-agent-brief prepare`** command: runs the full deterministic pipeline
  (source collection → Scout → Screener → Claim Ledger → draft artifacts).
  Replaces the disabled `run` command in `/generate-brief` workflow.

### Fixed

- **`/generate-brief` main path restored**: Step 3 now calls `multi-agent-brief prepare`
  instead of the disabled `run` command.  First-time users can now generate a brief
  without hitting a broken pipeline gate.
- **`competitors propose` renamed to `competitors init`**: CLI only creates an empty
  template — LLM-assisted discovery uses the `/propose-competitors` slash command.
  Removed deceptive "LLM recommendation" claim from CLI help text.
- **Version unified**: `pyproject.toml`, `__init__.py`, and `CHANGELOG` all read `0.3.1`.
- **Pipeline order corrected** in market-competitor module docs (Analyst → Editor → Auditor → Formatter).
- **`multi-agent-brief run`** now prints a migration message pointing to `prepare` instead
  of a generic error.
- **AGENTS.md** references updated from `run` to `prepare`.

## [0.3.0] — 2026-06-05

### Added

- **Market & Competitor Intelligence Analysis Module** — 首个可插拔 AnalysisModule
  - `competitor_universe.yaml` 配置合同 + `competitor_candidates.yaml` 审核流程
  - CLI: `multi-agent-brief competitors propose | list | merge`
  - 竞对感知 Source Planning: 为每个 primary 竞对 × 维度自动生成定向搜索任务
  - `EntityEventEnricher`: 确定性实体/事件类型/地理/维度标注，接入 Scout 与 Screener 之间
  - `build_events`: 归并 entity-tagged Claim 为 MarketEvent，推测事件状态
  - 5 个中间产物: `events.json` / `competitor_matrix.json` / `coverage_report.json` / `watchlist.json` / `evidence_pack.json`
  - 跨期状态追踪: `event_history.jsonl` + change_status (new/changed/unchanged/cancelled/resolved)
  - 6 种专项审计: comparison_missing_entity_evidence / capacity_status_missing / metric_basis_missing / unsupported_market_trend / single_source_interpretation / competitor_coverage_gap
  - 3 个新 subagent: `market-competitor-planner` / `market-competitor-analyst` / `market-competitor-auditor`
  - 通用 `AnalysisModule` 接口 + Registry: 未来 earnings/policy/patent 模块可复用
  - 模块禁用时零影响 — 现有 589 tests 全过（73 新增）
- Onboarding 扩展: `market_scope` 和 `competitor_preferences` 字段

### Changed

- 移除 README update check CI job（`.githooks/pre-push` 同步清理）

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
