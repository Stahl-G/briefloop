# Support Matrix

BriefLoop is the only current project and product name. The former project
acronym is retired. Literal compatibility and history identifiers such as
`multi-agent-brief`, `/mabw`, `multi_agent_brief`, `mabw.*`, and `MABW-080`
remain only where supported commands, schemas, workspaces, or archived
experiments require them; they are not an implementation-lineage alias. This
matrix lists commands by their public `briefloop` name; the
`multi-agent-brief` compatibility entrypoint is noted once in the CLI rows.

Each capability has one of the following statuses:

| Status | Meaning |
|---|---|
| **Supported** | Actively tested, documented, and considered stable for the v0.11.0 product baseline. |
| **Experimental** | Functional but may change without notice. Not guaranteed for production. |
| **Archived Experimental** | Retained for explicit research, reference-run reproduction, and scorecard audit. Not a product onboarding or delivery surface. |
| **Interface Only** | Abstract interface exists; no concrete implementation shipped. |
| **CLI-only** | Installs and exposes deterministic CLI commands, but does not include source-clone runtime asset trees. |
| **Source-clone-only** | Requires repository files that are not shipped as Python package data. |
| **Deprecated** | Still present but scheduled for removal. Use the replacement. |
| **Not shipped** | Not included in this distribution surface. |

For control-surface capabilities, **Supported** means deterministic commands,
contracts, and regression tests are present. It does not imply output-quality
validation unless that is stated separately.

## Core Pipeline

