# Changelog

All notable changes to the multi-agent-brief-workflow project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- **Breaking:** the supported Python floor is now 3.12. `requires-python`
  moves from `>=3.9` to `>=3.12`, so the next release refuses to install on
  Python 3.9-3.11. Setup and install scripts enforce the same floor at
  preflight, probe versioned interpreters (`python3.14`/`python3.13`/
  `python3.12`) when the unversioned `python3` is too old, and recreate an
  existing venv whose interpreter is broken or below the floor instead of
  reusing it. CI runs the full test suite on macOS and Windows with Python
  3.12 in parallel (pytest-xdist worksteal); Linux full-suite legs are
  retired by explicit maintainer decision, while Linux install and CLI
  smoke coverage remains.
- **Breaking:** runtime identity must now be explicit. Dedicated adapters inject
  their fixed canonical identity, while generic CLI users pass `--runtime`.
  New state accepts only `hermes`, `claude`, `opencode`, `codex`, `codebuddy`,
  or `operator`; historical `auto` / `manual` / implicit `controls` manifests remain read-only until
  an explicit reset starts a new canonical run and archives the old manifest.

## [0.12.1] — 2026-07-14

### Changed

- Bound the experimental WorkBuddy / CodeBuddy workflow to an explicit
  two-phase permission model: checked-in role agents draft only their
  handoff-assigned artifacts, while a command-capable main session re-reads the
  handoff and runs deterministic BriefLoop CLI transactions. Missing main-session
  command capability is a hard stop, and host-visible invocation of the exact
  checked-in role is required before claiming role delegation.
- Added the repo-local Northstar product-governance Skill with bounded evals,
  plus bilingual Architecture Reference v0.4.0 reading editions and a
  deterministic source/render guard. The report remains a historical v0.11.12
  snapshot; current architecture and support truth remain in the existing
  status and support-matrix documents.

## [0.12.0] — 2026-07-13

### Changed

- Completion projection and WorkBuddy now expose canonical recovery action
  vocabulary (`request_recovery_decision`, `rerun_from_stage`, and bound
  finalize actions); delivery eligibility no longer implies delivery success,
  which requires a current-run bound delivery outcome event.
- Rewrote the BriefLoop operator skill to the v1.0 RC operating contract
  (`briefloop-operator-skill-v0.2.0`): delivery truth is `finalize_report.json`
  plus the completion projection (`briefloop workbuddy diagnose --json`), never
  file existence; supersede recovery marks downstream artifacts stale until
  regenerated; agent artifact intake identity rules are documented as
  fail-closed; the version matrix now separates "v1.0 RC Landed Surfaces" from
  "Pending Before v1.0" (intake normalization, pilot evidence satisfaction).
  Runtime command surfaces (`/briefloop`, `/mabw`, `/generate-brief`, OpenCode
  adapters) were updated to the transactional finalize + delivery-truth flow,
  and the Claude skill wrapper became a model-invoked background protocol
  (`user-invocable: false`) so `/briefloop` resolves to the writer command.
- Rewrote the experimental WorkBuddy Skill (`.agents/skills/briefloop-workbuddy/`)
  in Chinese for its WorkBuddy first-user audience, preserving all CLI command
  strings, Run Card fields, role names, and control boundaries; skill pack
  tests now assert the Chinese contract.
- Archived superseded documentation (old MABW architecture references, dated
  2026-06-11 memos, one-off design notes) under `docs/archive/` with an
  archive policy README; current docs no longer link archived material as
  implementation truth.
- Made finalize promotion transactional: reader output is rendered and checked
  as a candidate before `output/brief.md` or `output/delivery/` are updated, and
  successful promotion records the delivery artifacts and their sha256 hashes in
  `finalize_report.json` (the single delivery-truth record; `deliver` and
  finalize-complete verify those artifacts). Failed reader-clean finalization
  writes a fail report but leaves any prior delivery bundle unchanged.
- Changed the PyPI / package-index distribution name from
  `multi-agent-brief-workflow` to `briefloop` so the eventual published package
  can support `pipx install briefloop`. The Python import package remains
  `multi_agent_brief`, the `multi-agent-brief` console script remains a
  compatibility entrypoint, and first-user docs still must not claim
  package-index install support until a real PyPI artifact is published and
  smoke-tested.
- Rewrote the experimental WorkBuddy Skill path to use `--runtime codebuddy`
  and checked-in CodeBuddy-compatible role agents for full workflow runs,
  instead of defaulting to the host-agnostic operator handoff. The main
  WorkBuddy/CodeBuddy session still owns deterministic CLI transactions; role
  agents only draft handoff-assigned artifacts and this does not add gate
  authority, delivery approval, release authority, semantic proof, or
  output-quality proof.
- Added experimental Gmail delivery through the optional `gws` CLI:
  `briefloop deliver --workspace <workspace> --target gmail --channel draft
  --recipient <email>` creates a Gmail draft, while `--channel send`
  explicitly sends the message. Both paths record redacted delivery events and
  do not attach audit/control files, approve delivery, authorize release, or
  prove semantic truth.
- Bound experimental Semantic Assessment Reports to checked input artifacts
  (`audited_brief`, Claim Ledger, Atomic Claim Graph, and Evidence Span
  Registry) with relative paths, hashes, sizes, freshness projection, and
  human-adjudication record linkage. Legacy unbound reports remain readable as
  advisory projections, and this does not add Claim-Support Matrix writes, gate
  authority, delivery approval, release authority, or semantic proof.
- Added a deterministic CodeBuddy adapter smoke guard that validates
  source-clone `.codebuddy` Skill/agent assets and a fresh `--runtime codebuddy`
  handoff. The smoke is release-readiness coverage only; it does not launch
  CodeBuddy, prove delegated runtime execution, approve delivery, authorize
  release, or prove semantic truth.
- Added experimental `--runtime codebuddy` handoff generation for source-clone
  CodeBuddy operation. The handoff names the project Skill and role-agent
  assets, records CodeBuddy runtime capabilities, and keeps deterministic CLI
  transactions in the main CodeBuddy session. This does not add gate authority,
  delivery approval, release authority, semantic proof, or output-quality proof.
- Added an experimental CodeBuddy project Skill adapter under
  `.codebuddy/skills/briefloop/`. The adapter keeps orchestration in the main
  CodeBuddy session, points to the WorkBuddy/CodeBuddy canonical Skill
  references, and can invoke project role agents explicitly. It does not add a
  `codebuddy` runtime, gate authority, delivery approval, release authority, or
  semantic proof.
- Added experimental CodeBuddy project role sub-agent source assets for
  BriefLoop Scout, Analyst, Editor, Auditor, and Formatter. These are
  source-clone-only drafting adapters and do not add CodeBuddy runtime support,
  gate authority, delivery approval, release authority, or semantic proof.
- Added a v1.0 pilot evidence gate document and advisory release-consistency
  check. The normal guard verifies that the evidence record exists and states
  its current status; the v1.0 release operator must run the same check with
  `--require-satisfied` before claiming v1.0 readiness. This is release
  evidence bookkeeping only, not semantic proof, output-quality proof, delivery
  approval, or release authority.
- Hardened the first-user documentation guard so README and Chinese README keep
  the user-facing document block focused on Getting Started, Weekly Loop,
  Troubleshooting, and the golden reference workspace.
- Added a read-only `briefloop status` progress projection with user-language
  work labels such as `prepare sources`, `select claims`, `audit brief`, and
  `build quality package`. When post-finalize Quality Panel closeout is
  recommended or stale, status may prioritize `briefloop quality summarize` as
  the suggested next command before delivery. The projection is
  diagnostic/operator guidance only and does not create stage, gate, delivery,
  or release authority.
- Added public BriefLoop contact entrypoints for `briefloop.ai`,
  `hello@briefloop.ai`, `contact@briefloop.ai`, `help@briefloop.ai`, and
  `security@briefloop.ai`, with explicit support/security boundaries.
- Productized first-user routing surfaces so README and first-user guides route
  by supported report job (`industry-weekly`, `management-monthly`,
  `document-review`) while the product baseline guard prevents internal report
  pack ids or control-plane vocabulary from returning to those first-run route
  blocks.
- Added static `_BUNDLE_README.md` guidance files inside generated delivery and
  audit bundle archives so non-developer reviewers know which files to open
  first. These guidance files are packaging instructions only; they do not
  create delivery approval, release authority, or semantic-proof claims.

## [0.11.12] — 2026-07-04

