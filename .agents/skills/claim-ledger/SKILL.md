---
name: claim-ledger
description: Builds stable source-grounded claim ledger entries from screened candidates. Use after output/intermediate/screened_candidates.json exists and before analyst drafting.
---

# Claim Ledger Skill Contract

## Scope

This is a runtime skill contract. It describes the capability and artifact contract for this role.

It is not the platform-specific subagent definition. Claude Code subagents live in `.claude/agents/`; OpenCode subagents live in `.opencode/agents/`; Codex custom agents live in `.codex/agents/`; Hermes child tasks are created through `delegate_task`.

## Purpose

Convert screened candidates into stable, source-grounded claim ledger entries.

## Use When

Use after screened_candidates.json exists, whether default Scout or strict Screener produced it.

## Inputs

- `output/intermediate/screened_candidates.json`

## Outputs

- `output/intermediate/claim_ledger.json`

## Work

- Create stable claim IDs.
- Preserve statement, evidence text, source URL/path, source date, retrieved date, topic, claim type, and confidence.
- Merge overlapping candidates only when traceability remains clear.
- Keep language strength aligned with evidence strength.

## Handoff

Pass claim_ledger.json to analyst.