| Capability | Status |
|---|---|
| Subagent workflow (default topology: Scout finds + screens; strict topology: independent Screener; Claim Ledger → Analyst → Delivery Editor → Auditor) | Supported |
| Fresh SQLite-only Codex control path (`briefloop.db`, strict DTOs, UoW receipts, Store-derived `CoreRunNextAction`) | Experimental |
| Store assessment continuation and normal successor guidance snapshot (`quality laj assessment-next`; `runtime successor-start`; introduced with schema v13 and carried into current schema 19) | Experimental in v0.15.3; fresh current-schema workspaces only. Assessments remain explicitly Human-authorized and result-selected; a separate Human successor request can atomically freeze compatible active-approved guidance for Analyst/Editor only. Older development SQLite workspaces are unsupported and must be recreated |
| Manifest-bound atomic runtime source-pack intake (1–256 ordered members, stable source IDs/URLs/incident time, one Invocation/UoW/Receipt, zero partial registration) | Experimental |
| Workspace-local Codex kit binding (config, Skill, reference, all role files; tamper/delete/extra/symlink fail closed) | Experimental |
| Codex `single_session` topology (shared context, distinct Receipt-backed invocations, stage-separated self-review) | Experimental |
| Legacy runtime handoff (`agent_handoff.md` + `agent_handoff.json`) | Deprecated; not an authority or new-run entrypoint |
| JSON/JSONL runtime control files (`runtime_manifest.json`, `workflow_state.json`, `artifact_registry.json`, `event_log.jsonl`) | Projection/legacy only; never accepted as runtime authority |
| Stage runtime/model provenance on completion transactions | Supported |
| Audience profile runtime surface (`audience_profile.md` + `audience_profile_snapshot.md`) | Supported |
| Legacy Improvement Ledger / Memory files (`improvement/ledger.jsonl`, `improvement/memory.md`, `improvement_memory_snapshot.md`) | Retired (LD2-3); these files have no code reader or writer. Experimental post-final Human disposition, edited guidance, approval/status, and successor snapshots are append-only SQLite records; no legacy file is resurrected or read |
| Orchestrator control switchboard (`orchestrator_control_switchboard.json`, optional `control_selections.json`) | Supported |
| Feedback control files (`feedback_issues.json`, `repair_plan.json`, conditional `delta_audit_report.json`) | Supported |
| Stage-scoped quality gate control files (`gates/auditor_quality_gate_report.json`, `gates/finalize_quality_gate_report.json`; legacy latest projection `quality_gate_report.json`) | Supported |
| Atomic Claim Graph (`atomic_claim_graph.json` schema, coverage/type validation, Analyst/Editor contract boundary, and reader-residue projection) | Experimental |
| Evidence Span Registry (`evidence_span_registry.json` schema, source-pack byte binding, archive projection, and Source Appendix trace view) | Experimental |
| Legacy Durable Source Evidence Pack materializer (`sources materialize-pack`) | Unsupported/retired; the parser returns `runtime_command_unsupported` with zero writes. The optional manifest contract remains readable for existing artifacts but has no writer through this command |
| Claim-Support Matrix (`claim_support_matrix.json` schema, cross-artifact validation, and gate/status projection from explicit support records) | Experimental |
| Semantic Assessment Report (`semantic_assessment_report.json` schema and reference validation) | Experimental schema/contract only. The producer, proposal projection, status visibility, and human adjudication writer were deleted with the legacy stack in LD2-3; no current runtime role creates the artifact. The remaining optional validation is non-blocking and has no support, Gate, delivery, or release authority |
| v0.11 product-facing workspace entries (`briefloop new industry-weekly`, `briefloop new management-monthly`, `briefloop new document-review`) mapped to canonical ReportPacks (`market_weekly`, `management_monthly`, `evidence_extract`) with local-first skeletons and control-spine defaults | Supported |
| ReportSpec / ReportPack baseline contracts for the v0.11 product baseline (`report_spec.yaml`, packaged `market_weekly`, `management_monthly`, and `evidence_extract`, `packs list/show`, and `validate-report-spec`) | Supported |
| Experimental `solar-stock-periodic` ReportPack and schema-19 source plan (`briefloop new solar-stock-periodic --search-backend tavily`, optional paired `--report-window-start/--report-window-end`, Codex-kit preparation, fixed equity/event/theme universe, 20 independent Tavily tasks, per-task Extract outcomes, and fresh-only acquisition bundle) plus the `market-data` CLI channel (strict `market_data_snapshot.v2`; profile-bound TOYO XLSX, JSON/CSV, or bounded no-redirect Yahoo fill; manual cells win while product-owned formula outputs are recomputed; pre-provider active-invocation guard; adjusted history, FX, valuations, events, gaps/conflicts; two comparison tables, seven deterministic PNG charts, JSON projection and hash-bound manifest) | Experimental; source-discovery/report-contract and structured market-data surface only; required-series gaps/conflicts can block; no invented price/valuation/causality; workbook display objects are not evidence; undated/out-of-window news sources are background and cannot satisfy current-week high-priority coverage |
| Wider Product OS extensions: ReportTemplate / PolicyProfile registry, Citation Profile Split metadata, Reader Template Conformance warning projection, template renderer MVP, `solar-periodic` / `solar_industry_periodic`, internal release-mode approval records, Quality Panel / Quality Summary / static HTML projection, Trajectory Regulation read-only projection, Materiality Selection diagnostic projection, and `extract` source/scope registration | Experimental |
| Public `briefloop packs bundle` command | Unsupported/retired on SQLite workspaces; the authority guard returns `runtime_command_unsupported` before bundle, file, or Store effects |
| Internal deterministic ReportBundle seam | Experimental/internal and independently safe-read/publication capability-gated; not a CLI, Gate, approval, package-ready, delivery, or publication authority |
| Provenance projection control file (`provenance_graph.json`) | Supported |
| Finalize delivery bundle (`output/delivery/brief.md` + configured DOCX) | Supported |
| Source appendix audit/control copy (`source_appendix.md`) | Supported |
| `briefloop` CLI | Supported |
| `multi-agent-brief` CLI | Supported compatibility entrypoint with identical behavior; retained for existing scripts and installs |
| `briefloop run --workspace <path> --runtime codex` plus `runtime next/invocation-start/invocation-validate/invocation-accept/invocation-fail/apply/diagnose`; strict JSON proposal contract preflight | Experimental; fresh SQLite-only, no JSON migration or fallback |
| `briefloop runtime successor-start --workspace <path> --direction-json <json> --run-id <id> [--include-approved-guidance]` | Experimental in v0.15.3; explicit Human-started normal same-workspace successor. With opt-in, one atomic Core transaction freezes the complete compatible active-approved set within 16-item / 65,536-byte bounds for Analyst and Editor only. Exact replay returns the original Receipt/snapshot; conflicts and limit failures write nothing. No provider, role, evidence, Gate, finalize, delivery, repair, or automatic-learning authority |
| `briefloop runtime continue --workspace <path>` with Store-frozen Tavily discovery authorization | Experimental; credential remains in private workspace `.env` until Human rotation/removal; each separately Human-confirmed, Store-recorded attempt executes the frozen atomic task matrix; Solar Stock Periodic freezes 20 independent tasks (11 equities, 5 event-only entities, 4 themes), each requests up to 20 advanced Search results, all eligible unique URLs are Batch Extracted in groups of 20, and an under-covered task may receive one deterministic 30-day backfill; the only product safety envelope is 800 unique URLs; exact replay never redials and failures never auto-retry; the canonical bundle freezes exact safe exchanges, per-task status, and per-URL outcomes; Search snippets are never claims-eligible and only successful non-empty Extract content enters Intake; partial success commits successful URLs only, while all-failed Extract creates no source or execution authorization; HumanSourcePack never counts as Tavily success; focused synthetic transport is tested; live usefulness, reliability, cost, coverage, success rate, and acquisition-to-`finalized_local` performance NOT MEASURED; POSIX/macOS capability is checked before any provider call; current Windows returns `checkout_publication_unsupported` with zero provider/network access, no source promotion, and no later execution/finalization/approval/package/delivery authority |
| Legacy runtime names and `operator --recipe fast-rerun` | Unsupported for the SQLite-only active path |
| `briefloop status --workspace <path>` | Supported read-only Store projection for SQLite workspaces; JSON-only workspace unsupported |
| Legacy `briefloop deliver` commands | Unsupported on SQLite; approval and delivery use typed Store actions through `runtime apply` |
| Legacy `briefloop sources decide` command | Unsupported on SQLite; source discovery runs through the runtime-host route |
| Legacy `briefloop deliver --target feishu\|gmail` forms | Unsupported on SQLite; no user-reachable delivery entry remains — the connector execution layer is retained only as a tested internal seam |
| Legacy `state`, `gates`, `repair`, `finalize`, `delivery`, `controls`, and feedback mutators | Unsupported on SQLite; public handlers removed in LEGACY-DELETE with typed rejections retained; the legacy JSON runtime-state stack is declared internal debt tracked as LEGACY-DELETE-2 |
| Guidance Manifestation diagnostic projection | Retired in LEGACY-DELETE-2 D1; `status` and Quality Panel payloads no longer carry the `guidance_manifestation` fold-in key, and the `[status] guidance_manifestation` formatter line is removed |
| Support-Calibrated Wording diagnostic projection | Retired in LEGACY-DELETE-2 D1; `status` and Quality Panel payloads no longer carry the `support_wording` fold-in key, and the `[status] support_wording` formatter line is removed |
| `briefloop state import-fact-layer --runtime <canonical-runtime>` | Unsupported on SQLite; retired public CLI |
| `briefloop controls build-switchboard/show/select/validate` | Unsupported on SQLite; retired public CLI |
| `briefloop runtime install --workspace <path> --runtime codex` | Experimental; packaged/non-editable Codex kit |
| `briefloop feedback ingest/plan/resolve/show/validate` | Unsupported on SQLite; retired public CLI |
| `briefloop gates check/show/validate` | Unsupported on SQLite; retired public CLI |
| `briefloop provenance build/show/validate` | Unsupported on SQLite; retired public CLI |
| `briefloop improve propose/list/show/approve/reject/revert/stats/validate/rebuild` | Unsupported on SQLite; retired public CLI |
| `briefloop eval-cases list/validate/run` | Retired (LD2-3); the legacy-runtime evaluation driver was deleted with the runtime-state stack. Packaged fixture data is preserved for the EF-1/EF-2 Store-native evaluation rebuild |
| `briefloop experiments 080 validate-case` | Retired (LD2-3); the 080 tooling was deleted with the legacy runtime-state stack. Scorecard reproduction is satisfied by git history and run archives |
| MABW-080 archived experiment test suite | Retired (LD2-3); suite and on-demand CI job deleted with the 080 tooling. Scorecard reproduction is satisfied by git history and run archives |
| `briefloop experiments 080 register-run` | Retired (LD2-3); the 080 tooling was deleted with the legacy runtime-state stack. Scorecard reproduction is satisfied by git history and run archives |
| `briefloop experiments 080 score-run` | Retired (LD2-3); the 080 tooling was deleted with the legacy runtime-state stack. Scorecard reproduction is satisfied by git history and run archives |
| `briefloop experiments 080 import-assessment` | Retired (LD2-3); the 080 tooling was deleted with the legacy runtime-state stack. Scorecard reproduction is satisfied by git history and run archives |
| `briefloop experiments 080 summarize` | Retired (LD2-3); the 080 tooling was deleted with the legacy runtime-state stack. Scorecard reproduction is satisfied by git history and run archives |
| `briefloop experiments 080 scaffold-condition` | Retired (LD2-3); the 080 tooling was deleted with the legacy runtime-state stack. Scorecard reproduction is satisfied by git history and run archives |
| `briefloop experiments laj shadow-run` | Experimental; public/synthetic offline-shadow execution and exact replay only; advisory output has zero workflow, Gate, finalize, delivery, repair, approval, or next-action authority |
| `briefloop experiments laj present` | Experimental; deterministic standalone JSON/Markdown/HTML projection of one verified shadow archive; invalid, missing, stale, abstained, or unavailable results display no advice and have zero runtime authority |
| `briefloop experiments laj study-preflight/budgeted-shadow-run/study-compare` | Experimental; strict target eligibility, complete-trial call/token budget, and offline exact dimension/span-overlap comparison only; advisory with zero runtime authority; utility and efficacy NOT MEASURED |
| `briefloop init --from-onboarding` | Supported |
| `briefloop init --web [--port <n>]` | Experimental; one-shot loopback wizard that creates the workspace through the same ControlStore bootstrap path and returns the real TransactionReceipt |
| `briefloop quality html --workspace <path> [--open] [--laj-view <laj.json>]` | Experimental; best-effort, capability-gated local static read-only five-tab surface: verified Store-bound `finalized_local` Brief, deterministic Quality, Store-bound structured Market Data when present, optional advisory LAJ (NOT MEASURED), and current Store-native Human guidance state; unsupported platforms return no projection path; static export has no write, approval, package, delivery, publication, persistent-server, or learning affordance |
| `briefloop quality laj policy-set/assess/status/retry/assessment-next/assessment-run/assessment-list/review-open/disposition/draft/approve/deactivate/revert/supersede/review-status` | Experimental v0.15.3 Store-qualified post-final assessment series and secured local Human review. Multiple independently Human-authorized generations append policy/request/result/abandonment Receipts; `assessment-next` is a read-only, self-describing request projection. Explicit result selection is required; generation 2+ never auto-runs or redials. Approval alone has no later-run effect; reuse requires the separate explicit successor command. Historical result status/deactivate/revert/supersede remains exact-result-bound, while browser `review-open` stays current-head-only. LAJ remains advisory and utility NOT MEASURED; no Gate/finalize/delivery/Core authority |
| `briefloop onboard` | Supported |
| `briefloop doctor` | Supported |
| `briefloop extract --workspace <path> --scope <text> --source <file>` | Unsupported on SQLite; retired public CLI |
| `briefloop sources add-file/add-rss/add-web-search` | Unsupported on SQLite; retired public CLI forms; source intake runs through the runtime-host route |
| `briefloop approval init/record` and `briefloop release check` | Unsupported on SQLite; retired public CLI; release checks run through deterministic release tooling |
| `briefloop inputs extract` | Unsupported on SQLite; retired public CLI |
| `briefloop inputs classify` | Unsupported on SQLite; retired public CLI |
| `briefloop finalize` | Unsupported on SQLite; finalize runs through the Codex runtime host as typed Store actions |
| `briefloop audit` | Supported |

