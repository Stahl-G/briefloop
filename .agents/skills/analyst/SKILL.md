---
name: analyst
description: Drafts the Analyst working brief from user context and the frozen Claim Ledger. Use after the Claim Ledger freeze transaction has produced output/intermediate/claim_ledger.json and write output/intermediate/audited_brief.md; Python freezes output/intermediate/analyst_draft_snapshot.md during analyst stage-complete.
---

# Analyst Skill Contract

## Scope

This is a runtime skill contract. It describes the capability and artifact contract for this role.

It is not the platform-specific subagent definition. Claude Code subagents live in `.claude/agents/`; OpenCode subagents live in `.opencode/agents/`; Codex custom agents live in `.codex/agents/`; Hermes child tasks are created through `delegate_task`.

## Purpose

Draft the Analyst working management brief from the frozen claim ledger and user context.

## Use When

Use after the Claim Ledger freeze transaction has produced `claim_ledger.json`.

## Inputs

- `user.md`
- `config.yaml`
- frozen `output/intermediate/claim_ledger.json`
- `output/input_classification.json` when present, especially entries under `context`

## Outputs

- `output/intermediate/audited_brief.md` as the Analyst working draft

## Work

- Write a management-ready brief in the workspace output language.
- Treat `output/intermediate/audited_brief.md` as the working draft. Python will
  freeze it into `output/intermediate/analyst_draft_snapshot.md` during
  `state stage-complete --stage analyst`.
- Use frozen `output/intermediate/claim_ledger.json` entries as the factual evidence base.
- Do not read `output/intermediate/claim_drafts.json`; it is a pre-freeze writer
  artifact, not Analyst evidence.
- Use `input/context/` files listed in `output/input_classification.json` only as
  non-evidence style, structure, and background references.
- Attach valid [src:<claim_id>] citations to important factual statements, using
  only claim IDs that exist in the Claim Ledger.
- Include dates, numbers, locations, parties, and caveats when the ledger supports them.
- Preserve uncertainty and source limitations.
- Use plain Markdown headings; do not wrap heading text in inline formatting
  such as `# **Heading**` or `### *Heading*`.
- Do not cite or introduce facts from `input/context/`; those files do not enter
  the Claim Ledger.
- Do not write or edit `output/intermediate/analyst_draft_snapshot.md`; it is a
  Python control artifact.
- Do not create, edit, rewrite, or repair `output/intermediate/claim_ledger.json`.

## Handoff

Pass the working `audited_brief.md` to editor. Editor owns the final
`output/intermediate/audited_brief.md` consumed by Auditor and finalize.
