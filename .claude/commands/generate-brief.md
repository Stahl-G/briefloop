---
description: Generate a real source-grounded brief using CLI preparation + Claude Code subagents
argument-hint: "<workspace-path>"
---

You are generating a real user-facing brief for workspace: $ARGUMENTS

The Python CLI prepares deterministic reader-facing files and intermediate audit artifacts. A polished final brief should still be reviewed by Claude Code subagents or a human using the Claim Ledger and audit outputs.

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
   - This produces `output/brief.md` for readers and `output/intermediate/` audit artifacts.

3.5 **Market & Competitor Module (if enabled):**
   - Check if `$ARGUMENTS/competitor_universe.yaml` has non-empty entities.
   - If yes and the module is enabled in config.yaml:
     - Read `$ARGUMENTS/output/intermediate/market_competitor/evidence_pack.json`
     - Invoke the **market-competitor-analyst** subagent to generate
       `$ARGUMENTS/output/intermediate/market_competitor/analysis_cards.json`.
     - Invoke the **market-competitor-auditor** subagent to run 6 specialist
       audits and update `$ARGUMENTS/output/intermediate/audit_report.json`.
   - If no entities or module is disabled, skip this step.

4. Read:
   - $ARGUMENTS/output/intermediate/claim_ledger.json
   - $ARGUMENTS/output/intermediate/audited_brief.md
   - $ARGUMENTS/user.md

5. Invoke the **analyst** subagent:
   - Write the final brief from claim_ledger.json and user.md.
   - Use only claim_ledger.json as source evidence.
   - Preserve all valid [src:CLAIM_ID] citations.
   - Include dates for news items.
   - If `$ARGUMENTS/output/intermediate/market_competitor/analysis_cards.json` exists,
     read it and merge competitive analysis into the competitor section using
     supporting_claim_ids from AnalysisCards for [src:CLAIM_ID] citations.
   - Target a real weekly brief, not a thin bullet list.
   - Write the auditable brief to $ARGUMENTS/output/intermediate/audited_brief.md.

6. Invoke the **editor** subagent:
   - Polish for management / research team readability.
   - Remove invalid [SRC:], [SOURCE:], [src:] residue.
   - Remove Claude/Codex process residue.
   - Preserve valid [src:CLAIM_ID] in audited_brief.md.

7. Invoke the **auditor** subagent:
   - Audit $ARGUMENTS/output/intermediate/audited_brief.md against $ARGUMENTS/output/intermediate/claim_ledger.json.
   - This is the final delivery audit — distinct from the Python pipeline's draft-level audit.
   - Check orphan citations, unsupported facts, unsupported numbers, missing dates, investment advice language, and process residue.
   - Write/update $ARGUMENTS/output/intermediate/audit_report.json.

8. Regenerate DOCX:
   - Regenerate $ARGUMENTS/output/brief.md by stripping [src:CLAIM_ID] from the audited brief.
   - Regenerate the configured named Markdown copy from `output.filename_template` if enabled.
   - If DOCX is configured, run the formatter or conversion command from the stripped reader brief.
   - Ensure $ARGUMENTS/output/brief.docx exists if docx is configured.

9. Final response:
   - Report artifact paths.
   - Report audit status.
   - Report any remaining limitations.
   - Do not claim success if audit failed.
