---
name: claim-ledger
description: Builds source-grounded claim drafts from screened candidates. Use after output/intermediate/screened_candidates.json exists and before Python freezes output/intermediate/claim_ledger.json.
---

# Claim Ledger Skill Contract

## Scope

This is a runtime skill contract. It describes the capability and artifact contract for this role.

It is not the platform-specific subagent definition. Claude Code subagents live in `.claude/agents/`; OpenCode subagents live in `.opencode/agents/`; Codex custom agents live in `.codex/agents/`; Hermes child tasks are created through `delegate_task`.

## Purpose

Convert screened candidates into source-grounded claim drafts for deterministic Python freezing.

## Use When

Use after screened_candidates.json exists, whether default Scout or strict Screener produced it.

## Inputs

- `output/intermediate/screened_candidates.json`

## Outputs

- `output/intermediate/claim_drafts.json`

## Work

- Write claim drafts without `claim_id` fields.
- Preserve `candidate_id`, statement, evidence text, source URL/path, source
  title/name, publisher/institution, provider source type, reader-facing source
  category, source date, retrieved date, topic, claim type, and confidence.
- `source_url` is only for HTTP(S) URLs. Do not put titles, source names, source IDs, search queries, or local paths in `source_url`.
- Local-file or packaged sources may omit `source_url` only when `source_path` plus `source_title` or `source_name` and `source_category` are preserved.
- Merge overlapping candidates only when traceability remains clear; otherwise keep separate drafts.
- Keep language strength aligned with evidence strength.
- Do not write `output/intermediate/claim_ledger.json`; Python creates it with `briefloop state freeze-claim-ledger`.

## Handoff

Return after `claim_drafts.json` exists. The Orchestrator must run `briefloop state freeze-claim-ledger --workspace <workspace>` before `state stage-complete --stage claim-ledger`.
