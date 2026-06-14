# Changelog

All notable changes to the multi-agent-brief-workflow project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.8.1] — 2026-06-14

### Added

- **Control-trace timing projection**: status and run archives now expose event-log-derived timing buckets for completed, incomplete, unknown, or contaminated traces without mutating runtime state or claiming exact model runtime.
- **Fast-rerun frozen fact-layer archive and import**: finalized run archives now include a hash-verified frozen fact layer, and `state import-fact-layer` can import a complete archived fact layer into a new workspace for same-evidence downstream reruns.
- **Fast-rerun runtime handoff**: `run --recipe fast-rerun` now requires a valid imported fact layer, starts from Analyst, and explicitly avoids replaying source-discovery, Scout, Screener, or Claim Ledger history.
- **Fast-rerun freshness and public fixture coverage**: imported fact layers are checked against the target workspace freshness window at delivery time, and public-safe fixtures cover clean import, no-delivery import state, and source-plan rejection.
- **MABW-080 run registration**: `experiments 080 register-run` registers completed workspace runs into existing MABW-080 cases as `run_record.json` experiment metadata.

### Changed

- **Run integrity normalization is shared and fail-closed on malformed persisted state**: read surfaces may project unknown/non-reference status for invalid control state, while persisted workflow integrity remains `clean` or `contaminated`.
- **Run archive manifests now preserve fact-layer and timing projections**: archives record source evidence packs, input classification, candidate claims, screened candidates, Claim Ledger, timing, and fast-rerun freshness projections by hash.
- **Experiment registration verifies archive bytes**: MABW-080 registration validates archived fact-layer file hashes and source-pack hashes before comparing the archive with the case frozen fact layer.

### Boundaries

- v0.8.1 adds measurement infrastructure and fast-rerun control transactions. It does not score output quality, prove semantic truth, run 080 summaries, scaffold experimental conditions, or promote Codex to supported parity.
- Fast-rerun is Experimental. It supports hash-verified same-evidence downstream rerun inspection; it is not a gate-skipping lite mode.
- MABW-080 remains Experimental. `register-run` records run metadata only; `score-run`, `summarize`, manifestation assessment import, and condition scaffolding are not shipped in v0.8.1.

## [0.7.5] — 2026-06-13

### Added

- **Stage-scoped quality gate reports**: `gates check --stage auditor` and `gates check --stage finalize` now write separate authoritative reports under `output/intermediate/gates/`. The legacy `output/intermediate/quality_gate_report.json` remains a latest/compatibility projection and is no longer the frozen authority for both stages.
- **Run integrity marker**: runtime state now records whether a run remains clean single-shot reference evidence or has become contaminated by reset, older-stage replay, or frozen-artifact mutation. Contaminated runs can still be completed locally, but should not be packaged as clean reference evidence.
- **Deterministic repair router**: added `multi-agent-brief repair route` to map known gate/audit/control findings to the owning stage and allowed artifacts without executing repair or calling an agent.
- **Codex experimental runtime kit hardening**: Codex custom-agent assets remain Experimental, with clearer workspace-local install and control-flow guidance.

### Changed

- **Source-discovery evidence boundary tightened**: `source_candidates.yaml` is treated as planning/review only. It cannot be merged as evidence, and source-discovery completion requires durable source evidence instead of a plan-only artifact.
- **Runtime/source hardening**: web-search configuration now rejects ambiguous modes, disabled search cannot run through `sources decide --search`, workspace `.env` loading is allowlisted, and invalid provider config no longer contributes source items.
- **Audit binding moved into Python control state**: finalize verifies frozen Claim Ledger, audited brief, and audit report hashes through deterministic runtime state instead of trusting auditor-written binding metadata.
- **Run archive added for finalized runs**: finalized runs are archived under `output/runs/<run_id>/` with delivery, intermediate, control files, and SHA-256 manifest entries so repeated weekly runs do not erase historical evidence chains.
- **Run integrity contamination made transactional**: contamination state and `run_integrity_contaminated` events now commit together; event append failure rolls back workflow state, and duplicate contamination reasons are no-ops.
- **Repair routing honors gate metadata**: router output now trusts existing `repair_owner`, `repair_stage_id`, and `repair_artifact_id` fields before falling back to deterministic heuristics.
- **Docs-only CI safety**: docs-only changes now run public-safety, terminology, version, and release-consistency checks so README/docs cannot bypass release guardrails.