### Added

- Added fifteen-minute pilot documentation and deterministic first-run demo
  Quality Panel surfacing. The demo remains API-free, source-clone oriented,
  synthetic, and not an output-quality proof.
- Added WorkBuddy install documentation, Chinese WorkBuddy documentation, and a
  trigger-only WorkBuddy Assistant prompt template. These docs keep the
  WorkBuddy Skill as the local capability surface, describe Assistant as a
  remote trigger into a local Skill-enabled WorkBuddy session, and do not add
  WorkBuddy delegated runtime support, gate authority, delivery approval,
  release approval, or semantic proof claims.
- Added a source-clone WorkBuddy Skill bundle under
  `integrations/workbuddy/briefloop/`, with WorkBuddy-facing quickstart,
  workspace workflow, artifact-boundary, status/gate, repair, and safety
  references. The bundle uses the `operator` runtime path and deterministic
  BriefLoop CLI transactions only. It is not shipped as Python wheel/sdist
  package data yet and does not add WorkBuddy runtime authority, gate authority,
  delivery approval, release approval, or semantic proof claims.
- Added `briefloop workbuddy pack-skill` /
  `multi-agent-brief workbuddy pack-skill` to build a deterministic local
  WorkBuddy Skill zip and sidecar manifest from source-clone files. The package
  is a local Skill archive, not a WorkBuddy Marketplace publication, Python
  package-data surface, runtime authority layer, gate authority, delivery
  approval, release approval, or semantic proof claim.
- Added `operator` runtime as the host-agnostic compact operation path for
  environments without a dedicated BriefLoop runtime adapter. `manual` remains
  a legacy CLI alias that resolves to `operator`; generated handoff artifacts
  now record the operator runtime and its non-delegation assumptions without
  changing stage order, artifact contracts, gates, delivery, or release
  authority.
- Added `semantic-support adjudicate` to record human accept/reject decisions
  for valid Semantic Assessment Report proposal rows in
  `semantic_support_acceptance_ledger.json` with event-log linkage. These
  records do not write Claim-Support Matrix rows, route repair, run gates,
  approve delivery, authorize release, or prove semantic truth.

### Changed

- Declared `operator` in the orchestrator runtime contract and narrowed
  operator handoff internals for host-agnostic compact operation.
- Documented the draft-promote ownership matrix for agent-authored drafts,
  Python validation/promotion, and authoritative artifacts.

### Fixed

- Preserved explicit online-search opt-outs from `briefloop onboard` when the
  saved `onboarding.json` is replayed through `briefloop init --from-onboarding`.
- Hardened public-safety sha256 scanning so checksum fields are allowed by
  span, while token-like values near checksum text are still scanned.
- Required screened-candidate discard reason codes and aligned Hermes-facing
  prompts with that contract.
- Classified unavailable Quality Panel states as missing instead of neutral
  informational badges.
- Froze deterministic demo Quality Panel timestamps and surfaced Quality Panel
  artifacts in the first-run demo path.
- Blocked delivery when refreshed run-integrity state is invalid.
- Hardened post-merge control projections and added semantic support auditor
  dogfood fixtures for proposal-only coverage.
- Clarified Semantic Support Auditor role wording so human accept/reject
  records adjudication only and does not create support truth or authoritative
  audit findings.

## [0.11.9] — 2026-07-04

### Added

- **Bilingual Quality Panel HTML toggle**: `quality_panel.html` now embeds a
  static CSS-only English / Chinese label toggle for the human-readable panel
  view while keeping `quality_panel.json` as the single untranslated machine
  facts source. The HTML remains script-free, dependency-free, SHA-bound to the
  sibling JSON projection, and does not add quality judgments, gate authority,
  delivery approval, or release authority.
- **pipx / PyPI packaging prep**: added future package-index readiness
  documentation, neutral PyPI project metadata, and release-checklist guardrails
  for a later `pipx` path while keeping source-clone setup as the current
  launch install path. This does not publish a PyPI artifact, rename the Python
  package, remove the `multi-agent-brief` console script, or make `pipx` a
  current install instruction.
- **Evidence Extract MinerU-derived Markdown bridge**: `briefloop extract` /
  `multi-agent-brief extract` can now bind an already-present adjacent
  `.mineru.md` representation for PDF/binary `evidence_extract` sources,
  keeping the original source bytes in the source lock while using the derived
  Markdown for deterministic logical-page and text-span seed registration. This
  does not run MinerU automatically, parse PDFs by itself, perform rendered-page
  visual inspection, extract tables or figures, judge semantic support,
  generate Claim-Support Matrix rows, approve delivery, authorize publication,
  or close the full Evidence Extraction Mode scope.

### Fixed

- **Architecture reference version label**: corrected the mislabeled
  `docs/mabw-architecture-reference-v0.2.0.md` path by turning it into a
  compatibility pointer and moving the legacy MABW v0.3.0 architecture
  reference to `docs/mabw-architecture-reference-v0.3.0-legacy.md`. This is a
  documentation-label fix only, not a product capability change.
- **Repository hygiene**: removed an unrelated Understand Anything graph-merge
  helper, its dedicated test, and a zero-reference source-quality helper module.
  This is repo cleanup only, not a BriefLoop product behavior or support-status
  change.
- **Orphan module triage**: removed test-only effort-budget and market
  competitor enrichment modules with their dedicated tests, and marked the
  runtime safety surface registry as a test-only structural guard. This is
  layer-boundary cleanup only; it does not change runtime behavior, gates,
  delivery, release authority, or support status.
- **Assessment target contract namespace**: moved the production assessment
  target contract from the `experiments` namespace to `contracts`, leaving a
  compatibility shim for older experiment imports. This is layer-boundary
  cleanup only; it does not change target ids, artifact paths, experiment
  behavior, gates, delivery, release authority, or support status.
- **Runtime handoff domain boundary**: moved runtime handoff domain helpers out
  of CLI-owned modules and moved `InitProfile` into a workspace domain module,
  leaving compatibility exports for existing callers. This is layer-boundary
  cleanup only; it does not change handoff schema, handoff wording, runtime
  state files, gates, delivery, release authority, or support status.
- **Shared test workspace helpers**: added shared pytest fixtures and test
  helpers for repeated workspace skeleton and SHA-256 setup in CLI/status/
  delivery tests. This is test infrastructure cleanup only; it does not change
  runtime behavior, artifact contracts, gates, delivery, release authority, or
  support status.

## [0.11.6] — 2026-07-03

### Added

- **Release checklist**: added `docs/release-checklist.md` as an
  operator-facing release preparation checklist covering version/tag/release
  checks, release consistency, product baseline, launch smoke, public-claim
  guardrails, GitHub release existence, and package metadata when applicable.
  This is release operations documentation only, not a capability claim,
  benchmark claim, or roadmap commitment.
- **Launch/demo smoke guard**: added `scripts/check_launch_smoke.py` to verify
  the fresh source-checkout demo path reaches import, CLI version, demo init,
  doctor, and runtime handoff from a temporary workspace. The release
  consistency check runs the JSON mode. This is setup/handoff readiness only;
  it does not call an LLM, require a private path or API key, prove semantic
  truth, prove output-quality improvement, approve delivery, or authorize
  release.
- **Evidence Extract source lock v1**: `briefloop extract` /
  `multi-agent-brief extract` now writes
  `output/intermediate/evidence_extract_source_lock.json` plus an audit copy,
  binding registered `input/sources/evidence_extract/` files to file size and
  SHA-256 so status/artifact-registry checks can detect later source-byte
  drift.
- **Evidence Extract page inventory seed v1**: `briefloop extract` /
  `multi-agent-brief extract` now writes
  `output/intermediate/evidence_extract_page_inventory.json` plus an audit
  copy, binding the inventory to the source lock and giving UTF-8 text sources
  deterministic logical page IDs. PDF/binary sources remain registered-only and
  are flagged as requiring a future extraction tool. This is bounded
  source-lock/page-seed/span registration for `document-review` /
  `evidence_extract`; it does not parse PDFs or binary files, render pages for
  visual inspection, extract tables or figures, generate an evidence ledger or
  Claim-Support Matrix, judge semantic support, draw legal/disclosure
  conclusions, approve delivery, or authorize publication.
