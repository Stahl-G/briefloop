---
name: formatter
description: Writes and validates final artifacts: brief.md, claim_ledger.json, audit_report.json, source_map.md. Use when implementing or reviewing output file writing, JSON validity, source maps, Markdown/DOCX/PDF rendering contracts.
---

# Formatter Skill

## Purpose

Writes and validates final artifacts: brief.md, claim_ledger.json, audit_report.json, source_map.md.

## When To Use

Use when implementing or reviewing output file writing, JSON validity, source maps, Markdown/DOCX/PDF rendering contracts.

## Responsibilities

- Write files only inside configured output directories.
- Validate JSON artifacts.
- Validate cited claim IDs exist.
- Preserve deterministic formatting.

## Hard Rules

- Do not hide failed audits.
- Do not change substantive content to hide rendering defects.
- Do not overwrite user files outside output directories.

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
