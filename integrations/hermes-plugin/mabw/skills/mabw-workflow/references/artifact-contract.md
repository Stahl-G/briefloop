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

## Optional Quality Gate Control Files

This file is created only by `multi-agent-brief gates check`.

- `output/intermediate/quality_gate_report.json`

## Optional Provenance Projection Files

This file is created only by `multi-agent-brief provenance build`.

- `output/intermediate/provenance_graph.json`

## Audience Runtime Context Files

These files are runtime context, not workflow artifacts, source evidence,
quality gates, feedback controls, or provenance projection inputs.

- `audience_profile.md`
- `output/intermediate/audience_profile_snapshot.md`

The Orchestrator reads the frozen snapshot at run start and passes a concise
taste summary to delegated roles. Mid-run edits to `audience_profile.md` apply
to the next run.

## Orchestrator Control Switchboard Files

These files are runtime control context, not workflow artifacts, source
evidence, feedback controls, quality gates, or provenance projection inputs.
Selections record Orchestrator intent only; selection is not execution.

- `output/intermediate/orchestrator_control_switchboard.json`
- `output/intermediate/control_selections.json`

## Final Outputs

- `output/brief.md`
- configured named Markdown output
- `output/brief.docx` when configured