Feedback commands structure issues and repair plans for the Orchestrator. They do not automatically edit brief artifacts or execute repair.

Quality gate commands write deterministic gate reports and can block unsafe current-stage continue/finalize decisions. They include material-fact, freshness, target-relevance, coverage/omission-continuity, and editor-new-fact checks. The coverage/omission check detects high-priority selected screened candidates that disappear before Claim Ledger or cited-brief references without an explicit limitation; it is not full-world recall. Quality gates do not fetch sources, rewrite briefs, execute repair, or create feedback issues automatically.

Evaluation cases are developer/CI regression checks for control-surface behavior. They do not create workflow artifacts, run subagents, fetch sources, score prose, call an LLM judge, or execute repair.

Provenance commands write a deterministic workspace-local audit/debug graph from existing control files. They do not fetch sources, execute workflow stages, replay the runtime, execute repair, verify semantic truth, or block `finalize` by default.

Audience profile files are workspace-local runtime context. The active run uses the frozen per-run snapshot exposed through handoff; these files are not source evidence, artifact contracts, quality gates, provenance graph nodes, or stage blockers.

Legacy Improvement Ledger files are retired as of LD2-3. The code that projected approved guidance into `improvement/memory.md` and froze `output/intermediate/improvement_memory_snapshot.md` during `run`/`start`/`handoff` was deleted with the legacy runtime-state stack, so these files remain inert and have no reader or writer. Experimental post-final Human review now records finding dispositions, Human-edited guidance drafts, and separate approval/status revisions in SQLite. Those records are not evidence, source material, Claim Ledger input, repair instructions, semantic proof, or an output-quality guarantee; approval alone never creates an implicit later-run effect.
In v0.15.3, a Human can separately start a normal
same-workspace successor with a new strict `RunDirection` and explicit
`--include-approved-guidance`. The Core transaction freezes only the complete
compatible active-approved set into immutable Store records for Analyst/Editor;
without opt-in it freezes an empty snapshot. Later live status changes do not
rewrite the successor snapshot. Current direction and evidence take precedence,
and the snapshot has no source, Claim Ledger, Gate, repair, finalize, delivery,
or Core authority. Utility is NOT MEASURED and the mechanism is not automatic
learning.