### Boundaries

- v0.7.5 does not claim semantic proof, autonomous repair, automatic learning, Codex parity, or output-quality improvement.
- Codex remains Experimental. Real-workspace control-flow E2E reached terminal delivery, but clean repair semantics and specialist parity are not yet promoted to supported-runtime claims.
- `repair route` is a read-only router. It does not create repair plans, mutate artifacts, execute repair, or decide taste.

## [0.7.4] — 2026-06-12

### Added

- **Audit binding consistency check**: `finalize` now rejects stale audit reports that still mention claim IDs absent from the current Claim Ledger, record blocking audit findings, or carry stale ledger/brief binding metadata.
- **Public failure study**: added a public-safe organoid-industry failure study showing how a readable brief can still overstate source support, and why v0.8 focuses on source-to-claim semantic support calibration.

### Changed

- **Release public-safety check**: `check_release_consistency.py` now runs the tracked-file public-safety scan so release checks fail on local paths, token-like strings, environment-file references, or configured private terms.
- **Source appendix wording**: public docs now state that source appendices are appended inside the reader delivery files when configured, while standalone `output/source_appendix.md` remains an audit/control copy.

### Boundaries

- **Traceability, not semantic proof**: release-facing wording now states that registered source links show where a claim entered the workflow, but do not yet prove that each source semantically supports every sub-claim. Source-to-claim semantic support remains a v0.8 evaluation target.
- **Distribution boundary**: v0.7.4 release notes use source clone plus demo scripts as the primary get-started path. Homebrew, curl, and PowerShell installer assets remain non-primary installer surfaces until separately packaged and smoke-tested.

## [0.7.3] — 2026-06-12

### Added

- **Release safety scan**: added `scripts/check_public_safety.py` and focused tests for public-safe release surfaces, including local path, token-like, environment-file, and configurable banned-term checks.
- **Private onboarding guardrail**: root `onboarding.json` is ignored so personal onboarding answers do not accidentally enter release commits.
- **Delivery artifact integrity**: `finalize_report.json` records delivery artifact hashes, and `multi-agent-brief deliver` rejects artifacts that changed after finalize.

### Changed

- **Runtime prompt hardening**: generated Orchestrator and Claude command guidance now states that stage completion is defined by `state stage-complete`, not by artifact existence or natural-language completion claims.
- **Configuration authority clarified**: screener/runtime guidance now treats `max_source_age_days` and `fail_on_stale_source` as authoritative config and forbids prompt-only freshness exceptions.
- **Onboarding privacy boundary clarified**: `/mabw new` guidance now forbids inferring company or organization from maintainer identity, repo history, private memory, prior workspaces, local directories, or previous reports.

### Boundaries

- v0.7.3 is a release-hardening patch over v0.7.2. It does not add new autonomous learning, role topology changes, output-quality scoring, public raw trace packs, or A-grade manifestation experiments.

## [0.7.2] — 2026-06-12

### Added

