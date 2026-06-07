---
name: multi-agent-brief-hermes
description: Runs Multi-Agent Brief Workflow workspaces inside Hermes using delegate_task child agents, source cache, cron scheduling, and final rendering. Use when the user asks Hermes to generate, schedule, continue, or inspect a MABW brief from a workspace.
license: MIT
compatibility: Requires Hermes with delegate_task support plus terminal and file access to a workspace with the multi-agent-brief CLI installed.
metadata:
  author: multi-agent-brief-workflow
  version: 0.5.6
  tags:
    - hermes
    - cron
    - brief
    - research
    - delegate-task
---

# Multi-Agent Brief Workflow for Hermes

## Scope

This skill is the Hermes runtime contract for MABW. It applies when Hermes is asked to set up, schedule, or run a MABW workspace.

Hermes is a native runtime: the Hermes parent agent orchestrates the run and uses `delegate_task` children for role work. Python CLI commands provide setup, validation, source tooling, final rendering, and audit support.

## Use When

Use this skill when the user asks Hermes to:

- create or continue a MABW brief workspace
- generate a management, market, policy, competitor, or research brief
- schedule daily source cache collection or weekly/monthly brief generation
- run the MABW workflow inside Hermes rather than Claude Code

## Standard Setup Path

For a real workspace:

```bash
multi-agent-brief onboard
multi-agent-brief init <workspace> --from-onboarding onboarding.json
multi-agent-brief run --workspace <workspace> --runtime hermes
```

For an existing workspace:

```bash
multi-agent-brief doctor --config <workspace>/config.yaml
multi-agent-brief hermes prompt --config <workspace>/config.yaml
```

After setup, report repository path, virtual environment path, workspace path, version, and doctor status. Offer to continue in Hermes using delegated child tasks.

## Delegated Brief Run

Parent orchestration sequence:

```text
doctor
→ source discovery when configured
→ input governance when available
→ delegate_task scout
→ delegate_task screener
→ delegate_task claim-ledger
→ delegate_task analyst
→ delegate_task editor
→ delegate_task auditor
→ finalize
```

Read `references/delegate-task-sequence.md` before creating child tasks. Each child task needs complete context, explicit inputs, expected output path, and return summary requirements.

## Daily Source Cache

Daily cache mode collects source signals and writes cache files without drafting a final brief.

Read `references/source-cache-contract.md` before writing cache files.

## Cron Scheduling

Use cron for durable scheduling and `delegate_task` for per-run child work.

Read `references/cron-patterns.md` before creating or editing Hermes cron jobs.

## Reporting

After a delegated run, report:

- `output/brief.md`
- configured named Markdown copy when enabled
- `output/brief.docx` when configured
- `output/intermediate/audited_brief.md`
- `output/intermediate/claim_ledger.json`
- `output/intermediate/audit_report.json`
- audit status and remaining limitations
