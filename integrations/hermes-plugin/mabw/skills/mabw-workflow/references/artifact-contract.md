# MABW Artifact Contract

## Required Inputs

- `config.yaml`
- `sources.yaml`
- `user.md`
- `input/`

## Intermediate Outputs

- `output/intermediate/agent_handoff.md`
- `output/intermediate/agent_handoff.json`
- `output/intermediate/runtime_manifest.json`
- `output/intermediate/workflow_state.json`
- `output/intermediate/artifact_registry.json`
- `output/intermediate/event_log.jsonl`
- `output/intermediate/candidate_claims.json`
- `output/intermediate/screened_candidates.json`
- `output/intermediate/claim_ledger.json`
- `output/intermediate/audited_brief.md`
- `output/intermediate/audit_report.json`

## Optional Feedback Control Files

These files are created or updated only by `multi-agent-brief feedback ingest`, `feedback plan`, and `feedback resolve`.

- `output/intermediate/feedback_issues.json`
- `output/intermediate/repair_plan.json`
- `output/intermediate/delta_audit_report.json`

## Final Outputs

- `output/brief.md`
- configured named Markdown output
- `output/brief.docx` when configured