- **Reader-final output gate**: `finalize` now records `finalize_report.json.reader_clean` and rejects reader-facing Markdown/DOCX/source appendix outputs that leak internal source markers, raw claim/source IDs, local paths, debug residue, process wording, or blank citation/source-index rows.
- **Runtime completion transactions**: added `multi-agent-brief state stage-complete` and `state finalize-complete` for deterministic success-path bookkeeping. These commands validate and record completion claims; they do not execute stages, invoke agents, call `finalize`, or repair content.
- **Claude Code five-verb writer entrypoint**: added `/mabw` for Claude Code with `new`, `run`, `status`, `feedback`, and `deliver`, plus `multi-agent-brief claude install` support for the Claude writer path.
- **Improvement Ledger supersession hygiene**: added top-level immutable `supersedes_id`, deterministic duplicate proposal warnings, approved supersession fork rejection, non-materializable superseder warnings, and revert-time warnings when old guidance re-exposes.
- **Read-only writer status**: added the writer-facing status model for current run status, source-trail surface readiness, approved reader preferences, and delivery guardrails without refreshing or mutating runtime state. It points to Claim Ledger / audit / source appendix surfaces rather than tracing individual numbers itself.
- **Product-definition docs**: added the Chinese golden path, Chinese weekly-use script, and writer-facing trust map for the four product concepts behind v0.7.2.
- **Public integration summary and launch checklist**: added a public-safe solar integration reference summary and a Chinese launch-validation checklist for golden-path self-test and fresh-clone pilot validation.
- **On-ramp language**: added three entry paths ("look once", "run once", and "live with it") while keeping Claim Ledger, gates, human delivery, execution trace, and frozen snapshots as non-negotiable accountability surfaces.
- **v1.0 freeze list**: added a maintainer-facing freeze checklist for runtime state, artifact contracts, gate reports, Improvement Ledger schema, handoff, eval-case runner actions, and deferred v0.8 surfaces.
- **Improvement origin runtime metadata**: human-feedback Improvement Ledger proposals capture `origin_runtime` when runtime state exists; this is audit/rendering metadata only and is not used for routing, filtering, or materialization.

### Changed

- **Success path uses transactions**: generated handoff/runtime guidance now routes successful stage progress through `state stage-complete` and terminal delivery through `state finalize-complete`; `state decide` remains for retry, repair, human review, and block decisions.
- **Delivery path hardened**: `/mabw deliver` and runtime handoff guidance require gates, strict state checks, final rendering, reader-final cleanliness, and `finalize-complete` before terminal completion is recorded.
- **Improvement materialization remains computed**: superseded guidance is a read-time/materialization computation, not a stored ledger status. Reverting a superseder can re-expose the previous approved entry by design.
- **Five-verb language clarified**: `doctor` remains a diagnostic/maintainer command, not a sixth writer verb. Claude Code is the first-class writer / five-verb path; Hermes remains a supported delegated/scheduled runtime path.

### Boundaries

- v0.7.2 does not add autonomous learning, automatic repair, automatic approval, output-quality scoring, role-topology compression, manifestation metrics, retrieval memory, or runtime-specific guidance filtering.
- v0.7.2 does not include `operator_reported_model`; model/run observation metadata is deferred to v0.7.3 / v0.8 scorecard design.
- v0.7.2 does not include generic ledger provenance fields, `improvement/intake.jsonl`, or `improvement/candidates.jsonl`; intake/candidate parking-lot work is deferred to v0.7.3+.
- v0.7.2 does not include role topology convergence, guidance manifestation reports, runtime-specific guidance filtering, or a public A-grade reference run.

## [0.7.0] — 2026-06-10

### Added

- **Improvement Ledger lifecycle**: added `multi-agent-brief improve propose/list/show/approve/reject/revert/stats/validate/rebuild` for human-authored, human-approved reader-preference guidance.
- **Improvement Memory projection**: approved materializable guidance is deterministically projected into `improvement/memory.md`; `improve rebuild` writes only that projection and does not mutate runtime state, handoff, events, or snapshots.
- **Frozen per-run Improvement Memory snapshot**: `run`, `start`, and `handoff` freeze eligible guidance into `output/intermediate/improvement_memory_snapshot.md` and expose only that snapshot through handoff.
- **Runtime manifest improvement block**: `runtime_manifest.json.improvement` records `ledger_sha256`, `memory_sha256`, `snapshot_path`, `snapshot_sha256`, and `materialized_entry_ids` for the active run.
- **Product-definition guardrail**: machine-checkable feedback issues stay in feedback/repair/gate surfaces unless a human rewrites them as persistent audience guidance.
- **Public-safe eval cases**: added packaged eval cases proving unapproved entries are not materialized, approved guidance is frozen, and reverted entries are removed from the next snapshot.
- **Improvement module docs**: added `docs/modules/improvement.md` for command lifecycle, files, semantics, and non-goals.

