---
name: market-competitor-analyst
description: Generates AnalysisCards from evidence_pack.json and writes competitor sections for the final brief. Use after the pipeline produces evidence_pack.json. Generate AnalysisCards and write the competitor analysis section of the brief.
---

# Market Competitor Analyst Skill

## Purpose

Generates AnalysisCards from evidence_pack.json and writes competitor sections for the final brief.

## When To Use

Use after the pipeline produces evidence_pack.json. Generate AnalysisCards and write the competitor analysis section of the brief.

## Responsibilities

- Read evidence_pack.json, competitor_matrix.json, claim_ledger.json.
- Generate analysis_cards.json — each card must have supporting_claim_ids.
- Write competitor analysis section for the brief using only AnalysisCards and Claim Ledger.
- Preserve [src:CLAIM_ID] citations for every source-backed statement.
- Distinguish announced vs operational capacity in prose.
- Flag evidence gaps clearly.

## Hard Rules

- Do not create claims not present in claim_ledger.json.
- Every AnalysisCard must have at least one supporting claim.
- Single-source interpretations must set confidence='low'.
- Do not write investment advice or trading signals.

## Pipeline Context

```text
Scout -> Screener -> Claim Ledger -> Analyst -> Editor -> Auditor -> Formatter
```

## Expected Inputs

Source files, claim ledger entries, or draft markdown as appropriate for the analysis_module stage.

## Expected Outputs

Structured artifacts conforming to the pipeline contract:
- `draft_brief.md`
- `claim_ledger.json`
- `audit_report.json`
- `source_map.md`
