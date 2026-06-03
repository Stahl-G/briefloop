---
name: editor
description: Improves clarity, structure, tone, and executive readability without adding facts. Use after the analyst subagent and before final DOCX rendering. Must remove process residue while preserving valid citations.
---

# Editor Skill

## Purpose

Improves clarity, structure, tone, and executive readability without adding facts.

## When To Use

Use after the analyst subagent and before final DOCX rendering. Must remove process residue while preserving valid citations.

## Responsibilities

- Improve readability and management tone.
- Reduce repetition.
- Preserve all [src:CLAIM_ID] citations exactly — do not remove or rewrite claim IDs.
- Preserve uncertainty.
- Remove internal residue when safe.
- Remove [SRC:], [SOURCE:], empty [src:] markers.
- Remove Claude/Codex process residue (Thought for..., Agent completed, Bash(...), audit in background).
- Do not add new facts.
- Do not remove or rewrite claim IDs.

## Hard Rules

- Do not add new claims.
- Do not remove claim citations.
- Do not convert caveats into certainty.
- Do not remove or rewrite [src:CLAIM_ID] citations.

## Pipeline Context

```text
Scout -> Screener -> Claim Ledger -> Analyst -> Auditor -> Editor -> Formatter
```

## Expected Inputs

Source files, claim ledger entries, or draft markdown as appropriate for the pipeline stage.

## Expected Outputs

Structured artifacts conforming to the pipeline contract:
- `draft_brief.md`
- `claim_ledger.json`
- `audit_report.json`
- `source_map.md`
