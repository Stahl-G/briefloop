# Runtime Recipes

Runtime recipes describe how an external Orchestrator may assign existing MABW
stages to runtime agents or humans. They are not Python workflows and they do
not add new artifact contracts.

Every recipe starts after:

```bash
briefloop run --workspace <workspace>
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

## Operator Runtime Compact Workflow

Use `--runtime operator` when the host does not have a dedicated BriefLoop
runtime adapter such as Hermes, Claude Code, Codex, or OpenCode. This is a
host-agnostic compact operator workflow. It does not assume subagent or delegate
capability. The legacy `--runtime manual` value remains a compatibility alias
for `operator`.

The compact workflow compresses role assignment:

```text
run handoff
→ one operator prepares candidate/screened claims
→ same operator creates claim_ledger.json through the deterministic freeze transaction
→ same operator drafts/edits audited_brief.md with [src:<claim_id>]
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

- One operator may perform scout + screener + claim-ledger preparation.
- One operator may perform analyst + editor drafting.
- If the host provides a real child-agent/delegate tool, the operator may
  delegate the named role.
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
- Do not claim subagents ran unless the host actually provided delegation.
- Do not claim compact workflow is quality-equivalent to full specialist
  delegation.

## Fast Rerun Recipe

Use when a fresh workspace has already imported a complete archived frozen fact
layer with `state import-fact-layer`, and the operator wants to rerun the
writing/audit side against the same evidence.

Product rule:

```text
same frozen evidence, new writing -- verified by hash
```

Import first:

```bash
briefloop state import-fact-layer \
  --workspace <new_workspace> \
  --archive <source_workspace>/output/runs/<run_id>/manifest.json
```

Create handoff:

```bash
briefloop run --workspace <new_workspace> --recipe fast-rerun
```

This recipe is handoff guidance only. It does not import the fact layer by
itself, make Python execute writing stages, or generate the brief.

Required imported fact-layer artifacts:

```text
input/sources/*
output/input_classification.json
output/intermediate/candidate_claims.json
output/intermediate/screened_candidates.json
output/intermediate/claim_ledger.json
```

Runtime Orchestrator behavior:

```text
state import-fact-layer
→ run --recipe fast-rerun
→ analyst
→ editor
→ auditor
→ gates/state review
→ finalize
```

Allowed:

- Reuse only the hash-verified fact-layer artifacts copied from the archive by
  `state import-fact-layer`.
- Start model-backed content work at Analyst.
- Expect finalize completion to re-check imported fact-layer freshness against
  the target workspace reporting window before archiving the rerun.
- Use this recipe for private instrumentation and manifestation reruns.

Not allowed:

- Do not run `run --recipe fast-rerun` without a valid
  `runtime_manifest.fact_layer_import`.
- Do not silently fall back to a full run if the import is missing or invalid.
- Do not regenerate source-discovery, input-governance, Scout, Screener, or
  Claim Ledger outputs inside the fast-rerun path.
- Do not synthesize upstream `stage-complete` or `decision_recorded` events for
  imported stages.
- Do not add facts outside the imported Claim Ledger.
- Do not use this as proof of output-quality improvement.
- Do not expose `improvement/memory.md`; use only the frozen per-run snapshot
  when present.

## Existing Draft Review

Existing-draft review is not a standalone v0.7.0 mode. Treat it as a compact
workflow variant after `run` has created runtime state and handoff artifacts.
The draft may be placed under `input/context/` as reference context or converted
into auditable artifacts by the runtime/human process, but it must not bypass
Claim Ledger, audit, gates, or finalize boundaries.
