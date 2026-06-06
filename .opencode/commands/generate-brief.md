---
description: Generate a real source-grounded and audited brief
agent: brief-orchestrator
subtask: false
---

You are generating a real user-facing brief for workspace: $ARGUMENTS

The Python CLI does not run the brief workflow. OpenCode subagents execute Scout/Screener/Claim Ledger/Analyst/Editor/Auditor/Formatter roles; Python commands provide setup, source planning, validation, audit, and finalize tools.

Follow this sequence exactly:

1. Read:
   - $ARGUMENTS/config.yaml
   - $ARGUMENTS/user.md
   - $ARGUMENTS/sources.yaml

2. **Source discovery gate (llm_decide only):**
   If `sources.yaml` has `source.mode: llm_decide` and `source_candidates.yaml` does not exist or has not been merged, resolve sources before invoking Scout:
   - Run: `multi-agent-brief sources decide --config $ARGUMENTS/config.yaml`
   - Review the generated `$ARGUMENTS/source_candidates.yaml`.
   - Run: `multi-agent-brief sources decide --config $ARGUMENTS/config.yaml --merge`

3. **Doctor gate:**
   - Run: `multi-agent-brief doctor --config $ARGUMENTS/config.yaml`
   - Fix any issues before proceeding.

4. Invoke **brief-scout** to write `$ARGUMENTS/output/intermediate/candidate_claims.json`.

5. Invoke **brief-screener** to write `$ARGUMENTS/output/intermediate/screened_candidates.json`.

6. Invoke **brief-claim-ledger** to write `$ARGUMENTS/output/intermediate/claim_ledger.json`.

7. Read:
   - $ARGUMENTS/output/intermediate/claim_ledger.json
   - $ARGUMENTS/user.md

8. Invoke the **brief-analyst** subagent:
   - Write the final brief from claim_ledger.json and user.md.
   - Use only claim_ledger.json as source evidence.
   - Preserve all valid [src:CLAIM_ID] citations.
   - Write the auditable brief to $ARGUMENTS/output/intermediate/audited_brief.md.

9. Invoke the **brief-editor** subagent:
   - Polish for management / research team readability.
   - Preserve valid [src:CLAIM_ID] in audited_brief.md.

10. Invoke the **brief-auditor** subagent:
   - Audit $ARGUMENTS/output/intermediate/audited_brief.md against $ARGUMENTS/output/intermediate/claim_ledger.json.

11. Finalize reader artifacts:
   - Run: `multi-agent-brief finalize --config $ARGUMENTS/config.yaml`

12. Final response:
   - Report artifact paths.
   - Report audit status.
   - Do not claim success if audit failed.
