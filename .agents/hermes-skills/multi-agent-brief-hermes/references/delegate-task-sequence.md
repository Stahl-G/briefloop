# Hermes Delegate Task Sequence

The Hermes parent agent is the Orchestrator main agent. It uses `delegate_task` children for role work and Python CLI tools for setup, validation, and rendering. Each child starts with a fresh context, so include the workspace path, inputs, output artifact path, role goal, and return summary requirements in the child context.

Read these contract references before delegation:

- `configs/orchestrator_contract.yaml`
- `configs/stage_specs.yaml`
- `configs/artifact_contracts.yaml`
- `configs/policy_packs/default.yaml`

Control loop:

```text
Read workspace context -> read contract references -> identify the next stage -> delegate a specialist or Python tool -> check the expected artifact -> decide continue / retry_stage / delegate_repair / request_human_review / block_run / finalize.
```

## Parent Responsibilities

1. Read `config.yaml`, `sources.yaml`, `user.md`, `input/`, and `input/hermes_cache/` when present.
2. Read `output/intermediate/audience_profile_snapshot.md` and summarize relevant taste guidance for delegated roles. Do not treat `audience_profile.md` as source evidence.
3. Run `multi-agent-brief doctor --config <workspace>/config.yaml`.
4. Run source discovery when configured.
   If runtime WebSearch reports `Did 0 searches`, or every query returns an empty result set, stop and request human review. Do not switch to source-planner or continue with stale sources.
5. Run input governance when available.
6. Create `output/intermediate/` when needed.
7. Delegate child tasks in sequence.
8. Verify each expected artifact exists and is non-empty before selecting the next decision.
9. Decide `retry_stage`, `request_human_review`, or `block_run` with `state decide`; for `delegate_repair`, use the deterministic repair route/start/complete transaction. Use completion transactions for success paths.
10. Run quality gates, strict state check, and `state stage-complete` before finalize.
11. Run `multi-agent-brief finalize --config <workspace>/config.yaml` after audit readiness and gate readiness.
12. Run `state finalize-complete` after finalize writes reader-facing artifacts.
13. Optionally run `multi-agent-brief provenance build/show/validate` for audit/debug projection after runtime state exists.

## Child Task Templates

### Scout

Goal: Extract candidate reportable items for a MABW brief. In default topology, screen them in the same Scout stage.

Context should include:

- Workspace path
- Approved evidence inputs, cached source packages, local source files, and source config
- Write path: `output/intermediate/candidate_claims.json`
- Default topology write path: `output/intermediate/screened_candidates.json`
- Required fields: source path or URL, source date, evidence text, topic, claim type, confidence
- Default topology screening fields: selected candidates, excluded candidates with reasons, and screening policy snapshot

Optional Scout chunk parallelism is runtime-internal only. If the Hermes parent
splits source clusters across multiple Scout children, child outputs are
scratch/intermediate runtime material, not workflow artifacts. The parent must
join chunk outputs deterministically before writing `candidate_claims.json`.
Stable ordering must use source identity, source path or URL, source date, topic,
and evidence text, not child completion order. Do not append to
`candidate_claims.json` from chunk workers. Duplicates and near-duplicates must
be represented or excluded with reasons; do not silently drop chunk-level
outputs. Only the final joined `candidate_claims.json` and, in default topology,
`screened_candidates.json` count for stage completion.

Toolsets: `file`, `terminal`, `web` when source access is enabled.

### Screener (strict topology or explicit repair/review)

Goal: Screen and rank MABW candidate claims.

Input: `output/intermediate/candidate_claims.json`
Write: `output/intermediate/screened_candidates.json`

Use only when `role_topology: strict` keeps screening independent, or when the Orchestrator explicitly routes a screening repair/review task. Rank, deduplicate, freshness-check, and capacity-cap candidates while preserving source evidence and exclusion reasons.

### Claim Ledger

Goal: Build MABW claim drafts for deterministic Python freezing.

