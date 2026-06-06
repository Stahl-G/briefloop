---
description: Generate a real source-grounded brief using explicit Claude Code subagents
argument-hint: "<workspace-path>"
---

You are generating a real user-facing brief for workspace: $ARGUMENTS

MABW is subagent-first. The Python CLI does not run the briefing workflow and `multi-agent-brief prepare` must not be used. Python commands are tools for setup, source planning, validation, audit checks, and finalize/rendering.

Follow this sequence exactly:

1. Read:
   - $ARGUMENTS/config.yaml
   - $ARGUMENTS/user.md
   - $ARGUMENTS/sources.yaml

2. **Source discovery gate (llm_decide only):**
   If `sources.yaml` has `source_strategy.profile: llm_decide` and `source_candidates.yaml` does not exist or `metadata.status` is not `merged`, resolve sources before invoking Scout:
   - If web search is enabled but unconfigured, explain supported options first: Tavily, Exa, Brave, Firecrawl, Serper, runtime_websearch, or configure_later. Do not recommend Tavily as the only option.
   - Run: `multi-agent-brief sources decide --config $ARGUMENTS/config.yaml`
   - Review the generated `$ARGUMENTS/source_candidates.yaml`.
   - Run: `multi-agent-brief sources decide --config $ARGUMENTS/config.yaml --merge`
   - Only proceed after sources are resolved, OR if the user explicitly chooses local input-only mode.

3. **Doctor gate:**
   - Run: `multi-agent-brief doctor --config $ARGUMENTS/config.yaml`
   - Fix any issues before proceeding.

4. Invoke the **scout** subagent:
   - Read approved workspace sources and source packages.
   - Extract candidate reportable items without writing final prose.
   - Write `$ARGUMENTS/output/intermediate/candidate_claims.json`.

5. Invoke the **screener** subagent:
   - Rank, dedupe, freshness-check, and capacity-cap candidate items.
   - Write `$ARGUMENTS/output/intermediate/screened_candidates.json`.

6. Invoke the **claim-ledger** subagent:
   - Convert screened candidates into stable, source-grounded claims.
   - Write `$ARGUMENTS/output/intermediate/claim_ledger.json`.

7. **Market & Competitor Module (if enabled):**
   - Check if `$ARGUMENTS/competitor_universe.yaml` has non-empty entities.
   - If yes and the module is enabled in config.yaml:
     - Read or create `$ARGUMENTS/output/intermediate/market_competitor/evidence_pack.json`.
     - Invoke the **market-competitor-analyst** subagent to generate `$ARGUMENTS/output/intermediate/market_competitor/analysis_cards.json`.
     - Invoke the **market-competitor-auditor** subagent to run specialist audits and update `$ARGUMENTS/output/intermediate/audit_report.json`.
   - If no entities or module is disabled, skip this step.

8. Invoke the **analyst** subagent:
   - Read `$ARGUMENTS/output/intermediate/claim_ledger.json` and `$ARGUMENTS/user.md`.
   - Write the auditable brief using only Claim Ledger evidence.
   - Preserve all valid [src:CLAIM_ID] citations.
   - Include dates for news items.
   - If analysis cards exist, merge supported competitive analysis using supporting_claim_ids for citations.
   - Target a real weekly brief, not a thin bullet list.
   - Write `$ARGUMENTS/output/intermediate/audited_brief.md`.

9. Invoke the **editor** subagent:
   - Polish for management / research team readability.
   - Remove invalid [SRC:], [SOURCE:], [src:] residue.
   - Remove Claude/Codex process residue.
   - Preserve valid [src:CLAIM_ID] in `audited_brief.md`.

10. Invoke the **auditor** subagent:
   - Audit `$ARGUMENTS/output/intermediate/audited_brief.md` against `$ARGUMENTS/output/intermediate/claim_ledger.json`.
   - Check orphan citations, unsupported facts, unsupported numbers, missing dates, investment advice language, and process residue.
   - Write/update `$ARGUMENTS/output/intermediate/audit_report.json`.

11. Invoke the **formatter** subagent / finalize tool:
   - Run `multi-agent-brief finalize --config $ARGUMENTS/config.yaml`.
   - Confirm `$ARGUMENTS/output/brief.md` strips [src:CLAIM_ID] from the audited brief.
   - Confirm the configured named Markdown copy exists if enabled.
   - Confirm `$ARGUMENTS/output/brief.docx` exists if DOCX is configured.

12. Final response:
   - Report artifact paths.
   - Report audit status.
   - Report any remaining limitations.
   - Do not claim success if audit failed.