### Changed

- **Public roadmap and support status**: v0.7.0 now documents Improvement Ledger / Memory as the implemented public-control-surface slice while keeping FrictionStore, autonomous learning, retrieval memory, runtime-specific filtering, and output-quality validation deferred.
- **Packaged eval fixtures**: package data now includes public-safe `improvement/ledger.jsonl` and `improvement/memory.md` eval fixtures.

### Boundaries

- v0.7.0 does not add autonomous learning, automatic repair, semantic proof, output quality guarantees, RAG/retrieval memory, runtime-specific guidance filtering, ledger compaction, policy-pack authoring, or automatic workflow execution. `FeedbackIssue` is evidence, not guidance; guidance must be human-authored and human-approved.

## [0.6.9] — 2026-06-09

### Added

- **Workspace runtime kit installer**: added `multi-agent-brief runtime install --workspace <workspace> --runtime opencode|claude|all` to copy OpenCode/Claude Code project commands, agents, and a small workspace skill into the business workspace.
- **Runtime asset inventory**: added `docs/runtime-asset-inventory.md` and `scripts/check_runtime_asset_parity.py` to distinguish packaged contract/eval data from source-clone-only runtime assets.
- **Runtime recipes**: added `docs/runtime-recipes.md` to document full subagent and compact human-assisted workflow recipes without adding a Python workflow mode.
- **Install smoke hardening**: expanded non-dev CI smoke to check state show/check, absence of stage outputs after `run`, package-only runtime asset boundaries, and wheel install behavior.

### Changed

- **Install/runtime truth**: README, README_en, support matrix, roadmap, and architecture docs now distinguish package-installed CLI behavior from source-clone runtime assets such as `.agents/`, `.claude/`, `.opencode/`, `.codex/`, and the Hermes plugin source tree.
- **Workspace-local runtime guidance**: users can install runtime assets into a workspace to avoid OpenCode/Claude reading the MABW source checkout during normal workspace execution.

### Boundaries

- v0.6.9 is a stabilization release. It does not add FrictionStore, improvement proposal commands, policy-pack authoring, automatic repair, automatic source fetching, or a Python brief-generation pipeline. Runtime kit selection and installation do not execute the brief workflow.

## [0.6.8] — 2026-06-09

### Added

- **Reader-facing source appendix**: `multi-agent-brief finalize` can generate `output/source_appendix.md` from sources cited in `output/intermediate/audited_brief.md` and resolved through `output/intermediate/claim_ledger.json`.
- **Source appendix compatibility**: `source_appendix` is the new output format name; legacy `source_map` output format requests are treated as a compatibility alias.
- **Public-safe eval case**: added a packaged eval case proving finalize can write a reader-facing appendix without leaking raw claim IDs, source IDs, evidence text, local paths, or unused ledger sources.

### Changed

- **Formatter guidance**: formatter role contracts and runtime command surfaces now mention configured source appendix rendering and its reader-facing safety boundary.
- **Default output format**: new onboarding/default profiles now use `source_appendix` instead of the old `source_map` label.

### Boundaries

- The source appendix is a reader-facing source list, not source evidence, semantic proof, provenance, a runtime gate, or a workflow execution artifact. It does not fetch sources, rewrite claims, create citations, modify the Claim Ledger, or expose internal `[src:CLAIM_ID]` markers in final reader artifacts.

## [0.6.7] — 2026-06-09

### Added

- **Orchestrator Control Switchboard**: added `multi-agent-brief controls build-switchboard/show/select/validate` for deterministic runtime control recommendations and Orchestrator selection records.
- **Switchboard control files**: `run`, `start`, and `handoff` now create `output/intermediate/orchestrator_control_switchboard.json` and expose it through `control_switchboard_files`; `control_selections.json` is created only when the Orchestrator explicitly records a selection.
- **Runtime event trace**: event logs can record switchboard build, selection, and validation events.
- **Public-safe eval case**: added a packaged eval case proving that selecting a control does not execute it.

