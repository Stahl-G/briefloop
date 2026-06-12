---
name: final-quality-harness
description: Reviews final text delivery gates for reader-facing brief quality. Use when changing final quality checks after source audit has passed.
---

# Final Quality Harness Skill Contract

## Scope

This is a runtime skill contract. It describes the capability and artifact contract for this role.

It is not the platform-specific subagent definition. Claude Code subagents live in `.claude/agents/`; OpenCode subagents live in `.opencode/agents/`; Codex custom agents live in `.codex/agents/`; Hermes child tasks are created through `delegate_task`.

## Purpose

Review final text delivery gates for publication readiness.

## Use When

Use when changing final report depth, quiet-week handling, front-page metadata, stale-current framing, executive summary quality, or internal residue checks.

## Inputs

- `output/delivery/brief.md`
- `output/delivery/<named>.docx` when DOCX delivery is configured
- `output/intermediate/audit_report.json`
- final quality rule code or tests

## Outputs

- final quality findings
- updated final quality rules or tests when requested

## Work

- Check whether reader-facing output is publication-ready after factual audit.
- Identify delivery blockers that are not source-support problems.
- Keep final quality checks as harness rules and regression tests.

## Handoff

Return delivery readiness status and any blocking final-quality issues.
