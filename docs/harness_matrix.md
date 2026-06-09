# Harness Matrix

This matrix migrates the reusable delivery-gate design from local weekly-report prototypes into a public-safe open-source form.

The core principle:

```text
draft facts pass
  -> final text quality pass
  -> rendered document quality pass
  -> delivery allowed
```

A brief with correct facts can still be blocked if it is too thin, poorly structured, stale-but-current-framed, or unreadable after rendering.

## Protocol

Final delivery gates use:

```json
{
  "harness_protocol": "BRIEF_HARNESS_V2",
  "target": "draft | final | rendered",
  "verdict": "PASS | PASS_WITH_WARNINGS | BLOCKED",
  "checks": []
}
```

The current code exposes this through `FinalQualityAuditAgent`. It is intentionally separate from the default MVP draft audit, so synthetic examples can stay small while production workflows can opt into strict final gates.

## Gate Matrix

| Gate | Target | Severity | Purpose |
| --- | --- | --- | --- |
| Source ID existence | Draft | Blocking | Every `[src:CLAIM_ID]` must resolve to the Claim Ledger. |
| Number/source coverage | Draft | Warning or blocking by config | Important numbers need same-line source support. |
| Source freshness | Draft | Warning or blocking by config | Stale sources cannot be framed as report-window developments. |
| Redaction scan | Draft and final | Blocking | Credentials, private paths, and sensitive identifiers must not leak. |
| Final report depth | Final | Blocking | Normal reports need enough analysis depth unless marked quiet week. |
| Executive summary bullet separation | Final/rendered | Blocking | Summary bullets must remain separate, not merged in rendering. |
| Wide table check | Final/rendered | Blocking | Tables wider than four columns should be converted before DOCX output. |
| Front-page metadata | Final | Blocking | Coverage, cutoff, and source-priority notes should be visible. |
| Stale-current framing | Final | Blocking | Old dated events cannot be described as current/latest without a new change. |
| DOCX text depth | Rendered | Blocking | Rendered output must preserve enough text content. |
| DOCX dependency availability | Rendered | Blocking | Production DOCX validation must fail if `python-docx` is missing. |

## Default Thresholds

These defaults are deliberately configurable:

| Rule | Default |
| --- | --- |
| Normal report Markdown length | `>= 8000` chars unless `quiet_week` is true |
| Rendered DOCX text length | `>= 7800` chars unless `quiet_week` is true |
| Executive summary bullets | `5` standalone `▸` bullets |
| Table width | max `4` columns |
| Required metadata labels | `coverage`, `source priority`, `cutoff` |
| Main chapter count | disabled by default; configure per report template |
| Stale-current threshold | report `max_source_age_days` when available |

## Sanitizer Contract

A future final-report sanitizer may restructure presentation, but it must not invent facts or analysis.

Allowed deterministic cleanup:

- Deduplicate title and coverage lines.
- Inject public-safe front-page metadata if missing.
- Convert wide Markdown tables into readable row sections.
- Rename stale-current labels such as "current update" to neutral "event/status" when the source is old.
- Preserve reader-facing source appendices while removing engineering IDs from reader-facing output.

## Formatter Contract

A future DOCX renderer should keep these safety rails:

- Main report headings map to Word `Heading 1`.
- Summary bullet lines remain separate paragraphs.
- Footer contains page fields.
- Margins come from a style template or documented defaults.
- Tables wider than four columns are converted before rendering.

Renderer-level checks should not be replaced by prompt instructions alone.

## Agent Prompt Contract

Analyst and Editor prompts should align with hard gates:

- Normal reports target the configured minimum depth.
- Quiet-week mode must be explicit.
- Front-page metadata is mandatory when configured.
- Markdown tables max out at the configured column limit.
- Sections need analytical depth, not filler.
- Final output must not contain internal workflow terms.
- Summary bullets must be separate lines.
- Old/background events must not be described as "this week", "current", "latest", `本期动态`, or `本周新增` unless they truly changed during the report window.

## Non-Portable Items

Do not port brand-specific chapter names, company wording, private source paths, credentials, delivery channels, or hard-coded report filenames into this public repository.
