---
name: briefloop
description: Use when creating, running, continuing, inspecting, repairing, finalizing, or reviewing a BriefLoop SQLite/Codex workspace from a DeepSeek Harness (DSH) session, including Solar Stock Periodic, market data, AI Second Opinion, Human observations, approved guidance, and successor runs. Prefer the single-session runtime continue path; use exact Store actions only for recovery. Do not use for ordinary repository development unless the user is changing this Skill.
---

# BriefLoop Operator (DeepSeek Harness)

BriefLoop is the only current project and product name. The former project
acronym is retired.

## Scope

Operate a BriefLoop workspace from initialization through `finalized_local` and
optional post-final review. The ordinary path is one active DeepSeek Harness
(DSH) session and one exact role invocation at a time. Dispatch a role as a DSH
subagent on the matching BriefLoop preset from the workspace kit; do not create
an agent swarm, parallelize dependent roles, or reconstruct the workflow from
filenames.

The SQLite Store and its Receipts are the only runtime authority. A
Store-derived `CoreRunNextAction` decides sequence and legality. Agents write
invocation-scoped proposals; deterministic Python services validate, accept,
freeze, and advance them. Human decisions take effect only through strict,
Store-bound requests. DSH presets, skills, and tools are a comfort layer only:
they never write SQLite or decide legality.

The five action kinds are `delegate`, `deterministic`, `human_decision`,
`blocked`, and `complete`.

Read [references/controlstore-v2.md](references/controlstore-v2.md)
before low-level recovery, applying a Human request, or diagnosing an uncertain
external call.

## Purpose

- Move an authorized workspace through the shortest supported path without
  weakening evidence, Gate, approval, or append-only boundaries.
- Keep content work separate from authoritative state changes.
- Stop visibly on Human decisions, invalid proposals, integrity failures, and
  terminal states.
- Expose post-final AI advice and Human improvement records without turning
  either into a Gate or delivery decision.

## Use When

Use this Skill when the user asks to:

- initialize or continue a real BriefLoop workspace;
- inspect a stalled run, Gate, role proposal, or finalization state;
- run Solar Stock Periodic discovery and market-data projection;
- open, diagnose, or use AI Second Opinion;
- record a Human observation, approve guidance, or start a successor.

For source-code changes, follow repository development instructions instead.

## Inputs

Resolve and retain:

- the exact workspace path; the Store runtime adapter is `codex` in this
  experimental slice, while DSH is the operating environment;
- `briefloop.db`, run id, Store revision, and current action fingerprint;
- the exact `RoleTaskEnvelope` when role work is active;
- the exact `request_schema_id` and request bytes for a Human decision;
- explicit Human authorization for provider calls, delivery, guidance reuse,
  or another attempt.

Never read or repeat credential values. A configured key is capability, not
authorization to call a provider.

## Outputs

Produce only what the current state allows:

- one valid scratch proposal for the exact active invocation;
- one deterministic Store transaction and Receipt;
- one fully bound Human request after explicit confirmation;
- one typed stop report for a block or attention state; or
- one terminal report naming `finalized_local`, `package_ready`, or
  `delivered` exactly as recorded.

Post-final output may also include a static `output/brief_pages.html`, an
actionable local review URL, Store-native Human observations/guidance, or an
explicit successor snapshot. These projections never become runtime authority.
`browser_unavailable` preserves the safe static path;
`projection_unavailable` has no path because no safe projection was written.

## Work

### 1. Enter the current Store state

Start with read-only diagnosis:

```bash
briefloop runtime diagnose --workspace <workspace>
```

For a newly initialized workspace, install/select the packaged Codex runtime
through the normal `briefloop init` / `briefloop run --runtime codex` path. Do
not edit `.codex/` after it has been bound to the Store.

To prepare the DSH operating kit for a workspace:

```bash
briefloop runtime install --workspace <workspace> --runtime dsh
briefloop run --workspace <workspace> --runtime dsh
```

The DSH kit lands under `.dsh/`. Copy its role presets into your DSH preset
root once (see `references/controlstore-v2.md`), then dispatch each role as a
subagent on its matching preset. The kit is replaceable comfort material: the
Store stays codex-bound and does not re-bind on install.

### 2. Prefer bounded continuation

For an authorized run, use:

```bash
briefloop runtime continue --workspace <workspace>
```

Dispatch its result exactly:

- `role_work_required`: read the exact envelope. If its
  `dispatch_instruction` is `execute_in_current_session`, perform that one role
  task here. If it is `delegate_exact_role`, materialize the dispatch context
  with the read-only `briefloop_role_dispatch` tool and start exactly one DSH
  subagent with its returned `dispatch_prompt` (the matching `briefloop-<role>`
  preset carries the role contract). Write only the named files under the
  envelope's scratch directory, run the embedded `contract show` and
  `runtime invocation-validate` commands, then call `runtime continue` again.
- `proposal_invalid`: repair only the current scratch proposal from the typed
  violations, validate again, and do not create another invocation.
