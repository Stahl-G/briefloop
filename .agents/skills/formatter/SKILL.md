---
name: formatter
description: Renders reader-facing outputs from the audited brief after audit readiness. Use after audit_report.json exists and run finalize to produce output/brief.md, configured DOCX/Markdown artifacts, and configured reader-facing source appendices.
---

# Formatter Skill Contract

## Scope

This is a runtime skill contract. It describes the capability and artifact contract for this role.

It is not the platform-specific subagent definition. Claude Code subagents live in `.claude/agents/`; OpenCode subagents live in `.opencode/agents/`; Codex custom agents live in `.codex/agents/`; Hermes child tasks are created through `delegate_task`.

## Purpose

Render reader-facing outputs from the audited internal brief after audit readiness.

## Use When

Use after auditor has produced audit_report.json and audited_brief.md is ready for delivery.

## Inputs

- `output/intermediate/audited_brief.md`
- `output/intermediate/audit_report.json`
- `config.yaml`

## Outputs

- `output/brief.md`
- `output/source_appendix.md when configured`
- configured named Markdown output when enabled
- `output/brief.docx when configured`
- `output/intermediate/finalize_report.json when available`

## Work

- Run or follow multi-agent-brief finalize --config <workspace>/config.yaml.
- Strip internal [src:CLAIM_ID] markers from reader-facing artifacts.
- Generate configured `source_appendix.md` from cited Claim Ledger sources only.
- Preserve the meaning and structure of the audited brief.
- Do not expose raw claim IDs, source IDs, evidence text, local paths, or file URLs in reader-facing appendices.
- Treat the source appendix as a reader-facing source list, not semantic proof that claims are true.
- Report final artifact paths and rendering status.

## Handoff

Return final artifact paths, audit status, and remaining limitations.
