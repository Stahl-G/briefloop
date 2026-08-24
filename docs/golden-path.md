# BriefLoop v0.15.3 SQLite Golden Path

This is the shortest product path for a normal BriefLoop user. This product
path is not an experiment harness, benchmark protocol, or reference-run
showcase. It answers one practical question: how do I create, run, inspect, and
deliver a traceable business brief without bypassing the control spine?

Use this path when you want one of the supported product-baseline
workspaces:

| Product entry | Internal ReportPack | Best for |
|---|---|---|
| `industry-weekly` | `market_weekly` | recurring market, industry, policy, or competitor updates |
| `management-monthly` | `management_monthly` | recurring management review and executive briefing packages |
| `document-review` | `evidence_extract` | local document evidence review with explicit scope |

`solar-stock-periodic` is the fresh schema-19 experimental capital-markets
weekly entry released in v0.15.3. The wider legacy `solar-periodic` Product OS
extension also remains experimental. Neither is part of the stable v0.11
product baseline.

## Boundary

BriefLoop helps create business briefs with traceable claims, source discipline,
quality gates, event logs, and human delivery. It does not prove semantic truth.
It does not authorize public release, publish reports, eliminate
hallucinations, or replace human review.

The product layer wraps the control spine. It must preserve the Claim Ledger,
artifact registry, quality gates, event log, archive, source appendix, support
records, human delivery approval, and frozen artifact integrity.

## 1. Create A Workspace

Choose the product entry that matches the work.

```bash
briefloop new industry-weekly ./weekly-brief \
  --company "ExampleCo" \
  --industry "industrial equipment" \
  --audience "management team" \
  --title "ExampleCo Industry Weekly" \
  --language en-US

briefloop new management-monthly ./monthly-review \
  --company "ExampleCo" \
  --audience "executive team" \
  --title "ExampleCo Management Monthly" \
  --language en-US

briefloop new document-review ./document-review \
  --company "ExampleCo" \
  --audience "review team" \
  --title "ExampleCo Document Review" \
  --language en-US
```

The workspace is local-first. It writes `report_spec.yaml`, `config.yaml`,
`sources.yaml`, `user.md`, `input/`, and `.gitignore`. It does not run stages,
fetch hidden sources, or deliver anything.

## 2. Add Source Materials

For `industry-weekly` and `management-monthly`, start with a few prepared local
text files:

```bash
cp ./sources/*.md ./weekly-brief/input/sources/
```

For a Human-reviewed upload flow, use the one-shot local initialization wizard
and review its canonical source manifest before submitting:

```bash
briefloop init ./document-review --web
```

The retired `briefloop extract` and `briefloop sources ...` commands are not
available on SQLite workspaces. If `runtime continue` requests a Human source
pack, follow that exact Store-bound request.

Binary/PDF inputs are not automatically converted into supported evidence by
the product entry alone. If a binary source is registered-only, convert or
extract it through the supported input path before asking the runtime to use its
contents as evidence.

## 3. Start The Runtime Handoff

Create or refresh the runtime handoff:

```bash
briefloop run --workspace ./weekly-brief --runtime codex
```

`run` is a handoff launcher. It does not mark stages complete by itself and does
not bypass deterministic transactions.

Continue with the Store-derived action:

```bash
briefloop runtime continue --workspace ./weekly-brief
```

Complete only the exact returned role proposal or deterministic action, then
continue again. Stop at `needs_human`, `needs_attention`, `finalized_local`, or
`terminated`.

## 4. Inspect Status Before Acting

Use status whenever you are unsure:

```bash
briefloop status --workspace ./weekly-brief
briefloop status --workspace ./weekly-brief --json
```

Status is read-only. It shows current stage, missing artifacts, blockers, gate
state, product projections, and next safe actions. If a control artifact is
missing or stale, follow the named deterministic command instead of editing the
artifact by hand.

## 5. Handle Feedback As Feedback

When a finalized brief needs reader feedback, open the secured local Review
Session rather than editing frozen artifacts:

```bash
briefloop quality laj review-open --workspace ./weekly-brief
```

Human observations are not source evidence or model findings. Guidance requires
a separate approval and explicit successor opt-in before reuse. The retired
`briefloop feedback` command family is unavailable on SQLite workspaces.

## 6. Deliver Only After Gates Pass

Finalization and any later package/delivery step are typed Store actions. Keep
using bounded continuation:

```bash
briefloop runtime continue --workspace ./weekly-brief
```

At `finalized_local`, the local reader files normally include:

```text
output/brief.md
output/brief_pages.html
```

An explicitly authorized package/delivery may additionally create
`output/delivery/brief.md` and a named DOCX. SQLite workspaces do not expose a
standalone `briefloop deliver` command or a force-deliver path.

Audit and control artifacts stay in the workspace for review and traceability.
They are not a second reader delivery:

```text
output/intermediate/claim_ledger.json
output/intermediate/audit_report.json
output/source_appendix.md
event_log.jsonl
```

If the reader-final gate fails, do not move or publish the files manually. Open
the referenced gate or finalize report, repair through the workflow, and rerun
the deterministic delivery path.

## 7. A Clean First Run Checklist

For a first product run, keep the scope small:

- one product entry: `industry-weekly`, `management-monthly`, or
  `document-review`;
- three to five local text sources;
- no hidden web crawling;
- no manual edits to frozen control files;
- no force-deliver path or delivery override flag;
- human review before sharing the reader-facing files.

When this path is confusing, treat the confusion as a product documentation bug.
Do not compensate by bypassing ledgers, gates, events, archive, or human
delivery.
