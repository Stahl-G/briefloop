---
description: Generate a real source-grounded brief using CLI + Claude Code subagents
argument-hint: "<workspace-path>"
---

You are generating a real user-facing brief for workspace: $ARGUMENTS

Follow this sequence exactly:

1. Read:
   - $ARGUMENTS/config.yaml
   - $ARGUMENTS/user.md
   - $ARGUMENTS/sources.yaml

2. If sources.yaml has llm_decide, unresolved source_discovery, weak search_tasks, or missing live sources, invoke the source-planner subagent.

3. Run:
   - `multi-agent-brief doctor --config $ARGUMENTS/config.yaml`
   - `multi-agent-brief run --config $ARGUMENTS/config.yaml`

4. Read:
   - $ARGUMENTS/output/claim_ledger.json
   - $ARGUMENTS/output/brief.md
   - $ARGUMENTS/user.md

5. Invoke the analyst subagent:
   - Rewrite the brief into the configured output language.
   - Use only claim_ledger.json.
   - Preserve all valid [src:CLAIM_ID] citations.
   - Include dates for news items.
   - Target a real weekly brief, not a thin bullet list.

6. Invoke the editor subagent:
   - Polish for management / research team readability.
   - Remove invalid [SRC:], [SOURCE:], [src:] residue.
   - Remove Claude/Codex process residue.
   - Preserve valid [src:CLAIM_ID].

7. Invoke the auditor subagent:
   - Audit output/brief.md against output/claim_ledger.json.
   - Check orphan citations, unsupported facts, unsupported numbers, missing dates, investment advice language, and process residue.
   - Write/update output/audit_report.json.

8. Regenerate DOCX:
   - If the CLI supports docx formatting, run the formatter or conversion command.
   - Ensure output/brief.docx exists if docx is configured.

9. Final response:
   - Report artifact paths.
   - Report audit status.
   - Report any remaining limitations.
   - Do not claim success if audit failed.
