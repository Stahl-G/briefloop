# Roadmap

This roadmap reflects the post-v0.5.7 baseline: subagent-first runtime handoff + Hermes primary path + input governance. See [v1 pre-MAS refactor roadmap](agents/reference/v1-pre-mas-refactor-roadmap.zh-CN.md) for the detailed Chinese agent reference.

## Completed (v0.1 — v0.5.7)

v0.5.7 moved the project from a Python pipeline to a subagent-first architecture:

- **Subagent-first runtime handoff**: `multi-agent-brief run` is no longer a Python brief generator — it's a runtime handoff launcher. External subagent workflow: scout → screener → claim-ledger → analyst → editor → auditor → finalize.
- **Hermes primary path**: Hermes adapter provides native `delegate_task` child pipelines, cron scheduling, daily source cache, and cached_package wiring.
- **Thin CLI router**: `main.py` slimmed to a routing layer, command logic in 13 `cli/*_commands.py` modules.
- **Platform adapters**: Claude Code, OpenCode, Codex subagent configs generated from `configs/agent_roles.yaml`.
- **Input governance**: `inputs classify` CLI + Scout evidence-only contract + ManualProvider hard gate, preventing feedback/instruction/context contamination.
- **Quality gates**: deterministic audit, editorial governance, final quality, limitation hygiene.
- **Analysis modules**: market competitor and policy regulatory, both using the same registry.

## Strategy

Before v1.0, do not keep expanding providers, topic modules, or delivery channels. Priority order:

```text
Release & Runtime Contract Cleanup
→ Runtime Artifact Contract
→ Quality Mainline
→ Golden Runs & Evaluation
→ Packaging & Distribution
→ v1.0 Stable Baseline
→ v2.0 MAS Runtime (deferred)
```

---

## v0.5.8: Release And Runtime Contract Cleanup

**Goal**: make v0.5.7's architecture internally consistent.

Must do:

