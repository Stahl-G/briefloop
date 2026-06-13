---
name: formatter
description: Renders the final reader delivery bundle from the audited brief after audit readiness. Use after audit_report.json exists and run finalize to produce output/delivery/brief.md, configured delivery DOCX, and internal audit/control records.
---

# Formatter Skill Contract

## Scope

This is a runtime skill contract. It describes the capability and artifact contract for this role.

It is not the platform-specific subagent definition. Claude Code subagents live in `.claude/agents/`; OpenCode subagents live in `.opencode/agents/`; Codex custom agents live in `.codex/agents/`; Hermes child tasks are created through `delegate_task`.

## Purpose

Render the final reader delivery bundle from the audited internal brief after audit readiness.

## Use When

Use after auditor has produced audit_report.json and audited_brief.md is ready for delivery.

## Inputs

- `output/intermediate/audited_brief.md`
- `output/intermediate/audit_report.json`
- `config.yaml`

## Outputs

- `output/delivery/brief.md`
- `output/delivery/<named>.docx when configured`
- `output/source_appendix.md when configured as an audit/control copy`
- `output/intermediate/finalize_report.json when available`

## Work

- Run or follow multi-agent-brief finalize --config <workspace>/config.yaml.
- Strip internal [src:<claim_id>] markers from reader-facing delivery artifacts.
- Generate configured `source_appendix.md` from cited Claim Ledger sources only as an audit/control copy.
- Preserve the meaning and structure of the audited brief.
- Do not expose raw claim IDs, source IDs, evidence text, local paths, or file URLs in delivery artifacts or source appendix audit copies.
- Treat the source appendix as an audit/control source list, not semantic proof that claims are true.
- Do not present Claim Ledger, Audit Report, or Audited Brief as user delivery files.
- Report final delivery paths and rendering status.

## Handoff

Return final delivery paths, audit status, and remaining limitations.
