# Architecture Status

This page separates current implementation state from roadmap goals. Use it before planning roadmap-driven changes.

Public naming note: BriefLoop is the public project name for the v0.9
compatibility period. MABW remains the implementation lineage and compatibility
surface for `multi-agent-brief`, the `briefloop` shell alias, `/briefloop`,
`/mabw`, Python package/module paths, artifact names, workspace formats, and
experiment IDs. This page describes implemented runtime capability, not a
breaking rename.

## Implemented Public Baseline

- The standard user path is subagent-first.
- `multi-agent-brief run` creates runtime handoff artifacts rather than generating a full brief by itself.
- Runtime handoff now initializes minimum runtime state and artifact registry control files.
- Feedback issues and bounded repair plans can be structured, validated, and recorded without executing repair.
- The default role topology lets Scout perform discovery and screening while keeping `candidate_claims.json` and `screened_candidates.json` as distinct artifacts; strict topology can keep Screener independent.
- Topology-satisfied stages are recorded in workflow state and event log; they do not synthesize a separate downstream stage execution history.
- Claim Ledger freeze is Python-owned: Claim Ledger agents write `claim_drafts.json` without claim IDs, then `state freeze-claim-ledger` assigns deterministic IDs, writes canonical `claim_ledger.json`, records freeze metadata, and gates Claim Ledger stage completion on the frozen ledger.
- Stage completion transactions can record runtime/model provenance for the stage in workflow state and event log metadata; this is audit metadata only and is not an output-quality claim.
- Deterministic material-fact, freshness, target-relevance, and editor-new-fact gates can write stage-scoped quality gate reports without fetching sources, rewriting briefs, or executing repair.
- Packaged public-safe evaluation cases can validate known gates, feedback, runtime blocker, and Hermes path regressions for development and CI.
- Optional deterministic provenance projection can write a workspace-local audit/debug graph from existing control files.
- Workspace-local audience taste profiles can be frozen into per-run snapshots and exposed through runtime handoff as context.
- The Orchestrator control switchboard can surface deterministic control recommendations and record enable/defer/reject selections without executing those controls.
- Finalize writes the reader delivery bundle under `output/delivery/`, appending the source appendix to delivery Markdown/DOCX while retaining `output/source_appendix.md` as an audit/control copy. Delivery artifacts must not expose internal claim IDs, source IDs, evidence text, local paths, or file URLs.
- Runtime asset availability is now explicit: packaged installs include contract configs and public-safe eval fixtures, while source runtime assets such as `.agents/`, `.claude/`, `.opencode/`, `.codex/`, and Hermes plugin files are source-clone-only unless copied into a workspace with `multi-agent-brief runtime install`.
- The Improvement Ledger lifecycle can preserve human-authored, human-approved reader guidance in `improvement/ledger.jsonl`, project approved materializable entries into `improvement/memory.md`, freeze per-run `output/intermediate/improvement_memory_snapshot.md`, and expose only the frozen snapshot through handoff.
- Packaged public-safe evaluation cases now cover Improvement Memory control behavior: unapproved entries are not materialized, approved guidance is frozen, and reverted entries disappear from the next snapshot.
- Experimental Atomic Claim Graph controls can validate an optional
  `output/intermediate/atomic_claim_graph.json`, check whole-ledger coverage and
  deterministic Claim Ledger type consistency, expose Analyst/Editor
  no-new-atom contract boundaries, and project atom-ID reader residue. This is
  structural visibility only, not evidence-span support sufficiency.
- Experimental Evidence Span Registry controls can validate an optional
  `output/intermediate/evidence_span_registry.json`, bind declared spans to
  durable `input/sources/` bytes, archive span/source hashes, and project a
  reader-safe source appendix span summary plus a separate
  `output/source_appendix_trace.md` audit copy. This is span-level
  traceability and archive reproducibility only, not semantic support
  assessment or support-sufficiency gating.
