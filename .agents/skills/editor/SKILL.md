---
name: editor
description: Improves clarity, structure, tone, and executive readability without adding facts. Use when improving final Markdown prose, documentation wording, or report readability after audit issues are resolved.
---

# Editor Skill

## Purpose

Improves clarity, structure, tone, and executive readability without adding facts.

## When To Use

Use when improving final Markdown prose, documentation wording, or report readability after audit issues are resolved.

## Responsibilities

- Improve readability and management tone.
- Reduce repetition.
- Preserve all [src:CLAIM_ID] citations exactly — do not remove or rewrite claim IDs.
- Preserve uncertainty.
- Remove internal residue when safe.
- Do not add new facts.
- Do not remove or rewrite claim IDs.

## Hard Rules

- Do not add new claims.
- Do not remove claim citations.
- Do not convert caveats into certainty.
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
