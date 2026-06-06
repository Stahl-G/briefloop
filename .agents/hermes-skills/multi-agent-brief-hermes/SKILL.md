---
name: multi-agent-brief-hermes
description: Run Multi-Agent Brief Workflow workspaces from Hermes cron, collecting daily source packages and triggering audited weekly/monthly briefs.
version: 0.5.5
author: multi-agent-brief-workflow
license: MIT
platforms:
  - linux
  - macos
  - windows
tags:
  - hermes
  - cron
  - brief
  - research
  - workflow
---

# Multi-Agent Brief Workflow for Hermes

Use this skill when a Hermes cron job needs to collect daily signals for a MABW workspace or trigger an audited weekly/monthly brief.

## Hard Rules

- Treat Hermes cron as a fresh session. Rely only on this skill, the cron prompt, `AGENTS.md`, and files in the configured workdir/workspace.
- Do not invent internal company facts. Use only public or user-provided source evidence.
- Do not paste API keys, tokens, raw private logs, or credentials into files or messages.
- Daily scout jobs collect source packages only. They must not write final management briefs.
- Weekly/monthly jobs must run `doctor` before `prepare`.
- If `prepare`, final quality, rendered output, or audit gates fail, report the blocking findings and do not mark the output delivery-ready.
- Reader-facing artifacts must not contain `[src:CLAIM_ID]`; internal audited markdown may retain citations.

## Daily Scout Workflow

1. Read the cron prompt for the absolute workspace path and cache directory.
2. Collect public, citable source signals relevant to the configured company, industry/theme, audience, and report language.
3. Write one JSON file under the cache directory named `YYYY-MM-DD.json`.
4. Use this item shape when possible:

```json
{
  "source_id": "HERMES_YYYYMMDD_001",
  "source_name": "Source name",
  "source_type": "hermes_daily_cache",
  "title": "Short source title",
  "content": "Concise factual summary with enough context for claim extraction.",
  "url": "https://example.com/source",
  "published_at": "YYYY-MM-DD",
  "reliability": "high",
  "metadata": {
    "collected_by": "hermes",
    "collection_cadence": "daily"
  }
}
```

5. End with a short count of saved usable items and any source gaps.

## Weekly / Monthly Brief Workflow

1. Confirm the workspace has `config.yaml` and `sources.yaml`.
2. Ensure `sources.yaml` enables `cached_package` with `input/hermes_cache`.
3. Run:

```bash
multi-agent-brief doctor --config <workspace>/config.yaml
multi-agent-brief prepare --config <workspace>/config.yaml
```

4. If prepare succeeds, run:

```bash
multi-agent-brief finalize --config <workspace>/config.yaml
```

5. Report artifact paths for `brief.md`, named Markdown, DOCX if generated, `claim_ledger.json`, `audit_report.json`, and `run_manifest.json`.

## Source Cache Contract

The MABW `cached_package` provider can read JSON, Markdown, and text files from the configured cache directory. Prefer JSON arrays or objects with an `items` array. Each item should preserve URL, publication date, source name, and reliability where available.

## Hermes Cron Notes

- Attach this skill to each cron job with `--skill multi-agent-brief-hermes`.
- Use `--workdir <repo-root>` so Hermes loads repository instructions and runs commands from the project.
- Pin `--profile <name>` only when the Hermes profile already exists.
- Do not call `send_message` for the normal cron destination; Hermes delivers the final response automatically.