Control switchboard files are runtime control context. Python surfaces deterministic recommendations and records Orchestrator enable/defer/reject selections; selection is not execution and does not run gates, feedback planning, provenance projection, source discovery, repair, or subagents.

Role topology controls runtime role assignment, not the accountable artifact set.
The default topology lets Scout write both `candidate_claims.json` and
`screened_candidates.json`; strict topology keeps Screener independent. Both
paths still require the Claim Ledger, auditable draft, stage-scoped gate
reports, audit report, event log, archive, and human-triggered delivery. This is
not a speed-improvement claim.

Stage runtime/model provenance is recorded when completion transactions are
called with explicit runtime/model values. It is audit metadata in workflow
state and event log records only; it does not prove output quality, support
strength, or model performance.

Claim Ledger freeze is a deterministic control transaction. Claim Ledger agents
write `claim_drafts.json` without claim IDs; Python assigns deterministic
`CL-####` IDs, writes the canonical `claim_ledger.json`, records freeze
metadata, and requires the frozen ledger before Claim Ledger stage completion.
This controls identity and freezing; it is not semantic proof or automatic claim
deduplication.

Atomic Claim Graph support is experimental. When present,
`atomic_claim_graph.json` is validated as an optional structural decomposition
of frozen Claim Ledger claims; Python checks schema, whole-ledger coverage,
deterministic type consistency, and reader-facing atom/process residue. The
graph is not source evidence, not a reader citation surface, and not support
sufficiency.

