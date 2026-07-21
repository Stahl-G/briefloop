---
name: mabw-workflow
description: Runs BriefLoop inside Hermes from chat-collected onboarding answers to workspace handoff and delegated brief execution. Use when the user asks Hermes to initialize, generate, schedule, or continue a BriefLoop brief.
---

# BriefLoop Workflow for Hermes

## Purpose

Use this skill to run Multi-Agent Brief Workflow through Hermes without relying on an interactive terminal wizard.

The Hermes parent agent is the Orchestrator main agent. It reads shared contract references, controls delegated stages, checks expected artifacts, and selects the next workflow decision.

Contract references:

- `configs/orchestrator_contract.yaml`
- `configs/stage_specs.yaml`
- `configs/artifact_contracts.yaml`
- `configs/policy_packs/default.yaml`

Orchestrator control loop:

```text
Read workspace context -> read contract references -> identify the next stage -> delegate a specialist or Python tool -> check the expected artifact -> decide continue / retry_stage / delegate_repair / request_human_review / block_run / finalize.
```

## Workflow

1. Collect the brief profile in chat.
2. Call `mabw_create_onboarding`.
3. Call `mabw_init_workspace`.
4. Call `mabw_run_handoff`.
5. Read `agent_handoff.md`.
6. Read `output/intermediate/audience_profile_snapshot.md` as the frozen runtime taste context.
7. Read `output/intermediate/orchestrator_control_switchboard.json` and record control choices with `briefloop controls select`.
8. Continue the Orchestrator-led delegated workflow with Hermes child tasks.

## Brief Profile Fields

- company_or_org
- industry_or_theme
- task_objective
- audience
- language
- cadence
- source_style
- output_style
- must_watch
- forbidden_sources
- web_search_mode

## Delegated Workflow

```text
doctor → source discovery when configured → input governance when available → scout → screener → claim-ledger → analyst → editor → auditor → gates check/state check/stage-complete → finalize → finalize-complete
```

Before `finalize`, run this explicit success path:

```bash
briefloop gates check --workspace <workspace>
briefloop state check --workspace <workspace> --strict
briefloop state stage-complete --workspace <workspace> --stage auditor --reason "Audit and quality gates passed."
briefloop finalize --config <workspace>/config.yaml
# proceed only when finalize_report.json reports delivery_promotion "promoted":
briefloop gates check --workspace <workspace> --stage finalize --brief <workspace>/output/brief.md
briefloop state finalize-complete --workspace <workspace> --reason "Reader-facing artifacts passed finalize checks."
briefloop status --workspace <workspace> --json
```

`finalize` only renders reader-facing outputs; it is not a quality-gate executor. A failed reader-clean does not promote delivery and leaves any prior delivery unchanged; do not report delivery unless the Store-native status projection reports `delivered=true` for the current run. The legacy completion projection / `workbuddy diagnose` surface is retired.

Selection is not execution. `controls select --selection enable` records Orchestrator intent only; explicitly run the selected CLI, subagent, or human action afterward.

Repair guidance is bounded runtime guidance. Repeated retry/repair budgets are enforced by `workflow_state.json.next_allowed_decisions` after `state check` or `state decide`; when trajectory regulation narrows decisions, use only `request_human_review` or `block_run`. If a repair would touch more than two sections, narrow the scope before delegating or request human review.

Optional audit/debug projection after runtime state exists:

```text
briefloop provenance build --workspace <workspace>
briefloop provenance validate --workspace <workspace>
```

Provenance projection is not semantic proof and is not required to finalize.

Audience memory is runtime context, not source evidence or an artifact gate. Read
`audience_profile_snapshot.md` at run start, summarize relevant taste guidance for
specialist roles, and ignore mid-run edits to `audience_profile.md` until the next run.

## References

Read these when needed:

- `references/onboarding-json.md`
- `references/delegated-workflow.md`
- `references/artifact-contract.md`