- **Trajectory Regulation decision narrowing**: repeated retry, repair-cycle,
  or blocker patterns for the current stage now deterministically narrow
  `workflow_state.next_allowed_decisions` to `request_human_review` and
  `block_run`, record a `trajectory_decision_narrowed` event, and surface the
  narrowing through status and runtime handoff. This does not add decision
  vocabulary, execute repair, change stage order, run gates, approve delivery,
  decide release readiness, or let Python perform agent work.
- **Release/evidence synthetic blocker regressions**: packaged public-safe
  evaluation cases now cover the remaining #96 release/evidence failure
  patterns: unauthorized institution branding, mixed metric scope,
  media-only legal/policy support, company-event claims missing latest official
  checks, third-party price snapshots for formal-release treatment, and formal
  release-candidate checks missing human approvals. These cases use explicit
  synthetic Claim-Support Matrix records and release-readiness metadata only;
  they do not add live source retrieval, automatic official-source judgment,
  semantic truth proof, or public-release authorization.
- **Minimal comparative evaluation packet**: added a public-safe v0.11.4
  comparison packet with three synthetic tasks, a direct prompt/template
  baseline arm, a BriefLoop-style workflow arm, frozen raw-output hashes, raw
  reviewer observations, and a second-reviewer subset. The release consistency
  check now runs `scripts/check_minimal_comparative_eval.py` to verify the
  packet shape and hash bindings. This is bounded evaluation evidence only; it
  does not claim general output-quality improvement, speed improvement,
  semantic truth proof, benchmark superiority, delivery approval, or release
  authorization.
- **Product OS reader-quality reference package**: the packaged
  `same_evidence_reader_quality_regression` eval case now generates
  Quality Panel JSON/summary/HTML plus clean delivery/audit bundle archives,
  and the docs include a public-safe v0.11.3 reference note. This is an
  inspectable reference-package regression signal only; it does not claim
  output-quality improvement, semantic proof, delivery approval, or release
  authorization.
- **Same-evidence reader-quality regression pack**: packaged public-safe
  evaluation cases now include a synthetic
  `same_evidence_reader_quality_regression` workspace that holds evidence
  inputs fixed while surfacing existing materiality-selection,
  reader-template-conformance, support-wording, and Quality Panel closeout
  projections. The eval runner can call `quality.summarize` to generate and
  validate Quality Panel JSON/summary/HTML artifacts in the fixture. This is a
  deterministic regression guard only; it does not score model output quality,
  prove semantic correctness, run subagents, fetch sources, or approve
  delivery/release.
- **Final quality scoped eval case**: packaged public-safe eval cases now include
  `final_abstract_quality_warning_surface`, which exercises the scoped #79
  warning surface on reader-facing Markdown and confirms the findings flow into
  Quality Summary without blocking, opening repair, or writing repair
  instructions into reader output. This remains deterministic warning
  projection only; it is not a semantic quality judge, delivery approval,
  release authority, or publication-readiness claim.
- **Quality Panel real-run closeout guidance**: finalize reports and
  `status --json` now project a post-finalize Quality Panel closeout
  recommendation pointing operators to
  `briefloop quality summarize --workspace <workspace>`. The Quality Panel and
  summary/HTML renderers also show the audit/delivery bundle separation for
  these artifacts. This is operator follow-up guidance only; finalize does not
  auto-generate Quality Panel artifacts, Quality Panel remains audit-bundle
  material when valid, and it does not approve delivery, decide release
  readiness, run gates, repair content, or prove report correctness.
- **Citation Profile Split**: packaged ReportTemplates can now declare
  `reader_contract.citation_profile` values (`executive`, `analyst`, or
  `audit`) so finalize reports and bundle manifests record the resolved
  reader/audit citation profile. Reader delivery keeps reader-safe source
  labels and does not expose Claim Ledger IDs, span IDs, local paths, or
  hashes; audit bundles retain the trace artifacts when present. This is
  citation-surface metadata only; it does not prove support, alter gates,
  remove audit trace, approve delivery, or decide release readiness.
- **Support-calibrated wording warnings**: `status --json` and Quality Panel
  now surface warning-only `support_wording` diagnostics when reader-facing
  Markdown uses strong or unframed wording for claims with explicit weak,
  downgrade-required, inferential, unsupported, or media/report-style support
  metadata. The projection consumes recorded Claim Ledger, source taxonomy, and
  valid Claim-Support Matrix policy signals when present. It does not judge
  claim truth, generate or accept support rows, run gates, block delivery,
  approve release, or create a quality score.
- **Reader Template Conformance v1**: packaged ReportTemplates can now declare
  warning-only reader contracts for required reader blocks, Markdown table
  slots, executive-summary length, and Source Appendix position. Status,
  handoff, finalize reports, and Quality Panel surface deterministic
  `report_template_conformance` diagnostics for finalized reader Markdown.
  This does not rewrite briefs, invent missing sections, parse DOCX content,
  run gates, block delivery, approve release, score prose quality, or prove
  semantic correctness.
- **Materiality-aware selection diagnostic projection**: `status --json` and
  Quality Panel now surface when excluded or deprioritized screened candidates
  match explicit PolicyProfile `materiality_terms` or workspace focus terms
  such as must-watch topics, with capacity/scope reason summaries and
  `request_human_review` / `review_materiality_exclusions` operator actions.
  This is deterministic keyword diagnostics only; Python does not infer
  semantic importance, mutate screening results, resurrect candidates, alter
  the Claim Ledger, run gates, approve delivery, or decide release readiness.
- **Guidance Manifestation diagnostic projection**: status and Quality Panel
  can now surface optional
  `output/intermediate/guidance_manifestation_report.json` labels for
  materialized approved guidance entries:
  `explicitly_reflected`, `partially_reflected`, `contradicted`, and
  `not_observable`. Packaged public-safe eval cases include a synthetic
  `not_observable` report. This is an observability diagnostic only; it does
  not mutate Improvement Memory, approve guidance, score quality, run gates,
  approve delivery, decide release readiness, or prove that guidance improved
  output.
- **Trajectory Regulation read-only projection**: `status --json` and Quality
  Panel now surface retry-stage, repair-cycle, repeated-blocker, and exhausted
  attempt-budget summaries derived from existing `workflow_state.json` and
  `event_log.jsonl`. Packaged public-safe eval cases include a synthetic
  repeated-retry case that projects `request_human_review` without mutating
  workflow state. This is operator guidance only; it does not write state,
  execute repair, run gates, approve delivery, decide release readiness, score
  quality, or prove output correctness.
- **v0.11 product golden path**: refreshed the public English/Chinese Golden
  Path docs around the supported `industry-weekly`, `management-monthly`, and
  `document-review` product entries, with explicit local-first, gates-on,
  human-delivery boundaries. The product-baseline readiness check now guards
  these docs against drifting back into experiment/scorecard language. This is
  documentation and release-readiness guardrail work only; it does not add an
  experiment harness, prove output quality, authorize release, or change runtime
  stage behavior.
- **Final Abstract Quality warning surface**: `gates check` now includes a
  warning-only `final_abstract_quality` gate for deterministic final-abstract
  risk patterns such as cadence/title mismatch, comparison framing without a
  basis section, recommendation/forecast/superlative framing without
  limitations, incomplete key-case bullets, and locally unsupported
  superlatives. Findings flow through normal Quality Panel / Quality Summary
  warning counts. This is not a prose-quality score, semantic quality judgment,
  truth proof, repair route, delivery approval, release authority, or
  publication-readiness claim.
- **Coverage/Omission gate foundation**: `gates check` now includes a
  deterministic `coverage_omission` gate that compares valid
  `screened_candidates.json` selected high-priority candidates against Claim
  Ledger `candidate_id` metadata and, for auditable briefs only, cited internal
  `[src:<claim_id>]` references. Reader-facing finalize checks do not require
  delivery Markdown to retain internal Claim Ledger markers. The gate warns by
  default, blocks under `--strict`, and ignores invalid or legacy screening
  artifacts instead of treating them as authority. This is a selected-item
  continuity check only; it does not infer full-world recall, prove semantic
  support, execute source discovery, or claim the system found every material
  item.
- **Evidence Extract text-span seed registry**: `briefloop extract` /
  `multi-agent-brief extract` now writes a valid
  `output/intermediate/evidence_span_registry.json` for registered UTF-8 text
  sources, preserving workspace-relative source paths, deterministic
  `SRC-###` / `ESP-###-01` ids, source-text character offsets
  (`char_start` / `char_end`), and raw-excerpt hashes. Binary/PDF sources
  remain registered-only with warnings. This is a bounded source/span
  registration surface only; it does not parse binary documents, assess
  semantic support, generate Claim-Support Matrix rows, draw legal or disclosure
  conclusions, run stages, approve delivery, or create release authority.