### Changed

- **Runtime guidance**: Hermes, Claude Code, OpenCode, Codex, and manual handoff text now instruct the Orchestrator to read the switchboard and record enable/defer/reject selections before explicitly executing selected controls.

### Boundaries

- Selection is not execution. `controls select --selection enable` records Orchestrator intent only; it does not run quality gates, feedback planning, provenance projection, source discovery, local/social signal collection, repair, or subagents. Privacy-sensitive controls require explicit human approval before they are execution-ready.

## [0.6.6] — 2026-06-09

### Added

- **Audience Profile Runtime Surface**: added workspace-local `audience_profile.md` as a human-editable reader taste and department preference file.
- **Frozen per-run snapshot**: `run`, `start`, and `handoff` now create or reuse `output/intermediate/audience_profile_snapshot.md` so the active run uses stable taste context even if the live profile is edited later.
- **Handoff references**: `agent_handoff.json` and `agent_handoff.md` now expose `audience_memory_files` separately from runtime state, feedback, quality gate, provenance, and expected workflow artifacts.
- **Runtime event trace**: event logs can record `audience_profile_snapshot_created` with profile/snapshot paths and hashes.

### Changed

- **Workspace init**: onboarding, direct init, and demo init now create an audience profile template.
- **Runtime guidance**: Hermes, Claude, OpenCode, Codex, and manual handoff text now instruct the Orchestrator to read the snapshot at run start, summarize relevant taste guidance, and pass it to delegated roles as context.

### Boundaries

- Audience profile files are runtime context, not source evidence, artifact contracts, quality gates, provenance graph nodes, or stage blockers. Python creates, freezes, exposes, and records the context; it does not enforce taste, update the profile automatically, route controls, or implement a long-term memory system.

## [0.6.5] — 2026-06-09

### Added

- **Provenance projection CLI**: added `multi-agent-brief provenance build`, `provenance show --json`, and `provenance validate` for deterministic workspace-local audit/debug graphs.
- **Provenance control artifact**: added optional `output/intermediate/provenance_graph.json` as a projection of existing runtime state, artifact registry, event log, Claim Ledger, feedback, repair, and quality gate control files.
- **Provenance eval case**: added a packaged public-safe eval case that validates provenance graph creation without leaking raw evidence text.
- **Runtime and handoff references**: handoff JSON/Markdown, Hermes prompts, and Hermes plugin references now expose optional provenance state separately from required workflow artifacts.

### Changed

- **Artifact activation**: `provenance_graph.json` stays `expected/not_checked` until `provenance build` creates it, so fresh workspaces are not blocked by missing provenance.
- **Runtime events**: event logs can record provenance build/validate outcomes without turning the event log into the graph source of truth.
- **Reference semantics**: provenance edges use citation wording such as `claim_cites_source`; the graph does not assert semantic truth or that a source proves a claim.

### Boundaries

- Provenance projection is optional audit/debug tooling. It does not execute workflow stages, replay a DAG, fetch sources, edit briefs, execute repair, verify semantic truth, or gate `finalize` by default.

## [0.6.4] — 2026-06-08

### Added

- **Public-safe evaluation cases CLI**: added `multi-agent-brief eval-cases list`, `eval-cases validate`, and `eval-cases run` for deterministic developer/CI regression checks.
- **Packaged eval fixtures**: bundled five public-safe workspace control cases plus one Hermes static invariant case so non-editable installs can run the default eval suite.
- **Fixture leakage scanner**: eval-case validation rejects shell-string commands, non-synthetic manifests, local paths, unsafe URLs, email domains, token-shaped values, prompt labels, and non-synthetic claim/source IDs.
- **Claude Code install helper**: added `multi-agent-brief claude install` to install `/generate-brief` and MABW subagents into a user-level Claude Code directory for Claude Desktop Code tab discovery.

