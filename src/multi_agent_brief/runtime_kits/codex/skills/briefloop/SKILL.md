---
name: briefloop
description: Use when operating this workspace through the SQLite-only BriefLoop Codex runtime.
---

# BriefLoop Codex Runtime

Read `references/controlstore-v2.md` completely before acting.

For a run with either the Store-frozen M2 execution authorization or the
narrow Store-frozen Tavily source-discovery authorization, prefer the bounded
controller seam:

```bash
briefloop runtime continue --workspace <workspace>
```

It applies only existing Store-derived deterministic effects. When it returns
`role_work_required`, write only the exact envelope's allowed scratch proposal,
then call `runtime continue` again. `proposal_invalid` is value-free guidance
and does not fail or replace the invocation. Stop on `needs_human`,
`needs_attention`, or truthful `finalized_local`. Discovery-only continuation
may run the doctor, source planner, and exact bound Tavily route. The workspace
credential remains local until the Human rotates/removes it, but each attempt
needs one distinct Human/Store authorization and permits at most one Search
followed by one batch Extract over at most five deduplicated URLs. Exact replay never
redials and failures never auto-retry. A typed recovery action requires the
Human to authorize another attempt or provide a HumanSourcePack. HumanSourcePack
never counts as Tavily success. Search snippets remain ineligible; only non-empty
successful Extract content can enter the single
atomic source/manifest/classification/execution-authorization promotion
receipt. A run without either authorization remains on the granular protocol
below.

The workspace kit supports Store binding and reopened/future sessions. It is
not evidence that an already-running Codex session hot-loaded newly written
project assets; the supported uninterrupted flow is an already-active
controller continuing with the protocol it already loaded.

On truthful `finalized_local`, `runtime continue` may return a best-effort
read-only `presentation`. Its relative static file is
`output/brief_pages.html`; `browser_unavailable` retains that safe relative
path, while `projection_unavailable` has no path because no safe projection
was written. Neither failure changes terminal truth. The HTML uses the exact Store-bound `reader_brief` and
is not approval, packaging, delivery, publication, or a persistent localhost
service. LAJ remains explicit hash-bound advisory input. Any Improvement tab is
a read-only Store-native Human-guidance projection, not a legacy ledger or reuse
action.

After a verified `finalized_local` head, only the root host may run the
separate explicit Human transaction:

```bash
briefloop runtime successor-start --workspace <workspace> \
  --direction-json '<strict RunDirection JSON>' \
  --run-id <new-run-id> \
  --include-approved-guidance
```

This is not a `CoreRunNextAction` kind or recovery reset. The final flag is the
explicit reuse opt-in. Python atomically freezes compatible active-approved
guidance within 16 items / 65,536 UTF-8 bytes; replay is exact and no provider
or role is called. Analyst/Editor may use an envelope
`FrozenGuidanceContext` only for audience fit, structure, style, and
expression. Current `RunDirection` and evidence govern; guidance is not fact,
source, Claim Ledger input, Gate, repair, or delivery authority. All other
roles receive none, and no role reads live guidance or retired Improvement
files.

The Store-derived `CoreRunNextAction` is the only sequence authority. Always
snapshot the exact current action JSON, then dispatch only its `action_kind`:

- `delegate`: run `runtime invocation-start`, obey the exact
  `RoleTaskEnvelope`, write only its scratch proposal, then have the root run
  `invocation-accept` or `invocation-fail`.
- `deterministic`: have the root run `runtime apply` with the exact action;
  never delegate deterministic authority. For an already-active
  `invocation_accept_or_fail`, exact-action apply and exact-envelope accept run
  the same proposal preflight before any Store write.
- `human_decision`: stop for the complete strict request named by
  `request_schema_id`; chat text is not approval.
- `blocked`: report the exact reason and make no mutation or fallback.
- `complete`: report the exact terminal effect; `package_ready` is not
  `delivered`.

Agents write only the filenames allowed inside the current invocation scratch
directory. Never write SQLite, receipts, ledger rows, canonical artifacts, or
frozen revisions. Never infer legality from JSON/JSONL, Markdown, HTML, status,
Quality Panel, checkout files, prompts, or memory. Never fall back to a legacy
handoff, JSON workspace, `operator`, migration, dual mode, or another runtime.
For a strict JSON role proposal, never guess its contract shape: run the exact
`briefloop contract show` and `briefloop runtime invocation-validate` commands
embedded in the current `RoleTaskEnvelope.task_instructions` before acceptance.
