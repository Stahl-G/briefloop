---
name: analyst
description: Drafts the Analyst working brief from user context and the Claim Ledger. Use after output/intermediate/claim_ledger.json exists and write output/intermediate/audited_brief.md; Python freezes output/intermediate/analyst_draft_snapshot.md during analyst stage-complete.
---

# Analyst Skill Contract

## Scope

This is a runtime skill contract. It describes the capability and artifact contract for this role.

It is not the platform-specific subagent definition. Claude Code subagents live in `.claude/agents/`; OpenCode subagents live in `.opencode/agents/`; Codex custom agents live in `.codex/agents/`; Hermes child tasks are created through `delegate_task`.

## Purpose

Draft the Analyst working management brief from the claim ledger and user context.

## Use When

Use after claim-ledger has written claim_ledger.json.

## Inputs

- `user.md`
- `config.yaml`
- `output/intermediate/claim_ledger.json`
- `output/input_classification.json` when present, especially entries under `context`

## Outputs

- `output/intermediate/audited_brief.md` as the Analyst working draft

## Work

- Write a management-ready brief in the workspace output language.
- Treat `output/intermediate/audited_brief.md` as the working draft. Python will
  freeze it into `output/intermediate/analyst_draft_snapshot.md` during
  `state stage-complete --stage analyst`.
- Use claim ledger entries as the factual evidence base.
- Use `input/context/` files listed in `output/input_classification.json` only as
  non-evidence style, structure, and background references.
- Attach valid [src:<claim_id>] citations to important factual statements, using
  only claim IDs that exist in the Claim Ledger.
- Include dates, numbers, locations, parties, and caveats when the ledger supports them.
- Preserve uncertainty and source limitations.
- Do not cite or introduce facts from `input/context/`; those files do not enter
  the Claim Ledger.
- Do not write or edit `output/intermediate/analyst_draft_snapshot.md`; it is a
  Python control artifact.

## Handoff

Pass the working `audited_brief.md` to editor. Editor owns the final
`output/intermediate/audited_brief.md` consumed by Auditor and finalize.
