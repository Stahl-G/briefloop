# Runtime Recipes

Runtime recipes describe how an external Orchestrator may assign existing MABW
stages to runtime agents or humans. They are not Python workflows and they do
not add new artifact contracts.

Every recipe starts after:

```bash
multi-agent-brief run --workspace <workspace>
```

The run command creates the runtime handoff, state files, audience snapshot, and
control switchboard. A recipe may compress role assignment, but it must preserve
the required accountable artifacts.

## Full Subagent Workflow

Use when the selected runtime can delegate specialist roles.

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
→ gates/state review
→ finalize
```

Expected runtime roles:

- scout
- screener
- claim-ledger
- analyst
- editor
- auditor
- formatter

Python remains the tool, validator, audit, control, and rendering layer. The
runtime Orchestrator decides when to delegate roles and when to run CLI tools.

## Compact Human-Assisted Workflow

Use when a user has one runtime agent, weak-model assistance, or a human in the
loop.

The compact workflow compresses role assignment:

```text
run handoff
→ one runtime agent or human-assisted agent prepares candidate/screened claims
→ same agent or human-assisted pass creates claim_ledger.json
→ same agent drafts/edits audited_brief.md with [src:CLAIM_ID]
→ audit/gates/state review
→ finalize
```

Required invariant:

```text
same required artifacts, fewer runtime roles
```

Minimum accountable artifacts:

```text
output/intermediate/candidate_claims.json
output/intermediate/screened_candidates.json
output/intermediate/claim_ledger.json
output/intermediate/audited_brief.md
output/intermediate/audit_report.json
output/delivery/brief.md
output/delivery/<named>.docx when DOCX output is configured
output/source_appendix.md as an audit/control copy when configured
```

Allowed compression:

- One runtime agent may perform scout + screener + claim-ledger preparation.
- One runtime agent may perform analyst + editor drafting.
- A human may assist with extracting claims or editing the audited brief.
- Python may validate, audit, gate-check, and finalize.

Not allowed:

- Do not skip `run` or handoff.
- Do not skip the Claim Ledger.
- Do not write the final reader brief directly from input files.
- Do not treat feedback as evidence.
- Do not treat `audience_profile.md` as source evidence.
- Do not finalize without audit, gates, and state review.
- Do not let Python execute scout, analyst, or editor behavior.
- Do not claim compact workflow is quality-equivalent to full specialist
  delegation.

## Fast Rerun Recipe

Use when a workspace already has a frozen fact layer and the operator wants to
rerun the writing/audit side, usually to test approved Improvement Memory
guidance or reader-facing changes.

Create handoff:

```bash
multi-agent-brief run --workspace <workspace> --recipe fast-rerun
```

This recipe is handoff guidance only. It does not make Python execute stages,
skip stages, or generate the brief.

Required frozen artifacts:

```text
output/intermediate/candidate_claims.json
output/intermediate/screened_candidates.json
output/intermediate/claim_ledger.json
```

Runtime Orchestrator behavior:

```text
run handoff
→ state check --strict
→ record pre-analyst state decisions for already-valid frozen artifacts
→ analyst
→ editor
→ auditor
→ gates/state review
→ finalize
```

Allowed:

- Reuse the existing source/candidate/screened/claim-ledger artifacts when they
  are present and valid.
- Start model-backed content work at Analyst.
- Use this recipe for private instrumentation and manifestation reruns.

Not allowed:

- Do not silently fall back to a full run if a frozen required artifact is
  missing or invalid.
- Do not regenerate Scout, Screener, or Claim Ledger outputs unless the operator
  intentionally abandons the fast rerun.
- Do not use this as proof of output-quality improvement.
- Do not expose `improvement/memory.md`; use only the frozen per-run snapshot
  when present.

## Existing Draft Review

Existing-draft review is not a standalone v0.7.0 mode. Treat it as a compact
workflow variant after `run` has created runtime state and handoff artifacts.
The draft may be placed under `input/context/` as reference context or converted
into auditable artifacts by the runtime/human process, but it must not bypass
Claim Ledger, audit, gates, or finalize boundaries.