Evidence Span Registry support is experimental since the v0.9.3 release.
When present, `evidence_span_registry.json` is validated as an optional span
trace artifact; Python checks schema, source-pack byte binding, raw-excerpt
hashes, optional offsets, archive hash projection, and Source Appendix trace
surfaces. It is not source-support judgment, not a Claim-Support Matrix, not a
support-sufficiency gate, and not a reader citation contract.

Claim-Support Matrix support is experimental since the v0.9.3 release.
When present, `claim_support_matrix.json` is validated as an optional
atom-to-evidence support-record artifact; Python checks schema vocabulary,
Claim Ledger / Atomic Claim Graph / Evidence Span Registry references,
high-materiality atom row coverage, and duplicate atom-span relation rows. A
present valid matrix can project explicit rows into read-only status summaries
and quality-gate findings for unsupported, contradicted, weak, or inferential
support records. Missing matrices remain non-blocking, and invalid matrices are
not consumed for support projection findings. This does not assess semantic
support, create automatic support records, decide release eligibility, or prove
truth.

Semantic Assessment Report support is experimental and, as of LD2-3, reduced to
the schema and its reference validation. `semantic_assessment_report.json` is
still validated as an optional proposal artifact — schema, assessor/row
provenance, Claim Ledger / Atomic Claim Graph / Evidence Span Registry
references, and high-materiality `llm_only` adjudication flags — but the
projection layer that turned a valid report into status counts, proposal-only
Claim-Support Matrix delta candidates, and the Quality Panel `semantic_support`
section was deleted with the legacy runtime-state stack. That section now
reports a constant `not_available`; on SQLite workspaces it already did, because
the Store projection never carried this key. Missing reports remain
non-blocking. The `semantic-support bind` / `adjudicate` commands and the
`semantic_support_acceptance_ledger.json` records they wrote are retired with
the stack. A Store-native producer would have to land before any of this
projects again. None of this did, or was ever able to, create support truth,
write the Claim-Support Matrix, route repair, gate delivery, decide release
eligibility, or prove truth.

ReportSpec / ReportPack baseline support is stable for the v0.11.0 product
baseline target when used through the product-facing entries
`industry-weekly`, `management-monthly`, and `document-review`. These entries
create local-first workspace skeletons, write canonical `report_spec.yaml`
values such as `market_weekly`, `management_monthly`, and `evidence_extract`,
and preserve the Claim Ledger, artifact registry, quality gates, event log,
archive, source appendix, support records, human delivery approval, and frozen
artifact integrity control spine. This baseline creates workspace structure and
contracts only; it does not run stages, fetch sources, approve delivery, prove
truth, or authorize publication.