- **Synthetic Product OS blocker eval cases**: packaged public-safe eval cases
  now include deterministic blockers for invalid source evidence pack manifests
  and forged release-readiness event links, plus artifact-registry status
  assertions in the eval-case runner. These cases validate control-surface
  behavior only; they do not prove output quality, source support, or release
  authorization.
- **Release branding readiness context**: `release check` now includes
  configured `release.branding` metadata in
  `output/intermediate/release_readiness_report.json` and blocks internal
  readiness when required institution branding or institution-use authorization
  context is missing or explicitly unauthorized. This is deterministic metadata
  validation only; it does not provide legal/compliance advice, authorize
  public release, publish externally, or bypass human delivery approval.
- **Feedback contamination regression**: added a v0.11.1 issue-closure
  regression proving feedback-only input text remains classified as feedback
  and is not exposed through the runtime handoff as evidence material. The
  regression also verifies the finalizer does not read feedback-only text into
  reader Markdown, delivery Markdown, or DOCX output. This is a boundary
  regression only; it does not add semantic contamination detection or convert
  feedback into Improvement Memory.
- **v0.11 product-baseline readiness check**: added
  `scripts/check_product_baseline.py` to verify the stable CLI product baseline
  entrypoints, canonical ReportPack mappings, local-first workspace skeletons,
  control-spine defaults, no force-deliver CLI surface, reference-run docs, and
  public boundary wording before a v0.11 release. This is a readiness guard
  only; it does not bump the version, promote wider Product OS support status,
  run stages, approve delivery, prove truth, or create release authority.
- **Release consistency product-baseline guard**: `check_release_consistency.py`
  now runs the v0.11 product-baseline readiness check so release prep fails
  closed if product-facing entries, ReportPack defaults, packaged parity, or
  public boundary wording drift. This is still a release-readiness check only;
  it does not promote wider Product OS support status or add runtime authority.
- **Product-baseline `packs` CLI surface guard**: the v0.11 readiness check now
  verifies real `packs list --json` and unknown-pack error output expose
  product-facing entries while preserving canonical internal ReportPack ids.
  This is a CLI contract check only; it does not rename ReportPack ids or
  change workspace behavior.
- **README canonicalization guard**: `README.md` and `README.zh-CN.md` are the
  canonical public README bodies, while `README_en.md` is retained as a short
  compatibility pointer to `README.md`. The v0.11 readiness and release checks
  now verify this split before release prep. This is a public-link and
  public-claim guard only; it does not change support status or product
  behavior.
- **v0.11 support-status alignment guard**: clarified that
  `industry-weekly`, `management-monthly`, and `document-review` are the
  stable v0.11 product-baseline workspace entries, while `solar-periodic`,
  Quality Panel, SourceHub Lite, internal release approvals, and other wider
  Product OS extensions remain experimental. The product-baseline readiness
  check now verifies this support-matrix split before release prep.

### Fixed

- **Trajectory Regulation completed-stage guidance**: retry/repair history for
  completed or non-current stages remains visible as diagnostic history, but no
  longer emits impossible `request_human_review` / `block_run` recommendations
  for stages that deterministic state transitions cannot currently accept.
- **Coverage gate stage-completion binding**: stage-scoped quality-gate reports
  must include the `coverage_omission` gate result before auditor/finalize
  completion can accept them, closing the upgraded-workspace gap where older
  three-gate reports could bypass coverage continuity checks.
- **Generated workspace selector floor**: `briefloop new` now initializes
  Product OS workspaces with `selector.max_items: 20`, matching the current
  `brief_quality.min_items: 20` floor instead of creating conservative
  workspaces that could not satisfy their own configured item count. The legacy
  `init` path now rejects explicit selector counts below that floor instead of
  writing internally conflicting configs.
- **Release branding blocker event binding**: release-readiness reports now
  compare the exact branding blocker list recorded by `release check`, so
  hand-edited branding blockers cannot remain valid merely by preserving a
  blocked/non-blocked boolean shape.
- **Release branding event-link binding**: `release_readiness_report.json`
  validation now requires the report `branding_context` status and blocked
  state to match the recorded `release_readiness_checked` event metadata, so
  hand-edited branding context cannot remain artifact-registry valid.
- **ReportPack support-status alignment**: baseline ReportPacks now expose
  machine-readable `status: supported` for `market_weekly`,
  `management_monthly`, and `evidence_extract`, while
  `solar_industry_periodic` remains `experimental`. The product-baseline
  readiness check now verifies these config and CLI statuses against the public
  support-matrix split.
- **Material-fact bibliography false positives**: deterministic audit now skips
  bibliography / source-reference sections when checking
  `number_without_source`, so numbers in source titles do not become blocking
  material-fact findings while body text remains checked.
- **README/public-claim release guards**: tightened `README_en.md` checks so the
  file must remain only the compatibility pointer, and expanded product-baseline
  public-claim rejection to catch modal truth-proof claims and publication
  overclaims that start with `without`.
- **Quality Panel legacy gate-report compatibility**: Quality Panel now surfaces
  legacy `output/intermediate/quality_gate_report.json` status separately from
  v0.10 scoped auditor/finalize gate reports. Legacy reports are not treated as
  scoped reports, and workspaces with reader-clean `pass` now receive a scoped
  gate-report regeneration action instead of a generic finalize-hygiene action.

## [0.10.7] — 2026-06-29

### Added

- **Secret hygiene import command**: added `multi-agent-brief secrets import`
  to copy allowlisted API keys into a workspace `.env` while redacting
  stdout/stderr to `present` plus a SHA-256 prefix. Doctor guidance now points
  operators to this command for private key setup; it does not print or log
  secret values.
- **Source metadata contract hardening**: candidate claims, screened
  candidates, claim drafts, and Claim Ledger validation now separate provider
  `source_type` from reader-facing `source_category`, reject non-URL text in
  `source_url`, and preserve `source_category` into frozen Claim Ledger
  metadata. This is contract validation only; source appendix rendering,
  metadata enrichment, and source-policy gates remain separate follow-up
  surfaces.
- **Claim metadata freeze/enrichment hardening**: Claim Ledger freeze and the
  deterministic `state enrich-claim-metadata --from-source-evidence`
  transaction now preserve `source_url`, `source_type`, and `source_category`
  in claim metadata so source appendix rendering has stable source identity
  inputs. The transaction still only enriches metadata, updates hashes,
  registry, workflow, and events atomically, and remains fail-closed after
  finalize or downstream completion.
- **Secret and source URL safety hardening**: `secrets import` now fails closed
  unless the target already looks like a BriefLoop workspace, preventing typo
  paths from creating stray `.env` files, and source metadata URL validation now
  requires an HTTP(S) scheme with a network location.
- **Source Appendix rendering hardening**: reader-facing source appendices now
  prefer source title, category, publisher/institution, dates, URL, and provider
  type from Claim Ledger metadata, including usable local-file sources without
  URLs. Invalid URLs stay unlinked and incomplete source metadata is surfaced as
  appendix notes; this is rendering hardening only, not source-policy gating or
  semantic support proof.
- **Agent contract and repair guard hardening**: Scout and Claim Ledger runtime
  contracts now explicitly separate HTTP(S) `source_url`, local/package
  `source_path`, provider `source_type`, and reader-facing `source_category`.
  Gate/state repair guidance now spells out the owner-stage repair transaction
  path and warns against manually updating control files or SHA fields.
- **PolicyProfile resolver for zero-config workspaces**: `briefloop new` now
  accepts explicit `--policy-profile` overrides and deterministic
  `--industry` hints, writes the selected profile and resolution source into
  `report_spec.yaml`, and shows the source in validation/status projections.
  Ambiguous or low-confidence matches use the ReportPack default. This is not
  gate-time industry inference, compliance judgment, or release authority.
- **Product-facing ReportPack entry aliases**: `briefloop new` and
  `multi-agent-brief new` now accept user-facing entries such as
  `industry-weekly`, `management-monthly`, `document-review`, and
  `solar-periodic` while continuing to write canonical internal ReportPack ids
  such as `market_weekly`, `management_monthly`, `evidence_extract`, and
  `solar_industry_periodic` into `report_spec.yaml`. This is an entrypoint
  naming layer only, not a ReportPack/schema rename.
