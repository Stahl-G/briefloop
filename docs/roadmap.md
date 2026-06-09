# Public Roadmap

This is the public roadmap for Multi-Agent Brief Workflow. It describes product direction and version goals at a high level. Detailed implementation planning, schema drafts, prompt notes, private evaluation cases, and commercial scenario design are intentionally kept out of the public repository until they are stabilized in code.

## Direction

Multi-Agent Brief Workflow is moving toward an orchestrated, contract-governed briefing workflow:

```text
subagent-first runtime
→ orchestrator contracts
→ feedback and repair loop
→ checkpointed quality gates and evaluation
→ workspace memory and control switchboard
→ provenance-aware artifacts
→ policy packs and runtime parity
→ stable v1.0 baseline
```

The project is not trying to rebuild a full distributed multi-agent runtime before v1.0. Python remains a toolkit for setup, source handling, validation, audit, and rendering. The workflow runtime is coordinated by an external main agent and delegated subagents.

Two design principles guide the next phases:

- Stage boundaries are contract boundaries. Some gates are machine-only, some require human semantic approval, and some combine machine findings with Orchestrator judgment.
- Memory is workspace-local and human-governed. The project may add agent-proposed memory updates and frozen per-run snapshots, but it will not become a full long-term-memory or RAG platform before v1.0.

## Completed Baseline

### v0.5.7

- `multi-agent-brief run` became a runtime handoff launcher rather than a Python brief generator.
- The standard workflow moved to external subagents for source extraction, screening, claim ledger creation, drafting, editing, audit, and formatting.
- Hermes became the primary runtime path for scheduled and delegated brief workflows.
- Input governance separates evidence from feedback, instructions, and context.

### v0.5.8

- Old Python-pipeline narratives were removed from the standard path.
- The support matrix, release checks, and version consistency workflow were cleaned up.
- Install and runtime support boundaries were clarified.

### v0.6.0

- Shared Orchestrator authority, decision vocabulary, contract references, and runtime role parity were established.
- Runtime handoff artifacts now point every supported runtime to the same Orchestrator control model.
- Contract references are packaged with the Python distribution so `run` works after non-editable installs.
- Persisted runtime state, artifact registry execution, feedback repair, and provenance graph work remained later v0.6 milestones.

### v0.6.1

- Minimum runtime state control files were added for Orchestrator handoff runs.
- The artifact registry now records minimum file status without executing workflow stages.
- Stage-scoped blocking distinguishes pending downstream artifacts from artifacts that block the current stage.
- `state init`, `state check`, `state show`, and `state decide` provide CLI entry points for runtime inspection and decision recording.
- Automated repair execution and provenance graph work remain later v0.6 milestones.

### v0.6.2

- Feedback issue handling and bounded repair planning were added without turning Python into a repair executor.
- `feedback ingest`, `feedback plan`, `feedback resolve`, `feedback show`, and `feedback validate` provide CLI entry points for human feedback and audit findings.
- Feedback and repair control artifacts are tracked without exposing repair internals in the public roadmap.
- Feedback blocking is scoped to the current stage, and repair decisions still flow through the Orchestrator decision vocabulary.
- Python does not automatically edit brief artifacts, execute repair, or judge semantic repair success.

### v0.6.3

- Deterministic material-fact, freshness, and target-relevance gates were added for auditable artifacts.
- `gates check`, `gates show`, and `gates validate` provide CLI entry points for `quality_gate_report.json`.
- Quality gate blocking is scoped to the current stage and uses explicit blocking semantics rather than treating every high-severity finding as a runtime stop.
- Python does not live-fetch market data, recrawl sources, rewrite briefs, execute repair, or make semantic truth judgments.

### v0.6.4

- Packaged public-safe evaluation cases were added for developer and CI regression checks.
- `eval-cases list`, `eval-cases validate`, and `eval-cases run` provide CLI entry points for gates, feedback, runtime blocker, and Hermes static invariant cases.
- Evaluation cases use structured allowlisted actions rather than shell strings.
- Evaluation outputs remain developer/CI results and are not added to workflow artifact contracts.
- Python does not score prose, call an LLM judge, execute repair, run subagents, or fetch sources as part of eval cases.

