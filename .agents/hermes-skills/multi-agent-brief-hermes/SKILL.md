---
name: multi-agent-brief-hermes
description: Use when running BriefLoop workspaces inside Hermes with delegate_task subagents, source cache, cron scheduling, and deterministic CLI controls.
version: 0.14.0
author: multi-agent-brief-workflow
license: MIT
platforms:
  - linux
  - macos
  - windows
tags:
  - hermes
  - cron
  - brief
  - research
  - workflow
  - delegate_task
---

# BriefLoop for Hermes

Use this skill for a Hermes-native delegated BriefLoop run. Hermes is the
Orchestrator main agent; Hermes `delegate_task` children draft role-owned
artifacts, while BriefLoop CLI transactions validate, freeze, gate, and render.

## Read First

- Delegated run sequence: `references/delegate-task-sequence.md`
- Source cache mode: `references/source-cache-contract.md`
- Cron usage: `references/cron-patterns.md`
- Contracts: `configs/orchestrator_contract.yaml`, `configs/stage_specs.yaml`,
  `configs/artifact_contracts.yaml`, `configs/policy_packs/default.yaml`

## Operating Model

Parent control loop: read workspace context -> read contracts -> identify next
stage -> delegate a specialist or Python tool -> check the expected artifact ->
decide `continue`, `retry_stage`, `delegate_repair`, `request_human_review`,
`block_run`, or `finalize`.

Runtime files to inspect, not hand-edit:

- `output/intermediate/runtime_manifest.json`
- `output/intermediate/workflow_state.json`
- `output/intermediate/artifact_registry.json`
- `output/intermediate/event_log.jsonl`
- `output/intermediate/audience_profile_snapshot.md`
- `output/intermediate/orchestrator_control_switchboard.json`
- `output/intermediate/control_selections.json`
- `output/intermediate/feedback_issues.json`
- `output/intermediate/repair_plan.json`
- `output/intermediate/gates/auditor_quality_gate_report.json`
- `output/intermediate/gates/finalize_quality_gate_report.json`
- `output/intermediate/quality_gate_report.json`
- `output/intermediate/provenance_graph.json`

Use `briefloop controls select`; Selection is not execution.
Use feedback commands as `feedback ingest/plan/resolve/show/validate`, gate
commands as `gates check/show/validate`, and provenance commands as
`provenance build/show/validate`.
Do not treat `audience_profile.md` as evidence.

## Setup Workflow

Preferred Path: Hermes Plugin from `integrations/hermes-plugin/mabw`.

```text
/mabw <workspace>
-> mabw_create_onboarding
-> mabw_init_workspace
-> mabw_run_handoff
-> read agent_handoff.md
-> continue delegated workflow
```

Fallback: chat-to-JSON onboarding. Collect brief profile in chat, validate with
`briefloop onboard --validate onboarding.json`, initialize with
`briefloop init <workspace> --from-onboarding onboarding.json`, then run
`briefloop run --workspace <workspace> --runtime hermes`. Do not call `briefloop run` again mid-pipeline; use status, state, gates, and repair commands.

Project is cloned and ready.
I can continue generating the brief inside Hermes with delegate_task children.

## Daily Source Cache Workflow

For cache-only jobs, read workspace `config.yaml`, `sources.yaml`, and `user.md`,
write `input/hermes_cache/YYYY-MM-DD.json`, use `hermes_daily_cache`, report item
count and source gaps, then stop. Details: `references/source-cache-contract.md`.

## Hermes-native Delegated Brief Workflow

Parent Orchestration is summarized here; role details live in
`references/delegate-task-sequence.md`.

Roles: scout, screener, claim-ledger, analyst, editor, auditor, finalize.
Sections: Scout child, Screener child, Claim-ledger child, Analyst child, Editor child, Auditor child. source_url only for HTTP(S) URLs.

batch delegation with up to 3 scout children is runtime-internal only: child
outputs are scratch/intermediate runtime material, not workflow artifacts. The
parent must join chunk outputs deterministically before writing `candidate_claims.json`; stable ordering must use source identity, source path or URL, source date, topic, and evidence text, not child completion order. do not silently drop chunk-level outputs. Only the final joined
`candidate_claims.json` and, in default topology, `screened_candidates.json`
count for stage completion. In default topology, do not delegate Screener and do not call `state stage-complete --stage screener`.

If runtime WebSearch reports `Did 0 searches`, or every query returns an empty result set, stop and request human review. Do not switch to source-planner or
continue with stale sources.

Atomic graph boundary: `atomic_claim_graph.json` is an optional experimental
structural decomposition aid, not source evidence or proof of support. If it is
absent or invalid, do not repair it. Do not create, edit, rewrite, repair, or
extend it. Do not cite atom IDs in reader-facing prose or introduce material
atoms absent from the frozen Claim Ledger.

Required success-path ordering: gates check + state check + state stage-complete.

```bash
briefloop state freeze-claim-ledger --workspace <workspace>
briefloop state stage-complete --workspace <workspace> --stage claim-ledger --reason "Claim Ledger frozen from claim drafts."
#### 4. Analyst child
briefloop gates check --workspace <workspace> --stage auditor
briefloop state check --workspace <workspace> --strict
briefloop state stage-complete --workspace <workspace> --stage auditor --reason "Audit and quality gates passed."
briefloop finalize --config <workspace>/config.yaml
# only when finalize_report.json reports delivery_promotion "promoted":
briefloop gates check --workspace <workspace> --stage finalize --brief <workspace>/output/brief.md
briefloop state finalize-complete --workspace <workspace> --reason "Reader-facing artifacts passed finalize checks."
briefloop status --workspace <workspace> --json
```

Audit warnings, overstatement findings, support-calibration findings, and
quality-gate findings do not authorize direct edits to frozen artifacts. For
current-gate repair, run `briefloop gates show --workspace <workspace> --json`
and follow its required_commands; current-gate repair start must be scoped
with `--gate-stage` and `--gate-artifact`. For non-gate owner-stage routes,
run `briefloop repair route --workspace <workspace> --json` and start with
`--finding-id` / `--route-index`. Finish with `briefloop repair complete`, or
choose `request_human_review` / `block_run`.

Formatter/finalize reads `output/intermediate/audited_brief.md` as frozen input;
route repair to Editor if wording changes are needed. `finalize` is not a quality-gate executor. Provenance projection is not semantic proof.
