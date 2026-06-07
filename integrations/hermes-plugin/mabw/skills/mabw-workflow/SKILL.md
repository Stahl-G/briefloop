---
name: mabw-workflow
description: Runs Multi-Agent Brief Workflow inside Hermes from chat-collected onboarding answers to workspace handoff and delegated brief execution. Use when the user asks Hermes to initialize, generate, schedule, or continue a MABW brief.
---

# MABW Workflow for Hermes

## Purpose

Use this skill to run Multi-Agent Brief Workflow through Hermes without relying on an interactive terminal wizard.

## Workflow

1. Collect the brief profile in chat.
2. Call `mabw_create_onboarding`.
3. Call `mabw_init_workspace`.
4. Call `mabw_run_handoff`.
5. Read `agent_handoff.md`.
6. Continue the delegated workflow with Hermes child tasks.

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
scout → screener → claim-ledger → analyst → editor → auditor → finalize
```

## References

Read these when needed:

- `references/onboarding-json.md`
- `references/delegated-workflow.md`
- `references/artifact-contract.md`
