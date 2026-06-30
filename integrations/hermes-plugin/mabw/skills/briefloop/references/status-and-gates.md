# Status, Gates, Finalize, And Delivery

Read this before interpreting `status`, running gates, finalizing, or delivering.

## Status

`multi-agent-brief status --workspace <workspace>` is read-only. Use it first
when the user asks "where is this run?" or "what should happen next?"

Status can show:

- active repair
- run integrity and reference eligibility
- topology-satisfied stages
- `auditable_brief` target complete or incomplete
- ReportPack, PolicyProfile, ReportTemplate, source-evidence, release-mode, and
  Quality Panel projections when present
- next suggested command

Projection status is not authority by itself. Invalid optional artifacts must
not become support, release, gate, or delivery authority.

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
- `quality_summary.md` and `quality_panel.html` must be rendered from the
  sibling `quality_panel.json` and carry its SHA-256 binding.
- They may be included in the audit bundle when valid.
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
- `release_readiness_report.json` is an internal readiness projection, not an
  external publication authorization.
- Missing approvals are a human-review gap, not a gate bypass request.

## Auditable Target

For `assessment_target=auditable_brief`:

- auditor gate must pass before auditor stage completion
- target completion blocks finalize and delivery
- incomplete target blocks downstream reader-facing outputs
- next path is experiment registration/scoring/assessment, not reader delivery

## Delivery Target

For `assessment_target=delivery_brief` or normal workspaces:

- finalize renders reader-facing files
- `state finalize-complete` writes the authoritative run archive
- delivery remains human-triggered and gated
- non-reference-eligible delivery may be useful locally, but it is not clean
  reference evidence
