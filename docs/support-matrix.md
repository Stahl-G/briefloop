# Support Matrix

BriefLoop is the public project name during the v0.9 compatibility period.
MABW remains the implementation lineage and compatibility surface. This matrix
continues to list supported CLI/runtime surfaces by their current compatible
names such as `multi-agent-brief`, `briefloop`, `/briefloop`, `/mabw`, and
MABW-080.

Each capability has one of the following statuses:

| Status | Meaning |
|---|---|
| **Supported** | Actively tested, documented, and considered stable for the v0.11.0 product baseline. |
| **Experimental** | Functional but may change without notice. Not guaranteed for production. |
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
| Role topology selector (`policy.role_topology`: `default`, `strict`, `human_assisted`) with topology-satisfied stages recorded in workflow state and event log | Supported |
| Runtime handoff (`agent_handoff.md` + `agent_handoff.json`) | Supported |
| Runtime state control files (`runtime_manifest.json`, `workflow_state.json`, `artifact_registry.json`, `event_log.jsonl`) | Supported |
| Stage runtime/model provenance on completion transactions | Supported |
| Audience profile runtime surface (`audience_profile.md` + `audience_profile_snapshot.md`) | Supported |
| Improvement Ledger / Memory (`improvement/ledger.jsonl`, `improvement/memory.md`, `improvement_memory_snapshot.md`) | Supported |
| Orchestrator control switchboard (`orchestrator_control_switchboard.json`, optional `control_selections.json`) | Supported |
| Feedback control files (`feedback_issues.json`, `repair_plan.json`, conditional `delta_audit_report.json`) | Supported |
| Stage-scoped quality gate control files (`gates/auditor_quality_gate_report.json`, `gates/finalize_quality_gate_report.json`; legacy latest projection `quality_gate_report.json`) | Supported |
| Atomic Claim Graph (`atomic_claim_graph.json` schema, coverage/type validation, Analyst/Editor contract boundary, and reader-residue projection) | Experimental |
| Evidence Span Registry (`evidence_span_registry.json` schema, source-pack byte binding, archive projection, and Source Appendix trace view) | Experimental |
| Durable Source Evidence Pack materialization (`sources materialize-pack`, `input/sources/*.json`, optional `source_evidence_pack_manifest.json` hash validation, and source taxonomy normalization) | Experimental |
| Claim-Support Matrix (`claim_support_matrix.json` schema, cross-artifact validation, and gate/status projection from explicit support records) | Experimental |
| Semantic Assessment Report (`semantic_assessment_report.json` schema, reference validation, proposal projection, status visibility, and optional human adjudication records in `semantic_support_acceptance_ledger.json`) | Experimental |
| v0.11 product-facing workspace entries (`briefloop new industry-weekly`, `briefloop new management-monthly`, `briefloop new document-review`) mapped to canonical ReportPacks (`market_weekly`, `management_monthly`, `evidence_extract`) with local-first skeletons and control-spine defaults | Supported |
| ReportSpec / ReportPack baseline contracts for the v0.11 product baseline (`report_spec.yaml`, packaged `market_weekly`, `management_monthly`, and `evidence_extract`, `packs list/show`, and `validate-report-spec`) | Supported |
| Wider Product OS extensions: ReportTemplate / PolicyProfile registry, Citation Profile Split metadata, Reader Template Conformance warning projection, template renderer MVP, `solar-periodic` / `solar_industry_periodic`, SourceHub Lite setup, internal release-mode approval records, Quality Panel / Quality Summary / static HTML projection, Trajectory Regulation read-only projection, Guidance Manifestation diagnostic projection, Materiality Selection diagnostic projection, `extract` source/scope registration, and `packs bundle` delivery/audit manifest projection | Experimental |
| Provenance projection control file (`provenance_graph.json`) | Supported |
| Finalize delivery bundle (`output/delivery/brief.md` + configured DOCX) | Supported |
| Source appendix audit/control copy (`source_appendix.md`) | Supported |
| `multi-agent-brief` CLI | Supported |
| `briefloop` shell CLI alias | Supported |
| `multi-agent-brief run --workspace <path>` | Supported |
| `multi-agent-brief run --workspace <path> --recipe fast-rerun` | Experimental |
| `multi-agent-brief status --workspace <path>` | Supported |
| `multi-agent-brief deliver --workspace <path> --target local` | Supported |
| `multi-agent-brief deliver --workspace <path> --target feishu` | Experimental |
| `multi-agent-brief deliver --workspace <path> --target gmail --channel draft\|send` | Experimental |
| `multi-agent-brief state init/check/show/decide/freeze-claim-ledger/stage-complete/finalize-complete` | Supported |
| `multi-agent-brief state import-fact-layer` | Experimental |
| `multi-agent-brief controls build-switchboard/show/select/validate` | Supported |
| `multi-agent-brief runtime install --workspace <path> --runtime opencode\|claude\|codex\|all` | Source-clone-only |
| `multi-agent-brief feedback ingest/plan/resolve/show/validate` | Supported |
| `multi-agent-brief gates check/show/validate` | Supported |
| `multi-agent-brief provenance build/show/validate` | Supported |
| `multi-agent-brief improve propose/list/show/approve/reject/revert/stats/validate/rebuild` | Supported |
| `multi-agent-brief eval-cases list/validate/run` | Supported |
| `multi-agent-brief experiments 080 validate-case` | Experimental |
| `multi-agent-brief experiments 080 register-run` | Experimental |
| `multi-agent-brief experiments 080 score-run` | Experimental |
| `multi-agent-brief experiments 080 import-assessment` | Experimental |
| `multi-agent-brief experiments 080 summarize` | Experimental |
| `multi-agent-brief experiments 080 scaffold-condition` | Experimental |
| `multi-agent-brief init --from-onboarding` | Supported |
| `multi-agent-brief onboard` | Supported |
| `multi-agent-brief doctor` | Supported |
| `multi-agent-brief extract --workspace <path> --scope <text> --source <file>` | Experimental |
| `multi-agent-brief sources add-file/add-rss/add-web-search` | Experimental |
| `multi-agent-brief approval init/record` and `multi-agent-brief release check` | Experimental |
| `multi-agent-brief inputs extract` | Experimental |
| `multi-agent-brief inputs classify` | Supported |
| `multi-agent-brief finalize` | Supported |
| `multi-agent-brief audit` | Supported |