- Experimental Claim-Support Matrix controls can validate an optional
  `output/intermediate/claim_support_matrix.json` schema, validate its Claim
  Ledger / Atomic Claim Graph / Evidence Span Registry references, require
  high-materiality atom row coverage when the matrix is present, and project
  explicit atom-to-evidence rows into status summaries and quality-gate
  findings. This is a support-record control plane only, not automatic support
  assessment, semantic proof, release eligibility, or a support-sufficiency
  gate.
- Experimental Semantic Assessment Report controls can validate an optional
  `output/intermediate/semantic_assessment_report.json` schema, validate
  machine-checkable references to Claim Ledger claims, Atomic Claim Graph atoms,
  and Evidence Span Registry spans, project rows into proposal-only
  Claim-Support Matrix delta candidates, and surface read-only status counts.
  This is a proposal surface only, not accepted support truth, adjudication
  queue creation, delivery gating, release authority, or semantic proof.
- Experimental ReportSpec / ReportPack / ReportTemplate / PolicyProfile
  controls can validate a product-layer `report_spec.yaml`, inspect packaged
  report pack, section order template, and policy default contracts such as
  `market_weekly`, `management_monthly`, `solar_industry_periodic`,
  `manufacturing_default`, `solar_manufacturing_default`, `finance_default`,
  and `internet_default`, create conservative local-first
  workspace skeletons with `briefloop new <pack> <workspace>` /
  `multi-agent-brief new <pack> <workspace>`, and project finalized workspace
  artifacts into explicit delivery/audit bundle manifests.
  Workspaces with `report_spec.yaml` expose the resolved PolicyProfile in
  read-only status and generated handoff artifacts so defaults are traceable.
  `briefloop new` can use an explicit `--policy-profile` or deterministic
  `--industry` hint to write the selected profile and resolution source into
  `report_spec.yaml`; gates do not silently infer policy from natural-language
  industry strings.
  Workspaces with `report_spec.yaml` also expose the resolved packaged
  ReportTemplate section order in read-only status and generated handoff
  artifacts so product section contracts are visible before drafting.
  Resolved PolicyProfiles may tighten existing deterministic quality-gate
  strictness and reader-final forbidden-phrase checks through a limited adapter.
  These contracts describe report type metadata over the existing Claim Ledger,
  artifact registry, gates, event log, archive, source appendix, support
  records, frozen-artifact integrity, and human delivery approval spine. These
  product-layer surfaces do not run stages, render templates, rewrite content,
  create a second gate engine, judge industry compliance, verify internet
  rumors, bypass gates, deliver reports, provide tax or investment advice, or
  authorize publication.
- Python commands provide setup, source tooling, validation, audit support, and rendering.
- Hermes, Claude Code, Codex, OpenCode, and manual fallback are treated as agent runtime surfaces.
- Input governance can extract supported non-text input documents to Markdown with MinerU, then separates evidence from feedback, instructions, and background context.
- Old Python-pipeline framing is deprecated for the standard workflow.

## Roadmap Goals

The roadmap mentions concepts that are not necessarily implemented yet. Treat these as goals unless the code, tests, and support matrix show otherwise:

- Orchestrator contracts
- semantic evidence support verification
- quality evaluation and feedback loops
- private or commercial benchmark suites
- policy packs
- public-safe reference workflows
- FrictionStore, autonomous learning, retrieval memory, runtime-specific guidance filtering, and output-quality validation
- deferred semantic-governance structures such as semantic support scoring,
  human adjudication, release eligibility, and support-sufficiency gates; these
  are not the next default implementation track after the v0.9 support core

## Experimental Or Limited Surfaces

Features marked experimental, interface-only, or CLI-only should not be treated as stable user promises. Check the support matrix and CLI output before relying on them.

## Contributor Rule

Roadmap direction is not proof of implementation. When implementing a roadmap item, first identify the current code path, the owning validator or test, and whether the capability is public, experimental, or internal planning only.