- **Solar industry periodic ReportPack dogfood contract**: added packaged
  experimental `solar_industry_periodic` ReportPack / ReportTemplate contracts
  and a `solar_manufacturing_default` PolicyProfile for local-first solar
  manufacturing periodic-report work. This fixes report type, section-order,
  and deterministic product-default metadata only; it does not automatically
  generate solar reports, provide tax/compliance/investment advice, judge
  semantic truth, deliver reports, or authorize publication.
- **ReportTemplate section-order projection**: workspaces with
  `report_spec.yaml` now expose the resolved packaged ReportTemplate and
  section order in read-only `status` and generated runtime handoff artifacts.
  This is product section-order metadata only; it does not render templates,
  rewrite content, bypass gates, deliver reports, or authorize publication.
- **ReportTemplate section-conformance projection**: read-only `status` and
  generated runtime handoff artifacts now report whether existing audited/final
  reader Markdown headings cover the resolved ReportTemplate sections in order.
  This is diagnostic structure guidance only; it does not render templates,
  rewrite content, block gates, deliver reports, or authorize publication.
- **Report bundle packaging hygiene**: delivery/audit bundle projection now
  excludes common macOS, Office, and editor temporary files, records excluded
  packaging junk in the manifest, preserves UTF-8 artifact paths with
  deterministic ASCII fallback names, dedupes adjacent reader source labels,
  and renders Source Appendix URLs as DOCX hyperlinks where supported. This is
  packaging hygiene only; it is not template rendering, evidence sufficiency,
  delivery approval, or publication authorization.
- **Clean delivery/audit bundle archives**: `packs bundle --write-archives`
  now writes official clean `delivery_bundle.zip` and `audit_bundle.zip` files
  from the bundle manifest artifact sets, excluding stray legacy ZIP contents
  and package-root junk. These archives are deterministic export surfaces only;
  they do not render templates, bypass gates, approve delivery, or authorize
  publication.
- **Durable source evidence pack materialization**: added experimental
  `sources materialize-pack` to write explicit manual/cached-package source
  records into `input/sources/` plus
  `output/intermediate/source_evidence_pack_manifest.json`. The manifest is
  optional and hash-validated when present. This materializes source evidence
  bytes for archive reproducibility only; it does not treat source candidates,
  search summaries, or model summaries as evidence, and it does not assess
  semantic support or generate Claim-Support Matrix rows.
- **Evidence Extract product pack**: added an experimental `evidence_extract`
  ReportPack / ReportTemplate / PolicyProfile plus `briefloop extract` /
  `multi-agent-brief extract` source/scope registration. The command copies
  explicit local source files into the workspace and writes
  `extraction_scope.yaml` plus source registrations only; it does not parse
  PDFs, generate evidence spans, draw legal or disclosure conclusions, bypass
  gates, or authorize delivery.
- **SourceHub Lite setup commands**: added experimental `sources add-file`,
  `sources add-rss`, and `sources add-web-search` to register local text files,
  RSS feeds, and runtime web-search handoff tasks in `sources.yaml`. Local
  files are copied into the workspace before registration so external absolute
  paths are not persisted. Web-search tasks use `runtime_tool` handoff mode and
  do not execute Python web search, crawl the web, create source candidates as
  evidence, generate Evidence Span Registry entries, bypass gates, or authorize
  delivery.
- **Source taxonomy normalization**: durable source evidence records and Claim
  Ledger source metadata now preserve separate provider/storage
  `source_type`, retrieval/page `retrieval_source_type`, reader-facing
  `source_category`, and `underlying_evidence_type` fields. This clarifies
  cases such as a news article about a paper versus the paper itself; it is not
  source trust scoring, semantic support assessment, or a source-policy gate.
- **Source Appendix audit trace upgrade**: reader-facing Source Appendices now
  can display safe retrieval and underlying-evidence taxonomy labels, while the
  separate `output/source_appendix_trace.md` audit copy records claim/source
  mappings, source byte hashes, sizes, span IDs, and metadata completeness
  warnings. This is traceability and appendix hardening only; it does not prove
  source support, alter delivery gates, or authorize publication.
- **Screener discard audit trail**: object-shaped `screened_candidates.json`
  now supports deterministic discard audit validation when candidate totals or
  discard audit fields are present. Excluded/deprioritized entries must carry a
  stable reason code and short explanation under that audit surface, and totals
  must reconcile with selected plus discarded candidates. Legacy reason-only
  screened candidate artifacts remain accepted.
- **ReportTemplate render-plan projection**: read-only status and generated
  handoff artifacts now project the future render source artifact, section
  heading mapping, unresolved section diagnostics, and planned delivery targets
  for workspaces with a resolved ReportTemplate. This is render planning
  metadata only; it does not render templates, rewrite content, call finalize,
  bypass gates, deliver reports, or authorize publication.
- **ReportTemplate renderer MVP**: finalize now records an experimental
  `template_rendering` report and can apply the resolved ReportTemplate section
  order to reader Markdown before DOCX generation and reader-final checks. This
  renderer only reorders already-present sections; unresolved or extra
  top-level sections remain diagnostic/no-op. It does not create a second gate
  engine, assess semantic support, approve delivery, or authorize publication.
- **Internal release modes and human approval ledger**: added experimental
  `approval init`, `approval record`, and `release check` commands for internal
  review workflows. The commands write
  `output/intermediate/human_approval_ledger.json`,
  `output/intermediate/release_readiness_report.json`, and event-log records
  through deterministic CLI transactions. Release checks can report readiness
  for internal review modes only; they do not authorize public release, publish
  externally, bypass gates, or replace legal/compliance/IR owner judgment.
- **Quality Panel JSON projection foundation**: added an experimental
  `quality_panel.json` product-quality projection that summarizes existing
  control integrity, source evidence, gate, claim/support, and delivery hygiene
  surfaces. This is a machine-readable audit/control summary only; it does not
  run gates, create a quality score, decide release eligibility, approve
  delivery, prove semantic truth, or execute repair.
- **Quality Summary Markdown projection**: added optional
  `output/intermediate/quality_summary.md` as a compact human-readable summary
  rendered from valid `quality_panel.json`. This is an operator-readable
  projection only; it is not a quality score, gate report replacement, release
  authorization, delivery approval, truth proof, or repair action.
- **Quality summarize CLI**: added experimental
  `briefloop quality summarize --workspace <workspace>` to write
  `quality_panel.json` and source-bound `quality_summary.md` together. The
  command is a deterministic projection writer only; it does not run gates,
  create blockers, start repair, approve delivery, prove truth, or authorize
  release.
- **Quality Panel static HTML projection**: added optional
  `output/intermediate/quality_panel.html` as a static, dependency-free audit
  attachment rendered from valid `quality_panel.json`. The HTML uses inline CSS
  and no external assets, scripts, frontend runtime, quality score, release
  authority, delivery approval, truth proof, or gate reimplementation.
- **Quality Panel audit bundle integration**: report bundle projection now
  includes `quality_panel.json`, `quality_summary.md`, and
  `quality_panel.html` in audit bundles when present, while keeping them out of
  reader-facing delivery bundles. This is audit packaging only; it does not
  create a dashboard, quality score, release eligibility decision, delivery
  approval, truth proof, or gate replacement.

### Fixed

- **Release approval event-linkage hardening**: release readiness now rejects
  human approval ledger records whose `event_id` does not resolve to a matching
  current-run approval event, and artifact registry validation rejects forged or
  mismatched approval/readiness event references.
- **Evidence Extract force rerun source preservation**: `extract --force` now
  stages source bytes before clearing managed
  `input/sources/evidence_extract/` files, so rerunning extract with a
  previously copied managed source path can update scope without deleting the
  source before it is recopied.
- **Fast-rerun Claim Ledger enrichment chain validation**: fast-rerun import
  validation now accepts a Claim Ledger derived through a chained metadata
  enrichment record when the latest record still points back to the original
  imported Claim Ledger hash.
- **Hermes Claim Ledger completion handoff**: Hermes-generated skill and prompt
  guidance now run `state stage-complete --stage claim-ledger` after
  `state freeze-claim-ledger` and before Analyst delegation, so the runtime
  state machine advances with the frozen Claim Ledger.
- **Source Appendix source ID title fallback**: reader-facing source appendices
  no longer use raw ledger `source_id` values such as `SRC-001` as display
  titles when source title/name metadata is missing; they keep the generic
  source record title and surface the missing-title note instead.