The wider Product OS extensions remain experimental. Product-layer report
contracts can describe report type metadata, stable section order,
deterministic policy defaults such as `manufacturing_default`,
`finance_default`, and `internet_default`, dogfood defaults such as
`solar_manufacturing_default`, specialized extraction defaults such as
`evidence_extract_default`, explicit evidence-extract source/scope
registration, and a delivery/audit bundle manifest projection over existing
finalized artifacts. The CLI can list packaged packs/templates, validate a
`report_spec.yaml` including its resolved policy profile, show the resolved
profile in read-only status and generated handoff artifacts, tighten existing
deterministic quality-gate strictness and reader-final forbidden-phrase checks
through a limited adapter, show the resolved ReportTemplate section order in
read-only status and generated handoff artifacts, report read-only
section-conformance diagnostics for existing audited/final reader Markdown in
status and generated handoff artifacts, project read-only render-plan
diagnostics that name the future render source artifact, section heading
mapping, unresolved sections, and planned delivery targets, or apply the
resolved ReportTemplate section order during finalize for already-present
reader Markdown sections.
For `evidence_extract` workspaces, `extract` can copy explicit local source
files into `input/sources/evidence_extract/`, write `extraction_scope.yaml`,
update `sources.yaml` manual source entries, write
`output/intermediate/evidence_extract_source_lock.json` plus an audit copy that
binds registered source paths to file size and SHA-256, write a deterministic
page-inventory seed at `output/intermediate/evidence_extract_page_inventory.json`
for UTF-8 text sources, and write deterministic text-span seed entries to
`output/intermediate/evidence_span_registry.json` for UTF-8 text sources. This
remains bounded source/scope/source-lock/page-seed/span registration: it does
not parse PDFs or binary documents, render pages for visual inspection, extract
tables or figures, judge semantic support, generate Claim-Support Matrix rows,
draw legal or disclosure conclusions, run stages, or authorize delivery.
The legacy SourceHub Lite `sources add-file/add-rss/add-web-search` command
names are retired parser surfaces. They return
`runtime_command_unsupported` without source, workspace, Store, provider, or
network effects. Current source intake and public-web acquisition use
Store-derived runtime actions.
Internal release-mode approval commands can initialize
`human_approval_ledger.json`, append human approval decisions, and write
`release_readiness_report.json` for internal review modes. These reports may
show missing approvals or readiness for an internal review mode only. They do
not publish externally, authorize public release, replace legal/compliance/IR
owners, or weaken existing gates and human delivery approval requirements.
Quality Panel projection can summarize existing control integrity, source
evidence, gate, claim/support, and delivery hygiene surfaces into optional
`output/intermediate/quality_panel.json`, and Quality Summary can render a
compact human-readable `output/intermediate/quality_summary.md` from a valid
panel. Static Quality Panel HTML can render
`output/intermediate/quality_panel.html` from the same valid panel with inline
CSS and no external assets or frontend runtime. `quality summarize` can write
the legacy audit artifacts together. The current SQLite manual display surface
is `quality html --workspace <path> --laj-view <laj.json>`; with an explicit
view it can render
a strict, current-report-bound LAJ result as an experimental advisory-only
section; it does not call the evaluator, and absent, invalid, stale, failed, or
abstained LAJ does not change the panel overall status, blockers, recommended
authoritative actions, gates, or delivery truth. Bundle projection can include them in audit
bundles while keeping them out of reader-facing delivery bundles. These are
product-quality projections only: they do not run gates, replace gate reports,
create a quality score, decide release eligibility, approve delivery, prove
semantic truth, or execute repair.
Trajectory Regulation projection reads existing `workflow_state.json` and
`event_log.jsonl` records to surface repeated retry, repair-cycle, and blocker
patterns in status and Quality Panel recommended actions. It is read-only
operator guidance only: it does not write workflow state, start repair, execute
repair, run gates, block stages, approve delivery, or decide release readiness.
Guidance Manifestation projection is retired in LEGACY-DELETE-2 D1. The
optional `output/intermediate/guidance_manifestation_report.json` fold-in is
no longer read, and `status`/Quality Panel payloads no longer carry the
`guidance_manifestation` key. The legacy Improvement Memory projection and
snapshot are also retired; the Store-native successor snapshot is a distinct
development-main control record and does not revive those files.
Materiality Selection projection reads valid `screened_candidates.json`, the
resolved PolicyProfile materiality terms, and workspace focus terms to surface
excluded or deprioritized candidates that match explicit materiality/focus
terms after capacity or scope screening. It is deterministic keyword
diagnostics only: Python does not infer semantic importance, mutate screening
results, resurrect candidates, alter the Claim Ledger, run gates, approve
delivery, or decide release readiness.
Support-Calibrated Wording projection is retired in LEGACY-DELETE-2 D1. The
warning-only `support_wording` diagnostics are no longer computed, and
`status`/Quality Panel payloads no longer carry the `support_wording` key.
Workspace creation may use an
explicit `--policy-profile` or deterministic `--industry` hint, but the result
is written into `report_spec.yaml` with its resolution source and is not
silently re-inferred at gate time. These surfaces do not run
subagents, create a second gate engine,
turn section-conformance or render-plan diagnostics into gates,
deliver reports, authorize publication, judge industry compliance, verify
internet rumors, provide tax or investment advice, or provide a
lite/force-deliver path.

