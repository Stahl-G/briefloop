---
name: rendered-output-harness
description: Reviews rendered Markdown/DOCX/PDF output quality. Use when changing rendering fidelity checks, document formatting, heading mapping, margins, tables, or footer behavior.
---

# Rendered Output Harness Skill Contract

## Scope

This is a runtime skill contract. It describes the capability and artifact contract for this role.

It is not the platform-specific subagent definition. Claude Code subagents live in `.claude/agents/`; OpenCode subagents live in `.opencode/agents/`; Codex custom agents live in `.codex/agents/`; Hermes child tasks are created through `delegate_task`.

## Purpose

Review rendered output gates for document fidelity.

## Use When

Use when changing DOCX/PDF/Markdown rendering or rendered-output validation.

## Inputs

- `output/delivery/brief.md`
- `output/delivery/<named>.docx or configured rendered artifacts`
- `output/source_appendix.md` as an audit/control copy when source appendix is configured
- rendering code or tests

## Outputs

- rendering findings
- updated rendered-output checks or tests when requested

## Work

- Check rendering depth, heading mapping, bullet separation, margins, footer/page fields, and wide table handling.
- Separate rendering failures from source-audit failures.
- Keep rendered-output checks executable where possible.

## Handoff

Return rendered artifact status and any blocking fidelity issues.
