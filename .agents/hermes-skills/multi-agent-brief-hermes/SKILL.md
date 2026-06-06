---
name: multi-agent-brief-hermes
description: Use this skill to run Multi-Agent Brief Workflow workspaces inside Hermes using Hermes delegate_task subagents, source cache, cron scheduling, and final rendering tools.
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
  - delegate_task
---

# Multi-Agent Brief Workflow for Hermes

Use this skill to run Multi-Agent Brief Workflow workspaces inside Hermes using Hermes delegate_task subagents, source cache, cron scheduling, and final rendering tools.

## Operating Model

Hermes is a native MABW runtime. The Hermes parent agent manages orchestration, artifact handoff, and gate checks. Hermes `delegate_task` children run scout, screener, claim-ledger, analyst, editor, and auditor tasks as isolated subagents. Python CLI tools handle init, doctor, sources decide, inputs classify, audit, finalize, and rendering support. Cron jobs provide durable scheduling; `delegate_task` provides child task dispatch within each run.

Brief generation follows the MABW subagent workflow:

```text
scout -> screener -> claim-ledger -> analyst -> editor -> auditor -> finalize
```

## Setup Workflow

1. Clone or open the repository.
2. Create and activate the Python virtual environment.
3. Install MABW.
4. Initialize the requested workspace.
5. Run doctor:

```bash
multi-agent-brief doctor --config <workspace>/config.yaml
```

6. Report the repo path, venv path, workspace path, version, and doctor status.
7. Offer to continue with a Hermes-native delegated brief run.

After a successful setup, present the result like this:

```
Project is cloned and ready.

Repository: <repo>
Virtual environment: <venv>
Workspace: <workspace>
Version: <version>
Doctor: passed

I can continue generating the brief inside Hermes. The next step uses Hermes delegate_task children for:
scout -> screener -> claim-ledger -> analyst -> editor -> auditor -> finalize.
```

## Daily Source Cache Workflow

1. Read workspace `config.yaml`, `sources.yaml`, and `user.md`.
2. Collect public, citable source signals.
3. Write JSON cache to `input/hermes_cache/YYYY-MM-DD.json`.
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

5. Report saved item count, source gaps, and cache file path.
6. Daily cache mode ends after source cache reporting.

## Hermes-native Delegated Brief Workflow

### Parent Orchestration

The Hermes parent agent manages the full pipeline:

1. Read workspace files:
   - `config.yaml`
   - `sources.yaml`
   - `user.md`
   - `input/`
   - `input/hermes_cache/` when present

2. Run doctor:

```bash
multi-agent-brief doctor --config <workspace>/config.yaml
```

3. If source discovery is configured:

```bash
multi-agent-brief sources decide --config <workspace>/config.yaml
```

Review and merge according to workspace policy.

4. If input governance is available:

```bash
multi-agent-brief inputs classify --config <workspace>/config.yaml
```

5. Create `output/intermediate/` if it does not exist.

6. Delegate child tasks with complete context and explicit artifact paths. Use `delegate_task` for each step.

7. After each child returns, verify the expected artifact exists and is non-empty before continuing to the next child.

8. When all children have completed and `audited_brief.md` exists, finalize:

```bash
multi-agent-brief finalize --config <workspace>/config.yaml
```

9. Report artifact paths and audit status.

### Delegation Sequence

#### 1. Scout child

Use `delegate_task` to extract candidate reportable items:

```python
delegate_task(
    goal="Extract candidate reportable items for a MABW brief",
    context="""
Workspace: <workspace>
Read approved evidence inputs, cached source packages, local source files, and source config.
Write: <workspace>/output/intermediate/candidate_claims.json

Output candidate reportable items only.
Each item should preserve source path or URL, source date if available, evidence text, topic, claim type, and confidence.
Return a summary with item count and source gaps.
""",
    toolsets=["file", "terminal", "web"]
)
```

For independent source clusters, the parent may use batch delegation with up to 3 scout children, then merge their outputs into one `candidate_claims.json`.

#### 2. Screener child

```python
delegate_task(
    goal="Screen and rank MABW candidate claims",
    context="""
Workspace: <workspace>
Input: output/intermediate/candidate_claims.json
Write: output/intermediate/screened_candidates.json

Rank, dedupe, freshness-check, and capacity-cap candidate items.
Preserve source identity and evidence fields.
Return included count, excluded count, and main exclusion categories.
""",
    toolsets=["file", "terminal"]
)
```

#### 3. Claim-ledger child

```python
delegate_task(
    goal="Build the MABW Claim Ledger",
    context="""
Workspace: <workspace>
Input: output/intermediate/screened_candidates.json
Write: output/intermediate/claim_ledger.json

Create stable claim IDs and source-grounded claim entries.
Preserve evidence text, source URL/path, publication date, retrieved date, topic, claim type, and confidence.
Return claim count and schema issues found.
""",
    toolsets=["file", "terminal"]
)
```

#### 4. Analyst child

```python
delegate_task(
    goal="Draft the audited MABW brief",
    context="""
Workspace: <workspace>
Inputs:
- user.md
- output/intermediate/claim_ledger.json

Write:
- output/intermediate/audited_brief.md

Write a management-ready brief in the workspace language.
Use Claim Ledger evidence for factual statements.
Preserve valid [src:CLAIM_ID] citations.
Include source dates where useful.
Return a section summary and any source limitations.
""",
    toolsets=["file", "terminal"]
)
```

#### 5. Editor child

```python
delegate_task(
    goal="Polish the audited MABW brief",
    context="""
Workspace: <workspace>
Input: output/intermediate/audited_brief.md
Write: output/intermediate/audited_brief.md

Improve readability, structure, and executive tone.
Preserve factual scope, uncertainty, and valid [src:CLAIM_ID] citations.
Return edits made and any unresolved issues.
""",
    toolsets=["file", "terminal"]
)
```

#### 6. Auditor child

```python
delegate_task(
    goal="Audit the MABW brief against the Claim Ledger",
    context="""
Workspace: <workspace>
Inputs:
- output/intermediate/audited_brief.md
- output/intermediate/claim_ledger.json

Write:
- output/intermediate/audit_report.json

Check source support, orphan citations, unsupported numbers, missing dates, stale framing, process residue, and delivery readiness.
Return audit status, blocking findings, and recommended fixes.
""",
    toolsets=["file", "terminal"]
)
```

#### 7. Finalize

Parent runs:

```bash
multi-agent-brief finalize --config <workspace>/config.yaml
```

Then reports:

- `output/brief.md`
- configured named Markdown copy if enabled
- `output/brief.docx` if configured
- `output/intermediate/audited_brief.md`
- `output/intermediate/claim_ledger.json`
- `output/intermediate/audit_report.json`

## Source Cache Contract

The MABW `cached_package` provider can read JSON, Markdown, and text files from the configured cache directory. Prefer JSON arrays or objects with an `items` array. Each item should preserve URL, publication date, source name, and reliability where available.

## Hermes Cron Notes

- Attach this skill to each cron job with `--skill multi-agent-brief-hermes`.
- Use `--workdir <repo-root>` so Hermes loads repository instructions and runs commands from the project.
- Pin `--profile <name>` when the Hermes profile already exists.
- Hermes delivers the final response through the configured cron destination.

