# Public Roadmap

This is the public roadmap for BriefLoop. It describes product direction and version goals at a high level. Detailed implementation planning, schema drafts, prompt notes, private evaluation cases, and commercial scenario design are intentionally kept out of the public repository until they are stabilized in code. Retired names appear only inside literal archived or compatibility identifiers defined by `docs/briefloop-naming.md`.

## Direction

BriefLoop is moving toward an orchestrated, contract-governed briefing workflow:

```text
subagent-first runtime
→ orchestrator contracts
→ feedback and repair loop
→ checkpointed quality gates and evaluation
→ workspace memory and control switchboard
→ provenance-aware artifacts
→ policy packs and runtime parity
→ stable v0.11.0 product baseline
→ v1.0 first-user evidence and frozen product contract
→ evidence-bound review and evidence-led substrate decisions
```

The project is not trying to rebuild a full distributed multi-agent runtime before the v0.11.0 product baseline. Python remains a toolkit for setup, source handling, validation, audit, and rendering. The workflow runtime is coordinated by an external main agent and delegated subagents.

Two design principles guide the next phases:

- Stage boundaries are contract boundaries. Some gates are machine-only, some require human semantic approval, and some combine machine findings with Orchestrator judgment.
- Memory is workspace-local and human-governed. The project may add agent-proposed memory updates and frozen per-run snapshots, but it will not become a full long-term-memory or RAG platform before the v0.11.0 product baseline.

## Completed Baseline

### v0.5.7

- `briefloop run` became a runtime handoff launcher rather than a Python brief generator.
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
- `gates check`, `gates show`, and `gates validate` provide CLI entry points for stage-scoped quality gate reports under `output/intermediate/gates/`; `quality_gate_report.json` remains a latest/legacy projection.
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
- The reader-facing source list is appended to delivery Markdown/DOCX under `output/delivery/`, while `output/source_appendix.md` remains an audit/control copy. It is generated only from claims actually cited in `output/intermediate/audited_brief.md` and resolved through `output/intermediate/claim_ledger.json`.
- Reader-facing output must not expose raw claim IDs, source IDs, evidence text, local paths, or `file://` URLs.
- The appendix is not source evidence, semantic proof, a runtime state file, a provenance graph, or a workflow gate.

### v0.6.9

- Install/runtime asset parity is stabilized before v0.7 improvement-proposal work.
- Package installs include Python CLI code, packaged contracts, policy packs, and packaged public-safe eval fixtures.
- Source runtime directories such as `.agents/`, `.claude/`, `.codex/`, `.opencode/`, and `integrations/hermes-plugin/` are documented as source-clone-only unless copied into a workspace.
- `briefloop runtime install --workspace <workspace> --runtime opencode|claude|codex|all` installs workspace-local OpenCode/Claude Code runtime kits and Experimental Codex custom-agent assets from a source clone.
- v0.6.9 does not add FrictionStore, improvement proposal commands, policy-pack authoring, or automatic workflow execution.

### v0.7.0

- Improvement Ledger lifecycle commands are implemented: `improve propose/list/show/approve/reject/revert/stats/validate/rebuild`.
- Human-authored, human-approved reader guidance can be stored in `improvement/ledger.jsonl`.
- Approved materializable guidance is projected into `improvement/memory.md` and frozen per run into `output/intermediate/improvement_memory_snapshot.md`.
- Runtime handoff exposes only the frozen snapshot, not live `improvement/memory.md`.
- `runtime_manifest.json.improvement` records `ledger_sha256`, `memory_sha256`, `snapshot_path`, `snapshot_sha256`, and `materialized_entry_ids` for the active run.
- Public-safe eval cases validate control behavior for unapproved, approved, and reverted improvement entries.
- v0.7.0 does not add FrictionStore, autonomous learning, retrieval memory, runtime-specific guidance filtering, output-quality validation, ledger compaction, policy-pack authoring, or automatic workflow execution.

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

Public sequencing after v0.7.0 moves toward FrictionStore, policy packs, reference workflows, and runtime parity while preserving the subagent-first runtime boundary.

Public implementation overviews:

- [Implementation overview index](implementation/README.md)
- [v0.5.9 Orchestrator Contract Preparation](implementation/v0.5.9-orchestrator-prep.md)
- [v0.6.0 Explicit Orchestrator Contract](implementation/v0.6.0-explicit-orchestrator-contract.md)

Non-goals:

