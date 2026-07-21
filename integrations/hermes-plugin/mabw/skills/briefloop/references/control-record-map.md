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
- `output/intermediate/semantic_support_acceptance_ledger.json`
- `output/intermediate/quality_panel.json`
- `output/intermediate/quality_summary.md`
- `output/intermediate/quality_panel.html`
- `output/intermediate/evidence_extract_source_lock.json`
- `output/intermediate/evidence_extract_page_inventory.json`
- `output/runs/<run_id>/`

Use the owning CLI transaction instead.

Owning commands for recent control-tool projections:

- `briefloop quality summarize --workspace <workspace>` writes
  `quality_panel.json`, source-bound `quality_summary.md`, and static
  `quality_panel.html`. `finalize_report.json` and `status --json` may
  recommend this post-finalize closeout, but they do not write these artifacts
  automatically.
- `briefloop approval init` and `briefloop approval record`
  write `human_approval_ledger.json` with event-log linkage.
- `briefloop release check` reads `human_approval_ledger.json` and
  writes a fresh `release_readiness_report.json` with event-log linkage and
  configured `branding_context` metadata. Do not treat a readiness report as
  refreshed merely because an approval was recorded.
- `briefloop semantic-support bind --workspace <workspace>` seals
  `semantic_assessment_report.json` checked-input hashes after the auditor writes
  the report and before human adjudication. This binding is trace metadata only,
  not support truth.
- `briefloop semantic-support adjudicate` writes
  `semantic_support_acceptance_ledger.json` with event-log linkage for human
  accept/reject decisions on valid, fresh, checked-input-bound Semantic
  Assessment Report proposal rows. It records adjudication only; it does not
  write Claim-Support Matrix rows, gate reports, workflow state, repair routes,
  delivery state, or release state.
- `briefloop finalize` writes `finalize_report.json` (including
  `delivery_promotion`), the single delivery-truth
  record: staged-candidate reader projection results, `delivery_artifacts`,
  their SHA-256 hashes, promotion status, reader-clean results, and the
  resolved citation profile (`executive`, `analyst`, or `audit`). `deliver`,
  `state finalize-complete`, and the Store-native status projection verify
  delivery against this record. There is no separate delivery manifest; do not create a
  second record carrying delivery artifacts or hashes, and do not patch this
  one by hand.
- `briefloop status --workspace <workspace> --json` is the only canonical
  reader of delivery truth: the Store-native status projection reports
  receipt-bound `terminal_state`, `package_ready`, and `delivered` fields
  (`projection_source` carries `store_revision` and receipt ids), and
  `briefloop runtime next --workspace <workspace>` reports workflow
  progression truth. Both are read-only; adapters must not substitute their
  own delivery reconstruction for them. The legacy completion projection /
  `workbuddy diagnose` surface is retired.
- `briefloop extract` / `briefloop extract` writes
  `evidence_extract_source_lock.json`, `evidence_extract_page_inventory.json`,
  and audit copies for `document-review` / `evidence_extract` workspaces. The
  lock binds registered `input/sources/evidence_extract/` files to file size
  and SHA-256. If a PDF/binary source already has an adjacent MinerU-derived
  Markdown representation, the lock also binds that `.mineru.md` file and the
  inventory/span registry use the derived text while the original source bytes
  remain the root evidence object. PDF/binary files without derived Markdown are
  still registered-only and require explicit extraction. These artifacts are
  not automatic MinerU execution, rendered-page visual checks, evidence
  ledgers, support judgments, citation gates, or delivery approvals.

- Materiality Selection is a status / Quality Panel projection derived from
  existing `screened_candidates.json`, PolicyProfile materiality terms, and
  workspace focus terms. It has no standalone control file and must not be
  patched into screening output, Claim Ledger, gates, delivery, or release
  records.


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
