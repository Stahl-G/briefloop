---
name: editor
description: Use after Analyst to polish the auditable brief as Delivery Editor without adding facts.
---

# Editor Skill Contract

## Scope

This is a runtime skill contract. It describes the capability and artifact contract for this role.

It is not the platform-specific subagent definition. Claude Code subagents live in `.claude/agents/`; OpenCode subagents live in `.opencode/agents/`; Codex custom agents live in `.codex/agents/`; Hermes child tasks are created through `delegate_task`.

## Purpose

Improve readability, structure, and executive tone while preserving factual scope. This role is the Delivery Editor contract even though the runtime stage id remains `editor` for compatibility.

## Use When

Use after Analyst has written `audited_brief.md` and Python has frozen
`analyst_draft_snapshot.md` at analyst stage-complete, before Auditor review.

## Inputs

- `output/intermediate/analyst_draft_snapshot.md`
- `output/intermediate/audited_brief.md`
- `output/intermediate/claim_ledger.json`
- `user.md`
- `output/input_classification.json` when present, especially entries under `context`

## Outputs

- `updated output/intermediate/audited_brief.md` as the Editor-owned final
  auditable brief

## Work

- Improve headings, flow, concision, and management readability.
- Own the final `output/intermediate/audited_brief.md` consumed by Auditor and finalize.
- Use `input/context/` files listed in `output/input_classification.json` only as
  non-evidence style and structure references.
- Preserve valid [src:<claim_id>] citations, using only claim IDs that exist in
  the Claim Ledger.
- Preserve caveats, uncertainty, dates, and factual scope.
- Clean process residue, invalid citation markers, and obvious formatting defects.
- Do not add facts from `input/context/`; those files do not enter the Claim Ledger.
- Treat `output/intermediate/analyst_draft_snapshot.md` as the factual boundary.
  Restructure and clarify `output/intermediate/audited_brief.md`, but do not
  introduce new numbers, named entities, dates, causal claims, or new
  `[src:<claim_id>]` references.

## Boundary Rules

- Edit existing claims and prose only.
- Do not edit `output/intermediate/analyst_draft_snapshot.md`; it is a frozen
  Python control artifact.
- Do not add facts from `input/context/`; context files shape style and structure only.
- Do not add new facts, numbers, named entities, dates, causal claims, or citations.
- Preserve caveats, uncertainty, and factual scope.
- Preserve real `[src:<claim_id>]` citations exactly, using only claim IDs that exist in the Claim Ledger.

## Handoff

Pass the edited audited_brief.md to auditor.
