# Roadmap

This roadmap shifts the project from feature expansion to a stable, auditable v1.0 baseline before any v2.0 MAS Runtime work. The detailed agent-facing reference is kept in Chinese at [docs/agents/reference/v1-pre-mas-refactor-roadmap.zh-CN.md](agents/reference/v1-pre-mas-refactor-roadmap.zh-CN.md).

## Strategy

Before v1.0, the project should not replace the current pipeline or keep expanding providers, topic modules, and delivery channels without a stronger quality baseline. The priority order is:

```text
Claim knowledge model
→ Schemas / contracts / run manifest
→ Audit and release gates
→ Reference workflow
→ Golden datasets
→ v1.0 stable baseline
→ v2.0 MAS Runtime
```

After v1.0, the current sequential pipeline should remain as the reference engine, fallback engine, and quality benchmark for any future MAS Runtime.

## v0.4: Knowledge & Governance Contracts

Goal: define the core data contracts that a future Shared World can reuse.

Scope:

- Upgrade Claim modeling with `FACT`, `CASE`, `INTERPRETATION`, `HYPOTHESIS`, `ACTION`, and `TO_VERIFY`.
- Add Evidence Relation semantics: `DIRECT`, `COMPARABLE`, `HISTORICAL_ANALOGY`, and `BACKGROUND`.
- Version core contracts: `SourceItem`, `CandidateItem`, `Claim`, `AnalysisPack`, `AuditReport`, `OutputArtifact`, `RunManifest`.
- Add `run_manifest.json` with run ID, config hash, provider/module status, audit status, errors, output artifacts, and hashes.
- Fix semantic audit states so `not_configured` and `not_run` cannot be reported as `pass`.
- Structure Audit Findings by repair owner and blocking level.
- Move high-value harness rules toward configurable, tested rule packs.

Out of scope:

- MAS Runtime.
- Full RAG.
- Complex model routing.
- More search backends.
- Large numbers of topic modules.

Done when:

- Claims can express facts, interpretations, hypotheses, actions, comparable evidence, and historical context without blurring confidence boundaries.
- Core contracts have schema/version/fixture/tests.
- Runs can be traced and compared through manifests.
- Audit reports no longer confuse skipped checks with passing checks.

## v0.5: Production Reference Workflow

Goal: produce one realistic, reproducible, audit-ready workflow.

Scope:

- Freeze one official path: interactive init → source discovery/confirmation → doctor → prepare → Analyst → Editor → Final Auditor → Markdown / DOCX → Human Review.
- Keep one maintainer-local reference workflow and one public-safe synthetic demo.
- Add Audience Profiles for management, research, IR, and legal/compliance readers.
- Add DOCX templates for Executive Brief, Research Note, and Formal Internal Report, with basic layout validation.
- Add Final Clean gates for reader-facing output.
- Add a Policy & Regulatory Risk Module as a second Analysis Module to validate the module interface beyond competitor analysis.
- Add a minimal HistoryStore for previous briefs, previous claims, entity history, and repeat/novelty checks.
- Add `low` / `medium` / `high` / `xhigh` effort budgets without building full model routing.

Done when:

- A new user can complete the official workflow from the README.
- The reference workflow and synthetic demo are reproducible.
- Markdown and DOCX have publish-level quality gates.
- Two meaningfully different Analysis Modules use the same registry.
- Historical context cannot silently become current-period fact.

## v1.0: Stable Baseline

Goal: freeze the current sequential pipeline as the long-term maintained baseline and v2.0 benchmark.

Scope:

- Golden datasets for normal weekly, sparse market, conflicting sources, quiet week, and high-risk input.
- Benchmark metrics for source counts, claim counts, citation coverage, unsupported statements, high-risk findings, audit status, runtime, cost, and artifact hashes.
- Contract compliance tests for SourceProvider, AnalysisModule, AuditAgent, OutputRenderer, and DeliveryConnector.
- Release consistency gate for package version, CHANGELOG, README, Git tag, agent configs, schema versions, and release notes.
- Formal support matrix: Supported / Experimental / Interface Only / Deprecated.
- Create a `v1-maintenance` branch for fixes, governance gaps, compatibility, and documentation.

Done when:

- v1.0 runs all officially supported capabilities from a fresh install.
- v1.0 has stable interfaces, public-safe benchmarks, and regression metrics.
- v1.0 can serve as the comparison and fallback engine for future MAS Runtime work.
- README, changelog, tags, schemas, and generated agent configs no longer drift.

## v2.0: MAS Runtime Candidate

v2.0 should not become the main path before v1.0 is frozen. After v1.0, a `mas-runtime` / `v2` branch can explore a true MAS runtime.

Recommended first scope: `mas-runtime-foundation`.

- Shared World / SQLite Event Store.
- Typed Event / AgentMessage envelope.
- TaskBoard, leases, and a minimal Contract Net or bidding protocol.
- AgentState, inbox cursors, and capability registry.
- ClaimProposal state machine.
- Deterministic ClaimReducer that turns proposals into the official Claim Ledger.
- Run replay and v1-compatible Claim Ledger export.

Not in the first scope:

- Full Analyst / Editor / Auditor / Formatter migration.
- Multi-server deployment, Kafka, or Redis.
- One-shot migration of every connector and analysis module.
- Making v2 the README main path.

See [v2.0 MAS Runtime Evaluation](mas-v2-evaluation.zh-CN.md) for the Chinese technical evaluation.

## Deferred

The following should stay constrained before v1.0:

- More search backends and delivery channels.
- Full model routing.
- Full RAG or long-term memory system.
- Many new topic modules.
- Scheduling, multi-tenant permissions, and enterprise deployment.
- Unfinished PDF / Email / Slack / Telegram support.

Unstable capabilities must be labeled as Experimental or Interface Only in README and CLI output.
