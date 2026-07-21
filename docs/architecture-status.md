# Architecture Status

This page separates current implementation state from roadmap goals. Use it before planning roadmap-driven changes.

Naming authority: BriefLoop is the only current project and product name. The
former project acronym is retired. Literal compatibility and history identifiers
such as `multi-agent-brief`, `/mabw`, `multi_agent_brief`, `mabw.*`, and
`MABW-080` remain only where existing commands, schemas, workspaces, or archived
experiments require them; they are not an implementation-lineage alias. This
page describes implemented runtime capability, not a breaking deep rename.

## Implemented Public Baseline

- The active new-run path is Experimental, fresh SQLite-only Codex. `briefloop
  init` writes the strict bootstrap; `briefloop run --runtime codex` creates or
  verifies `briefloop.db` and returns one Store-derived `CoreRunNextAction`.
- SQLite ControlStore receipts and ledger relations are the sole runtime
  authority. Strict Pydantic DTOs are the write boundary; deterministic domain
  services own effects; agents write only invocation scratch proposals.
- Runtime source intake accepts an ordered 1–256 member source pack. The host
  validates every member before mutation and registers the full pack through
  one Invocation, one UoW, and one Receipt; partial pack commits are forbidden.
- The workspace-local Codex kit is execution input, not decoration: `run` and
  runtime commands verify its exact config, Skill, reference, and role-file
  inventory against the Store-bound adapter identity.
- `role_topology=single_session` uses one shared Codex session with distinct
  Receipt-backed role invocations and stage-separated self-review. It is not an
  independent-review claim.
- JSON-only workspaces are unsupported and are never imported, migrated,
  dual-read, dual-written or used as fallback. JSON/JSONL and reader/status/QP
  files are replaceable projections only.
- The former handoff/control-file runtime paths were deleted in
  LEGACY-DELETE-2-3 (the legacy `runtime_state` stack and its consumer layer);
  retired public commands keep their fail-closed typed rejections, and the
  authority guard remains the single classification point for workspace
  authority.
- Runtime identity is explicit and single-writer: dedicated adapters inject one
  fixed canonical literal, generic CLI users pass `--runtime`, and subsequent
  status/handoff/transaction consumers reuse `runtime_manifest.runtime` without
  guessing or rewriting it. Historical `auto` / `manual` / implicit `controls` manifests are
  read-only until an explicit reset starts a canonical run.
- Runtime handoff now initializes minimum runtime state and artifact registry control files.
- Feedback issues and bounded repair plans can be structured, validated, and recorded without executing repair.
- The default role topology lets Scout perform discovery and screening while keeping `candidate_claims.json` and `screened_candidates.json` as distinct artifacts; strict topology can keep Screener independent.
- Topology-satisfied stages are recorded in workflow state and event log; they do not synthesize a separate downstream stage execution history.
- Claim Ledger freeze is Python-owned: Claim Ledger agents write `claim_drafts.json` without claim IDs, then `state freeze-claim-ledger` assigns deterministic IDs, writes canonical `claim_ledger.json`, records freeze metadata, and gates Claim Ledger stage completion on the frozen ledger.
- Stage completion transactions can record runtime/model provenance for the stage in workflow state and event log metadata; this is audit metadata only and is not an output-quality claim.
- Deterministic material-fact, freshness, target-relevance,
  coverage/omission-continuity, and editor-new-fact gates can write
  stage-scoped quality gate reports without fetching sources, rewriting briefs,
  inferring full recall, or executing repair.
- Packaged public-safe evaluation cases can validate known gates, feedback,
  runtime blocker, durable source evidence pack, event-linked release
  readiness, trajectory-regulation, and Hermes path regressions for development
  and CI.
- Trajectory Regulation can deterministically narrow the current stage's
  `workflow_state.next_allowed_decisions` to `request_human_review` and
  `block_run` after retry, repair-cycle, or repeated-blocker budgets are
  exhausted. It records this as control state and event-log evidence, but does
  not execute repair, run gates, approve delivery, decide release readiness, or
  perform agent work.