Feedback commands structure issues and repair plans for the Orchestrator. They do not automatically edit brief artifacts or execute repair.

Quality gate commands write deterministic gate reports and can block unsafe current-stage continue/finalize decisions. They include material-fact, freshness, target-relevance, coverage/omission-continuity, and editor-new-fact checks. The coverage/omission check detects high-priority selected screened candidates that disappear before Claim Ledger or cited-brief references without an explicit limitation; it is not full-world recall. Quality gates do not fetch sources, rewrite briefs, execute repair, or create feedback issues automatically.

Evaluation cases are developer/CI regression checks for control-surface behavior. They do not create workflow artifacts, run subagents, fetch sources, score prose, call an LLM judge, or execute repair.

Provenance commands write a deterministic workspace-local audit/debug graph from existing control files. They do not fetch sources, execute workflow stages, replay the runtime, execute repair, verify semantic truth, or block `finalize` by default.

Audience profile files are workspace-local runtime context. The active run uses the frozen per-run snapshot exposed through handoff; these files are not source evidence, artifact contracts, quality gates, provenance graph nodes, or stage blockers.

Improvement Ledger files are human-governed workspace memory controls. Approved materializable guidance is projected into `improvement/memory.md` and frozen into `output/intermediate/improvement_memory_snapshot.md` during `run`/`start`/`handoff`. The snapshot is taste/audience guidance only; it is not evidence, source material, Claim Ledger input, repair instruction, semantic proof, or an output-quality guarantee.

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

