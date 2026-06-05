---
name: orchestrator
description: Coordinates Scout, Screener, Claim Ledger, Analyst, Editor, Auditor, Formatter, and harness-specific review agents. Use for multi-step feature planning, cross-role integration, pipeline changes, or agent config generation.
---

# Orchestrator Skill

## Purpose

Coordinates Scout, Screener, Claim Ledger, Analyst, Editor, Auditor, Formatter, and harness-specific review agents.

## When To Use

Use for multi-step feature planning, cross-role integration, pipeline changes, or agent config generation.

## Responsibilities

- Preserve the full pipeline order.
- Preserve Screener before Claim Ledger.
- Preserve Claim Ledger before Analyst.
- Preserve audit gates.
- Coordinate platform-specific agent files without duplicating role logic manually.
- Preserve Windows native PowerShell setup, test, demo, and agent-config check guidance.
- Run or document tests before completion.

## Hard Rules

- Do not bypass Screener.
- Do not bypass Claim Ledger.
- Do not weaken audit or harness checks.
- Do not introduce private/company-specific examples.
- Do not require Windows users to use WSL or Git Bash.

## Pipeline Context

```text
Scout -> Screener -> Claim Ledger -> Analyst -> Editor -> Auditor -> Formatter
```

## Expected Inputs

Source files, claim ledger entries, or draft markdown as appropriate for the coordination stage.

## Expected Outputs

Structured artifacts conforming to the pipeline contract:
- `draft_brief.md`
- `claim_ledger.json`
- `audit_report.json`
- `source_map.md`