- Optional deterministic provenance projection can write a workspace-local audit/debug graph from existing control files.
- Workspace-local audience taste profiles can be frozen into per-run snapshots and exposed through runtime handoff as context.
- The Orchestrator control switchboard can surface deterministic control recommendations and record enable/defer/reject selections without executing those controls.
- Finalize writes the reader delivery bundle under `output/delivery/`, appending the source appendix to delivery Markdown/DOCX while retaining `output/source_appendix.md` as an audit/control copy. Reader-facing appendices can show safe source identity and taxonomy labels, while `output/source_appendix_trace.md` can carry internal claim/source/span IDs, source paths, source byte hashes, and metadata completeness warnings for audit review. Delivery artifacts must not expose internal claim IDs, source IDs, evidence text, local paths, or file URLs.
- Runtime asset availability is now explicit: packaged installs include contract configs and public-safe eval fixtures, while source runtime assets such as `.agents/`, `.claude/`, `.opencode/`, `.codex/`, `.codebuddy/`, and Hermes plugin files are source-clone-only unless copied into a workspace with `briefloop runtime install` or used directly from a source checkout where documented.
- The Improvement Ledger lifecycle was retired in LD2-3. Its projection and per-run freeze code lived in the deleted legacy stack, so `improvement/ledger.jsonl`, `improvement/memory.md`, and `improvement_memory_snapshot.md` have no reader or writer. The `improvement_*` event vocabulary in `contracts/v2.py` survives. A Store-native replacement is MU-2 work.
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
- Experimental offline-shadow LAJ tools can execute or exactly replay one
  public/synthetic Semantic Evaluator trial and deterministically render its
  verified archive as standalone JSON, Markdown, and static HTML. These files
  are visibly advisory second-opinion artifacts only. An explicitly supplied,
  strict `laj.json` can also appear as a separated read-only section in the
  deterministic Quality Panel when it matches the current `output/brief.md`;
  Quality Panel generation never calls the evaluator. Missing, malformed,
  failed, stale, or abstained LAJ never changes workflow status, gates,
  finalization, delivery, repair, approval, contamination, reference
  eligibility, or next-action authority; evaluator usefulness and efficacy are
  not measured.
- The v0.11 product-baseline target has stable product-facing workspace
  entries for `briefloop new industry-weekly`, `briefloop new
  management-monthly`, and `briefloop new document-review`. These entries map
  to canonical internal ReportPack ids `market_weekly`,
  `management_monthly`, and `evidence_extract`, create conservative
  local-first workspace skeletons, and preserve the Claim Ledger, artifact
  registry, quality gates, event log, archive, source appendix, support
  records, human delivery approval, and frozen-artifact integrity control
  spine. This is a workspace setup and contract baseline only; it does not run
  stages, fetch sources, parse PDFs, approve delivery, prove truth, or
  authorize publication.
