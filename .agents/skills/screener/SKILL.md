---
name: screener
description: Filters, ranks, deduplicates, freshness-checks, and capacity-caps Scout candidates before Claim Ledger. Use when implementing or reviewing novelty scoring, source-tier ranking, topic caps, stale source filtering, or previous-report deduplication.
---

# Screener Skill

## Purpose

Filters, ranks, deduplicates, freshness-checks, and capacity-caps Scout candidates before Claim Ledger.

## When To Use

Use when implementing or reviewing novelty scoring, source-tier ranking, topic caps, stale source filtering, or previous-report deduplication.

## Responsibilities

- Filter and rank Scout candidates.
- Deduplicate exact and near-duplicate items.
- Enforce topic capacity caps.
- Detect previous-report overlap.
- Exclude stale or low-confidence candidates according to config.
- Preserve source identity and evidence for included candidates.
- Record exclusion reasons when practical.

## Hard Rules

- Do not create new facts.
- Do not pass stale weekly items unless config permits.
- Do not drop source identity.
- Do not exceed topic capacity caps.

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