Semantic Assessment Report support is experimental in the v0.9.4 release.
When present, `semantic_assessment_report.json` is validated as an optional
proposal artifact; Python checks schema, assessor/row provenance,
Claim Ledger / Atomic Claim Graph / Evidence Span Registry references, and
high-materiality `llm_only` adjudication flags. Present valid reports can
project proposal-only Claim-Support Matrix delta candidates and read-only status
counts. Missing reports remain non-blocking, and invalid reports are not
projected. Humans may record accept/reject adjudication decisions for proposal
rows through `semantic-support adjudicate`, which writes
`semantic_support_acceptance_ledger.json` and an event-log record. These records
do not by themselves create support truth, write the Claim-Support Matrix, route
repair, gate delivery, decide release eligibility, or prove truth.

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
mapping, unresolved sections, and planned delivery targets, apply the resolved
ReportTemplate section order during finalize for already-present reader
Markdown sections, or write a bundle manifest with `packs bundle`.
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
SourceHub Lite commands can copy explicit local text files into
`input/sources/sourcehub/`, register RSS feeds, and register runtime web-search
handoff tasks in `sources.yaml`. This is source setup only: local files remain
workspace-local evidence inputs, RSS registration does not fetch feeds, and
web-search handoff uses `runtime_tool` mode without executing Python web
search. SourceHub Lite does not turn source candidates or search summaries into
evidence, generate Evidence Span Registry entries, run stages, bypass gates, or
authorize delivery.
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
these artifacts together, and bundle projection can include them in audit
bundles while keeping them out of reader-facing delivery bundles. These are
product-quality projections only: they do not run gates, replace gate reports,
create a quality score, decide release eligibility, approve delivery, prove
semantic truth, or execute repair.
Trajectory Regulation projection reads existing `workflow_state.json` and
`event_log.jsonl` records to surface repeated retry, repair-cycle, and blocker
patterns in status and Quality Panel recommended actions. It is read-only
operator guidance only: it does not write workflow state, start repair, execute
repair, run gates, block stages, approve delivery, or decide release readiness.
Guidance Manifestation projection reads optional
`output/intermediate/guidance_manifestation_report.json` labels for approved
guidance entries already materialized into the run. It can surface
`explicitly_reflected`, `partially_reflected`, `contradicted`, and
`not_observable` counts in status and Quality Panel. These labels are
human/imported diagnostic assessments; Python only validates and counts them.
This does not mutate Improvement Memory, approve guidance, create a quality
score, run gates, approve delivery, prove output improvement, or decide release
readiness.
Materiality Selection projection reads valid `screened_candidates.json`, the
resolved PolicyProfile materiality terms, and workspace focus terms to surface
excluded or deprioritized candidates that match explicit materiality/focus
terms after capacity or scope screening. It is deterministic keyword
diagnostics only: Python does not infer semantic importance, mutate screening
results, resurrect candidates, alter the Claim Ledger, run gates, approve
delivery, or decide release readiness.
Support-Calibrated Wording projection reads existing reader Markdown, Claim
Ledger metadata, source taxonomy, and valid Claim-Support Matrix policy
signals to surface warning-only `support_wording` diagnostics for strong or
unframed wording on weak, downgrade-required, inferential, unsupported, or
media/report-style support metadata. It does not judge claim truth, generate or
accept support rows, run gates, block delivery, approve release, or create a
quality score.
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

Durable Source Evidence Pack materialization is experimental. The
`sources materialize-pack` command can turn explicit manual or cached-package
source records into workspace-local source evidence files under
`input/sources/` and an optional hash-checked
`output/intermediate/source_evidence_pack_manifest.json`. This helps ordinary
recurring reports archive reproducible source bytes. It does not upgrade
`source_candidates.yaml`, search summaries, model summaries, or source plans
into evidence; it does not assess semantic support, generate Claim-Support
Matrix rows, or authorize delivery.
Generated source evidence records preserve separate provider/storage
`source_type`, retrieval/page `retrieval_source_type`, reader-facing
`source_category`, and `underlying_evidence_type` metadata. This taxonomy is
identity normalization only; it is not trust scoring, source-policy gating,
semantic support judgment, or compliance review.

Fast-rerun fact-layer import is an experimental control transaction. It can
import a complete, clean, archived frozen fact layer into a new runtime run for
downstream rerun inspection. It does not register 080 experiment runs, score
output quality, summarize experiments, or prove semantic truth.
`run --recipe fast-rerun` requires an existing valid
`runtime_manifest.fact_layer_import`; it writes runtime handoff guidance from
Analyst onward and must not synthesize upstream source-discovery, Scout,
Screener, or Claim Ledger execution history.

