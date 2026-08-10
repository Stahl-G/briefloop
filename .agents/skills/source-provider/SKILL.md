---
name: source-provider
description: Configures, validates, collects, and normalizes source provider outputs. Use when working on sources.yaml, provider configuration, cached packages, source collection, or doctor findings.
---

# Source Provider Skill Contract

## Scope

This is a runtime skill contract. It describes the capability and artifact contract for this role.

It is not the platform-specific subagent definition. Claude Code subagents live in `.claude/agents/`; OpenCode subagents live in `.opencode/agents/`; Codex custom agents live in `.codex/agents/`; Hermes child tasks are created through `delegate_task`.

## Purpose

Configure and validate source-provider inputs, then normalize the provider
output supplied by the deterministic runtime.

For a SQLite ControlStore v2 run, the runtime host is the sole provider-I/O
owner. This role is proposal-only: never call Tavily (or any external
provider), open a network connection, read an API credential, or write
 SQLite, receipts, stage state, frozen artifacts, projections, `sources.yaml`,
 or another invocation's files. The host performs the authorized acquisition,
 freezes bounded provider response bytes (with value-free failure metadata),
 and lists the exact scratch proposal files
this role may write.

## Use When

Use when working on sources.yaml, provider configuration, source collection, cached packages, or doctor checks.

## Inputs

- `sources.yaml`
- `config.yaml`
- provider outputs supplied by the host
- `input/`
- `input/hermes_cache/`

## Outputs

- normalized source packages
- doctor report
- provider validation findings
- cached package source configuration when applicable

## Work

- Validate enabled providers and source configuration.
- Normalize provider items supplied by the host; do not collect them directly
  during a SQLite ControlStore v2 invocation.
- Deduplicate and label collected items.
- Surface provider configuration and collection issues clearly.

## Handoff

Pass normalized evidence material to scout.
