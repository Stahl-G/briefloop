---
name: briefloop
description: Use when operating this workspace through the SQLite-only BriefLoop Codex runtime.
---

# BriefLoop Codex Runtime

Read `references/controlstore-v2.md` completely before acting.

For an M2-authorized `finalized_local` run, prefer the bounded controller seam:

```bash
briefloop runtime continue --workspace <workspace>
```

It applies only existing Store-derived deterministic effects. When it returns
`role_work_required`, write only the exact envelope's allowed scratch proposal,
then call `runtime continue` again. `proposal_invalid` is value-free guidance
and does not fail or replace the invocation. Stop on `needs_human`,
`needs_attention`, or truthful `finalized_local`. A run without the execution
authorization remains on the granular protocol below.

The workspace kit supports Store binding and reopened/future sessions. It is
not evidence that an already-running Codex session hot-loaded newly written
project assets; the supported uninterrupted flow is an already-active
controller continuing with the protocol it already loaded.

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
