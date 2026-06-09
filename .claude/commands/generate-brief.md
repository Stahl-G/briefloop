---
description: Generate a real source-grounded brief through the Orchestrator main agent and explicit Claude Code subagents
argument-hint: "<workspace-path>"
---

You are the Orchestrator main agent generating a real user-facing brief for workspace: $ARGUMENTS.

MABW uses an external subagent workflow. Python CLI commands provide setup, source planning, validation, audit checks, and final rendering tools.

Read shared contract references before delegation:

- `configs/orchestrator_contract.yaml`
- `configs/stage_specs.yaml`
- `configs/artifact_contracts.yaml`
- `configs/policy_packs/default.yaml`

Orchestrator control loop:

```text
Read workspace context -> read frozen audience profile snapshot -> read control switchboard -> read contract references -> identify the next stage -> delegate a specialist or Python tool -> check the expected artifact -> decide continue / retry_stage / delegate_repair / request_human_review / block_run / finalize.
```

Follow this sequence:

1. Initialize runtime handoff/control context:
   - Run `multi-agent-brief run --workspace $ARGUMENTS --runtime claude --skip-doctor`.
   - Read `$ARGUMENTS/output/intermediate/agent_handoff.md`.
   - Read `$ARGUMENTS/output/intermediate/audience_profile_snapshot.md`.
   - Read `$ARGUMENTS/output/intermediate/orchestrator_control_switchboard.json`.
   - Summarize relevant taste guidance for delegated roles.
   - Do not treat `audience_profile.md` as source evidence; mid-run profile edits apply to the next run.
   - Record control choices with `multi-agent-brief controls select`; selection is not execution.

2. Read:
   - $ARGUMENTS/config.yaml
   - $ARGUMENTS/user.md
   - $ARGUMENTS/sources.yaml
   - workspace input/source files

3. **Source discovery gate (llm_decide only):**
   If `sources.yaml` has `source_strategy.profile: llm_decide` and `source_candidates.yaml` is missing or unmerged, resolve sources before invoking Scout:
   - Explain supported web-search options neutrally: Tavily, Exa, Brave, Firecrawl, Serper, runtime_websearch, or configure_later.
   - Run `multi-agent-brief sources decide --config $ARGUMENTS/config.yaml`.
   - Review `$ARGUMENTS/source_candidates.yaml`.
   - Run `multi-agent-brief sources decide --config $ARGUMENTS/config.yaml --merge` after source approval.
   - Local input-only mode can proceed directly to the doctor gate.

4. **Doctor gate:**
   - Run `multi-agent-brief doctor --config $ARGUMENTS/config.yaml`.
   - Resolve reported issues before proceeding.

5. **Input governance gate (if available):**
   - Run `multi-agent-brief inputs classify --config $ARGUMENTS/config.yaml`.
   - Give the scout subagent the evidence input list.
   - Give user feedback, instructions, and context files to the relevant subagents as guidance rather than factual evidence.

6. Invoke the **scout** subagent:
   - Read approved workspace sources, evidence inputs, and cached packages.
   - Extract candidate reportable items.
   - Write `$ARGUMENTS/output/intermediate/candidate_claims.json`.
   - Check the expected artifact before selecting the next decision.

7. Invoke the **screener** subagent:
   - Rank, deduplicate, freshness-check, and capacity-cap candidate items.
   - Write `$ARGUMENTS/output/intermediate/screened_candidates.json`.
   - Check the expected artifact before selecting the next decision.

8. Invoke the **claim-ledger** subagent:
   - Convert screened candidates into stable, source-grounded claims.
   - Write `$ARGUMENTS/output/intermediate/claim_ledger.json`.
   - Check the expected artifact before selecting the next decision.

9. **Market & Competitor Module (if enabled):**
   - Check whether `$ARGUMENTS/competitor_universe.yaml` has non-empty entities.
   - If the module is enabled in config.yaml, use the market-competitor subagents to generate analysis cards and audit findings.
   - Merge supported competitive analysis through Claim Ledger citations.

10. Invoke the **analyst** subagent:
   - Read `$ARGUMENTS/output/intermediate/claim_ledger.json` and `$ARGUMENTS/user.md`.
   - Write the auditable brief using Claim Ledger evidence.
   - Preserve valid `[src:CLAIM_ID]` citations.
   - Include dates for news items.
   - Write `$ARGUMENTS/output/intermediate/audited_brief.md`.
   - Check the expected artifact before selecting the next decision.

11. Invoke the **editor** subagent:
    - Polish for management or research-team readability.
    - Clean invalid citation markers and process residue.
    - Preserve valid `[src:CLAIM_ID]` citations in `audited_brief.md`.
    - Check the expected artifact before selecting the next decision.

12. Invoke the **auditor** subagent:
    - Audit `$ARGUMENTS/output/intermediate/audited_brief.md` against `$ARGUMENTS/output/intermediate/claim_ledger.json`.
    - Check citation support, numbers, dates, advice language, and process residue.
    - Write or update `$ARGUMENTS/output/intermediate/audit_report.json`.
    - Check the expected artifact before selecting the next decision.

13. Run deterministic quality gates and refresh runtime state before finalize:
    - Confirm quality gate selection in `control_selections.json`, or record it with `multi-agent-brief controls select --workspace $ARGUMENTS --control quality_gates --selection enable --reason "Use quality gates before finalize."`.
    - Run `multi-agent-brief gates check --workspace $ARGUMENTS`.
    - Run `multi-agent-brief state check --workspace $ARGUMENTS --strict`.
    - If state is not blocked, run `multi-agent-brief state decide --workspace $ARGUMENTS --stage auditor --decision continue --reason "Audit and quality gates passed."`.
    - If state is blocked, choose `delegate_repair`, `request_human_review`, or `block_run`; do not finalize.

14. Invoke the **formatter** subagent / finalize tool only after the gates/state decision path passes:
    - Run `multi-agent-brief finalize --config $ARGUMENTS/config.yaml`.
    - Remember: `finalize` is not a quality-gate executor.
    - Confirm `$ARGUMENTS/output/brief.md` is reader-facing.
    - Confirm `$ARGUMENTS/output/source_appendix.md` exists when configured and does not expose raw claim IDs, source IDs, evidence text, local paths, or `file://` URLs.
    - Confirm the configured named Markdown copy exists if enabled.
    - Confirm `$ARGUMENTS/output/brief.docx` exists if DOCX is configured.

15. Optional audit/debug provenance projection after runtime state exists:
    - Run `multi-agent-brief provenance build --workspace $ARGUMENTS`.
    - Run `multi-agent-brief provenance show --workspace $ARGUMENTS --json`.
    - Run `multi-agent-brief provenance validate --workspace $ARGUMENTS`.
    - Treat provenance as citation/control projection, not semantic proof.

16. Final response:
    - Report artifact paths.
    - Report audit status.
    - Report quality gate status.
    - Report switchboard selections.
    - Report optional provenance graph path when created.
    - Report remaining limitations.
