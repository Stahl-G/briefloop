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
- Check overstatement: brief wording must not be broader, stronger, more causal,
  more current, or more quantified than the cited Claim Ledger entry and evidence support.
- Check support-strength calibration: wording must match each cited claim's
  claim_type, confidence, evidence_relation, limitations, source date, and metadata caveats.
- Check confidence mismatch: low-confidence or indirect evidence must not be
  presented as definitive, causal, current, or high-certainty.
- Check limitation leakage: source limitations and applicability caveats in the
  ledger must not disappear from reader-facing conclusions.
- Run deterministic audit tools when available.
- Do not read `output/intermediate/claim_drafts.json`; audit the frozen
  `output/intermediate/claim_ledger.json` consumed by Analyst and Editor.
- Do not create, edit, rewrite, or repair `output/intermediate/claim_ledger.json`.
- Do not read or reuse a prior `output/intermediate/audit_report.json` unless
  the Orchestrator explicitly routes an auditor-repair task. The current
  `audit_report.json` is this stage's output, not input.
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
- Report whether deterministic draft or final harness checks should be run by
  the Orchestrator or Python tools. Do not coordinate other agents.
- Report audit readiness only. Formatter, finalize, and deterministic gates
  decide delivery completion.

## Optional Semantic Support Auditor

- Run this proposal-only check only when the runtime supports subagent
  delegation or the operator explicitly runs the semantic support auditor role.
- It compares `output/intermediate/audited_brief.md` against the frozen
  `output/intermediate/claim_ledger.json` and proposes support-calibration
  issues such as overstatement, missing limitations, or unsupported numbers.
- Judge claims only against frozen Claim Ledger evidence. Do not use external
  knowledge or infer missing sources.
- Write only `output/intermediate/semantic_assessment_report.json`. Its rows are
  proposals, not audit findings, gate results, or release decisions.
- Do not edit frozen artifacts, and do not write `audit_report.json`,
  `auditor_quality_gate_report.json`, `claim_support_matrix.json`,
  `workflow_state.json`, or `event_log.jsonl`.
- Semantic support proposals do not gate finalize, delivery, or release by
  themselves. Python validates and projects the proposal artifact; a human must
  accept a proposal before it becomes authoritative.

## Handoff

Pass audit status to formatter/finalize.