- **Claim metadata enrichment rerun source type repair**: rerunning
  `state enrich-claim-metadata --from-source-evidence` now also repairs stale
  top-level `source_type: local_file` when existing claim metadata already
  matches the imported source authority, so Source Appendix rendering receives
  the corrected provider type.
- **Claim Ledger freeze source type defaults**: claim drafts that provide a
  whitespace-only `source_type` are now materialized as `local_file` during
  Claim Ledger freeze, matching the claim-draft validator's default local-file
  semantics.
- **Source URL malformed-host validation**: source metadata URL validation now
  treats parser errors such as malformed bracketed hosts as normal validation
  failures instead of letting `urlparse()` exceptions escape contract checks.
- **Source metadata local-file default validation**: claim drafts that omit
  `source_type` and `source_url` are now validated the same way Claim Ledger
  freeze materializes them, as local-file sources that must carry reader-facing
  source title/name and `source_category`.
- **Enriched source type rendering**: claim metadata enrichment now mirrors
  imported `source_url` and non-default `source_type` into the Claim Ledger
  fields read by Source Appendix rendering, so non-local imported sources are
  not displayed as default local-file sources.
- **PolicyProfile resolver provenance hardening**: `briefloop new` now treats
  `--industry` as the authoritative deterministic resolver hint before falling
  back to company text, and ReportSpec validation rejects
  `report_pack.default_policy_profile` provenance when the resolved profile
  does not match the pack default.

## [0.10.1] — 2026-06-22

### Added

- **Experimental ReportSpec / ReportPack registry**: added product-layer
  contracts for report type metadata and packaged experimental packs
  (`market_weekly`, `management_monthly`), plus read-only CLI surfaces
  `multi-agent-brief packs list`, `multi-agent-brief packs show <pack_id>`,
  and `multi-agent-brief validate-report-spec <report_spec.yaml>`. These are
  contract/registry surfaces only; they do not create workspaces, run stages,
  render templates, bypass gates, deliver reports, or authorize publication.
- **BriefLoop compatibility aliases**: added `briefloop` as a shell CLI alias
  for `multi-agent-brief`, and `/briefloop` as a Claude writer command alias
  for the existing five-verb `/mabw` surface. The original CLI and `/mabw`
  command remain supported.
- **Experimental product workspace skeletons**: added
  `multi-agent-brief new <report-pack> <workspace>` / `briefloop new
  <report-pack> <workspace>` to create conservative local-first workspaces
  from packaged ReportPacks, including `report_spec.yaml`, workspace config,
  source config, user instructions, and input folders. This is setup only; it
  does not run stages, render templates, deliver reports, approve publication,
  or bypass gates.
- **BriefLoop alias help polish**: `briefloop --help` now displays
  `usage: briefloop` while `multi-agent-brief --help` keeps the stable engine
  CLI name.
- **Experimental ReportTemplate registry and bundle projection**: added
  packaged `market_weekly` and `management_monthly` section-order template
  contracts plus `multi-agent-brief packs templates` and
  `multi-agent-brief packs bundle --workspace <workspace>` for a reproducible
  delivery/audit bundle manifest over finalized workspace artifacts. This is a
  projection surface only; it does not render templates, move artifacts, bypass
  gates, deliver reports, or authorize publication.
- **Experimental PolicyProfile registry**: added a product-layer
  `PolicyProfile` schema/registry with packaged `manufacturing_default`, plus
  ReportPack default binding and optional ReportSpec override validation.
  `validate-report-spec` now reports the resolved policy profile. This records
  deterministic product defaults only; it does not adapt quality gates, change
  runtime behavior, judge industry compliance, decide truth, or authorize
  release.
- **Experimental PolicyProfile skeletons**: added conservative
  `finance_default` and `internet_default` profile skeletons alongside
  `manufacturing_default`. These are public-safe product defaults only; they do
  not provide finance compliance judgment, investment-advice detection, internet
  rumor verification, source authority, gate adaptation, or release authority.
- **PolicyProfile projection visibility**: `status --json` / human-readable
  status and generated runtime handoff artifacts now surface the resolved
  PolicyProfile id, source, hash, and compact product-policy summary when a
  workspace has `report_spec.yaml`. This is traceability for product metadata
  only; it does not judge compliance or truth, bypass the control spine, or
  authorize release.
- **PolicyProfile deterministic gate adapter**: resolved PolicyProfiles can
  tighten existing deterministic quality-gate strictness and reader-final
  forbidden-phrase checks. This is a limited adapter over existing gates, not a
  second gate engine, semantic support assessment, industry compliance
  judgment, truth proof, release authority, delivery override flag, or
  force-deliver path.
- **PolicyProfile dogfood fixtures**: added public-safe synthetic fixtures for
  resolved profile projection, deterministic gate-adapter strictness, and
  reader-final forbidden-phrase checks. These fixtures do not establish
  industry compliance, investment-advice detection, rumor verification,
  release readiness, truth proof, or report quality claims.

## [0.9.4] — 2026-06-22

### Added

- **Experimental Semantic Assessment Report schema**: added an optional
  `output/intermediate/semantic_assessment_report.json` contract for auditable
  semantic support assessment proposals over claim atoms and evidence spans.
  This is schema foundation only; it does not judge truth, mutate the
  Claim-Support Matrix, create human adjudication queue items, gate delivery,
  decide release eligibility, or grant support authority.
- **Semantic Assessment Report reference validation**: present Semantic
  Assessment Report artifacts now validate machine-checkable references to
  Claim Ledger claims, Atomic Claim Graph atoms, and Evidence Span Registry
  spans, and require uncertain high-materiality `llm_only` rows to be flagged
  for human adjudication. This remains proposal validation only; it does not
  judge support semantics, write the Claim-Support Matrix, create an
  adjudication queue, or decide release eligibility.
- **Semantic Assessment Report proposal projection**: added a pure helper that
  projects Semantic Assessment Report rows into proposal-only Claim-Support
  Matrix delta candidates after callers have validated the report. The
  projection does not write accepted support rows, create adjudication queue
  items, gate delivery, judge support semantics, or decide release eligibility.
- **Semantic Assessment Report status surface**: `status --json` and the
  human-readable status report now expose read-only proposal counts for present
  valid Semantic Assessment Reports, including `llm_only`, high uncertainty,
  high disagreement, and human-adjudication flags. The human-readable status
  line explicitly labels the surface as `proposal_only`. This does not add
  delivery gates, release authority, adjudication queue items, or accepted
  Claim-Support Matrix writes.
- **Semantic Assessment Report dogfood fixtures**: added public-safe synthetic
  fixtures for direct support, partial/weak support, unsupported proposals,
  assessor disagreement, high uncertainty, unknown references, and
  high-materiality `llm_only` adjudication requirements. These fixtures validate
  the proposal surface only; they do not create support truth, adjudication
  queues, delivery gates, or release authority.

## [0.9.3] — 2026-06-21

### Added

- **Experimental Evidence Span Registry schema**: added an optional
  `output/intermediate/evidence_span_registry.json` contract and runtime
  validation for source-level evidence spans with recomputable raw-excerpt
  hashes. This is schema foundation only and does not perform semantic support
  assessment, Evidence Span support scoring, Claim-Support Matrix generation,
  or support-sufficiency gating.
- **Evidence span source-pack binding**: present Evidence Span Registry
  artifacts now validate that each span points to a durable `input/sources/`
  file and that the declared raw excerpt and optional character offsets match
  the source bytes. This is source-pack binding only; it does not add semantic
  support assessment, Claim-Support Matrix behavior, support-sufficiency gates,
  or source appendix UI.
- **Evidence span archive projection**: finalized run archives now include a
  hash-only Evidence Span Registry projection when a present registry is valid,
  including registry bytes, archived source-pack paths, source file hashes,
  source sizes, span IDs, raw-excerpt hashes, and offsets. Invalid registries
  are recorded as invalid without span/source projection. This is archive
  reproducibility only; it does not add semantic support assessment,
  Claim-Support Matrix behavior, support-sufficiency gates, or source appendix
  UI.
- **Evidence span source appendix trace view**: finalize now adds reader-safe
  Evidence Span summary counts to the Source Appendix when a present registry is
  valid, and writes raw span details only to `output/source_appendix_trace.md`
  as an audit copy. This does not add semantic support assessment,
  Claim-Support Matrix behavior, support-sufficiency gates, or a delivery
  artifact.
