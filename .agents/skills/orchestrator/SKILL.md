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
- Check expected artifacts after each delegated stage.
- Make stage decisions with continue, retry_stage, delegate_repair, request_human_review, block_run, and finalize.
- Treat repair guidance as bounded runtime guidance, not an automatic trajectory regulator:
  if the same stage has already needed roughly three retry/repair rounds, prefer
  `request_human_review` or `block_run`; if a repair would touch more than two
  sections, narrow the scope before delegating or request human review.
- Keep Python positioned as tools, validators, and renderers.
- Keep control selections separate from execution; selection is not execution.
- Update generation sources when generated platform adapter files change.
- Run focused tests for changed areas.

## Handoff

Return the next stage, delegated role, expected artifact, decision, reason summary, and validation command or tool check.
