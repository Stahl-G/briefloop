---
name: briefloop
description: Use when operating a fresh SQLite-only BriefLoop workspace from Codex, or when changing its runtime protocol and public claims.
---

# BriefLoop Codex Protocol

## Scope

This is the canonical repo-local protocol for the active BriefLoop runtime.
Fresh runs are SQLite-only and Codex-only. The Store-derived
`CoreRunNextAction` is the sole sequence authority; agents never reconstruct
legality from files, prompts, prior turns, or projections.

For a business workspace, read `references/codex-controlstore-v2.md` completely
before acting. For repository changes or public wording, also read
`references/repo-development.md` or `references/public-claims.md`. Use
`references/version-matrix.md` to distinguish the installed release from the
next release target.

## Purpose

- Keep the Codex host on the exact Store-approved action.
- Keep agent work proposal-only and invocation-scoped.
- Keep deterministic effects, receipts, frozen artifacts, approval, and
  delivery under Python/ControlStore authority.
- Make unsupported, blocked, stale, and terminal states visible without
  improvising a fallback.

## Use When

Use this skill for:

- `briefloop run --workspace <workspace> --runtime codex`
- `briefloop runtime next`, `continue`, `successor-start`, `diagnose`, `invocation-start`,
  `invocation-accept`, `invocation-fail`, or `apply`
- Codex role dispatch and invocation scratch proposals
- package-ready, human approval, delivery authorization, or delivery status
- repository changes to the Codex runtime protocol or claims about it

Do not route current work through the retired JSON control plane, legacy
handoffs, `operator`, or another runtime.

## Inputs

For a runtime workspace, require:

- a fresh workspace accepted by `briefloop run --runtime codex`
- `briefloop.db` as the sole run authority
- the exact current `CoreRunNextAction` JSON
- for role work, the materialized `RoleTaskEnvelope`
- for human decisions, the exact strict request named by
  `request_schema_id`

Config and source setup files are initialization inputs. After initialization,
their mutable bytes do not decide runtime legality.

## Outputs

Return or materialize only the contract required by the current action:

- `delegate`: one recorded invocation and one scratch-only proposal (or one
  recorded invocation failure)
- `deterministic`: one host-applied deterministic effect
- `human_decision`: one human-reviewed strict request applied by the host
- `blocked`: the exact reason/effect and no mutation
- `complete`: the exact terminal effect, preserving the distinction between
  `package_ready` and `delivered`

After a successful transaction, obtain a fresh action. Never reuse a prior
action snapshot as the next instruction.

## Work

Follow `references/codex-controlstore-v2.md` as an executable state machine.

For a run with either the Store-frozen M2 execution authorization or the
narrow Store-frozen Tavily source-discovery authorization, use
`briefloop runtime continue --workspace <workspace>` as the bounded controller
seam. On `role_work_required`, perform only the exact current-session envelope
work and call it again. Stop on `proposal_invalid`, `needs_human`,
`needs_attention`, or `finalized_local` as directed; never invent the missing
request or repair. During discovery-only continuation, the host may run the
doctor, source planner, and exact bound Tavily route. The workspace credential
remains local until the Human rotates/removes it, but each attempt needs one
distinct Human/Store authorization and permits at most one Search followed by
one batch Extract over at most five deduplicated URLs. Exact replay never redials and
failures never auto-retry. A typed recovery action requires the Human to
authorize another attempt or provide a HumanSourcePack. HumanSourcePack never
counts as Tavily success. Search snippets remain ineligible; only non-empty
successful Extract content can participate in the single atomic
source/manifest/classification/execution-authorization promotion receipt.
Unauthorized runs retain the granular/manual protocol.

An init-web response does not hot-load the newly installed workspace kit into
the initiating Codex process. The uninterrupted path is an already-active
controller continuing under its already-loaded BriefLoop protocol; the kit is
Store-bound for verification and reopened/future workspace sessions.

When authorized continuation returns truthful `finalized_local`, its
`presentation` is best-effort and read-only. A successful static projection is
`output/brief_pages.html`; `browser_unavailable` retains that safe relative
path, while `projection_unavailable` has no path because no safe projection
was written. Neither result changes finalization truth. The file contains the exact Store-bound
`reader_brief`, not mutable workspace Markdown, and is not approval, packaging,
delivery, publication, or a persistent localhost service. LAJ appears only
when an explicit hash-bound advisory view is supplied. Any Improvement tab is a
read-only projection of Store-native Human guidance state; it is not a legacy
Improvement Ledger, approval action, successor request, or proof of reuse.

After a verified `finalized_local` head, a Human may explicitly start a normal
same-workspace successor with:

```bash
briefloop runtime successor-start --workspace <workspace> \
  --direction-json '<strict RunDirection JSON>' \
  --run-id <new-run-id> \
  --include-approved-guidance
```

This is a separate root-host Human transaction, not a sixth
`CoreRunNextAction` kind and not recovery reset. The final flag is the explicit
reuse opt-in; without it the successor freezes an empty guidance snapshot.
Python atomically creates the successor and freezes the complete compatible,
active Human-approved set within the 16-item / 65,536-byte bounds. Exact replay
returns the original Receipt/snapshot; conflicts and limit failures write
nothing and no provider or role is called.

When an Analyst or Editor `RoleTaskEnvelope` contains
`FrozenGuidanceContext`, use it only for audience fit, structure, style, and
expression. Current `RunDirection` and evidence govern. Guidance is not a fact,
source, Claim Ledger input, Gate rule, repair command, or delivery authority.
All other roles receive no guidance context. Never reread a live guidance head
or retired Improvement file; replay uses the immutable Store snapshot.

Hard boundaries:

- Never write SQL, `briefloop.db`, a Receipt, ledger row, or transaction row.
- Never write a canonical artifact or frozen revision directly.
- Never write outside the current invocation's `scratch_directory`, and only
  use `allowed_output_filenames`.
- Never treat Markdown, HTML, JSON/JSONL, status, Quality Panel, handoff, or
  checkout files as legality.
- Never invent a role, stage, provider, request, retry, approval, or delivery
  decision.
- For strict JSON role proposals, never guess the contract shape. Run the exact
  `contract show` and `runtime invocation-validate` preflight commands embedded
  in the current `RoleTaskEnvelope.task_instructions` before acceptance.
- Never replace exact-role delegation with root drafting, or replace
  current-session execution with a subagent.
- Never infer successor guidance reuse from approval, prior chat, status, or a
  live ledger. It requires the explicit Human successor request and exact
  frozen envelope context.
- Never fall back to legacy JSON, `operator`, migration, dual read/write, or
  another runtime.
- Treat `runtime_action_stale`, invalid envelopes, Store integrity failures,
  and unsupported publication as fail-closed outcomes.
- `package_ready` means a local package is ready for human-controlled next
  steps. Only `complete` with `effect_kind=delivered` means delivery succeeded.

## Handoff

Include:

- workspace path, run id, Store revision, action kind, effect kind, and action
  fingerprint
- envelope path and invocation id when a role invocation exists
- whether the next step is Codex role work, deterministic host work, a strict
  human request, a block, or a terminal report
- all fixed reason codes or unsupported boundaries encountered
- successor snapshot identity when a normal successor or frozen guidance
  context is in scope
- the explicit statement that projections were not used to decide legality
