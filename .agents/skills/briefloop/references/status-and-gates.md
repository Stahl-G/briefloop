# Status, Gates, Finalize, And Delivery

Read this before interpreting `status`, running gates, finalizing, or delivering.

## Status

`briefloop status --workspace <workspace>` is read-only. Use it first
when the user asks "where is this run?" or "what should happen next?"

Status can show:

- active repair
- run integrity and reference eligibility
- topology-satisfied stages
- `auditable_brief` target complete or incomplete
- ReportPack, PolicyProfile, ReportTemplate, source-evidence, release-mode, and
  Quality Panel / Trajectory Regulation / Materiality Selection /
  Reader Template Conformance projections when present
- next suggested command

Projection status is not authority by itself. Invalid optional artifacts must
not become support, release, gate, or delivery authority.

Trajectory Regulation is read-only. It derives retry, repair-cycle, and
repeated-blocker counts from `workflow_state.json` and `event_log.jsonl`, then
may suggest `request_human_review` or `block_run` as operator actions. It does
not write workflow state, start repair, run gates, execute repair, approve
delivery, or decide release readiness.

Materiality Selection is diagnostic-only. It reads valid
`screened_candidates.json`, resolved PolicyProfile `materiality_terms`, and
workspace focus terms to surface excluded or deprioritized candidates that
matched explicit materiality/focus terms after capacity or scope screening.
Treat it as operator review guidance only. It does not infer semantic
importance, mutate screening output, resurrect candidates, alter the Claim
Ledger, run gates, approve delivery, decide release readiness, or prove output
quality.

Reader Template Conformance is warning-only. It reads resolved ReportTemplate
`reader_contract` metadata and existing finalized reader Markdown to surface
missing reader blocks, overlong executive summaries, missing Markdown table
slots, and Source Appendix position warnings. It can appear in status, handoff,
`finalize_report.json`, and Quality Panel. It does not rewrite content, invent
sections, parse DOCX content, run gates, block delivery, approve release, score
prose quality, or prove semantic correctness.

Citation profiles split reader and audit citation surfaces. A ReportTemplate
may declare `reader_contract.citation_profile` as `executive`, `analyst`, or
`audit`; finalize reports and bundle manifests record the resolved profile.
Reader delivery must stay reader-safe and must not expose Claim Ledger IDs,
span IDs, local paths, or hashes. Audit bundles keep trace artifacts when
present. Citation profiles do not prove support, relax gates, remove audit
trace, approve delivery, decide release readiness, or create a quality score.

Semantic support proposal adjudication is human-owned and event-recorded. After
the auditor writes `semantic_assessment_report.json`, run
`semantic-support bind --workspace <workspace>` before any human adjudication to
seal the report's checked-input hashes. Then use
`semantic-support adjudicate --workspace <workspace> --proposal-id <id>` to
record an explicit human accept/reject decision for a valid, fresh, bound
proposal row. Adjudication writes `semantic_support_acceptance_ledger.json` and
an event-log record only; it does not edit the Semantic Assessment Report, write
Claim-Support Matrix rows, route repair, run gates, block or approve delivery,
authorize release, or prove truth. If a later repair changes audited inputs,
the bound report becomes stale; rerun the auditor and bind the new report before
adjudicating again.

## Gates

`gates check` writes stage-scoped gate reports. It is not a read-only helper.
Do not rerun a frozen stage-scoped gate report unless the runtime permits it
through the proper rerun/repair path.

Blocking findings stop stage completion. Warning-only findings are still
evidence and should be reported, but they are not Python proof of semantic
failure.

Coverage/omission findings are deterministic continuity checks over valid
`screened_candidates.json`, Claim Ledger metadata, and cited brief references.
They detect high-priority selected screened candidates that disappear without an
explicit limitation or omission reason. They are not full-world recall checks,
semantic support proof, or source-discovery completeness claims.
Stage-scoped gate reports must include `coverage_omission`, `material_fact`,
`freshness`, and `target_relevance` results before auditor/finalize completion
can accept them.

Final abstract quality findings are warning-only deterministic pattern
surfaces. They flag scope/title, comparison-basis, limitation, key-case, and
superlative risks; they do not score prose, prove quality, approve delivery, or
create repair routes or release authority.

Legacy `output/intermediate/quality_gate_report.json` is a latest/compatibility
projection. Stage-scoped gate authority lives under
`output/intermediate/gates/*_quality_gate_report.json`.

## Quality Panel And Summary

`quality_panel.json`, `quality_summary.md`, and `quality_panel.html` are
experimental product-quality audit/control projections.

