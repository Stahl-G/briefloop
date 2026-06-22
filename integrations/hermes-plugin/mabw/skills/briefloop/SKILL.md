---
name: briefloop
description: BriefLoop/MABW operator protocol router. Use when a task involves operating a BriefLoop workspace, MABW-080 experiment tooling, future BriefLoop-090 readiness work, repair/gates/status/finalize/delivery decisions, repo-development contract changes, naming compatibility, or public claims about BriefLoop.
---

# BriefLoop Operator Protocol

## Scope

This skill is the canonical repo-local operator protocol for BriefLoop, formerly
MABW. It routes agents to the right public docs, runtime commands, and safety
boundaries when operating workspaces, running MABW-080 experiment tooling for
080 cases or future 090 readiness work, changing the repo, or writing public
claims.

This skill is not the runtime handoff for a specific workspace and is not a
complete CLI manual. Prefer the generated handoff for a run, current CLI help,
`docs/architecture-status.md`, and `docs/support-matrix.md` when they conflict
with this skill.

## Purpose

Keep BriefLoop operation aligned with the control-plane architecture:

- BriefLoop is not the agent. BriefLoop is the loop.
- Agents draft, inspect, route, and report. Deterministic commands write state,
  freeze artifacts, run gates, record events, and deliver archives.
- Frozen artifacts and control files are not edited directly.
- Public claims must not exceed the artifacts, tests, and support matrix.

## Use When

Use this skill when the user asks about any of these surfaces:

- workspace operation, `/briefloop`, `/mabw`, `briefloop`,
  `multi-agent-brief run`, status, gates, repair, finalize, delivery, or
  runtime handoff behavior
- MABW-080 experiment setup, registration, scorecards, blind packs, assessment
  import, summaries, or future BriefLoop-090 readiness/fresh-rerun work that
  still uses the shipped `experiments 080` command namespace
- repo-development changes that affect agent operation, control contracts,
  generated runtime assets, public docs, or release claims
- BriefLoop/MABW naming, compatibility, or public support status

Do not use this skill for unrelated business drafting or source analysis unless
the user explicitly wants that work operated through a BriefLoop workspace.

## Inputs

First classify the mode before acting:

- `runtime-workspace`: a workspace with `config.yaml`, `sources.yaml`, `user.md`,
  `input/`, or `output/intermediate/`
- `experiment-080-090`: an MABW-080 experiment case, condition workspace,
  scorecard, blind pack, reveal mapping, assessment file, or future 090
  readiness artifact using current 080 tooling
- `repo-development`: this source repository, tests, runtime assets, CLI, docs,
  support matrix, generated agents, or release files
- `public-claims`: README, release note, HN/GitHub wording, support status, or
  marketing/research claims

Then read the matching reference:

- runtime workspaces: `references/runtime-workspace.md`
- experiments: `references/experiment-080-090.md`
- owner-stage repair: `references/repair-protocol.md`
- status, gates, finalize, and delivery boundaries:
  `references/status-and-gates.md`
- repo work: `references/repo-development.md`
- public claims: `references/public-claims.md`
- naming and compatibility: `references/naming-and-compatibility.md`
- control-file ownership: `references/control-record-map.md`
- hard red lines: `references/red-lines.md`
- current skill/runtime compatibility: `references/version-matrix.md`

## Outputs

Return the next safe action for the classified mode:

- exact read-only inspection command, transaction command, or repo test command
- whether the action is read-only, a deterministic transaction, or a human-owned
  decision
- any blocker, contamination, active repair, target-complete, or support-status
  caveat that changes what is safe to do next
- the reference file or public doc used to make the call

Do not promise ready-to-send output, truth proof, hallucination elimination,
model-performance improvement, or output-quality improvement unless current
public artifacts support that exact claim.

## Work

Hard boundaries:

- Do not edit frozen artifacts.
- Do not edit `workflow_state.json`, `artifact_registry.json`,
  `runtime_manifest.json`, `event_log.jsonl`, gate reports, or experiment
  scorecards to make state look better.
- Do not bypass gates, stage completion, repair transactions, or
  `finalize-complete`.
- Do not auto-deliver; delivery remains human-triggered and gated.
- Do not approve Improvement Memory without explicit human approval.
- Do not continue from an active owner-stage repair except through
  `repair complete` or read-only inspection.
- Do not run finalize/delivery for `assessment_target=auditable_brief` after the
  auditable target is complete. Register, score, and export assessment artifacts
  instead.
- Do not describe planned v0.9 support-sufficiency controls as implemented.

When changing repo behavior, update source-of-truth files first, then generated
assets or tests. If a PR changes how agents should operate BriefLoop, update
this skill or its references and note the skill impact.

## Handoff

When handing off to another agent or operator, include:

- selected mode
- reference file read
- current workspace or repo path
- next safe command
- artifacts or control files that must not be edited
- whether human judgment or deterministic CLI transaction is required
- public-claim boundary if the task involves docs, releases, demos, or
  experiment evidence
