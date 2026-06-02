---
name: scout
description: Extracts candidate reportable items from local markdown, text, JSON, and future connector sources. Use when inspecting source inputs or extracting candidate items before screening.
---

# Scout Skill

## Purpose

Extracts candidate reportable items from local markdown, text, JSON, and future connector sources.

## When To Use

Use when inspecting source inputs or extracting candidate items before screening.

## Responsibilities

- Find reportable signals.
- Preserve source path, source ID, source date, and evidence text.
- Mark vague, stale-looking, duplicate-looking, or low-confidence items.
- Return candidates, not final analysis.

## Hard Rules

- Do not write final brief prose.
- Do not rank or capacity-cap candidates.
- Do not create unsupported facts.

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
