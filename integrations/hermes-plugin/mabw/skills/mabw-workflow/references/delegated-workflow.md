# Delegated Workflow

After `mabw_run_handoff` writes `agent_handoff.md`, continue in Hermes as the parent agent.

## Sequence

```text
source discovery when configured
→ doctor
→ scout
→ screener
→ claim-ledger
→ analyst
→ editor
→ auditor
→ finalize
```

## Artifact Handoff

Each step should check the expected artifact path before continuing.

- scout writes `output/intermediate/candidate_claims.json`
- screener writes `output/intermediate/screened_candidates.json`
- claim-ledger writes `output/intermediate/claim_ledger.json`
- analyst writes `output/intermediate/audited_brief.md`
- editor updates `output/intermediate/audited_brief.md`
- auditor writes `output/intermediate/audit_report.json`
- finalize writes `output/brief.md` and configured rendered outputs
