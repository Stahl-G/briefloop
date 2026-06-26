---
name: screener
description: Strict-topology independent screening role. Use when Scout has not already produced screened_candidates.json, or when screening repair/review is explicitly routed.
---

# Screener Skill Contract

## Scope

This is a runtime skill contract. It describes the capability and artifact contract for this role.

It is not the platform-specific subagent definition. Claude Code subagents live in `.claude/agents/`; OpenCode subagents live in `.opencode/agents/`; Codex custom agents live in `.codex/agents/`; Hermes child tasks are created through `delegate_task`.

## Purpose

Select the most relevant, fresh, non-duplicative candidates before claim ledger creation when `role_topology: strict` is active or when the Orchestrator routes an explicit screening repair/review task.

## Use When

Use after Scout has written `candidate_claims.json` only when the selected topology keeps Screener independent. In default topology, Scout writes both `candidate_claims.json` and `screened_candidates.json`, so this role should not run unless explicitly routed for repair/review.

## Inputs

- `output/intermediate/candidate_claims.json`
- `config.yaml`
- `user.md`

## Outputs

- `output/intermediate/screened_candidates.json`

## Work

- Read `candidate_claims.json` as the frozen found universe.
- Rank candidates by relevance, freshness, source quality, and user focus.
- Deduplicate exact and near-duplicate candidates.
- Apply topic capacity and reporting-window rules from config.
- Preserve source identity and evidence text.
- Record exclusion reasons for every dropped or deprioritized candidate with a stable
  `reason_code` and short `explanation`. Use reason codes such as
  `duplicate_source`, `stale_source`, `capacity_capped`, `weak_relevance`,
  `off_focus`, `low_confidence`, `low_tier`, or `unsafe_evidence_boundary`.
- Write `screened_candidates.json` with selected candidates, excluded candidates,
  stable discard records, and the screening policy snapshot.

## Freshness Policy

- Treat workspace config freshness settings as authoritative.
- Do not retain stale sources beyond `max_source_age_days` when `fail_on_stale_source` is true, unless the input artifact/config contains an explicit structured override.
- If the configured freshness window leaves too few candidates, report this as a screening blocker or needs-human-review condition. Do not silently relax the threshold.
- Screening rationale may explain staleness, but explanation is not approval.

## Boundary Rules

- Screen existing Scout candidates only.
- Do not rediscover source material or add new candidates from source files.
- Do not rewrite `candidate_claims.json`.
- Never mint `claim_id` values. The Claim Ledger freeze transaction owns claim IDs.

## Handoff

Pass screened_candidates.json to claim-ledger.