- **Experimental Claim-Support Matrix schema**: added an optional
  `output/intermediate/claim_support_matrix.json` contract and runtime
  schema validation for atom-to-evidence-span support records. This is schema
  and vocabulary foundation only; it does not assess support, validate
  cross-artifact references, route repairs, add gates, decide release
  eligibility, or claim support sufficiency.
- **Claim-Support Matrix policy projection helper**: added a pure deterministic
  helper that projects explicit matrix rows into atom-level policy signals such
  as blocking rows, weak support, downgrade requirements, adjudication
  requirements, and inference-framing requirements. This does not assess
  semantic support, write workspace state, add gates/status integration, or
  decide release eligibility.
- **Claim-Support Matrix cross-artifact validation**: present matrices now
  validate claim, atom, and evidence-span references against sibling Claim
  Ledger, Atomic Claim Graph, and Evidence Span Registry artifacts, and require
  high-materiality atoms to have explicit support rows. Missing matrices remain
  optional; this does not assess semantic support, add gates/status
  integration, or decide release eligibility.
- **Claim-Support Matrix gate/status projection**: present valid matrices now
  project explicit atom-level support records into quality-gate findings and
  read-only status summaries. Missing or invalid matrices remain non-blocking;
  this does not assess semantic support, prove truth, or decide release
  eligibility.

### Changed

- **Claim-Support Matrix public documentation alignment**: updated README,
  support matrix, architecture status, and operator-skill references to describe
  the current experimental support-record control plane: schema validation,
  cross-artifact validation, and gate/status projection from explicit rows. This
  remains separate from semantic support assessment, truth proof, release
  eligibility, or support-sufficiency gates.

## [0.9.1] — 2026-06-20

### Added

- **Experimental Atomic Claim Graph schema**: added an optional
  `output/intermediate/atomic_claim_graph.json` contract and runtime validation
  for structured atomic decomposition of Claim Ledger claims. This is a schema
  foundation only and does not perform semantic atomization, evidence-span
  extraction, claim-support scoring, or support-sufficiency gating.
- **Atomic Claim Graph coverage/type validation**: present
  `atomic_claim_graph.json` artifacts now receive deterministic whole-ledger
  coverage and Claim Ledger type-consistency checks. The graph remains optional
  and this does not perform semantic atomization or support-sufficiency
  assessment.
- **Analyst/Editor Atomic Claim Graph boundary**: Analyst and Editor contracts
  now treat present `atomic_claim_graph.json` files as optional experimental
  decomposition aids only. The Claim Ledger remains the factual evidence base;
  this adds no no-new-atom checker, gate, CLI, or support-sufficiency claim.
- **Atomic reader residue and coverage projection**: present valid Atomic Claim
  Graphs now produce deterministic reader-text projection metadata for atom ID
  residue and Claim Ledger citation coverage. The quality-gate projection is
  warning-only; reader-final residue checks remain blocking for delivery output.
  This does not perform semantic matching or support-sufficiency assessment.

## [0.9.0] — 2026-06-19

### Added

- **BriefLoop public project name**: introduced BriefLoop as the public
  project-facing name for the v0.9 compatibility period.
- **Naming and compatibility policy**: added `docs/briefloop-naming.md` to
  define BriefLoop, brief-loop engineering, the reserved BriefCI technical
  sub-layer, and the MABW compatibility surface.
- **Brief-loop engineering explainer**: added
  `docs/brief-loop-engineering.md` to define the failure -> finding -> repair
  -> regression -> human review -> release decision loop.

### Changed

- **Public framing**: README, documentation index, support matrix, architecture
  status, red lines, and roadmap now describe BriefLoop as the public name and
  MABW as the implementation lineage / compatibility surface.
- **v0.9 roadmap direction**: changed the public v0.9 direction from
  distribution/reference workflows to support sufficiency and brief-loop
  engineering.

### Compatibility

- No runtime surface was renamed in v0.9.0. The `multi-agent-brief` CLI,
  `/mabw` commands, `multi_agent_brief` Python package/module path,
  `multi-agent-brief-workflow` distribution name, workspace formats, artifact
  names, and MABW experiment IDs remain compatible.

### Boundaries

- v0.9.0 is a brand/public-framing preview release. It does not implement
  Atomic Claim Graph, Evidence Span Registry, Claim-Support Matrix, semantic
  proof, automatic hallucination elimination, autonomous repair, or
  ready-to-send output guarantees.

## [0.8.6] — 2026-06-19

### Added

- **Auditable-brief assessment target**: MABW-080 now supports
  `assessment_target=auditable_brief`, allowing content-level experiment runs
  to stop at the frozen audited brief, audit report, auditor gate report, and
  auditor-complete boundary instead of requiring finalize, delivery,
  reader-clean, DOCX/PDF, or delivery archive artifacts.
- **Python-owned auditable target contract**: status, register-run, score-run,
  and downstream guards now project auditable target readiness from workflow
  state, artifact hashes, auditor gate results, run integrity, audit binding,
  and event-log evidence instead of workspace prose.
- **Python-owned audit binding for auditable runs**: auditor completion records
  bind the frozen Claim Ledger, audited brief, audit report, auditor gate
  report, relevant repair transactions, and current-run auditor completion
  event.
- **Treatment-isolation projection for MABW-080**: baseline, memory, and
  prompt-only conditions now have machine-checkable visibility boundaries:
  baseline cannot see guidance material, memory receives guidance only through
  the approved Improvement Memory snapshot, and prompt-only receives guidance
  only through the explicit prompt guidance block.
- **Condition-blind assessment packs**: MABW-080 can export blind audited-brief
  packs and import assessments through a reveal mapping that binds blind item
  IDs, audited-brief hashes, scorecard hashes, condition identity, run IDs, and
  guidance entry IDs.
- **Unsupported strategic implication warning**: quality gates can emit a
  warning-only `unsupported_strategic_implication` finding for strategic demand,
  procurement, municipal-buyer, policy-demand, or partnership language that is
  not lexically supported by the frozen Claim Ledger.

### Changed

- **Formal summary denominator hardened**: `experiments 080 summarize` now
  separates raw observations from formal interpretable metrics and excludes
  scorecards that fail control, treatment-isolation, audit-binding,
  blind-assessment, or hash-bound readiness checks.
- **Auditable target handoff and finalize behavior hardened**: when
  `assessment_target=auditable_brief` is complete, runtime guidance and CLI
  guards direct operators to register, score, and export assessment artifacts
  instead of continuing to finalize or delivery.
- **Repair invalidation made stricter**: owner-stage repairs now stale
  downstream artifacts until the proper producer reruns, and stale repair
  baselines are derived from repair-time metadata rather than mutable refreshed
  registry hashes.
- **Gate and status projections made target-aware**: status output no longer
  reports auditable target completion from stale clean workflow state, missing
  repair events, incomplete audit bindings, or contradictory gate reports.
- **Blind-pack artifact discovery bounded**: `export-blind-pack` checks direct
  artifact candidates before recursive discovery and limits recursive lookup to
  explicit workspace roots.

### Fixed

- Prevented formal MABW-080 metrics from trusting self-declared blind metadata
  without rechecking current scorecard and target-artifact hashes.
- Prevented refreshed artifact-registry hashes from being treated as stale
  repair baselines when downstream artifacts did not exist at repair start.
- Prevented incomplete or contradictory auditable target projections from
  suggesting delivery/finalize paths.

### Boundaries

- v0.8.6 is A-controlled readiness hardening for a future formal MABW-090
  rerun. It is not proof that Improvement Memory improves output quality.
- `auditable_brief` evidence is internal auditable-draft evidence. It is not a
  management-ready delivery claim and does not cover reader-clean, DOCX/PDF, or
  final delivery quality.
- Python validates hashes, schema, event-log bindings, target readiness,
  treatment isolation, and imported assessment structure. Python still does not
  judge prose quality, semantic manifestation, factual regression, strategic
  soundness, or output quality.
- Contaminated, stale, unbound, non-blind, or treatment-leaking runs may remain
  useful as failure evidence, but must not enter the formal interpretable
  denominator.

## [0.8.5] — 2026-06-16

### Added

