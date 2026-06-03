---
name: rendered-output-harness
description: Reviews rendered output gates for DOCX/PDF/Markdown rendering fidelity. Use when working on rendered document validation, DOCX text depth, heading mapping, margins, footer fields, or wide table conversion.
---

# Rendered Output Harness Skill

## Purpose

Reviews rendered output gates for DOCX/PDF/Markdown rendering fidelity.

## When To Use

Use when working on rendered document validation, DOCX text depth, heading mapping, margins, footer fields, or wide table conversion.

## Responsibilities

- Validate rendered text depth.
- Validate heading mapping.
- Validate bullet separation after rendering.
- Validate wide table conversion.
- Validate DOCX/PDF dependency behavior.
- Keep renderer-level checks separate from prompt instructions.

## Hard Rules

- Do not hide rendering defects by changing substantive content.
- Do not silently pass rendered-output validation if required dependencies are missing.
- Do not treat prompt instructions as a substitute for deterministic rendering checks.

## Pipeline Context

```text
Scout -> Screener -> Claim Ledger -> Analyst -> Auditor -> Editor -> Formatter
```

## Expected Inputs

Source files, claim ledger entries, or draft markdown as appropriate for the harness stage.

## Expected Outputs

Structured artifacts conforming to the pipeline contract:
- `draft_brief.md`
- `claim_ledger.json`
- `audit_report.json`
- `source_map.md`
