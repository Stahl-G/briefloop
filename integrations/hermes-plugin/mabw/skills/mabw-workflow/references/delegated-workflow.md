# Delegated Workflow

After `mabw_run_handoff` writes `agent_handoff.md`, continue in Hermes as the Orchestrator main agent.

Read these contract references before delegation:

- `configs/orchestrator_contract.yaml`
- `configs/stage_specs.yaml`
- `configs/artifact_contracts.yaml`
- `configs/policy_packs/default.yaml`

Control loop:

```text
Read workspace context -> read contract references -> identify the next stage -> delegate a specialist or Python tool -> check the expected artifact -> decide continue / retry_stage / delegate_repair / request_human_review / block_run / finalize.
```

## Sequence

```text
doctor
→ source discovery when configured
→ input governance when available
→ scout
→ screener
→ claim-ledger
→ analyst
→ editor
→ auditor
→ finalize
```

## Artifact Handoff

Each step should check the expected artifact path before selecting the next decision.

- scout writes `output/intermediate/candidate_claims.json`
- screener writes `output/intermediate/screened_candidates.json`
- claim-ledger writes `output/intermediate/claim_ledger.json`
- analyst writes `output/intermediate/audited_brief.md`
- editor updates `output/intermediate/audited_brief.md`
- auditor writes `output/intermediate/audit_report.json`
- finalize writes `output/brief.md` and configured rendered outputs

If audit findings or human feedback exist, use `multi-agent-brief feedback ingest`, `feedback plan`, `feedback resolve`, `feedback show --json`, and `feedback validate` to structure issues and create a bounded repair plan. These commands do not execute repair or edit brief artifacts.