- no full DAG runtime
- no wholesale rewrite of all agents
- no final report rendering redesign
- no new search provider expansion

### v0.7 — Improvement Ledger And Controlled Memory

Goal: preserve bounded, evidence-linked, human-gated reader-preference guidance as auditable workspace memory without making it autonomous learning.

Implemented in v0.7.0:

- Improvement Ledger lifecycle.
- Approved guidance materialization into deterministic memory projection.
- Frozen per-run Improvement Memory snapshot exposed through handoff.
- Public-safe eval cases for Improvement Memory control behavior.

Deferred:

- FrictionStore and automatic recurring-failure detection.
- Autonomous learning.
- Retrieval memory or RAG platform behavior.
- Runtime-specific guidance filtering.
- Output-quality validation for improvement guidance.
- Ledger compaction.
- Policy-pack-driven memory routing.

Non-goals:

- no public release of private golden examples
- no automatic self-modification of the main branch
- no raw prompt, raw log, or private feedback injection into public prompts
- no full RAG platform or autonomous long-term-memory system

### v0.8 — Measurement, Fast Rerun, Role Topology, And Evaluation

Goal: make the runtime trace measurable, make same-evidence reruns cheaper, simplify the default role topology without weakening accountable artifacts, and define the first evaluation protocol for whether approved guidance manifests without causing regressions.

Public scope:

- Add run-integrity and timing surfaces so runtime traces can distinguish clean, incomplete, and contaminated runs before performance claims are made.
- Add planned fast-rerun infrastructure for hash-verified same-evidence rewrites without skipping writer, auditor, gates, finalize-complete, human delivery, or archive.
- Simplify the default role topology while preserving the same accountable artifact set, including candidate claims, screened candidates, Claim Ledger, audit report, gate reports, and delivery bundle.
- Keep policy-pack and recipe work minimal at first: enough to support fast-rerun and default/strict topology choices, not a full mode-registry expansion.
- Preserve a single public support matrix.
- Define guidance manifestation and guidance regression measurements for real runtime traces. `origin_runtime` may be used for analysis, but not for runtime filtering or routing.

Non-goals:

- no disclosure of commercial policy-pack internals before they are stable
- no full mode-registry expansion before minimal recipe/pack surfaces are proven
- no runtime-specific artifact schema forks
- no separate simplified pipeline for GUI or messaging entry points
- no `lite mode`, no gate-skipping fast path, and no partial fact-layer imports
- no claim that v0.7 Improvement Memory has already improved output quality before the v0.8 protocol is run

### v0.9 — Support Sufficiency Core

Goal: move from source-level traceability toward a minimum support-sufficiency
core while preserving literal legacy identifiers needed by existing users and
archived experiments.

Public scope:

- Use BriefLoop as the only project name; legacy identifiers remain literals, not an alternate name or implementation lineage.
- Implement the minimum support-sufficiency path:
  - Atomic Claim Graph
  - Evidence Span Registry
  - Claim-Support Matrix
- Keep Semantic Assessment Report as a proposal-only experimental surface:
  semantic assessments may propose support labels, uncertainty, disagreement,
  and adjudication needs, but they do not mutate the Claim-Support Matrix,
  create adjudication queue items, gate delivery, decide release eligibility, or
  prove truth.
- Keep `multi-agent-brief`, `/mabw`, Python package/module paths, artifact names, workspace formats, and frozen experiment IDs such as `MABW-080` compatible during the v0.9 period; recommend `briefloop` and `/briefloop` to new users.
- Keep unsupported channels clearly labeled as experimental, interface-only, or CLI-only.

Deferred semantic-governance surfaces:

- human adjudication
- coverage and omission gates
- semantic regression
- release eligibility
- quality packs
- finding-to-repair workflows

These are not the next default implementation track. They may reopen after the
product layer has stable report contracts and real user paths.

### v0.10 — Product OS And Report Packs

Goal: wrap the support-sufficiency core in a usable recurring-report product
layer without weakening the accountability spine.

Public scope:

- Add ReportSpec and ReportPack contracts so BriefLoop can know what kind of
  report is being produced.
- Introduce initial report packs such as `market_weekly`,
  `management_monthly`, and later `evidence_extract`.
- Improve zero-config workspace creation while keeping `briefloop` as
  the public CLI, `multi-agent-brief` as its compatibility entrypoint,
  `/briefloop` as the writer command, and `/mabw` as the compatibility writer command.
- Separate reader-facing delivery bundles from audit/control bundles as an
  export/projection layer, without silently moving or deleting existing control
  artifacts.
