---
name: brief-onboarding
description: Captures brief requirements in chat and writes onboarding.json for workspace initialization. Use when a user wants to set up a real MABW workspace — collect answers in chat, write onboarding.json, then run init --from-onboarding.
---

# Brief Onboarding Skill Contract

## Scope

This is a runtime skill contract. It describes the capability and artifact contract for this role.

It is not the platform-specific subagent definition. Claude Code subagents live in `.claude/agents/`; OpenCode subagents live in `.opencode/agents/`; Codex custom agents live in `.codex/agents/`; Hermes child tasks are created through `delegate_task`.

## Purpose

Capture business requirements from chat answers to initialize, configure, and start a real brief workspace.

## Use When

Use before workspace creation when company, topic, audience, language, cadence, source preference, or output style are not yet defined.

## Inputs

- user answers in natural language
- optional existing onboarding.json for review

## Outputs

- `onboarding.json`

## Work

### Human terminal path

Run `multi-agent-brief onboard` for an interactive terminal wizard.

### Agent runtime path (chat-to-JSON)

1. Collect brief profile in chat — ask for company, industry, task objective, audience, language, cadence, source style, output style, must-watch topics, excluded sources, and source/search mode. Accept natural-language answers and confirm defaults.
2. Write onboarding.json in the schema expected by `multi-agent-brief init --from-onboarding`.
3. Validate with `multi-agent-brief onboard --validate onboarding.json`.
4. Optionally generate a template with `multi-agent-brief onboard --template`.

## Handoff

Run `multi-agent-brief init <workspace> --from-onboarding onboarding.json`, then
run `multi-agent-brief run --workspace <workspace> --runtime <canonical-runtime>`.
Dedicated adapters provide their fixed runtime identity; generic CLI users must
choose explicitly.
