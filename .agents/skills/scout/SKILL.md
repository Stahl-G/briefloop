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

- Read source packages, Tavily/RSS/local input outputs.
- Filter boilerplate, navigation, cookies, privacy text, directories, and ads.
- Extract structured claims from source content.
- Each claim must include: statement, evidence_text, source_url, published_at or retrieved_at, topic, claim_type, confidence.
- Preserve source path, source ID, source date, and evidence text.
- Mark vague, stale-looking, duplicate-looking, or low-confidence items.
- Return candidates, not final analysis.
- Do not invent facts.

## Hard Rules

- Do not write final brief prose.
- Do not rank or capacity-cap candidates.
- Do not create unsupported facts.
- Do not invent claims not present in source material.

## Pipeline Context

```text
Scout -> Screener -> Claim Ledger -> Analyst -> Auditor -> Editor -> Formatter
```

## Expected Inputs

Source files, claim ledger entries, or draft markdown as appropriate for the pipeline stage.

## Expected Outputs

Structured artifacts conforming to the pipeline contract:
- `draft_brief.md`
- `claim_ledger.json`
- `audit_report.json`
- `source_map.md`
