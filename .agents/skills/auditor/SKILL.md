---
name: auditor
description: Audits source support, freshness, unsupported numbers, redaction risk, duplicate claims, placeholders, and harness failures. Use before final delivery of any real brief. Must verify brief against claim_ledger.json and enforce quality thresholds.
---

# Auditor Skill

## Purpose

Audits source support, freshness, unsupported numbers, redaction risk, duplicate claims, placeholders, and harness failures.

## When To Use

Use before final delivery of any real brief. Must verify brief against claim_ledger.json and enforce quality thresholds.

## Responsibilities

- Review final brief against claim_ledger.json and audit_report.json.
- Check unsupported facts — every important statement must have a [src:CLAIM_ID].
- Check missing citations — claims in ledger not cited in brief.
- Check orphan citations — [src:CLAIM_ID] in brief not found in ledger.
- Check stale sources — sources older than configured reporting window.
- Check investment-advice language — no trading signals or investment recommendations.
- Check redaction risks — no private identifiers, internal paths, or confidential content.
- Check low-confidence source leakage.
- Check process residue and placeholders.
- Check [SRC:] or process residue remains in final text.
- Check weekly brief has enough claims (default: >= 20) unless quiet-week exception configured.
- Check source dates are present for claims in final brief.
- Recommend fixes for each finding.
- Prefer running python deterministic audit commands where available.
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
- `draft_brief.md`
- `claim_ledger.json`
- `audit_report.json`
- `source_map.md`