- Keep local files and simple source setup first-class; broad connector and UI
  work stays later.
- Add release modes and human approval records for internal review workflows,
  without claiming external publication authorization.

Non-goals:

- no SaaS-first product
- no heavy UI before the CLI product path works
- no IR/disclosure readiness claim
- no report pack that bypasses Claim Ledger, gates, event log, archive,
  reader-final gate, source appendix, or human delivery
- no confusion between the `/briefloop` writer command and the BriefLoop skill
  operator protocol surface
- no automatic public release or external publication command

### v0.11.0 — Stable Weekly/Monthly Brief Product

Goal: freeze a modest, local-first, file-state-driven, contract-governed CLI
product for recurring business reports.

v0.11.0 should provide:

- product-facing zero-config entries such as `briefloop new industry-weekly`
  and `briefloop new management-monthly`, while keeping canonical internal
  ReportPack ids such as `market_weekly` and `management_monthly`
- a `document-review` entry mapped to the internal `evidence_extract` report
  pack for page/span-cited document work
- local-file first runs through the report loop
- stable Markdown and DOCX output
- preserved Claim Ledger, source appendix, gates, event log, support records,
  and archive surfaces
- stable ReportSpec and ReportPack contracts
- at least three report packs
- clear delivery/audit bundle separation
- explicit human delivery
- no force-deliver path or delivery override flag
- clear runtime dependence, support status, and non-goals

### v1.0.0 — Frozen Product Contract And First-User Evidence

Goal: close the release-candidate safety work, freeze the supported product
contract, and obtain public-safe evidence that a new user can complete and
understand the recurring-report workflow in a fresh environment.

Public scope:

- Close the completion, recovery, agent-artifact intake, evidence, and delivery
  truth invariants required by the v1.0 release boundary.
- Run the executable readiness checks and record at least one qualifying
  first-user pilot in `docs/v1-pilot-evidence.md`.
- Freeze the supported CLI, workspace, control-record, gate, archive, and
  delivery surfaces identified by the release process.
- Keep JSON/JSONL as the sole authoritative control-state substrate for v1.0.

Non-goals:

- no storage-authority migration as a v1.0 blocker
- no SQLite or other database as a second control plane
- no broad framework migration mixed into RC hardening
- no output-quality or management-readiness claim beyond recorded evidence

### Post-v1 — Evidence-Led Product Evolution

Goal: let real use determine the next supported product surface instead of
pre-committing the public roadmap to a framework or storage migration.

Public direction:

- Extend evidence-bound semantic review only where frozen inputs, deterministic
  checks, and human responsibility remain explicit.
- Prioritize report packs and workflow improvements that repeated Pilot or
  production use shows are valuable.
- Evaluate infrastructure or persistence changes through bounded experiments
  and public-safe architecture decisions before they become product promises.
- Require any future authority-substrate proposal to demonstrate a real failure
  it removes, a safe migration path, and one authoritative truth throughout.

Non-goals:

- no technology choice promoted to product value without user evidence
- no long-lived dual authority or agent-writable control state
- no claim that a storage or framework change improves report quality by itself
- no detailed implementation sequence in the public roadmap before approval

## Research Track

A local Studio preview after the CLI product path works may reopen as a
research track only after the v1 Pilot produces relevant user evidence.
Studio must call existing CLI/service
transactions, must not mutate frozen artifacts directly, and must not provide a
force-deliver path or delivery override flag.

Later research releases may add IR/disclosure support packs as review-support
surfaces, not publication automation. Those packs may flag forward-looking
statements, materiality review items, KPI consistency issues, and
evidence-annex gaps, but they must not claim automatic materiality decisions,
SEC-ready filing automation, or replacement of lawyers, auditors, IR officers,
or disclosure committees.

v2.0 is a future research track, not the short-term product promise. After the
product baseline is stable, the project may explore a more formal multi-agent
runtime, including shared state, task boards, replay, and richer coordination
protocols.

Until these product outcomes are evidenced, the project will not prioritize:

- distributed multi-server orchestration
- enterprise multi-tenancy
- full long-term memory or RAG platform work
- automatic main-branch self-modification
- broad connector expansion for its own sake

## Planning Privacy

Public roadmap files should not include detailed schema drafts, full contract examples, private golden cases, commercial scenario design, private prompt notes, or failure taxonomies. Those details belong in ignored internal planning files until they are implemented and safe to publish.
