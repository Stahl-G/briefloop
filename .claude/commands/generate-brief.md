---
description: Generate a real source-grounded brief using CLI preparation + Claude Code subagents
argument-hint: "<workspace-path>"
---

You are generating a real user-facing brief for workspace: $ARGUMENTS

The Python CLI prepares intermediate artifacts only. The final brief must be written by Claude Code subagents using Claim Ledger and audit outputs.

Follow this sequence exactly:

1. Read:
   - $ARGUMENTS/config.yaml
   - $ARGUMENTS/user.md
   - $ARGUMENTS/sources.yaml

2. **Source discovery gate (llm_decide only):**
   If `sources.yaml` has `source.mode: llm_decide` and `source_candidates.yaml` does not exist or has not been merged, you MUST resolve sources before running the pipeline:
   - Run: `multi-agent-brief sources decide --config $ARGUMENTS/config.yaml`
   - Review the generated `$ARGUMENTS/source_candidates.yaml`.
   - Run: `multi-agent-brief sources decide --config $ARGUMENTS/config.yaml --merge`
   - Only proceed after sources are resolved, OR if the user explicitly chooses local input-only mode.

3. **Prepare intermediate artifacts:**
   - Run: `multi-agent-brief doctor --config $ARGUMENTS/config.yaml`
   - Fix any issues before proceeding.
   - Run: `multi-agent-brief run --config $ARGUMENTS/config.yaml`
   - This produces `draft_brief.md`, `claim_ledger.json`, `audit_report.json`, `source_map.md` — these are intermediate artifacts, not the final brief.

4. Read:
   - $ARGUMENTS/output/claim_ledger.json
   - $ARGUMENTS/output/draft_brief.md
   - $ARGUMENTS/user.md

5. Invoke the **analyst** subagent:
   - Write the final brief from claim_ledger.json and user.md.
   - Use only claim_ledger.json as source evidence.
   - Preserve all valid [src:CLAIM_ID] citations.
   - Include dates for news items.
   - Target a real weekly brief, not a thin bullet list.
   - Write the final brief to $ARGUMENTS/output/brief.md.

6. Invoke the **editor** subagent:
   - Polish for management / research team readability.
   - Remove invalid [SRC:], [SOURCE:], [src:] residue.
   - Remove Claude/Codex process residue.
   - Preserve valid [src:CLAIM_ID].

7. Invoke the **auditor** subagent:
   - Audit the final $ARGUMENTS/output/brief.md against $ARGUMENTS/output/claim_ledger.json.
   - This is the final delivery audit — distinct from the Python pipeline's draft-level audit.
   - Check orphan citations, unsupported facts, unsupported numbers, missing dates, investment advice language, and process residue.
   - Write/update $ARGUMENTS/output/audit_report.json.

8. Regenerate DOCX:
   - If the CLI supports docx formatting, run the formatter or conversion command.
   - Ensure $ARGUMENTS/output/brief.docx exists if docx is configured.

9. Final response:
   - Report artifact paths.
   - Report audit status.
   - Report any remaining limitations.
   - Do not claim success if audit failed.