MABW-080 run registration is experimental experiment metadata tooling. It
registers completed workspace runs into an existing 080 case as `run_record.json`.
MABW-080 scorecard building is experimental deterministic metadata tooling. It
can build a scorecard draft from an existing `run_record.json`, archive/control
projections, target artifacts, and the case definition. The default
`delivery_brief` target keeps the full finalize, reader-clean, and archive
requirements. The `auditable_brief` target is content-level experiment metadata
for frozen audited brief plus auditor gate passage only; it is not a
management-ready delivery claim. Python fills control integrity, fact-layer
match, reader-clean or target-not-required status, gate/finalize/archive,
timing, and coverage-delta status when inputs are available. It does not score guidance
manifestation, summarize experiments, scaffold conditions, run workflow stages,
judge prose quality, or prove output quality.

MABW-080 assessment import is experimental experiment metadata tooling. It
validates externally supplied guidance-manifestation scores, merges them into a
scorecard, and derives the resulting validity class from deterministic control
fields plus the imported assessment method. Python does not judge whether
guidance manifested, whether prose improved, or whether a semantic regression
occurred.

MABW-080 case summarization is experimental experiment metadata tooling. It
aggregates existing scorecards into A/B/invalid counts, condition groups,
manifestation-score counts, reader-clean rates, coverage-delta status, timing
status, and invalid reasons. It discovers scorecards under the case directory
and can include explicit `--scorecard` paths for scorecards written elsewhere.
It does not judge output quality, run workflow stages, scaffold conditions, or
include invalid runs in A-grade denominators.

MABW-080 condition scaffolding is experimental experiment setup tooling. It
requires an initialized condition workspace, imports the frozen fact layer
through the deterministic fast-rerun transaction, writes condition metadata and
operator instructions, and leaves the run at Analyst. It does not create generic
workspace config, run subagents, gates, finalize, register runs, score runs,
summarize cases, or create Improvement Memory.

For the full experimental command sequence and public-claim boundaries, see
[MABW-080 experiment guide](experiments-080.md).
v0.9.1 includes one completed public-safe synthetic `auditable_brief` pilot with
condition-blind, hash-bound assessment. This supports only the documented
single-case observation; broader quality, delivery-readiness, factual
correctness, and generalization claims remain out of scope.

## Runtimes

| Runtime | Status |
|---|---|
| Hermes (`delegate_task` native pipeline + cron) | Supported |
| Claude Code (`/briefloop` and `/mabw` five-verb writer entrypoints + `/generate-brief` generated-handoff delegated stage workflow; installable with `multi-agent-brief claude install`) | Supported |
| OpenCode (subagent workflow) | Supported |
| Codex (custom-agent workflow via `runtime install`) | Experimental |
| CodeBuddy (`--runtime codebuddy` handoff using `.codebuddy/skills/briefloop/` and `.codebuddy/agents/briefloop-*.md`) | Experimental |
| Operator (host-agnostic compact workflow; `manual` is a legacy CLI alias) | Supported |

Claude Code is the first-class writer / five-verb path (`new`, `run`, `status`,
`feedback`, `deliver`). Hermes remains a supported delegated/scheduled runtime
path. Codex custom-agent assets are installable into a workspace, but Codex
remains Experimental until a real Codex control-trace smoke validates the
end-to-end specialist workflow. Other runtimes keep their existing workflow
entrypoints. The operator runtime does not assume subagent/delegate capability;
it is a compact operator workflow that still requires deterministic
transactions, artifacts, gates, and human-triggered delivery.

Runtime source assets under `.agents/`, `.claude/`, `.codex/`, `.opencode/`,
and `integrations/hermes-plugin/` are source-clone assets. Package-only installs
ship the CLI, packaged contracts, and packaged eval fixtures, but they do not
ship those source runtime directories as Python package data. Use
`multi-agent-brief runtime install --workspace <workspace> --runtime opencode|claude|codex|all`
from a source clone to copy OpenCode/Claude Code/Codex workspace-local runtime kits.

