---
name: scout
description: Extracts candidate reportable items from approved workspace source files and cached source packages. Use when a MABW brief workspace needs source evidence converted into output/intermediate/candidate_claims.json before screening.
---

# Scout Skill Contract

## Scope

This is a runtime skill contract. It describes the capability and artifact contract for this role.

It is not the platform-specific subagent definition. Claude Code subagents live in `.claude/agents/`; OpenCode subagents live in `.opencode/agents/`; Codex custom agents live in `.codex/agents/`; Hermes child tasks are created through `delegate_task`.

## Purpose

Extract source-grounded candidate reportable items from approved evidence inputs.

## Use When

Use after source discovery, doctor, and input governance have identified evidence material for a workspace.

## Inputs

- `config.yaml`
- `sources.yaml`
- `user.md`
- `input/sources/` (evidence files only, including `.mineru.md` files produced by `multi-agent-brief inputs extract`; also `input/` root files for backward compatibility)
- `input/hermes_cache/ when present`
- approved source packages or evidence input lists
- `output/intermediate/input_classification.json` (classify result, if available)

## Outputs

- `output/intermediate/candidate_claims.json`

## Work

- Read approved source files from `input/sources/` (and `input/` root for backward compatibility), cached source packages, and evidence input lists. If users provided PDF/DOCX/image evidence, read the extracted `.mineru.md` file, not the raw binary document.
- Do NOT read or extract claims from `input/feedback/`, `input/instructions/`, or `input/context/` — these contain editorial direction, task requirements, and background context, not factual evidence.
- If `input_classification.json` is available, use it as the authoritative file list.
- Extract candidate reportable items with source identity, evidence text, source date, topic, claim type, and confidence.
- Mark vague, stale-looking, duplicate-looking, or low-confidence candidates.
- Keep source wording and evidence traceable.
- Return candidates, not final analysis.

## Handoff

Pass candidate_claims.json to screener.
