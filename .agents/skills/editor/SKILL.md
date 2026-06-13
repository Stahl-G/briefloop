---
name: editor
description: Polishes the auditable brief for clarity and executive readability without adding facts. Use after analyst writes output/intermediate/audited_brief.md and update that file for auditor review.
---

# Editor Skill Contract

## Scope

This is a runtime skill contract. It describes the capability and artifact contract for this role.

It is not the platform-specific subagent definition. Claude Code subagents live in `.claude/agents/`; OpenCode subagents live in `.opencode/agents/`; Codex custom agents live in `.codex/agents/`; Hermes child tasks are created through `delegate_task`.

## Purpose

Improve readability, structure, and executive tone while preserving factual scope.

## Use When

Use after analyst has written audited_brief.md.

## Inputs

- `output/intermediate/audited_brief.md`
- `output/intermediate/claim_ledger.json`
- `user.md`
- `output/input_classification.json` when present, especially entries under `context`

## Outputs

- `updated output/intermediate/audited_brief.md`

## Work

- Improve headings, flow, concision, and management readability.
- Use `input/context/` files listed in `output/input_classification.json` only as
  non-evidence style and structure references.
- Preserve valid [src:<claim_id>] citations, using only claim IDs that exist in
  the Claim Ledger.
- Preserve caveats, uncertainty, dates, and factual scope.
- Clean process residue, invalid citation markers, and obvious formatting defects.
- Do not add facts from `input/context/`; those files do not enter the Claim Ledger.

## Handoff

Pass the edited audited_brief.md to auditor.
