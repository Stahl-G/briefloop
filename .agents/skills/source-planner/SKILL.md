---
name: source-planner
description: Chooses lightweight public, citable source-discovery categories and search tasks for a workspace. Use when sources.yaml uses llm_decide or when a workspace needs a source_candidates.yaml plan before source validation.
---

# Source Planner Skill Contract

## Scope

This is a runtime skill contract. It describes the capability and artifact contract for this role.

It is not the platform-specific subagent definition. Claude Code subagents live in `.claude/agents/`; OpenCode subagents live in `.opencode/agents/`; Codex custom agents live in `.codex/agents/`; Hermes child tasks are created through `delegate_task`.

## Purpose

Choose public, citable source-discovery categories, domains, and search tasks
for a workspace.

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

- Understand company, industry, audience, cadence, focus areas, freshness window,
  and source preference.
- Propose public, citable source categories, domains, and search tasks.
- Align sources with selected source profile and runtime search mode.
- Prepare candidates for review. Do not treat `source_plan_only` candidates as
  mergeable evidence.
- List existing `input/sources/` filenames for planning context. Do not read
  full source files unless `source_candidates.yaml` is missing or clearly
  inconsistent.
- Keep the plan lightweight: record the source window, available local source
  filenames, planned search categories/domains, and blocking gaps only when
  source discovery lacks a plausible source path.
- Do not judge claim support, rank reportable items, screen stale facts, or
  write source caveats that belong to Scout, Screener, Auditor, or gates.
- If runtime WebSearch is needed, produce the search plan and hand off to the
  Orchestrator/source-provider path to materialize durable source files.
  Durable runtime-search source files must include URL, source title/name,
  published date or retrieved_at, and raw excerpt/snippet. Summary-only notes
  are discovery hints, not evidence.
- Do not call `sources decide --search` unless `web_search.mode` is
  `external_api`.
- Do not call `sources decide --merge` on `source_plan_only` artifacts.
- Do not decide whether source-discovery is complete, and do not call
  `state stage-complete`.

## Handoff

After the search plan is ready, pass it to the Orchestrator/source-provider for
materialization or source configuration review.
