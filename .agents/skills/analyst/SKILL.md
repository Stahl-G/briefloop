---
name: analyst
description: Drafts the auditable management brief from user context and the Claim Ledger. Use after output/intermediate/claim_ledger.json exists and write output/intermediate/audited_brief.md.
---

# Analyst Skill Contract

## Scope

This is a runtime skill contract. It describes the capability and artifact contract for this role.

It is not the platform-specific subagent definition. Claude Code subagents live in `.claude/agents/`; OpenCode subagents live in `.opencode/agents/`; Codex custom agents live in `.codex/agents/`; Hermes child tasks are created through `delegate_task`.

## Purpose

Draft the auditable management brief from the claim ledger and user context.

## Use When

Use after claim-ledger has written claim_ledger.json.

## Inputs

- `user.md`
- `config.yaml`
- `output/intermediate/claim_ledger.json`
- `output/input_classification.json` when present, especially entries under `context`

## Outputs

- `output/intermediate/audited_brief.md`

## Work

- Write a management-ready brief in the workspace output language.
- Use claim ledger entries as the factual evidence base.
- Use `input/context/` files listed in `output/input_classification.json` only as
  non-evidence style, structure, and background references.
- Attach valid [src:CLAIM_ID] citations to important factual statements.
- Include dates, numbers, locations, parties, and caveats when the ledger supports them.
- Preserve uncertainty and source limitations.
- Do not cite or introduce facts from `input/context/`; those files do not enter
  the Claim Ledger.

## Handoff

Pass audited_brief.md to editor.
