---
name: mabw-workflow
description: Runs Multi-Agent Brief Workflow inside Hermes from chat-collected onboarding answers to workspace handoff and delegated brief execution. Use when the user asks Hermes to initialize, generate, schedule, or continue a MABW brief.
---

# MABW Workflow for Hermes

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
7. Read `output/intermediate/orchestrator_control_switchboard.json` and record control choices with `multi-agent-brief controls select`.
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
doctor → source discovery when configured → input governance when available → scout → screener → claim-ledger → analyst → editor → auditor → gates check/state check/state decide → finalize
```

Before `finalize`, run `multi-agent-brief gates check`, `state check --strict`, and `state decide --stage auditor --decision continue`. `finalize` only renders reader-facing outputs; it is not a quality-gate executor.

Selection is not execution. `controls select --selection enable` records Orchestrator intent only; explicitly run the selected CLI, subagent, or human action afterward.

Optional audit/debug projection after runtime state exists:

```text
multi-agent-brief provenance build --workspace <workspace>
multi-agent-brief provenance validate --workspace <workspace>
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