- **Fix Issue [#49](https://github.com/Stahl-G/multi-agent-brief-workflow/issues/49)**: clarify clone/source install vs CLI-only install boundaries, or package Hermes plugin / agent assets.
- **Update Homebrew formula**: currently points to an old version.
- **Tag v0.5.7** or adjust release consistency rules so CI no longer fails.
- **Rewrite `docs/roadmap.md` and `docs/architecture.md`**: remove `prepare` and old Python pipeline narratives.
- **Establish Support Matrix**: `Supported / Experimental / Interface Only / CLI-only / Deprecated` for Hermes, OpenCLI, local_signal, delivery, PDF, Homebrew/curl.

Done when:

- CI is green, no tag drift errors.
- README, AGENTS.md, CLAUDE.md match current entry points.
- Support Matrix doc exists with clear status labels for every capability.

---

## v0.5.9: Runtime Artifact Contract

**Goal**: make the subagent workflow verifiable, not just prompt-sequenced.

Must do:

- **`run_manifest.json` reuse**: record each intermediate artifact's existence, hash, producer, and status across handoff/runtime stages.
- **New artifact validators**: `validate-candidates`, `validate-screened`, `validate-ledger`, `validate-handoff` — auto-validated after each subagent output.
- **`inputs classify` result enters `agent_handoff.json`**: Scout must execute against the evidence list, not freely scan all of `input/`.
- **Runtime parity tests**: Hermes / Claude Code / OpenCode / Codex artifact contracts tested for consistency.
- **RelevanceGate**: output `output/intermediate/relevance_report.json`, placed after claim-ledger and before analyst. Decides which claims enter body, summary, appendix, or are discarded.

Done when:

- Any runtime execution produces a complete `run_manifest.json`.
- CI can run `validate-handoff` without LLM dependency.
- Scout only sees evidence files.

---

## v0.6.0: Quality Mainline

**Goal**: address DOCX quality gaps. The mainline is quality, not feature expansion.

Must do:

- **Upgrade `analysis_blocks` from sidecar tools to formal writer contract**: analysts must follow structured analysis block templates, not free-form writing.
- **Enforce Fact / Case / Interpretation / Limitation / Action / To Verify distinction**: Claim schema upgrade with epistemic type and evidence relation dual dimensions.
- **Wire in Issues [#19](https://github.com/Stahl-G/multi-agent-brief-workflow/issues/19), [#41](https://github.com/Stahl-G/multi-agent-brief-workflow/issues/41), [#43](https://github.com/Stahl-G/multi-agent-brief-workflow/issues/43)**.
- **RelevanceGate formalized**: decides which claims go into body, summary, appendix, or discard.
- **DeliveryGate**: checks language, audience match, topic relevance, entity relevance, section completeness, English leakage, template leakage.

Done when:

- Analyst outputs structured analysis blocks, not free-form full briefs.
- Every reader-facing report passes RelevanceGate + DeliveryGate.
- DOCX/Markdown output shows no section gaps or template leakage even with weaker models.
- Weak/free models only perform constrained local tasks (extract claims, write single analysis paragraphs), never one-shot full weekly briefs.

---

## v0.6.1 — v0.6.2: Evaluation And Golden Runs

**Goal**: objectively measure whether quality is improving.

Must do:

- **Build 5 golden workspace categories**: normal weekly, quiet week, sparse evidence, conflicting sources, feedback contamination.
- **Save expected artifacts per category** (no real private materials).
- **Quality metrics**: relevance, claim coverage, unsupported statements, language match, reader depth, DOCX render fidelity.
- **`mabw eval` command or CI golden smoke**: no requirement for identical model output, but contracts and quality gates must pass.

Done when:

- `mabw eval --golden normal_weekly` runs in CI.
- Every PR has quality regression signals, not just pytest pass/fail.

---

## v0.7: Packaging And Distribution

**Prerequisite**: v0.5.8 package asset issues resolved.

Must do:

- **Formally support curl / PowerShell / Homebrew upgrade paths**.
- **`multi-agent-brief assets install --profile hermes|claude|opencode|codex`**: one-command runtime adapter installation.
- **`multi-agent-brief assets doctor`**: check installed asset completeness and version match.
- **Package resources via `importlib.resources`**: no dependency on CWD being repo root.

Done when:

- `curl install.sh | bash` followed by `multi-agent-brief assets install --profile hermes` works.
- `assets doctor` outputs completeness and version check report.
- Homebrew formula points to latest version and installs correctly.

---

## v1.0: Stable Baseline

**v1.0 is not a MAS Runtime.** v1.0 freezes:

- subagent-first handoff contract
- Hermes primary path
- input governance (inputs classify + hard gates)
- RelevanceGate + DeliveryGate
- golden eval baseline (5 workspace categories + quality metrics)
- package/install story (curl / PowerShell / Homebrew / importlib.resources)
- Support Matrix

Scope:

- Golden datasets: normal weekly, quiet week, sparse evidence, conflicting sources, feedback contamination.
- Benchmark metrics: source count, claim count, citation coverage, unsupported statements, high-risk findings, audit status, runtime, cost, artifact hashes.
- Contract compliance tests for SourceProvider, AnalysisModule, AuditAgent, OutputRenderer, DeliveryConnector.
- Release consistency gate.
- `v1-maintenance` branch: fixes, governance gaps, compatibility, and documentation only.

Done when:

- v1.0 runs all officially supported capabilities from a fresh install.
- v1.0 has stable interfaces, public-safe benchmarks, and regression metrics.
- v1.0 serves as the comparison and fallback engine for future MAS Runtime work.

---

## v2.0: MAS Runtime (deferred)

v2.0 should not become the main path before v1.0 is frozen.

Recommended first scope: `mas-runtime-foundation`.

- Shared World / SQLite Event Store
- Typed Event / AgentMessage envelope
- TaskBoard, leases, minimal Contract Net
- AgentState / inbox cursor / capability registry
- ClaimProposal state machine
- Deterministic ClaimReducer
- Run replay and v1-compatible Claim Ledger export

Not in first scope:

- Full Analyst / Editor / Auditor / Formatter migration
- Multi-server, Kafka, Redis
- One-shot migration of all connectors and analysis modules
- v2 as README main path

See [v2.0 MAS Runtime Evaluation](mas-v2-evaluation.zh-CN.md).

---

## Deferred

Before v1.0, constrain:

- More search backends and delivery channels
- Full model routing
- Full RAG / long-term memory
- Many new topic modules
- Scheduling, multi-tenant, enterprise deployment
- Unfinished PDF / Email / Slack / Telegram

Unstable capabilities must be labeled Experimental or Interface Only.
