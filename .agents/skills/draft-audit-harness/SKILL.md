---
name: draft-audit-harness
description: Reviews and implements the draft-level audit harness: deterministic source checks plus QualityHarnessAuditAgent checks. Use when working on DeterministicAuditAgent, QualityHarnessAuditAgent, CompositeAuditAgent, or draft-level source/freshness/redaction checks.
---

# Draft Audit Harness Skill

## Purpose

Reviews and implements the draft-level audit harness: deterministic source checks plus QualityHarnessAuditAgent checks.

## When To Use

Use when working on DeterministicAuditAgent, QualityHarnessAuditAgent, CompositeAuditAgent, or draft-level source/freshness/redaction checks.

## Responsibilities

- Check missing or orphan [src:CLAIM_ID] references.
- Check number/source coverage.
- Check strict reporting-window freshness.
- Check missing source dates.
- Check placeholders.
- Check internal workflow residue.
- Check unsupported certainty wording.
- Check investment-advice style language.
- Check needs_recrawl and low-confidence claims appearing in briefs.
- Check low numeric source density.
- Check possible unit inflation.
- Check repeat/background claims in executive summaries.

## Hard Rules

- Do not weaken draft audit gates.
- Do not bypass CompositeAuditAgent.
- Do not treat semantic model output as source truth.

## Pipeline Context

```text
Scout -> Screener -> Claim Ledger -> Analyst -> Auditor -> Editor -> Formatter
```

## Expected Inputs

Source files, claim ledger entries, or draft markdown as appropriate for the harness stage.

## Expected Outputs

Structured artifacts conforming to the pipeline contract:
- `brief.md`
- `claim_ledger.json`
- `audit_report.json`
- `source_map.md`