Source appendices are reader-facing delivery artifacts generated during finalize from cited Claim Ledger sources. They can display safe source identity and taxonomy labels, while the separate source appendix trace audit copy can include internal claim/source/span IDs, source paths, source byte hashes, and metadata completeness warnings for review. They are not source evidence, semantic proof, runtime state, provenance graphs, or workflow gates.

The legacy `sources materialize-pack` name is retained only so callers receive
a deterministic `runtime_command_unsupported` response. It performs no source
or Store write. The optional
`output/intermediate/source_evidence_pack_manifest.json` schema and reader
contract remain available for existing artifacts, but this retired command is
not their producer. A source plan, source candidate, search summary, or model
summary remains discovery material rather than evidence.

Fast-rerun fact-layer import and the MABW-080 command suite were retired with
LD2-3. `state import-fact-layer`, `run --recipe fast-rerun`, and
`briefloop experiments 080 ...` are not current SQLite product or experiment
paths. Git history, archived experiment material, and frozen reference-run
records remain available for historical reproduction and audit; they do not
restore a writer, runtime action, or compatibility promise.

The archived [MABW-080 experiment guide](experiments-080.md) and
[BriefLoop-090 experiment closeout](reference-runs/briefloop-090-experiment-closeout.md)
record the former command semantics and one public-safe synthetic
`auditable_brief` pilot. That historical observation is not current capability
evidence and supports no broader quality, delivery-readiness, factual
correctness, or generalization claim.

## Runtimes

| Runtime | Status |
|---|---|
| Codex (`runtime install`, SQLite ControlStore, `single_session`) | Experimental active path |
| Hermes / Claude Code / OpenCode / CodeBuddy / Operator | Deleted with the legacy JSON runtime |

Codex is the only active fresh SQLite runtime in this cutover. It remains
Experimental until a real Codex control trace validates the end-to-end role,
receipt, Gate, approval and delivery correspondence. The former runtime
adapters are deleted and provide no JSON compatibility path and no fallback.

Runtime source assets under `.agents/skills/` are hand-maintained source-clone
assets. Package-only installs ship the CLI, packaged contracts, and packaged
eval fixtures, but they do not ship source runtime directories as Python package
data. Use
`briefloop runtime install --workspace <workspace> --runtime codex`
from any install to copy the packaged Codex workspace-local runtime kit.

## Source Providers

| Provider | Status |
|---|---|
| Manual (local md/txt/json files) | Supported |
| Web search — Tavily | Experimental narrow runtime-first Search + batch Extract route; persistent local credential is separated from per-attempt Human/Store phase-bounded authority; Search snippets are never claims-eligible; no automatic retry or hidden fallback; synthetic transport tested; live usefulness, reliability, cost, coverage, success rate, and acquisition-to-`finalized_local` performance NOT MEASURED |
| Web search — Exa | Supported |
| Web search — Brave | Supported |
| Web search — Firecrawl | Supported |
| Web search — Serper | Supported |
| RSS | Supported |
| SEC Filing resolver | Supported |
| Cached package (Hermes daily cache) | Supported |
| MinerU document parsing | Experimental |
| Local signal discovery | Experimental |
| OpenCLI provider | CLI-only |
| Feishu provider | Experimental |

## Analysis Modules

| Module | Status |
|---|---|
| Market Competitor | Supported |
| Policy & Regulatory | Supported |

## Quality Gates

