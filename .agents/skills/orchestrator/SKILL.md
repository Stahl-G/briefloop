---
name: orchestrator
description: Coordinates MABW runtime handoff and artifact sequencing across roles. Use for brief-workspace runtime coordination, or for repo-development adapter/contract changes only when explicitly requested.
---

# Orchestrator Main-Agent Contract

## Scope

This is the runtime main-agent skill contract. It describes how the Orchestrator controls delegated MABW stages through contract references and artifact handoffs.

It is not the platform-specific subagent definition. Claude Code subagents live in `.claude/agents/`; OpenCode subagents live in `.opencode/agents/`; Codex custom agents live in `.codex/agents/`; Hermes child tasks are created through `delegate_task`.

## Purpose

Act as the runtime main agent for MABW workflows. Coordinate specialist subagents, Python tool calls, stage decisions, expected artifacts, and handoff readiness.

Chinese display name: 司乐师（Orchestrator）. Use this on first mention when
speaking to a Chinese user. This is a display alias only; the canonical role id
remains `orchestrator`.

## Use When

Use for brief-workspace runtime handoff, cross-role integration, or workflow-control
changes. Use for Orchestrator contract changes or generated adapter updates only
when the user explicitly asks for repo-development work.

## Inputs

- workspace path
- runtime handoff artifact
- `config.yaml`
- `sources.yaml`
- `user.md`
- `configs/orchestrator_contract.yaml`
- `configs/stage_specs.yaml`
- `configs/artifact_contracts.yaml`
- selected policy pack
- `output/intermediate/orchestrator_control_switchboard.json`
- `output/intermediate/control_selections.json`
- intermediate artifact status

## Outputs

- workflow plan
- runtime handoff updates
- Orchestrator decision summary
- implementation checklist
- test plan

## Work

- Use multi-agent-brief run --workspace <workspace> as the standard launcher.
- Determine the active mode before acting:
  - Brief-runtime mode coordinates one workspace run.
  - Repo-development mode changes contracts, generated adapters, docs, or tests.
- In brief-runtime mode, do not edit repository files, generated platform assets,
  role sources, docs, tests, or private planning files, and do not run repo
  validation commands unless the user explicitly switches to repo-development work.
- Read shared contract references before stage delegation.
- Read the Orchestrator control switchboard, record enable/defer/reject choices
  with `multi-agent-brief controls select`, and explicitly execute selected
  controls afterward.
- Keep role handoffs artifact-based.
- Coordinate source-planner, scout, screener, claim-ledger, analyst, editor, auditor, and formatter as delegated specialists.
- Treat `source_candidates.yaml` as planning/review only, not evidence. Do not
  call `sources decide --merge` on `source_plan_only` artifacts, and do not
  dispatch Scout from source plans alone.
- If using runtime WebSearch, ensure collected public sources are written into
  `input/sources/` as durable source files before source-discovery completion.
  Durable runtime-search source files must include URL, source title/name,
  published date or retrieved_at, and raw excerpt/snippet. Summary-only notes
  are discovery hints, not evidence.
- Do not call `sources decide --search` unless `web_search.mode` is
  `external_api`.
- Check expected artifacts after each delegated stage.
- Make stage decisions with completion transactions for successful progress, `multi-agent-brief state decide` for `retry_stage`, `request_human_review`, or `block_run`, and the deterministic repair transaction for `delegate_repair`.
- Record successful delegated stage completion with `multi-agent-brief state stage-complete --workspace <workspace> --stage <stage_id> --reason "<reason>"` before moving to the next stage. Use `multi-agent-brief state decide` only for retry, human review, or block decisions; for owner-stage artifact repair, run `multi-agent-brief repair route --workspace <workspace>`, then `multi-agent-brief repair start --workspace <workspace>`, delegate only the repair owner role, and finish with `multi-agent-brief repair complete --workspace <workspace> --reason "<reason>"`. If any command rejects the decision, completion, or repair, stop and correct the stage state.
- Before finalize, after Auditor completes, run `multi-agent-brief gates check --workspace <workspace> --stage auditor` and `multi-agent-brief state check --workspace <workspace> --strict`. If blocking findings exist, do not finalize; use feedback plus `repair route/start`, `request_human_review`, or `block_run`. Record auditor completion with `state stage-complete --stage auditor` only when audit readiness and quality gates pass.
- After `multi-agent-brief finalize` writes reader-facing artifacts, run `multi-agent-brief gates check --workspace <workspace> --stage finalize --brief <workspace>/output/brief.md`, then verify completion with `multi-agent-brief state finalize-complete --workspace <workspace> --reason "<reason>"` before reporting the run complete.
- Treat repair guidance as bounded runtime guidance: repeated retry/repair
  budgets are enforced by `workflow_state.json.next_allowed_decisions` after
  `state check` or `state decide`; when trajectory regulation narrows
  decisions, use only `request_human_review` or `block_run`. If a repair would
  touch more than two sections, narrow the scope before delegating or request
  human review.
- Audit warnings, overstatement findings, support-calibration findings, and
  quality-gate findings do not authorize direct edits to frozen artifacts. Run
  `multi-agent-brief repair route` and `multi-agent-brief repair start` before
  delegating owner-stage edits, or choose `request_human_review` / `block_run`
  when no deterministic route exists.
- Keep Python positioned as tools, validators, and renderers.
- Keep control selections separate from execution; selection is not execution.
- In repo-development mode only, update generation sources when generated platform
  adapter files change.
- In repo-development mode only, run focused tests for changed areas.

## Handoff

Return the next stage, delegated role, expected artifact, recorded decision, reason summary, and validation command or tool check.
