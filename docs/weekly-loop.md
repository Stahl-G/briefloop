# Weekly Loop

Use this guide when BriefLoop is part of a recurring briefing rhythm. The goal
is not to remove judgment. The goal is to keep sources, claims, checks, repairs,
and delivery decisions visible.

## The Loop

```text
create or select workspace
-> bind sources before the run
-> run and continue the Store workflow
-> inspect the current action and quality projections
-> repair through the active invocation or stop for Human review
-> reach finalized_local
-> optionally record Human observations and approved guidance
```

## 1. Create Or Select A Workspace

For a new recurring brief:

| Report job | Start with | Best for |
|---|---|---|
| Industry or market weekly | `industry-weekly` | recurring market updates, competitor tracking, policy monitoring |
| Management monthly | `management-monthly` | executive reviews, monthly operating updates, management briefing packs |
| Document review | `document-review` | reviewing a set of documents with page/source traceability |

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

For a Human-reviewed upload flow, use the one-shot local initialization wizard
and review its source manifest before submitting:

```bash
briefloop init ./document-review --web
```

The retired `briefloop extract` and `briefloop sources ...` commands are not a
fallback for a SQLite workspace. If the active run requests a Human source
pack, follow the exact Store-bound request shown by `runtime continue` rather
than editing SQLite or frozen manifests.

Feedback, instructions, and context are not source evidence. Keep them in their
own input folders so claims do not inherit authority from comments or task
notes.

## 3. Run The Handoff

```bash
briefloop run --workspace ./weekly-brief --runtime codex
```

Then follow the generated handoff for your runtime. In normal use, agents draft
and inspect content while deterministic commands record state, freeze artifacts,
run checks, and prepare delivery.

Continue through the bounded Store path:

```bash
briefloop runtime continue --workspace ./weekly-brief
```

Repeat only after completing the exact returned role proposal or deterministic
action. Stop when it returns `needs_human`, `needs_attention`, `finalized_local`,
or `terminated`; do not infer a retry or another stage.

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

For post-final reader feedback, open the secured local Review Session and keep
the command running while the page is in use:

```bash
briefloop quality laj review-open --workspace ./weekly-brief
```

The Store-native observation/guidance lifecycle is Experimental in v0.15.3.
It keeps Human observations separate from model findings, and guidance approval
does not automatically affect a later run. The retired `briefloop feedback`
commands are unavailable on SQLite workspaces.

Do not use feedback as evidence. If a number, date, source, or factual claim is
wrong, treat it as a source or repair issue, not as a reader preference.

## 6. Deliver By Human Action

Finalization and any later package/delivery step are Store actions. Continue the
runtime until it reports the exact terminal or Human boundary:

```bash
briefloop runtime continue --workspace ./weekly-brief
```

At `finalized_local`, the local reader projection is normally available under:

```text
output/brief.md
output/brief_pages.html
```

An explicitly authorized package or delivery may also create files under
`output/delivery/`. There is no supported standalone `briefloop deliver`
command on SQLite and no delivery override for bypassing failed checks.

## 7. Keep The Workspace Reviewable

Good weekly hygiene:

- keep source files organized by date or issue;
- inspect `briefloop status --workspace <workspace>` before repair or delivery;
- keep fact problems separate from style preferences;
- approve only reusable feedback;
- start a new workspace when the audience, objective, or source policy changes.
