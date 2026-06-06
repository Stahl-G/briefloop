# Quality Harness

The public repo keeps the quality-control idea from local workflow prototypes, but ports it as generic, clean-room checks.

## Current Audit Stack

```text
audit tool
  -> CompositeAuditAgent
       -> DeterministicAuditAgent
       -> QualityHarnessAuditAgent
       -> optional semantic audit adapter
```

## Ported Generic Checks

The current harness includes public-safe versions of local weekly pipeline gates:

- Missing or orphan `[src:CLAIM_ID]` references
- Number-like values without same-line source references
- Strict reporting-window freshness checks
- Missing source dates under reporting-window mode
- Placeholder text such as `TBD` or `待补充`
- Internal workflow residue such as `Ledger`, `raw_text`, `Scout`, or `Packet`
- Process residue such as `Step 1`
- Generation/audit metadata residue
- Unsupported certainty wording without dates
- Investment-advice style language
- `needs_recrawl` claims appearing in a brief
- T5 or low-confidence source claims appearing in a brief
- Low numeric source-density
- Possible EIA thousand-MWh to GWh inflation
- Repeat/background claims appearing in the executive summary
- Excess stale/no-update filler phrases

## Clean-Room Boundary

Local prototypes may contain company-specific rules, templates, and delivery constraints. Those should not be copied verbatim into this public repo.

Instead, they should be migrated as:

- Generic configurable rules
- Industry modules
- Audience modules
- Connector/output interfaces
- Synthetic examples
- Tests

## Remaining Migration Backlog

The following prototype capabilities still need clean-room migration:

- Configurable rule packs instead of hard-coded harness regexes
- Previous-report baseline ingestion for novelty/repeat detection
- Source-tier policy by connector and audience
- Structured section taxonomy and required-section checks
- Publication-mode final-clean checks that remove internal source tags
- DOCX/PDF layout validation
- Semantic source-support comparison with a model-backed audit agent
- Editor repair classification such as editor-fixable vs analyst-blocking
