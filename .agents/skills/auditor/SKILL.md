---
name: auditor
description: Audits the auditable brief against the Claim Ledger before delivery. Use after editor completes output/intermediate/audited_brief.md and write output/intermediate/audit_report.json.
---

# Auditor Skill Contract

## Scope

This is a runtime skill contract. It describes the capability and artifact contract for this role.

It is not the platform-specific subagent definition. Claude Code subagents live in `.claude/agents/`; OpenCode subagents live in `.opencode/agents/`; Codex custom agents live in `.codex/agents/`; Hermes child tasks are created through `delegate_task`.

## Purpose

Audit the auditable brief against the claim ledger before final rendering.

## Use When

Use after editor has completed audited_brief.md.

## Inputs

- `output/intermediate/audited_brief.md`
- `output/intermediate/claim_ledger.json`
- `config.yaml`

## Outputs

- `output/intermediate/audit_report.json`

## Work

- Check source support, orphan citations, unsupported numbers, missing dates, stale framing, advice language, process residue, and delivery readiness.
- Run deterministic audit tools when available.
- Do not write audit binding metadata. Audit binding is Python control-plane
  state recorded by `state stage-complete --stage auditor` using deterministic
  SHA-256 hashes.
- Write `output/intermediate/audit_report.json` using the current AuditReport
  contract. Required top-level fields are `audit_status`, `audit_score`,
  `findings`, and `metadata`.
- Use `audit_status` as one of `pass`, `warning`, or `fail`, and `audit_score`
  as an integer from 0 to 100.
- Each finding must include `finding_id`, `severity`, `finding_type`, and
  `description`. Any `high` severity finding means the audit failed.
- Optional compatibility fields such as `status`, `checks`, or
  `blocking_finding_count` may be present, but they never replace
  `audit_status` or `audit_score`.
- Record blocking findings and recommended fixes.
- Mark distribution readiness only after delivery gates are satisfied.

## Handoff

Pass audit status to formatter/finalize.
