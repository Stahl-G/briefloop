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

- Read claim_ledger.json and user.md to understand context and available evidence.
- Draft management-ready sections using only Claim Ledger material.
- Attach [src:CLAIM_ID] citations to every important statement.
- Preserve every [src:CLAIM_ID] citation — do not remove or rewrite claim IDs.
- Preserve uncertainty and source limitations.
- Write concise analytical Chinese or English according to workspace language.
- Do not add unsupported facts.

## Hard Rules

- Do not add unsupported facts, numbers, or causality.
- Do not write investment advice or trading signals.
- Do not cite claims that do not exist in the ledger.
- Do not remove or rewrite [src:CLAIM_ID] citations.

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
