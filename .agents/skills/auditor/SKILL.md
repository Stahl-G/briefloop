---
name: auditor
description: Audits source support, freshness, unsupported numbers, redaction risk, duplicate claims, placeholders, and harness failures. Use when implementing or reviewing deterministic audit, quality harness, semantic audit adapter hooks, or final delivery gates.
---

# Auditor Skill

## Purpose

Audits source support, freshness, unsupported numbers, redaction risk, duplicate claims, placeholders, and harness failures.

## When To Use

Use when implementing or reviewing deterministic audit, quality harness, semantic audit adapter hooks, or final delivery gates.

## Responsibilities

- Protect source grounding.
- Check missing or orphan claim references.
- Check unsupported numbers.
- Check stale sources.
- Check redaction risks.
- Check low-confidence source leakage.
- Check process residue and placeholders.
- Coordinate draft and final harness agents when needed.

## Hard Rules

- Do not weaken audit gates to pass tests.
- Do not treat model judgment as source evidence.
- Do not mark blocked reports as distribution-ready.

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
