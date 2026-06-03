---
name: source-provider
description: Configures, validates, and collects information sources from manual inputs, RSS feeds, web search, APIs, and MCP/CLI tools. Use when implementing or reviewing source provider configuration, source collection, source normalization, or the doctor health-check command.
---

# Source Provider Skill

## Purpose

Configures, validates, and collects information sources from manual inputs, RSS feeds, web search, APIs, and MCP/CLI tools.

## When To Use

Use when implementing or reviewing source provider configuration, source collection, source normalization, or the doctor health-check command.

## Responsibilities

- Load and validate sources.yaml configuration.
- Instantiate enabled source providers (manual, rss, web_search, api, mcp, cli).
- Collect sources from all enabled providers.
- Normalize source items into a unified SourceItem structure.
- Deduplicate sources by dedupe_key.
- Filter sources by recency.
- Run doctor checks on source configuration health.
- Generate proper sources.yaml templates in init wizard.

## Hard Rules

- Do not write API keys into configuration files.
- Do not bypass source profile constraints.
- Do not claim sources are verified when they are only collected.
- Do not silently skip provider validation errors.

## Pipeline Context

```text
Scout -> Screener -> Claim Ledger -> Analyst -> Auditor -> Editor -> Formatter
```

## Expected Inputs

Source files, claim ledger entries, or draft markdown as appropriate for the coordination stage.

## Expected Outputs

Structured artifacts conforming to the pipeline contract:
- `draft_brief.md`
- `claim_ledger.json`
- `audit_report.json`
- `source_map.md`