| Gate | Status |
|---|---|
| Deterministic Audit | Supported |
| Editorial Governance | Supported |
| Final Quality (Final Clean) | Supported |
| Limitation Hygiene | Supported |
| Draft Audit Harness | Supported |
| Rendered Output Harness | Supported |
| Material-Fact / Freshness / Target-Relevance / Editor-New-Fact Gates | Supported |

## Evaluation & Regression Tooling

| Tool | Status |
|---|---|
| Packaged public-safe evaluation cases (`eval-cases`) | Retired (LD2-3); fixture data preserved for EF-1/EF-2 |
| MABW-080 experiment case validator (`experiments 080 validate-case`) | Retired (LD2-3) |
| MABW-080 run registration (`experiments 080 register-run`) | Retired (LD2-3) |
| MABW-080 scorecard builder (`experiments 080 score-run`) | Retired (LD2-3) |
| MABW-080 assessment import (`experiments 080 import-assessment`) | Retired (LD2-3) |
| MABW-080 case summary builder (`experiments 080 summarize`) | Retired (LD2-3) |
| MABW-080 condition scaffold (`experiments 080 scaffold-condition`) | Retired (LD2-3) |
| Workspace provenance projection (`provenance`) | Retired (LD2-3); the legacy workspace builder was deleted, the provenance graph contract (`provenance/model.py`, validator) remains |
| Runtime asset parity check (`scripts/check_runtime_asset_parity.py`) | Source-clone-only |
| Private/commercial benchmark cases | Not shipped |
| LLM-as-judge prose scoring | Not shipped |

## Delivery & Output

| Format / Channel | Status |
|---|---|
| Markdown (`output/delivery/brief.md`) | Supported |
| DOCX (`output/delivery/<named>.docx`) | Supported |
| Source appendix audit/control copy (`source_appendix.md`) | Supported |
| Named output copies | Supported |
| PDF | Experimental |
| Feishu delivery | Experimental |
| Gmail draft and explicit send delivery through optional `gws` CLI | Experimental |
| Slack delivery | Not shipped |
| Direct email send | Experimental; Gmail only through optional `gws` CLI and explicit `--channel send` |

## Analysis Tooling

| Tool | Status |
|---|---|
| `analysis-blocks` CLI | Supported |
| `limitation-hygiene` CLI | Supported |
| Audience profiles (`management` / `research` / `IR` / `legal-compliance`) | Supported |

## Installation & Distribution

| Method | Status |
|---|---|
| Source clone + `bash scripts/setup.sh` + `pip install -e ".[dev]"` | Supported |
| `pip install briefloop` / `pipx install briefloop` (PyPI) | Experimental; only after a real package-index artifact is published and smoke-tested |
| Homebrew formula source (`Formula/`) | Experimental; not a primary release path |
| curl installer (`install.sh`) | Experimental CLI-only installer asset |
| PowerShell installer (`install.ps1`) | Experimental CLI-only installer asset |
| Hermes plugin (`integrations/hermes-plugin/`) | Deleted with the legacy JSON runtime |
| WorkBuddy Skill source bundle (`.agents/skills/briefloop-workbuddy/`) and `workbuddy pack-skill` | Deleted with the legacy JSON runtime |
| CodeBuddy project Skill adapter (`.codebuddy/skills/briefloop/`) | Deleted with the legacy JSON runtime |
| CodeBuddy project role agents (`.codebuddy/agents/briefloop-*.md`) | Deleted with the legacy JSON runtime |

| Runtime asset | Source clone | Wheel / sdist / PyPI package |
|---|---|---|
| Packaged contracts (`configs/*.yaml`) | Supported | Supported |
| Packaged eval fixtures (`evaluation_cases/fixtures/`) | Supported (data only; runner retired in LD2-3) | Supported (data only; runner retired in LD2-3) |
| `.agents/skills/**` | Supported | Source-clone-only |
| Packaged Codex runtime kit (`src/multi_agent_brief/runtime_kits/codex/`) | Supported | Packaged and installable with `runtime install --runtime codex`; the installed workspace inventory is hash-bound and reverified before runtime actions |
| `scripts/install.sh`, `scripts/install.ps1`, `Formula/` | Supported | Source-clone-only |

## Legacy / Deprecated

| Item | Status |
|---|---|
| `briefloop prepare` | Deprecated (use `run` instead) |
| Python `BriefPipeline` | Removed |
| `briefloop start` | Deprecated (alias for `run`) |
| `briefloop handoff` | Deprecated (use `run`) |
