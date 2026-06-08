# Public Roadmap

This is the public roadmap for Multi-Agent Brief Workflow. It describes product direction and version goals at a high level. Detailed implementation planning, schema drafts, prompt notes, private evaluation cases, and commercial scenario design are intentionally kept out of the public repository until they are stabilized in code.

## Direction

Multi-Agent Brief Workflow is moving toward an orchestrated, contract-governed briefing workflow:

```text
subagent-first runtime
→ orchestrator contracts
→ feedback and repair loop
→ quality gates and evaluation
→ provenance-aware artifacts
→ policy packs and runtime parity
→ stable v1.0 baseline
```

The project is not trying to rebuild a full distributed multi-agent runtime before v1.0. Python remains a toolkit for setup, source handling, validation, audit, and rendering. The workflow runtime is coordinated by an external main agent and delegated subagents.

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
- Persisted runtime state, artifact registry execution, feedback repair, and provenance graph work remain later v0.6 milestones.

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
- Introduce public-safe failure-pattern evaluation cases.
- Add provenance once the feedback loop and quality gates are testable.
- Keep Python positioned as tools, validators, and renderers rather than the workflow runtime.

Public sequencing after v0.6.0:

- v0.6.1: runtime state and minimum artifact registry.
- v0.6.2: feedback issue handling and bounded repair MVP.
- v0.6.3: material-fact, freshness, and target-relevance gates.
- v0.6.4: public-safe evaluation cases from real failure patterns.
- v0.6.5: evidence and execution provenance graph.

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

Goal: turn recurring failures, audit findings, and human feedback into controlled improvement proposals.

Public scope:

- Track recurring failure patterns across runs.
- Generate improvement signals, patch plans, and regression-plan suggestions.
- Keep self-improvement proposal-only until a human or maintainer approves code changes.

Non-goals:

- no public release of private golden examples
- no automatic self-modification of the main branch
- no raw prompt, raw log, or private feedback injection into public prompts

### v0.8 — Policy Packs And Runtime Parity

Goal: support different brief contexts through configurable policy packs while keeping runtime behavior consistent.

Public scope:

- Introduce policy-pack concepts for audience, industry, cadence, and delivery expectations.
- Keep Hermes, Claude Code, Codex, OpenCode, and manual fallback aligned around the same artifact expectations.
- Preserve a single public support matrix.

Non-goals:

- no disclosure of commercial policy-pack internals before they are stable
- no runtime-specific artifact schema forks

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
