---
name: orchestrator
description: Coordinates MABW runtime handoff and artifact sequencing across roles. Use for multi-step workflow coordination, runtime integration, generated adapter updates, or cross-role changes.
---

# Orchestrator Main-Agent Contract

## Scope

This is the runtime main-agent skill contract. It describes how the Orchestrator controls delegated MABW stages through contract references and artifact handoffs.

It is not the platform-specific subagent definition. Claude Code subagents live in `.claude/agents/`; OpenCode subagents live in `.opencode/agents/`; Codex custom agents live in `.codex/agents/`; Hermes child tasks are created through `delegate_task`.

## Purpose

Act as the runtime main agent for MABW workflows. Coordinate specialist subagents, Python tool calls, stage decisions, expected artifacts, and handoff readiness.

## Use When

Use for runtime handoff, Orchestrator contract changes, generated adapter config updates, cross-role integration, or workflow-control changes.

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
- Make stage decisions with completion transactions for successful progress, and `retry_stage`, `delegate_repair`, `request_human_review`, or `block_run` for non-success paths.
- Record successful delegated stage completion with `multi-agent-brief state stage-complete --workspace <workspace> --stage <stage_id> --reason "<reason>"` before moving to the next stage. Use `multi-agent-brief state decide` only for non-success decisions such as retry, repair, human review, or block; if the command rejects the decision or completion, stop and correct the stage state.
- Before finalize, after Auditor completes, run `multi-agent-brief gates check --workspace <workspace>` and `multi-agent-brief state check --workspace <workspace> --strict`. If blocking findings exist, do not finalize; use feedback/repair, `request_human_review`, or `block_run`. Record auditor completion with `state stage-complete --stage auditor` only when audit readiness and quality gates pass.
- After `multi-agent-brief finalize` writes reader-facing artifacts, verify completion with `multi-agent-brief state finalize-complete --workspace <workspace> --reason "<reason>"` before reporting the run complete.
- Treat repair guidance as bounded runtime guidance, not an automatic trajectory regulator:
  if the same stage has already needed roughly three retry/repair rounds, prefer
  `request_human_review` or `block_run`; if a repair would touch more than two
  sections, narrow the scope before delegating or request human review.
- Keep Python positioned as tools, validators, and renderers.
- Keep control selections separate from execution; selection is not execution.
- Update generation sources when generated platform adapter files change.
- Run focused tests for changed areas.

## Handoff

Return the next stage, delegated role, expected artifact, recorded decision, reason summary, and validation command or tool check.
