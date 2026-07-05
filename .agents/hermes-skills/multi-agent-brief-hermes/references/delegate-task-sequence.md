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
3. Run `briefloop doctor --config <workspace>/config.yaml`.
4. Run source discovery when configured.
   If runtime WebSearch reports `Did 0 searches`, or every query returns an empty result set, stop and request human review. Do not switch to source-planner or continue with stale sources.
5. Run input governance when available.
6. Create `output/intermediate/` when needed.
7. Delegate child tasks in sequence.
8. Verify each expected artifact exists and is non-empty before selecting the next decision.
9. Decide `retry_stage`, `request_human_review`, or `block_run` with `state decide`; for `delegate_repair`, use the deterministic repair route/start/complete transaction. Use completion transactions for success paths.
10. Run quality gates, strict state check, and `state stage-complete` before finalize.
11. Run `briefloop finalize --config <workspace>/config.yaml` after audit readiness and gate readiness. Finalize reads `output/intermediate/audited_brief.md` as frozen input and must not patch it.
12. Run `state finalize-complete` after finalize writes reader-facing artifacts.
13. Optionally run `briefloop provenance build/show/validate` for audit/debug projection after runtime state exists.

## Child Task Templates

### Scout

Goal: Extract candidate reportable items for a BriefLoop brief. In default topology, screen them in the same Scout stage.

Context should include:

- Workspace path
- Approved evidence inputs, cached source packages, local source files, and source config
- Write path: `output/intermediate/candidate_claims.json`
- Default topology write path: `output/intermediate/screened_candidates.json`
- Required fields: statement, evidence text, source identity, source date, topic, claim type, confidence
- Source metadata: use `source_url` only for HTTP(S) URLs, `source_path` for local/package sources, and preserve source title/name, publisher when known, `source_category`, and provider `source_type`
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
`screened_candidates.json` count for stage completion. In default topology, do
not delegate Screener and do not call `state stage-complete --stage screener`;
Scout completion satisfies Screener by topology.

Toolsets: `file`, `terminal`, `web` when source access is enabled.

### Screener (strict topology or explicit repair/review)

Goal: Screen and rank BriefLoop candidate claims.

Input: `output/intermediate/candidate_claims.json`
Write: `output/intermediate/screened_candidates.json`

Use only when `role_topology: strict` keeps screening independent, or when the Orchestrator explicitly routes a screening repair/review task. Rank, deduplicate, freshness-check, and capacity-cap candidates while preserving source evidence and exclusion reasons.
Do not use this role to replay default-topology Screener after Scout completion.

### Claim Ledger

Goal: Build BriefLoop claim drafts for deterministic Python freezing.

Input: `output/intermediate/screened_candidates.json`
Write: `output/intermediate/claim_drafts.json`

Write source-grounded claim drafts without `claim_id` fields. Preserve evidence text, source URL/path, source title/name, publisher, `source_category`, provider `source_type`, publication date, retrieved date, topic, claim type, and confidence. `source_url` is only for HTTP(S) URLs; never put titles, source names, source IDs, search queries, or local paths in `source_url`. Do not write `output/intermediate/claim_ledger.json`; after the child returns, run:

```bash
briefloop state freeze-claim-ledger --workspace <workspace>
briefloop state stage-complete --workspace <workspace> --stage claim-ledger --reason "Claim Ledger frozen from claim drafts."
```

### Analyst

Goal: Draft the audited BriefLoop brief.

Inputs: `user.md`, `config.yaml`, frozen `output/intermediate/claim_ledger.json`
Write: `output/intermediate/audited_brief.md` as the Analyst working draft

Write a management-ready brief in the workspace language with valid `[src:<claim_id>]` citations that use real Claim Ledger IDs. Do not read `claim_drafts.json`; use frozen `claim_ledger.json` as the Claim Ledger input. If `atomic_claim_graph.json` is present and valid, use it only as an optional experimental structural decomposition aid for frozen Claim Ledger claims; it is not source evidence or proof of support. Do not create, edit, rewrite, repair, or extend `atomic_claim_graph.json`, and do not cite atom IDs in reader-facing prose. Do not introduce material atoms absent from the frozen Claim Ledger and, when present and valid, `atomic_claim_graph.json`. Use the Orchestrator-provided audience taste summary as style context, not as source evidence. Do not create, edit, rewrite, or repair `claim_ledger.json`. Do not write `analyst_draft_snapshot.md`; Python freezes it during analyst stage-complete.