- `needs_human`: stop. Show the consequential fields, exact decision choices,
  expected Store revision, and cost/retry effect. Chat approval alone does not
  mutate the Store.
- `needs_attention`: use read-only diagnosis and report the fixed reason code.
  Do not bypass the block or invent a fallback.
- `finalized_local`: report the final artifact/projection paths and preserve
  the distinction from approval, packaging, and delivery.

Do not fan out roles. BriefLoop's normal sequence is source planning/provider,
Scout, Screener, Claim Ledger, Analyst, Editor, Auditor, and finalization, but
the current Store action—not this list—decides what is next.

### 3. Solar Stock Periodic

Create a fresh schema19 workspace with:

```bash
briefloop new solar-stock-periodic <workspace>
# When the workbook already defines the reporting period, freeze it before run:
briefloop new solar-stock-periodic <workspace> \
  --report-window-start <YYYY-MM-DD> --report-window-end <YYYY-MM-DD>
```

The paired report-window flags are the Human confirmation for the workbook
period. They must be set before Store initialization; market-data ingest never
rewrites RunDirection.

Its frozen plan uses 20 independent first-pass tasks: 11 listed securities, 5
event-only entities, and 4 themes. Each task may request 20 advanced results;
coverage gaps may receive one deterministic 30-day targeted backfill. Never
collapse the plan into one broad query or restore the obsolete top-five path.

Search results are discovery candidates only. Only successful, non-empty Batch
Extract content can enter the source pack and later support claims. The runtime
owns provider calls and freezes per-task status; a specialist must not call
Tavily directly or treat a snippet as evidence.

Use the separate market-data channel as needed:

```bash
briefloop market-data fetch --workspace <workspace>
briefloop market-data ingest --workspace <workspace> --file <json-or-csv>
briefloop market-data ingest --workspace <workspace> \
  --file <weekly.xlsx> --profile toyo-weekly-v1
briefloop market-data fetch --workspace <workspace> \
  --workbook <weekly.xlsx> --profile toyo-weekly-v1
briefloop market-data project --workspace <workspace>
```

The XLSX-only `ingest` path is offline. The workbook-aware `fetch` path keeps
verified manual cells authoritative and uses Yahoo only to fill missing
securities, adjusted-close history, FX, or fields. Conflicts remain visible;
missing required price series block delivery. The projection writes comparison
tables, a JSON read model, seven deterministic PNG charts, and a hash-bound
chart manifest. Product-owned price, period-return, and FX-conversion formulas
are recomputed from frozen inputs; Excel formula caches are comparison-only.
An embedded workbook chart is display-only and never evidence.
Never copy structured market values into causal prose without a separately
frozen Claim Ledger claim.

### 4. AI Second Opinion and improvement

Open the current-head actionable review session with:

```bash
briefloop quality laj review-open --workspace <workspace>
```

Keep that command running while the browser session is in use. The page never
accepts or renders an API key. An assessment with no findings may be valid; it
is different from provider unable-to-assess, `local_derivation_failed`, and a
true `outcome_unknown` with no execution receipt.

If an execution receipt exists, recovery is local derivation/replay and must
not redial the provider. If no execution receipt exists after an uncertain
call, preserve outcome uncertainty and require a new explicit generation
decision before any new paid call.

Human observations are report-bound, append-only records; they are not model
findings. Guidance creation and approval are separate actions. Starting a
successor and including approved guidance are both explicit Human choices:

```bash
briefloop runtime successor-start --workspace <workspace> \
  --direction-json '<strict RunDirection JSON>' \
  --run-id <new-run-id> \
  --include-approved-guidance
```

Without `--include-approved-guidance`, the successor freezes an empty guidance
snapshot. `FrozenGuidanceContext` is available only to Analyst and Editor for
audience fit, structure, style, and expression. Current `RunDirection` and
evidence govern; guidance is never a fact, Gate rule, or delivery authority.

### 5. Fail closed

- Never write SQL, `briefloop.db`, Receipts, events, ledgers, or frozen
  artifacts directly.
- Never edit a frozen artifact; create the authorized new revision instead.
- Never reuse a stale action after `runtime_action_stale`.
- Never turn provider failure into “no event” or “no finding”.
- Never auto-retry an external provider call.
- Never infer that `package_ready` means `delivered`.
- Never fall back to a legacy JSON control plane or another runtime.

When a Human review exposes no lawful repair path, the current action may bind
`briefloop.run_termination_request.v2`. Show its exact action fingerprint,
Store revision, typed reason and irreversible consequence before applying it.
A successful termination returns `complete/run_terminated`; subsequent
`runtime continue` calls return `terminated` without role, provider,
finalization, post-final review, or delivery work. Start a new run for any
further work.

## Handoff

Report the workspace, run id, Store revision, action/effect kind, action
fingerprint, invocation/envelope when present, fixed reason codes, and the one
lawful next step. State whether any provider call occurred and whether its
execution evidence was frozen. For terminal work, distinguish finalized,
approved, packaged, delivered, and post-final advisory state.
