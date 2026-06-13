---
name: source-planner
description: Plans public, citable source discovery for a workspace. Use when sources.yaml uses llm_decide or when a workspace needs source_candidates.yaml before source validation.
---

# Source Planner Skill Contract

## Scope

This is a runtime skill contract. It describes the capability and artifact contract for this role.

It is not the platform-specific subagent definition. Claude Code subagents live in `.claude/agents/`; OpenCode subagents live in `.opencode/agents/`; Codex custom agents live in `.codex/agents/`; Hermes child tasks are created through `delegate_task`.

## Purpose

Plan public, citable source discovery for a workspace.

## Use When

Use when sources.yaml uses llm_decide, or when the workspace needs source candidates or search tasks.

## Inputs

- `user.md`
- `config.yaml`
- `sources.yaml`

## Outputs

- `source_candidates.yaml`
- search tasks or source-discovery notes as configured

`source_candidates.yaml` is a planning/review artifact, not source evidence.

## Work

- Understand company, industry, audience, cadence, focus areas, and source preference.
- Propose public, citable, timestamped sources.
- Align sources with selected source profile and runtime search mode.
- Prepare candidates for review. Do not treat `source_plan_only` candidates as
  mergeable evidence.
- If using runtime WebSearch, write collected public sources into
  `input/sources/` as durable source files before source-discovery completion.
  Durable runtime-search source files must include URL, source title/name,
  published date or retrieved_at, and raw excerpt/snippet. Summary-only notes
  are discovery hints, not evidence.
- Do not call `sources decide --search` unless `web_search.mode` is
  `external_api`.
- Do not call `sources decide --merge` on `source_plan_only` artifacts.

## Handoff

After approved sources exist as durable files or supported source configuration,
pass source configuration to doctor/source-provider.