### v0.6.5

- Optional deterministic provenance projection was added for workspace audit/debug review.
- `provenance build`, `provenance show`, and `provenance validate` provide CLI entry points for `provenance_graph.json`.
- The graph projects existing runtime state, artifact registry, event log, Claim Ledger, feedback, repair, and quality gate control files.
- Provenance edges use citation/control wording and do not assert that a source semantically proves a claim.
- Python does not execute workflow stages, fetch sources, replay a DAG, execute repair, verify semantic truth, or gate `finalize` by default as part of provenance projection.

### v0.6.6

- Workspace-local audience taste profiles were added as runtime context surfaces.
- `audience_profile.md` is human-editable and lives at the workspace root.
- `run`, `start`, and `handoff` create or reuse a frozen per-run `output/intermediate/audience_profile_snapshot.md`.
- Handoff JSON/Markdown expose `audience_memory_files` separately from expected artifacts and control files.
- Python does not treat audience profile content as source evidence, an artifact contract, a quality gate, provenance graph expansion, automatic learning, or a long-term memory system.

### v0.6.7

- The Orchestrator control switchboard was added as a runtime control surface.
- `run`, `start`, and `handoff` create `output/intermediate/orchestrator_control_switchboard.json`.
- `controls build-switchboard`, `controls show`, `controls select`, and `controls validate` provide CLI entry points for recommendations and explicit Orchestrator selections.
- `control_selections.json` records enable/defer/reject choices only when selected by the Orchestrator.
- Selection is not execution: Python does not automatically run gates, feedback planning, provenance projection, source discovery, repair, or subagents.

### v0.6.8

- Reader-facing source appendix generation was added to finalize.
- `source_appendix` is the current output format name; legacy `source_map` remains a compatibility alias.
- The reader-facing source list is appended to final Markdown/DOCX by default and kept as `output/source_appendix.md`; it is generated only from claims actually cited in `output/intermediate/audited_brief.md` and resolved through `output/intermediate/claim_ledger.json`.
- Reader-facing output must not expose raw claim IDs, source IDs, evidence text, local paths, or `file://` URLs.
- The appendix is not source evidence, semantic proof, a runtime state file, a provenance graph, or a workflow gate.

### v0.6.9

- Install/runtime asset parity is stabilized before v0.7 improvement-proposal work.
- Package installs include Python CLI code, packaged contracts, policy packs, and packaged public-safe eval fixtures.
- Source runtime directories such as `.agents/`, `.claude/`, `.codex/`, `.opencode/`, and `integrations/hermes-plugin/` are documented as source-clone-only unless copied into a workspace.
- `multi-agent-brief runtime install --workspace <workspace> --runtime opencode|claude|all` installs workspace-local OpenCode/Claude Code runtime kits from a source clone.
- v0.6.9 does not add FrictionStore, improvement proposal commands, policy-pack authoring, or automatic workflow execution.

## Next Milestones

### v0.5.9 — Roadmap Privacy And Architecture Status

Goal: keep a useful public roadmap while moving detailed implementation plans out of the public repository.

Public scope:

- Simplify the roadmap to version goals and module boundaries.
- Add current architecture status so contributors can distinguish implemented features from future targets.
- Add migration notes for the shift from the old Python-pipeline framing to the Orchestrator-first architecture.
- Add ignore rules for internal planning files.

Non-goals:

- no runtime behavior changes
- no new schemas
- no new source providers
- no prompt or agent role rewrites

### v0.6 — Orchestrator Contracts And Feedback Loop

Goal: make the main agent explicit, then quickly demonstrate a closed loop from output to feedback to bounded repair. The Orchestrator should coordinate specialist subagents, validate handoff artifacts, capture feedback, route repairs, and block unsafe progress.

Public scope:

- Define high-level Orchestrator responsibilities.
- Define four public contract categories:
  - Behavior
  - Process / Artifact
  - Fact-Grounding / Evidence
  - Quality / Audience