Input: `output/intermediate/screened_candidates.json`
Write: `output/intermediate/claim_drafts.json`

Write source-grounded claim drafts without `claim_id` fields. Preserve evidence text, source URL/path, publication date, retrieved date, topic, claim type, and confidence. Do not write `output/intermediate/claim_ledger.json`; after the child returns, run:

```bash
multi-agent-brief state freeze-claim-ledger --workspace <workspace>
multi-agent-brief state stage-complete --workspace <workspace> --stage claim-ledger --reason "Claim Ledger frozen from claim drafts."
```

### Analyst

Goal: Draft the audited MABW brief.

Inputs: `user.md`, `config.yaml`, frozen `output/intermediate/claim_ledger.json`
Write: `output/intermediate/audited_brief.md` as the Analyst working draft

Write a management-ready brief in the workspace language with valid `[src:<claim_id>]` citations that use real Claim Ledger IDs. Do not read `claim_drafts.json`; use frozen `claim_ledger.json` as the Claim Ledger input. Use the Orchestrator-provided audience taste summary as style context, not as source evidence. Do not create, edit, rewrite, or repair `claim_ledger.json`. Do not write `analyst_draft_snapshot.md`; Python freezes it during analyst stage-complete.

### Editor / Delivery Editor

Goal: Polish the audited MABW brief without adding facts.

Inputs: `output/intermediate/analyst_draft_snapshot.md`, `output/intermediate/audited_brief.md`
Write: `output/intermediate/audited_brief.md` as the Editor-owned final auditable brief

Improve readability, structure, and executive tone while preserving factual scope, caveats, uncertainty, and valid citations. Do not introduce new numbers, named entities, dates, causal claims, or citations.

### Auditor

Goal: Audit the MABW brief against the Claim Ledger.

Inputs: `output/intermediate/audited_brief.md`, frozen `output/intermediate/claim_ledger.json`
Write: `output/intermediate/audit_report.json`

Check source support, overstatement, support-strength calibration, confidence mismatch, evidence-relation mismatch, limitations, orphan citations, unsupported numbers, missing dates, stale framing, process residue, advice language, and delivery readiness. Do not read `claim_drafts.json` and do not create, edit, rewrite, or repair `claim_ledger.json`.

## Before Finalize Gate Path

After `audit_report.json` exists:

```bash
multi-agent-brief gates check --workspace <workspace> --stage auditor
multi-agent-brief state check --workspace <workspace> --strict
multi-agent-brief state stage-complete --workspace <workspace> --stage auditor --reason "Audit and quality gates passed."
```

If state is blocked by owner-stage artifact repair, do not use `state decide delegate_repair`. Run:

```bash
multi-agent-brief repair route --workspace <workspace>
multi-agent-brief repair start --workspace <workspace>
```

Delegate only the repair_owner role, allow edits only to allowed_artifacts, then run:

```bash
multi-agent-brief repair complete --workspace <workspace> --reason "<reason>"
```

After repair-complete, rerun downstream stages from must_rerun_from. For non-repair blocks, choose `request_human_review` or `block_run`; do not finalize. `finalize` is not a quality-gate executor.

After finalize writes reader-facing artifacts, run:

```bash
multi-agent-brief gates check --workspace <workspace> --stage finalize --brief <workspace>/output/brief.md
multi-agent-brief state finalize-complete --workspace <workspace> --reason "Reader-facing artifacts passed finalize checks."
```

Repair best practice: if the same stage has already needed roughly three retry/repair rounds, prefer `request_human_review` or `block_run`. If a repair would touch more than two sections, narrow the scope before delegating repair or request human review. This is runtime guidance only; v0.7 does not implement automatic retry counters or trajectory regulation.

Optional provenance projection after runtime state exists:

```bash
multi-agent-brief provenance build --workspace <workspace>
multi-agent-brief provenance show --workspace <workspace> --json
multi-agent-brief provenance validate --workspace <workspace>
```

Provenance projection is not semantic proof and is not required before finalize.