CodeBuddy handoff support is experimental and source-clone-based. It uses the
project Skill under `.codebuddy/skills/briefloop/` and role agents under
`.codebuddy/agents/briefloop-*.md`. The main CodeBuddy session remains the
Orchestrator and runs deterministic BriefLoop CLI transactions; role agents only
draft handoff-assigned artifacts. This does not add gate authority, delivery
approval, release authority, semantic proof, or output-quality proof.

## Source Providers

| Provider | Status |
|---|---|
| Manual (local md/txt/json files) | Supported |
| Web search — Tavily | Supported |
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
| Packaged public-safe evaluation cases (`eval-cases`) | Supported |
| MABW-080 experiment case validator (`experiments 080 validate-case`) | Experimental |
| MABW-080 run registration (`experiments 080 register-run`) | Experimental |
| MABW-080 scorecard builder (`experiments 080 score-run`) | Experimental |
| MABW-080 assessment import (`experiments 080 import-assessment`) | Experimental |
| MABW-080 case summary builder (`experiments 080 summarize`) | Experimental |
| MABW-080 condition scaffold (`experiments 080 scaffold-condition`) | Experimental |
| Workspace provenance projection (`provenance`) | Supported |
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
| `pip install multi-agent-brief-workflow` (PyPI) | Experimental |
| Homebrew formula source (`Formula/`) | Experimental; not a primary release path |
| curl installer (`install.sh`) | Experimental CLI-only installer asset |
| PowerShell installer (`install.ps1`) | Experimental CLI-only installer asset |
| Hermes plugin (`integrations/hermes-plugin/`) | Supported |
| WorkBuddy Skill source bundle (`.agents/skills/briefloop-workbuddy/`) and local zip packaging (`workbuddy pack-skill`) | Experimental; source-clone-only input, generated local Skill zip, not WorkBuddy Marketplace publication or wheel/sdist package data |
| CodeBuddy project Skill adapter (`.codebuddy/skills/briefloop/`) | Experimental; source-clone-only project Skill used by `--runtime codebuddy`; keeps orchestration in the main CodeBuddy session and is not a forked Skill |
| CodeBuddy project role agents (`.codebuddy/agents/briefloop-*.md`) | Experimental; source-clone-only project sub-agent assets for role drafting only; main CodeBuddy session still owns deterministic CLI transactions |

| Runtime asset | Source clone | Wheel / sdist / PyPI package |
|---|---|---|
| Packaged contracts (`configs/*.yaml`) | Supported | Supported |
| Packaged eval fixtures (`eval-cases`) | Supported | Supported |
| `.agents/skills/**` | Supported | Source-clone-only |
| `.agents/hermes-skills/**` | Supported | Source-clone-only |
| `.claude/agents/**` and `.claude/commands/**` | Supported | Source-clone-only |
| `.opencode/agents/**` and `.opencode/commands/**` | Supported | Source-clone-only |
| `.codex/config.toml` and `.codex/agents/**` | Experimental | Source-clone-only; installable into a workspace with `runtime install --runtime codex` |
| `integrations/hermes-plugin/**` | Supported | Source-clone-only |
| `.agents/skills/briefloop-workbuddy/**` | Experimental | Source-clone-only WorkBuddy Skill source; `workbuddy pack-skill` generates a local Skill zip from these files, but wheel/sdist installs still do not include them |
| `.codebuddy/skills/briefloop/**` | Experimental | Source-clone-only CodeBuddy project Skill adapter. It must stay in the main session, must not use `context: fork`, and does not run gates, approve delivery, or authorize release |
| `.codebuddy/agents/briefloop-*.md` | Experimental | Source-clone-only CodeBuddy project role sub-agents. They may draft handoff-assigned role artifacts only; they do not write control files, run gates, approve delivery, or authorize release |
| `integrations/workbuddy/briefloop/**` | Experimental legacy mirror | Source-clone-only compatibility copy; not the canonical WorkBuddy pack source |
| `scripts/install.sh`, `scripts/install.ps1`, `Formula/` | Supported | Source-clone-only |

## Legacy / Deprecated

| Item | Status |
|---|---|
| `multi-agent-brief prepare` | Deprecated (use `run` instead) |
| Python `BriefPipeline` | Removed |
| `multi-agent-brief start` | Deprecated (alias for `run`) |
| `multi-agent-brief handoff` | Deprecated (use `run`) |
