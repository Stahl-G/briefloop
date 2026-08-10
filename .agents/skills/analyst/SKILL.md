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
- optional `output/intermediate/atomic_claim_graph.json` when present and valid
- `output/input_classification.json` when present, especially entries under `context`

## Outputs

- `output/intermediate/audited_brief.md` as the Analyst working draft

## Work

- Write a management-ready brief in the workspace output language.
- Treat `output/intermediate/audited_brief.md` as the working draft. Python will
  freeze it into `output/intermediate/analyst_draft_snapshot.md` during
  `state stage-complete --stage analyst`.
- Use frozen `output/intermediate/claim_ledger.json` entries as the factual evidence base.
- When present and valid, use `output/intermediate/atomic_claim_graph.json` only
  as an optional experimental structural decomposition aid for frozen Claim
  Ledger claims. It is not source evidence and is not proof of support.
- Do not read `output/intermediate/claim_drafts.json`; it is a pre-freeze writer
  artifact, not Analyst evidence.
- Use `input/context/` files listed in `output/input_classification.json` only as
  non-evidence style, structure, and background references.
- Read the exact RoleTaskEnvelope task instructions and preserve every frozen
  configured RunDirection target term verbatim in the executive summary. These
  terms are RunDirection framing only, not evidence; do not invent supporting
  facts or citations solely to satisfy target visibility.
- Attach valid [src:<claim_id>] citations to important factual statements, using
  only claim IDs that exist in the Claim Ledger.
- Include dates, numbers, locations, parties, and caveats when the ledger supports them.
- Preserve uncertainty and source limitations.
- Use plain Markdown headings; do not wrap heading text in inline formatting
  such as `# **Heading**` or `### *Heading*`.
- Do not cite or introduce facts from `input/context/`; those files do not enter
  the Claim Ledger.
- Do not create, edit, rewrite, repair, or extend
  `output/intermediate/atomic_claim_graph.json`.
- If `atomic_claim_graph.json` is absent or invalid, do not repair it; continue
  from the frozen Claim Ledger unless Orchestrator routes a separate repair or
  human review.
- Do not cite atom IDs in reader-facing prose; cite only Claim Ledger IDs.
- Do not introduce material atoms absent from the frozen Claim Ledger and, when
  present and valid, `atomic_claim_graph.json`.
- Do not write or edit `output/intermediate/analyst_draft_snapshot.md`; it is a
  Python control artifact.
- Do not create, edit, rewrite, or repair `output/intermediate/claim_ledger.json`.

## Handoff

Pass the working `audited_brief.md` to editor. Editor owns the final
`output/intermediate/audited_brief.md` consumed by Auditor and finalize.
