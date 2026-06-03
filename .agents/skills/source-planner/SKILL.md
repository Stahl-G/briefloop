---
name: source-planner
description: Reads user.md, config.yaml, and sources.yaml to generate or refine source_candidates.yaml and search_tasks. Ensures all sources are public, citable, and timestamped. Use when planning source discovery, generating search tasks, or refining source candidates for a brief workspace.
---

# Source Planner Skill

## Purpose

Reads user.md, config.yaml, and sources.yaml to generate or refine source_candidates.yaml and search_tasks. Ensures all sources are public, citable, and timestamped.

## When To Use

Use when planning source discovery, generating search tasks, or refining source candidates for a brief workspace.

## Responsibilities

- Read user.md, config.yaml, and sources.yaml to understand the briefing context.
- Generate or refine source_candidates.yaml with public, citable, timestamped sources.
- Generate or refine search_tasks in sources.yaml.
- Ensure all proposed sources are public, citable, and timestamped.
- Only use public, citable sources — never include private or confidential content.
- Align source discovery with user industry, role, and focus areas.

## Hard Rules

- Do not propose private, internal, or confidential sources.
- Do not include credentials, tokens, or MNPI in source plans.
- Do not claim sources are verified before collection.
- Do not bypass source profile constraints.

## Pipeline Context

```text
Scout -> Screener -> Claim Ledger -> Analyst -> Auditor -> Editor -> Formatter
```

## Expected Inputs

Source files, claim ledger entries, or draft markdown as appropriate for the coordination stage.

## Expected Outputs

Structured artifacts conforming to the pipeline contract:
- `brief.md`
- `claim_ledger.json`
- `audit_report.json`
- `source_map.md`
