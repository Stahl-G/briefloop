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
- `briefloop runtime next`, `diagnose`, `invocation-start`,
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
- the explicit statement that projections were not used to decide legality