### Editor / Delivery Editor

Goal: Polish the audited BriefLoop brief without adding facts.

Inputs: `output/intermediate/analyst_draft_snapshot.md`, `output/intermediate/audited_brief.md`
Write: `output/intermediate/audited_brief.md` as the Editor-owned final auditable brief

Improve readability, structure, and executive tone while preserving factual scope, caveats, uncertainty, and valid citations. If `atomic_claim_graph.json` is present and valid, use it only as an optional experimental structural decomposition aid; if it is absent or invalid, do not repair it. Do not create, edit, rewrite, repair, or extend `atomic_claim_graph.json`. Do not introduce new numbers, named entities, dates, causal claims, citations, or material atoms absent from the frozen Claim Ledger and, when present and valid, `atomic_claim_graph.json`. Do not cite atom IDs in reader-facing prose.

### Auditor

Goal: Audit the BriefLoop brief against the Claim Ledger.

Inputs: `output/intermediate/audited_brief.md`, frozen `output/intermediate/claim_ledger.json`
Write: `output/intermediate/audit_report.json`

Check source support, overstatement, support-strength calibration, confidence mismatch, evidence-relation mismatch, limitations, orphan citations, unsupported numbers, missing dates, stale framing, process residue, advice language, and delivery readiness. Do not read `claim_drafts.json` and do not create, edit, rewrite, or repair `claim_ledger.json`.

## Before Finalize Gate Path

After `audit_report.json` exists:

```bash
briefloop gates check --workspace <workspace> --stage auditor
briefloop state check --workspace <workspace> --strict
briefloop state stage-complete --workspace <workspace> --stage auditor --reason "Audit and quality gates passed."
```

Do not call `briefloop run` again mid-pipeline to refresh handoff or state. Use `briefloop status`, `state show`, `gates check`, `state check`, and repair commands instead.

If state is blocked by owner-stage artifact repair, do not use `state decide delegate_repair`. Run:

```bash
briefloop repair route --workspace <workspace>
briefloop repair start --workspace <workspace>
```

Delegate only the repair_owner role, allow edits only to allowed_artifacts, then run:

```bash
briefloop repair complete --workspace <workspace> --reason "<reason>"
```

Audit warnings, overstatement findings, support-calibration findings, and quality-gate findings do not authorize direct edits to frozen artifacts. Use the repair route/start transaction before owner-stage edits, or choose `request_human_review` / `block_run` when no deterministic route exists.

After repair-complete, rerun downstream stages from must_rerun_from. For non-repair blocks, choose `request_human_review` or `block_run`; do not finalize. `finalize` is not a quality-gate executor. Formatter/finalize may write only reader-facing delivery artifacts and finalize control records; it must not edit `output/intermediate/audited_brief.md`, `output/intermediate/audit_report.json`, artifact registry, or workflow state. If reader-clean requires wording changes to the audited brief, stop and route repair to Editor before rerunning downstream stages.

After finalize writes reader-facing artifacts, run:

```bash
briefloop gates check --workspace <workspace> --stage finalize --brief <workspace>/output/brief.md
briefloop state finalize-complete --workspace <workspace> --reason "Reader-facing artifacts passed finalize checks."
```

Repair best practice: repeated retry/repair budgets are enforced by `workflow_state.json.next_allowed_decisions` after `state check` or `state decide`; when trajectory regulation narrows decisions, use only `request_human_review` or `block_run`. If a repair would touch more than two sections, narrow the scope before delegating repair or request human review. Trajectory regulation narrows operator decisions only; it does not execute repair, run gates, approve delivery, or perform agent work.

Optional provenance projection after runtime state exists:

```bash
briefloop provenance build --workspace <workspace>
briefloop provenance show --workspace <workspace> --json
briefloop provenance validate --workspace <workspace>
```

Provenance projection is not semantic proof and is not required before finalize.