### Changed

- **Structured eval actions**: eval cases dispatch allowlisted actions such as `gates.check`, `feedback.ingest`, and `state.decide` instead of parsing or executing shell commands.
- **Stage-explicit fixtures**: workspace cases declare `initial_stage` and prepare temporary runtime state explicitly, so cases validate control-surface behavior without executing workflow stages.
- **Partial assertions**: eval results compare only stable control outputs such as exit codes, expected control artifacts, gate findings, feedback issues, workflow state, and static text invariants.
- **Claude Code setup guidance**: README and setup scripts now include the optional install step for users who run Claude Code from Claude Desktop with a workspace or non-repository project folder selected.

### Boundaries

- Evaluation cases are developer/CI regression tools, not workflow artifacts. They do not score prose, run subagents, execute repair, fetch sources, call an LLM judge, or add `evaluation_report.json` to runtime artifact contracts.

## [0.6.3] — 2026-06-08

### Added

- **Quality Gates CLI**: added `multi-agent-brief gates check`, `gates show --json`, and `gates validate` for deterministic material-fact, freshness, and target-relevance checks.
- **Quality gate control artifact**: added optional `output/intermediate/quality_gate_report.json` as a separate Orchestrator control artifact.
- **Runtime gate events**: event logs now record quality gate checks and whether they produced blocking findings.

### Changed

- **Current-stage gate blocking**: `state check` and `state decide` now enforce blocking quality gate findings only for the current stage.
- **Gate-stage and repair-target separation**: quality gate findings now distinguish the stage being blocked from the stage/artifact that should own repair.
- **Required gate semantics**: `quality_gates.enabled` can require `quality_gate_report.json` before configured current stages continue.
- **Runtime handoff references**: handoff JSON/Markdown, Hermes prompts, and Hermes plugin references expose optional quality gate state separately from expected workflow artifacts.
- **Hermes main path**: Hermes guidance now runs `gates check`, `state check --strict`, and `state decide` before `finalize`; `finalize` alone is not a quality-gate executor.
- **Gate boundaries**: quality gates remain deterministic validators; they do not live-fetch market data, recrawl sources, rewrite briefs, execute repair, or make semantic truth judgments.

### Fixed

- **Optional control artifact activation**: `quality_gate_report.json` stays `expected/not_checked` until gates are explicitly run or enabled, avoiding misleading `missing` status in normal runs.
- **Reader-facing checks**: `output/brief.md` quality gates do not require internal `[src:CLAIM_ID]` markers.

## [0.6.2] — 2026-06-08

### Added

- **Feedback CLI**: added `multi-agent-brief feedback ingest`, `feedback plan`, `feedback resolve`, `feedback show --json`, and `feedback validate` for structured feedback issues, deterministic repair plans, and explicit resolution state.
- **Feedback control artifacts**: added `feedback_issues.json`, `repair_plan.json`, and conditional `delta_audit_report.json` as optional Orchestrator control artifacts.
- **Feedback event trace**: runtime event logs now record feedback issue creation, issue planning, and repair plan creation events.

### Changed

- **Stage-scoped feedback blocking**: blocking feedback only affects the current stage, so future-stage feedback does not block a fresh or earlier-stage workspace.
- **Runtime handoff references**: handoff JSON/Markdown and Hermes surfaces now expose optional feedback state files separately from expected workflow artifacts.
- **Bounded repair planning**: repair plans propose bounded Orchestrator decisions but do not execute repair or edit brief artifacts automatically.

### Fixed

- **Feedback/evidence separation**: feedback issue fields avoid claim-evidence naming and keep human feedback out of source evidence artifacts.

## [0.6.1] — 2026-06-08

### Added

