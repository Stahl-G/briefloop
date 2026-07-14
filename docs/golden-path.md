# BriefLoop v0.11 Product Golden Path

This is the shortest product path for a normal BriefLoop user. This product
path is not an experiment harness, benchmark protocol, or reference-run
showcase. It answers one practical question: how do I create, run, inspect, and
deliver a traceable business brief without bypassing the control spine?

Use this path when you want one of the supported v0.11 product-baseline
workspaces:

| Product entry | Internal ReportPack | Best for |
|---|---|---|
| `industry-weekly` | `market_weekly` | recurring market, industry, policy, or competitor updates |
| `management-monthly` | `management_monthly` | recurring management review and executive briefing packages |
| `document-review` | `evidence_extract` | local document evidence review with explicit scope |

`solar-periodic` remains experimental for command usage. In plain terms:
solar-periodic remains an experimental Product OS extension. It may be useful
for dogfood, but it is not part of the stable v0.11 product baseline.

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

For `document-review`, register source files with an explicit scope:

```bash
briefloop extract \
  --workspace ./document-review \
  --sources "./docs/*.md" \
  --scope "contracts, permits, production capacity, dates, named obligations"
```

Binary/PDF inputs are not automatically converted into supported evidence by
the product entry alone. If a binary source is registered-only, convert or
extract it through the supported input path before asking the runtime to use its
contents as evidence.

## 3. Start The Runtime Handoff

Create or refresh the runtime handoff:

```bash
briefloop run --workspace ./weekly-brief --runtime operator
```

In Claude Code, the writer command equivalent is:

```text
/briefloop run ./weekly-brief
```

Then follow the generated handoff. In the Claude writer path, keep using
`/briefloop status` and the generated handoff to choose the next safe action:

```text
/briefloop status ./weekly-brief
```

`run` is a handoff launcher. It does not mark stages complete by itself and does
not bypass deterministic transactions.

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

When a draft needs work, record feedback rather than editing frozen artifacts:

```bash
printf '%s\n' "Lead with business impact before listing news." > ./weekly-brief/input/feedback/human-feedback.md
briefloop feedback ingest \
  --workspace ./weekly-brief \
  --source human \
  --feedback ./weekly-brief/input/feedback/human-feedback.md
```

Feedback is not source evidence and is not automatically Improvement Memory.
Fact/source issues go through repair, audit, or gates. Stable reader
preferences require human approval before they are reused in a later run.

## 6. Deliver Only After Gates Pass

Use delivery after the run has passed the required gates and finalize state:

```bash
briefloop deliver --workspace ./weekly-brief
```

Reader-facing outputs are under:

```text
output/delivery/brief.md
output/delivery/<named-brief>.docx
```

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
