---
name: analyst
description: Drafts executive-readable brief sections using only Claim Ledger entries. Use when implementing or reviewing Markdown brief generation, section writing, or claim citation behavior.
---

# Analyst Skill

## Purpose

Drafts executive-readable brief sections using only Claim Ledger entries.

## When To Use

Use when implementing or reviewing Markdown brief generation, section writing, or claim citation behavior.

## Responsibilities

- Draft clear brief sections.
- Use only Claim Ledger material.
- Attach [src:CLAIM_ID] citations to important statements.
- Preserve uncertainty and source limitations.

## Hard Rules

- Do not add unsupported facts, numbers, or causality.
- Do not write investment advice or trading signals.
- Do not cite claims that do not exist in the ledger.

## Pipeline Context

```text
Scout -> Screener -> Claim Ledger -> Analyst -> Auditor -> Editor -> Formatter
```

## Expected Inputs

Source files, claim ledger entries, or draft markdown as appropriate for the pipeline stage.

## Expected Outputs

Structured artifacts conforming to the pipeline contract:
- `brief.md`
- `claim_ledger.json`
- `audit_report.json`
- `source_map.md`