- Beyond that baseline, experimental ReportSpec / ReportPack / ReportTemplate
  / PolicyProfile controls can validate a product-layer `report_spec.yaml`,
  inspect packaged report pack, section order template, and policy default
  contracts such as `solar_industry_periodic`, `manufacturing_default`,
  `solar_manufacturing_default`, `evidence_extract_default`,
  `finance_default`, and `internet_default`, and project finalized workspace
  artifacts into explicit delivery/audit bundle manifests.
  Workspaces with `report_spec.yaml` expose the resolved PolicyProfile in
  read-only status and generated handoff artifacts so defaults are traceable.
  `briefloop new` can use an explicit `--policy-profile` or deterministic
  `--industry` hint to write the selected profile and resolution source into
  `report_spec.yaml`; gates do not silently infer policy from natural-language
  industry strings.
  Workspaces with `report_spec.yaml` also expose the resolved packaged
  ReportTemplate section order in read-only status and generated handoff
  artifacts so product section contracts are visible before drafting. Read-only
  status and generated handoff artifacts can also project whether existing
  audited/final reader Markdown headings cover those sections in order. Packaged
  ReportTemplates can also declare warning-only reader contracts for required
  reader blocks, Markdown table slots, executive-summary length, and Source
  Appendix position; these diagnostics are surfaced through status, handoff,
  finalize reports, and Quality Panel without becoming gates or delivery
  approval. The same product layer exposes a render-plan projection that names
  the future render source artifact, section heading mapping, unresolved
  sections, and planned delivery targets before any renderer runs. During
  finalize, an experimental renderer can apply the
  resolved ReportTemplate section order to already-present reader Markdown
  sections before DOCX generation and reader-final checks; unresolved or extra
  top-level sections remain diagnostic/no-op.
  Packaged ReportTemplates can declare a reader/audit `citation_profile`
  (`executive`, `analyst`, or `audit`). Finalize reports and bundle manifests
  record the resolved profile so reader delivery can stay on reader-safe
  source labels while audit bundles preserve trace artifacts. This is
  citation-surface metadata only; it does not prove support, relax gates,
  remove audit trace, approve delivery, or decide release readiness.
  `sources materialize-pack` can materialize explicit manual or cached-package
  source records into `input/sources/` plus an optional hash-validated
  `source_evidence_pack_manifest.json`, giving recurring reports a durable
  source-evidence layer for archive reproducibility. Source evidence records
  preserve separate provider/storage `source_type`, retrieval/page
  `retrieval_source_type`, reader-facing `source_category`, and
  `underlying_evidence_type` metadata; this is taxonomy normalization, not
  trust scoring, source-policy gating, or semantic support judgment.
  `briefloop extract` can register an explicit
  extraction scope and copy local source files into an `evidence_extract`
  workspace's `input/sources/evidence_extract/` directory. It also writes a
  deterministic source-byte lock at
  `output/intermediate/evidence_extract_source_lock.json`, with an audit copy
  under `output/audit/`, so later status checks can detect registered source
  byte drift. It writes a deterministic page-inventory seed at
  `output/intermediate/evidence_extract_page_inventory.json`, giving UTF-8 text
  sources a single logical page ID while marking binary/PDF sources as
  registered-only and requiring a future extraction tool. For UTF-8 text
  sources, it also writes a deterministic text-span seed registry at
  `output/intermediate/evidence_span_registry.json` with source-text character
  offsets (`char_start` / `char_end`), page IDs, and raw-excerpt hashes. It
  still does not parse PDFs or binary documents, render pages for visual
  inspection, extract tables or figures, judge semantic support, generate
  Claim-Support Matrix rows, create legal or disclosure conclusions, run
  stages, approve delivery, or bypass gates.
  Experimental SourceHub Lite setup commands can copy local text evidence files
  into `input/sources/sourcehub/`, register RSS feeds, and register
  `runtime_tool` web-search handoff tasks in `sources.yaml`. These commands are
  source setup only: they do not execute web search, crawl the web, turn source
  candidates or search summaries into evidence, generate Evidence Span Registry
  entries, run stages, approve delivery, or bypass gates.
  Resolved PolicyProfiles may tighten existing deterministic quality-gate
  strictness and reader-final forbidden-phrase checks through a limited adapter.
  Internal release-mode approval commands can initialize
  `human_approval_ledger.json`, append human approval decisions, and write
  `release_readiness_report.json` with event-log records for internal review
  workflows. These checks separate internal readiness from authorization: they
  do not publish externally, authorize public release, replace legal/compliance
  or IR owners, or bypass existing gates and human delivery approval.
  Experimental Quality Panel projection can write
  `output/intermediate/quality_panel.json` as a machine-readable summary of
  existing control integrity, source evidence, gate, claim/support, and
  delivery hygiene surfaces, with optional
  `output/intermediate/quality_summary.md` as a compact human-readable
  projection from that panel and optional
  `output/intermediate/quality_panel.html` as a static no-JavaScript audit
  attachment. The experimental `quality summarize` command can
  write these artifacts together, and report bundle projection can include
  them in audit bundles while keeping them out of reader-facing delivery
  bundles. These product-quality audit/control
  projections do not run gates, create a quality score, replace gate reports,
  decide release eligibility, approve delivery, prove semantic truth, or execute
  repair.
  Experimental Trajectory Regulation projection can read
  `workflow_state.json` and `event_log.jsonl` to summarize repeated retry,
  repair, and blocker patterns in `status --json` and Quality Panel
  recommended actions. It is a read-only operator safety diagnostic: it does
  not change workflow state, start repair, run gates, block stages, approve
  delivery, or decide release readiness.
  Experimental Materiality Selection projection can read valid
  `screened_candidates.json`, the resolved PolicyProfile materiality terms, and
  workspace focus terms to surface excluded or deprioritized candidates that
  match explicit materiality/focus terms after capacity or scope screening.
  It is deterministic keyword diagnostics only: it does not infer semantic
  importance, mutate screening output, resurrect candidates, alter the Claim
  Ledger, run gates, approve delivery, or decide release readiness.
  These contracts describe report type metadata over the existing Claim Ledger,
  artifact registry, gates, event log, archive, source appendix, support
  records, frozen-artifact integrity, and human delivery approval spine. These
  product-layer surfaces do not run stages, create a second gate engine,
  block gates from section conformance or render-plan
  diagnostics, turn source plans/search summaries into evidence, create a
  semantic support assessor, judge industry compliance, verify internet rumors,
  bypass gates, approve delivery, provide tax or investment advice, or
  authorize publication.
- Python commands provide setup, source tooling, validation, audit support, and rendering.
- Hermes, Claude Code, Codex, OpenCode, experimental source-clone CodeBuddy,
  and operator fallback are treated as agent runtime surfaces. The CodeBuddy
  runtime handoff requires `.codebuddy/skills/briefloop/` and
  `.codebuddy/agents/briefloop-*.md` in the source checkout; it is not packaged
  runtime proof. The operator runtime is a host-agnostic compact workflow for
  environments without a dedicated runtime adapter. Historical `manual`
  manifests remain readable for diagnosis but cannot continue a run until an
  explicit reset records a canonical runtime.
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
