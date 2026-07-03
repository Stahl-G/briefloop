# Weekly Loop

Use this guide when BriefLoop is part of a recurring briefing rhythm. The goal
is not to remove judgment. The goal is to keep sources, claims, checks, repairs,
and delivery decisions visible.

## The Loop

```text
create or select workspace
-> add sources
-> run handoff
-> inspect quality summary
-> repair or record feedback
-> deliver by human action
-> keep approved feedback for next time
```

## 1. Create Or Select A Workspace

For a new recurring brief:

```bash
briefloop new industry-weekly ./weekly-brief
```

Other supported entries:

```bash
briefloop new management-monthly ./monthly-review
briefloop new document-review ./document-review
```

Use one workspace per recurring briefing package. Do not reuse a workspace for a
different audience, topic, or delivery standard unless you explicitly change the
workspace configuration.

## 2. Add Sources

Put local source files under:

```text
input/sources/
```

For document-review work, register source files and scope explicitly:

```bash
briefloop extract \
  --workspace ./document-review \
  --sources "./docs/*.md" \
  --scope "contracts, permits, production capacity, dates, named obligations"
```

Feedback, instructions, and context are not source evidence. Keep them in their
own input folders so claims do not inherit authority from comments or task
notes.

## 3. Run The Handoff

```bash
briefloop run --workspace ./weekly-brief
```

Then follow the generated handoff for your runtime. In normal use, agents draft
and inspect content while deterministic commands record state, freeze artifacts,
run checks, and prepare delivery.

## 4. Inspect Status And Quality Summary

Use status before taking action:

```bash
briefloop status --workspace ./weekly-brief
```

Open the quality summary when it exists:

```text
output/intermediate/quality_summary.md
```

Look for:

- missing sources;
- failed checks;
- stale or incomplete artifacts;
- reader-clean problems;
- repair recommendations.

Status and the quality summary are guidance surfaces. They do not approve
delivery or prove source support.

## 5. Repair Or Record Feedback

If a check fails, repair through the workflow instead of editing frozen files.
Use status to find the next safe action.

For reader feedback:

```bash
mkdir -p ./weekly-brief/input/feedback
printf '%s\n' "Lead with business impact before listing news." \
  > ./weekly-brief/input/feedback/human-feedback.md

briefloop feedback ingest \
  --workspace ./weekly-brief \
  --source human \
  --feedback ./weekly-brief/input/feedback/human-feedback.md
```

Do not use feedback as evidence. If a number, date, source, or factual claim is
wrong, treat it as a source or repair issue, not as a reader preference.

## 6. Deliver By Human Action

After required checks and finalize state pass:

```bash
briefloop deliver --workspace ./weekly-brief
```

Reader-facing files are under:

```text
output/delivery/
```

Audit and intermediate files stay with the workspace so the team can review how
the brief was produced. Delivery is human-triggered; there is no delivery
override path for bypassing failed checks.

## 7. Keep The Workspace Reviewable

Good weekly hygiene:

- keep source files organized by date or issue;
- inspect `briefloop status --workspace <workspace>` before repair or delivery;
- keep fact problems separate from style preferences;
- approve only reusable feedback;
- start a new workspace when the audience, objective, or source policy changes.
