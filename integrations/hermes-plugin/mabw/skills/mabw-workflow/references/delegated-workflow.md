# Delegated Workflow

After `mabw_run_handoff` writes `agent_handoff.md`, continue in Hermes as the Orchestrator main agent.

Read these contract references before delegation:

- `configs/orchestrator_contract.yaml`
- `configs/stage_specs.yaml`
- `configs/artifact_contracts.yaml`
- `configs/policy_packs/default.yaml`

Read `output/intermediate/audience_profile_snapshot.md` once at run start and
summarize the relevant taste guidance for delegated roles. The snapshot is
runtime context only; do not treat `audience_profile.md` as source evidence,
an artifact contract, a gate, or a provenance proof.

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

## Before Finalize Gate Path

After `audit_report.json` exists, run quality gates and refresh runtime state before selecting the finalize path:

```bash
multi-agent-brief gates check --workspace <workspace>
multi-agent-brief state check --workspace <workspace> --strict
multi-agent-brief state decide --workspace <workspace> --stage auditor --decision continue --reason "Audit and quality gates passed."
```

If `state check` reports blocking quality gate findings, choose `delegate_repair`, `request_human_review`, or `block_run` instead of finalizing. `multi-agent-brief finalize` only renders reader-facing outputs; it is not a quality-gate executor.

If audit findings or human feedback exist, use `multi-agent-brief feedback ingest`, `feedback plan`, `feedback resolve`, `feedback show --json`, and `feedback validate` to structure issues and create a bounded repair plan. These commands do not execute repair or edit brief artifacts.

When material-fact, freshness, or target-relevance gates are required, use `multi-agent-brief gates check`, `gates show --json`, and `gates validate` to create and inspect `output/intermediate/quality_gate_report.json`. Gate checks may block unsafe current-stage continue/finalize decisions, but repair ownership is still routed explicitly by the Orchestrator. Gate checks do not live-fetch sources, execute repair, or automatically create feedback issues; route failed gates into feedback explicitly when repair planning is needed.

## Optional Provenance Projection

After runtime state exists, use `multi-agent-brief provenance build`, `provenance show --json`, and `provenance validate` when an audit/debug view of run, stage, event, artifact, claim, source-reference, feedback, repair, and gate-finding connections is useful. This writes `output/intermediate/provenance_graph.json`.

Provenance projection is derived from existing control files. It is not semantic truth verification, not workflow replay, not repair execution, and not required before `finalize`.