- **Minimum runtime state**: `multi-agent-brief run`, `start`, and `handoff` now initialize Orchestrator control files: `runtime_manifest.json`, `workflow_state.json`, `artifact_registry.json`, and `event_log.jsonl`.
- **State CLI**: added `multi-agent-brief state init`, `state check`, `state show --json`, and `state decide` for runtime inspection, artifact status refresh, and Orchestrator decision recording.
- **Runtime state references in handoff**: `agent_handoff.json` and `agent_handoff.md` now expose `runtime_state_files` separately from workflow `expected_artifacts`.

### Changed

- **Stage-scoped artifact blocking**: required artifacts block only the consumer stage that needs them, so a fresh workspace starts with downstream artifacts as `expected/pending` rather than globally blocked.
- **Artifact path contract**: artifact registry paths are workspace-root relative, and `input_classification` now points to the CLI's actual default output path.
- **Runtime docs and Hermes surfaces**: runtime prompts and public docs now describe the v0.6.1 minimum state layer while keeping feedback repair and provenance graph work deferred.

### Fixed

- **Manifest semantic split**: v0.6.1 uses `runtime_manifest.json` for Orchestrator runtime state and leaves the legacy pipeline `run_manifest.json` semantics untouched.

## [0.6.0] — 2026-06-08

### Added

- **Explicit Orchestrator contract runtime**: added shared contract references for Orchestrator authority, stage order, artifact expectations, policy shell, and decision vocabulary.
- **Runtime role parity**: Hermes, Claude Code, Codex, OpenCode, and manual handoff now identify the Orchestrator as the runtime main agent and use the same stage decision language.
- **Orchestrator architecture docs**: added bilingual public architecture pages plus implementation notes for v0.5.9 prep and v0.6.0 contract scope.
- **Packaged contract configs**: bundled Orchestrator contract YAML files inside the Python package so non-editable installs can run `multi-agent-brief run` without a source checkout.

### Changed

- **Runtime handoff artifacts**: `agent_handoff.json` and `agent_handoff.md` now include contract references and the shared Orchestrator control loop.
- **Hermes plugin alignment**: Hermes plugin handoff now passes the detected repo workdir when available, and its delegated workflow reference matches `stage_specs.yaml`.
- **README updates**: both Chinese and English README files now point to the v0.6 Orchestrator architecture and state the v0.6.0 boundary.
- **Support matrix**: removed the remaining `BriefPipeline` interface wording; the old Python pipeline is marked removed.

### Fixed

- **Non-editable install handoff**: fixed `multi-agent-brief run --workspace ...` failing after non-editable archive/package installation because contract files were only available in the source repo.
- **Release consistency script**: release checks no longer import an ambient installed package when validating source version consistency.

## [0.5.8] — 2026-06-07

### Changed

- **版本号 0.5.7 → 0.5.8**：上游 `check_release_consistency.py` 要求版号与 tag 一致；0.5.7 从未打 tag，本次统一发布。
- **README 清理**：移除尚不可用的 CLI-only curl 安装路径和 Homebrew 引用（打包工作推迟到 v0.7）。
- **旧 `prepare` 叙事清理**：删除五份遗留 impl-plan 文档（`v0.4.0`、`v0.5.0`、`v0.5.1-*`、`v0.5.5-hermes-adapter`）和 `v1-pre-mas-refactor-roadmap.zh-CN.md`——旧执行计划和引用全部移除。最新路线图见 `docs/roadmap.zh-CN.md`。

### Added

- **`docs/support-matrix.md`**：建表明确所有能力的 Supported / Experimental / Interface Only / CLI-only / Deprecated 状态。
- **Issue [#49](https://github.com/Stahl-G/multi-agent-brief-workflow/issues/49) 边界明确化**：README 安装文档澄清 — agent assets（`.agents/`、`.claude/` 等）需 source clone 才能使用子智能体工作流。pip-only 安装仅提供确定性 CLI 命令。正式打包推迟到 v0.7。
- **版本管理自动化**：`VERSION` 为唯一真源；新增 `scripts/bump_version.py`（同步到所有文件）、`scripts/check_version_consistency.py`（CI 检查）、`scripts/release.sh`（自动发布）。`__init__.py` 改为 `importlib.metadata.version()` 动态读取。

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
