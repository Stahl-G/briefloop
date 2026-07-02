# Control Record Map

Read this when deciding whether a file may be edited, inspected, or used as
evidence.

## Python-Owned Control Files

Do not edit directly:

- `output/intermediate/runtime_manifest.json`
- `output/intermediate/workflow_state.json`
- `output/intermediate/artifact_registry.json`
- `output/intermediate/event_log.jsonl`
- `output/intermediate/gates/*_quality_gate_report.json`
- `output/intermediate/quality_gate_report.json`
- `output/intermediate/claim_ledger.json`
- `output/intermediate/improvement_memory_snapshot.md`
- `output/intermediate/human_approval_ledger.json`
- `output/intermediate/release_readiness_report.json`
- `output/intermediate/quality_panel.json`
- `output/intermediate/quality_summary.md`
- `output/intermediate/quality_panel.html`
- `output/intermediate/guidance_manifestation_report.json`
- `output/runs/<run_id>/`

Use the owning CLI transaction instead.

Owning commands for recent control-tool projections:

- `briefloop quality summarize --workspace <workspace>` writes
  `quality_panel.json`, source-bound `quality_summary.md`, and static
  `quality_panel.html`.
- `multi-agent-brief approval init` and `multi-agent-brief approval record`
  write `human_approval_ledger.json` with event-log linkage.
- `multi-agent-brief release check` reads `human_approval_ledger.json` and
  writes a fresh `release_readiness_report.json` with event-log linkage and
  configured `branding_context` metadata. Do not treat a readiness report as
  refreshed merely because an approval was recorded.
- `guidance_manifestation_report.json` is an imported/human diagnostic record
  for approved guidance manifestation labels; it is validated and surfaced by
  status / Quality Panel, but it is not an Improvement Memory writer and not a
  gate or release artifact.
- Materiality Selection is a status / Quality Panel projection derived from
  existing `screened_candidates.json`, PolicyProfile materiality terms, and
  workspace focus terms. It has no standalone control file and must not be
  patched into screening output, Claim Ledger, gates, delivery, or release
  records.
- Support-Calibrated Wording is a status / Quality Panel projection derived
  from existing reader Markdown, Claim Ledger metadata, source taxonomy, and
  valid Claim-Support Matrix policy signals. It has no standalone control file,
  does not create accepted support rows, and must not be patched into gates,
  delivery, or release records.

These files are operator/audit projections or approval records. They are not
agent draft surfaces, not final reader content, and not repair shortcuts.

## Agent-Owned Draft Surfaces

Agents may write only before the owning completion transaction freezes them:

- Scout: `candidate_claims.json` and, in default topology, `screened_candidates.json`
- Claim Ledger: `claim_drafts.json`
- Analyst: working `audited_brief.md`
- Editor: final auditable `audited_brief.md`
- Auditor: `audit_report.json`

After freeze, use owner-stage repair.

## Human-Owned Decisions

Human approval owns:

- Improvement Ledger approval/rejection/revert decisions
- delivery intent
- internal release-mode approval decisions recorded through
  `approval init` / `approval record`
- external assessment files
- semantic judgment that Python cannot deterministically validate