- Write them with `briefloop quality summarize --workspace <workspace>`.
- After successful finalize, `finalize_report.json` and `status --json` may
  project `quality_panel_closeout` as a post-finalize recommendation to run
  `briefloop quality summarize --workspace <workspace>`. This recommendation
  may be prioritized as the status suggested next command before delivery. It
  is not a gate, delivery blocker, delivery approval, release approval, or
  automatic writer.
- `quality_summary.md` and `quality_panel.html` must be rendered from the
  sibling `quality_panel.json` and carry its SHA-256 binding.
- They may be included in the audit bundle when valid. They remain excluded from reader-facing delivery bundles.
- They do not run gates, replace gate reports, create a quality score, repair
  artifacts, approve delivery, decide release eligibility, or prove truth.
- If stale or hand-edited, rerun `briefloop quality summarize`; do not patch
  them manually.

## Release Readiness

`approval init`, `approval record`, and `release check` write internal
release-mode approval records.

- Approval ledger records must be scoped to the current run and linked to
  matching event-log entries.
- The control artifacts are `human_approval_ledger.json` and
  `release_readiness_report.json`.
- When `config.yaml` declares `release.branding.required: true`,
  `release_readiness_report.json` also projects `branding_context` and blocks
  internal readiness if institution branding or institution-use authorization
  metadata is missing or explicitly unauthorized.
- Branding status and exact branding blockers must match the recorded
  `release_readiness_checked` event; do not hand-edit readiness reports.
- `release_readiness_report.json` is an internal readiness projection, not an
  external publication authorization.
- Missing approvals are a human-review gap, not a gate bypass request.

## Auditable Target

For `assessment_target=auditable_brief`:

- auditor gate must pass before auditor stage completion
- target completion blocks finalize and delivery
- incomplete target blocks downstream reader-facing outputs
- next path is experiment registration/scoring/assessment, not reader delivery

## Completion And Delivery Truth

Finalize writes a single authoritative delivery record: `finalize_report.json`.
Read delivery truth only through the Store-native status projection, never by
reconstructing it from files.

- Finalize is a transactional reader projection: it renders and checks a
  staged candidate first, and only successful reader-clean promotes
  `output/brief.md` and `output/delivery/`. A failed reader-clean writes a
  failed finalize report and leaves any prior delivery bundle unchanged.
- Successful promotion records `delivery_artifacts`, their SHA-256 hashes, and
  `delivery_promotion: "promoted"` in `finalize_report.json`. `deliver` and
  `state finalize-complete` verify those recorded artifacts; run them only
  after promotion, never against unpromoted output.
- The only canonical reader of delivery truth is the Store-native status
  projection `briefloop status --workspace <workspace> --json`. Its
  `terminal_state`, `package_ready`, and `delivered` fields are receipt-bound:
  `projection_source` carries the `store_revision` and the receipt ids of the
  transactions it projects. Workflow progression truth comes from
  `briefloop runtime next --workspace <workspace>`. Runtime adapters format
  these projections and must not reconstruct delivery truth from
  `workflow_state.json`, `event_log.jsonl`, projection files, or file
  existence. The legacy completion projection / `workbuddy diagnose` surface
  is retired; do not route agents to it.
- `package_ready=true` means the current run's reader package is ready for a
  delivery decision; it is not an action outcome. Claim completed delivery
  only when the status projection reports `delivered=true` for the current
  run. A `draft_created` terminal state is a draft outcome, not delivery
  success.
- After a `repair supersede-stage` recovery, the status projection and the
  generated handoff keep reporting the contamination and the required
  downstream reruns; delivery may become valid again after reruns, but the
  run stays `reference_eligible=false`.
- Recovery progress comes only from `recovery_state.status` and
  `recovery_state.recommended_recovery_action`. Do not derive it from
  `run_integrity`: a recovered run remains contaminated and non-reference even
  after the recovery state reaches `completed_non_reference`.

## Delivery Target

For `assessment_target=delivery_brief` or normal workspaces:

- finalize renders reader-facing files through the staged-candidate promotion
  above
- `state finalize-complete` writes the authoritative run archive
- delivery remains human-triggered and gated
- non-reference-eligible delivery may be useful locally, but it is not clean
  reference evidence
- Gmail delivery currently means `--target gmail --channel draft` or
  `--target gmail --channel send` through the optional `gws` CLI. The `send`
  path is an explicit external email side effect; it does not approve delivery,
  authorize publication, prove semantic truth, or attach audit/control
  artifacts.
