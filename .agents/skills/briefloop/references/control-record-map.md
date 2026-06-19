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
- `output/runs/<run_id>/`

Use the owning CLI transaction instead.

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
- external assessment files
- semantic judgment that Python cannot deterministically validate