- **Delivery snapshot convenience copies**: `finalize` still refreshes `output/delivery/` as the latest reader surface, and now also writes reader-facing copies under `output/delivery-history/<run_id-or-timestamp>/` before the authoritative run archive is created by `state finalize-complete`.
- **MABW-080 deterministic scorecard draft builder**: `experiments 080 score-run` can build scorecard metadata from a registered run, case definition, and available archive/control projections without scoring guidance manifestation or output quality.
- **MABW-080 assessment import**: `experiments 080 import-assessment` can merge externally supplied guidance-manifestation assessment into a scorecard and derive A/B/invalid validity classes from deterministic control fields plus assessment metadata. Python still does not judge prose quality, guidance manifestation, or semantic regression.
- **MABW-080 case summary builder**: `experiments 080 summarize` aggregates
  existing scorecards into deterministic A/B/invalid counts, condition groups,
  manifestation-score counts, reader-clean rates, coverage-delta status, timing
  status, and invalid reasons. It can include explicit `--scorecard` paths when
  scorecards live outside the case directory. It does not judge output quality
  or run workflow stages.
- **MABW-080 condition scaffold**: `experiments 080 scaffold-condition`
  imports the frozen fact layer into initialized baseline/memory/prompt-only
  workspaces and writes operator instructions. It does not create generic
  workspace config, run subagents, gates, finalize, registration, scoring, or
  summarization.
- **MABW-080 public-safe pilot skeleton**: added
  `experiments/080/cases/solar_public_001` with a synthetic frozen fact layer
  seed archive, guidance set, and assessment template. It is setup material, not
  completed A/B evidence or an output-quality claim.

### Boundaries

- v0.8.5 is an MABW-080 experiment harness release. It is not a claim that briefs are better, faster, semantically verified, or model-performance measured.
- **080 pilot observation boundary**: v0.8.5 records pilot-level observation
  that the intended guidance effect is observable: baseline showed weak
  manifestation, memory showed clean manifestation, and prompt-only
  over-applied. This is not treated as A-controlled proof because v0.8.6 still
  needs target-aware completion, Python-owned audit binding, repair invalidation,
  treatment isolation, and condition-blind assessment hardening.
- `score-run` fills deterministic control/readiness metadata only. It does not score guidance manifestation, prose quality, taste, factual regression, or output quality.
- `import-assessment` validates and merges externally supplied assessment metadata. Python does not decide whether guidance manifested.
- Delivery snapshots under `output/delivery-history/` are convenience copies. The immutable control archive remains `state finalize-complete` under `output/runs/<run_id>/`.

## [0.8.4] — 2026-06-16

### Added

- **Deterministic source provider join**: source provider batches now join through a stable ordering and digest helper so provider completion order does not decide dedupe winners or source ordering.
- **Opt-in source provider parallel collection**: parallel-safe source providers can run through an opt-in thread-pool path while unsafe providers remain serial ordering barriers. Joined results still flow through the deterministic source join.
- **Scout chunk join contract**: Scout runtime guidance now treats chunk outputs as scratch material and requires parent-side deterministic joining before workflow artifacts are written. Default topology may join into `candidate_claims.json` and `screened_candidates.json`; strict topology joins Scout output only into `candidate_claims.json`.
- **Quality gate evaluation helper**: deterministic quality gate finding evaluation is now isolated in a read-only helper with helper-level opt-in parallel execution. Report writing, legacy projection updates, and event emission remain single-writer serial transactions.
- **Stage runtime/model provenance**: `state stage-complete` and `state finalize-complete` can record explicit runtime/model values in workflow state and event log metadata as audit provenance only.
- **Owner-stage repair transaction**: deterministic `repair start` / `repair complete` transactions can route repair to the owner stage, record active repair state, restrict allowed artifacts, and keep contaminated runs non-reference-eligible.

### Changed

- **Repair boundaries hardened**: finalized runs cannot be reopened by stale repair reports, disallowed downstream artifact creation is blocked during repair, and no-op repairs are rejected unless a future explicit no-op path is added.
- **Onboarding title mapping fixed**: DOCX heading configuration is now kept separate from onboarding brief titles.

### Boundaries

- v0.8.4 is about safe parallelism foundations and deterministic repair routing. It is not a speed-improvement claim, output-quality claim, model-performance measurement, or semantic support signal.
- `gates check` remains serial by default in the user-facing CLI. Parallel gate evaluation is currently helper-level opt-in infrastructure.
- Scout chunk parallelism is a runtime contract only. MABW does not ship a Python Scout executor, semantic chunk extractor, or worker-output artifact append path.
- Stage runtime/model provenance is recorded only when completion commands are called with explicit values; normal runtime handoffs do not automatically supply it yet.

## [0.8.3] — 2026-06-16

### Added

- **Claim Draft contract**: added experimental `claim_drafts.json` validation for source-grounded draft claims without `claim_id` fields.
- **Claim Ledger freeze transaction**: added `multi-agent-brief state freeze-claim-ledger` so Python assigns deterministic `CL-####` IDs, writes canonical `claim_ledger.json`, records freeze metadata, and emits a `claim_ledger_frozen` event.
- **Claim Ledger completion enforcement**: `state stage-complete --stage claim-ledger` now requires a matching freeze record for the current ledger bytes.
- **Auditor support calibration contract**: Auditor role contracts now explicitly check overstatement, support-strength calibration, confidence mismatch, evidence-relation mismatch, and limitation leakage.

### Changed

- **Claim Ledger role boundary tightened**: Claim Ledger agents now draft `claim_drafts.json` and no longer author canonical `claim_ledger.json`.
- **Analyst/Auditor contracts aligned with frozen ledger semantics**: Analyst and Auditor read frozen `claim_ledger.json`, do not read `claim_drafts.json`, and must not edit the Claim Ledger.
- **Generated runtime assets regenerated**: Claude, Codex, OpenCode, Hermes, and hand-maintained skill text now reflect the Claim Freeze boundary.

### Boundaries

- v0.8.3 does not claim semantic proof, automatic semantic dedupe, output-quality improvement, autonomous repair, or Codex parity.
- Claim IDs are deterministic for the same freeze input under `sorted_sequential_v1`; this is not an incremental ID-stability promise after draft sets change.
- `claim_drafts.json` is a freeze input only. Downstream drafting, auditing, gates, source appendix, and finalize binding continue to use frozen `claim_ledger.json`.

## [0.8.2] — 2026-06-15

### Added

- **Role topology selector**: policy packs can select `default`, `strict`, or `human_assisted` role topology while preserving one canonical stage spec and the same accountable artifacts.
- **Topology-satisfied stage recording**: default topology lets Scout write both `candidate_claims.json` and `screened_candidates.json`, then records Screener as satisfied by topology instead of fabricating an independent Screener execution history. Strict topology remains available for independent screening.
- **Editor-new-fact quality gate**: stage-scoped quality gates now include a soft-by-default `editor_new_fact` check, backed by a Python-written Analyst draft snapshot, that flags editor-introduced numbers, claim references, and simple entity phrases. `--strict` can make those findings blocking.
- **Topology-aware status output**: human `status` output now shows topology-satisfied stages such as `screener complete via scout`, without changing the JSON schema or runtime state.
- **Packaged topology handoff smoke**: CI now verifies package-installed `init`/`run --workspace` handoff behavior for default topology and a strict-topology contract-base override.

### Changed

- **Role source and generated assets aligned with topology**: Scout/Screener and Delivery Editor wording now reflects default/strict topology while keeping Claim Ledger, auditable draft, audit report, gate reports, event log, and delivery artifacts separate.
- **Public docs aligned with topology**: README and support matrix wording now state that the default role assignment is shorter, but the accountability spine is not.
- **Runtime-state decomposition completed for v0.8.2 foundations**: the runtime-state facade now exposes a pinned surface while helpers are split into manifest/workflow, artifact registry, event log, completion gates, and operations modules.
- **Control-surface interpreters guarded**: run integrity, audit binding, quality gate binding, frozen artifact integrity, and stage-completion interpretation now have explicit structural tests to prevent helper drift.
- **Legacy dead code removed**: orphaned connector/model/history/source-map modules and channel stubs were removed without changing the supported runtime path.

### Boundaries

- Role topology convergence is not a speed-improvement claim and does not remove Claim Ledger, gate reports, audit report, event log, archive, or human-triggered delivery.
- `editor_new_fact` is deterministic lexical detection, not semantic proof that every edit is supported.
- The packaged topology smoke tests runtime handoff construction only. It does not bundle source-clone runtime kits or promote packaged `runtime install` beyond the existing support matrix.

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

- v0.7.3 is a release-hardening patch over v0.7.2. It does not add new autonomous learning, role topology changes, output-quality scoring, public raw trace packs, or benchmark claims. The repo includes experiment/evaluation harnesses and public evaluation packets; these are measurement infrastructure, not a benchmark claim.

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