- Establish a minimal runtime state and artifact status layer.
- Introduce a feedback and repair loop before expanding deeper provenance work.
- Add quality gates for material facts, source freshness, and target relevance.
- Classify stage gates as machine-only, human-in-the-loop, or mixed where that distinction affects Orchestrator decisions.
- Introduce public-safe failure-pattern evaluation cases.
- Keep provenance projection as audit/debug tooling while deferring semantic proof, replay, and graph-database style query systems.
- Keep Python positioned as tools, validators, and renderers rather than the workflow runtime.

Public sequencing after v0.6.9 moves toward FrictionStore, improvement proposals, and policy packs while preserving the subagent-first runtime boundary.

Public implementation overviews:

- [Implementation overview index](implementation/README.md)
- [v0.5.9 Orchestrator Contract Preparation](implementation/v0.5.9-orchestrator-prep.md)
- [v0.6.0 Explicit Orchestrator Contract](implementation/v0.6.0-explicit-orchestrator-contract.md)

Non-goals:

- no full DAG runtime
- no wholesale rewrite of all agents
- no final report rendering redesign
- no new search provider expansion

### v0.7 — FrictionStore And Improvement Proposals

Goal: turn recurring failures, audit findings, human feedback, and workspace memory signals into controlled improvement proposals.

Public scope:

- Track recurring failure patterns across runs.
- Generate improvement signals, patch plans, and regression-plan suggestions.
- Extend workspace-local memory cautiously for recurring feedback patterns after the audience snapshot baseline.
- Treat memory updates as agent-proposed and human-approved.
- Keep frozen per-run snapshots so a run does not change behavior midway because of memory written during that same run.
- Keep self-improvement proposal-only until a human or maintainer approves code changes.

Non-goals:

- no public release of private golden examples
- no automatic self-modification of the main branch
- no raw prompt, raw log, or private feedback injection into public prompts
- no full RAG platform or autonomous long-term-memory system

### v0.8 — Mode Registry, Policy Packs, And Runtime Parity

Goal: support different brief contexts and entry modes through configurable policy packs while keeping runtime behavior consistent.

Public scope:

- Introduce a mode registry so the same Orchestrator and specialist roles can support full runs, source-readiness checks, audit-only runs, repair-planning-only runs, audience-profile updates, and final-render-only flows.
- Introduce policy-pack concepts for audience, industry, cadence, and delivery expectations.
- Keep Hermes, Claude Code, Codex, OpenCode, and manual fallback aligned around the same artifact expectations.
- Keep CLI, Hermes GUI/plugin, and other runtime entry points backed by the same Orchestrator contracts and state files.
- Preserve a single public support matrix.

Non-goals:

- no disclosure of commercial policy-pack internals before they are stable
- no runtime-specific artifact schema forks
- no separate simplified pipeline for GUI or messaging entry points

### v0.9 — Distribution And Reference Workflows

Goal: make installation and demo workflows easier for new users.

Public scope:

- Improve package assets, install checks, and runtime setup diagnostics.
- Provide public-safe reference workflows.
- Keep unsupported channels clearly labeled as experimental, interface-only, or CLI-only.

### v1.0 — Stable Orchestrated Brief Workflow

Goal: freeze a stable, local-first, file-state-driven, contract-governed briefing workflow baseline.

v1.0 should provide:

- a clear Orchestrator-first workflow
- auditable artifacts
- evidence-aware drafting and audit gates
- checkpointed stage transitions with explicit machine, human, or mixed gate semantics
- workspace-local memory that separates correctness contracts from taste preferences
- a public-safe mode registry for common brief workflow entry points
- runtime parity across supported agent surfaces
- public-safe evaluation coverage
- reliable rendered outputs
- clear support and security boundaries

## Research Track

v2.0 is a future research track, not the short-term product promise. After v1.0, the project may explore a more formal multi-agent runtime, including shared state, task boards, replay, and richer coordination protocols.

Before v1.0, the project will not prioritize:

- distributed multi-server orchestration
- enterprise multi-tenancy
- full long-term memory or RAG platform work
- automatic main-branch self-modification
- broad connector expansion for its own sake

## Planning Privacy

Public roadmap files should not include detailed schema drafts, full contract examples, private golden cases, commercial scenario design, private prompt notes, or failure taxonomies. Those details belong in ignored internal planning files until they are implemented and safe to publish.
