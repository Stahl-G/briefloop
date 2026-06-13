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
Read workspace context -> read frozen audience profile snapshot -> read control switchboard -> read contract references -> identify the next stage -> delegate a specialist or Python tool -> check the expected artifact -> record the completion transaction -> decide retry_stage / delegate_repair / request_human_review / block_run / next stage / finalize.
```

## Mandatory Completion Transaction Rule

A stage is not complete when its artifact is written.
A stage is complete only after `multi-agent-brief state stage-complete` succeeds.

After each stage produces its expected artifact:

1. Verify the expected artifact exists at the declared path.
2. Run:
   `multi-agent-brief state stage-complete --workspace $ARGUMENTS --stage <stage_id> --reason "<reason>"`
3. If `stage-complete` fails, stop. Do not call the next specialist.
4. Only after `stage-complete` succeeds may you dispatch the next specialist or tool.

Never treat `state stage-complete` as after-the-fact bookkeeping.
It is the transaction that defines successful stage completion.
After `stage-complete` succeeds, that stage's output artifacts are frozen for downstream stages. Later stages must not rewrite them in place.
If a downstream stage finds a schema mismatch or invalid frozen upstream artifact, route repair back to the owner stage instead of editing the frozen artifact directly.

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

## Config Authority Rule

Configuration is authoritative.

Do not weaken or override `config.yaml` constraints in specialist prompts.
In particular, do not add free-text exceptions to `max_source_age_days`,
`fail_on_stale_source`, source mode, output safety, or audit settings.

If a config value appears unsuitable for the task, stop and ask the user to
change the workspace config or explicitly approve a structured override.
Do not silently reinterpret config based on report type, cadence, industry, or
your editorial judgment.

3. **Doctor gate:**
   - Run `multi-agent-brief doctor --config $ARGUMENTS/config.yaml`.
   - Resolve reported issues before proceeding.
   - Check the expected artifact or command completion evidence.
   - Run `multi-agent-brief state stage-complete --workspace $ARGUMENTS --stage doctor --reason "Doctor readiness checks passed."`.
   - If the transaction fails, stop and report the failure. Do not invoke the next specialist or tool.

4. **Source discovery transaction (all source profiles):**
   Source discovery is a workflow stage for every run, not only for `llm_decide`.
   Complete the `source-discovery` transaction before invoking Scout.
   - If `sources.yaml` has `source_strategy.profile: llm_decide` and `source_candidates.yaml` is missing or unmerged, resolve sources first:
     - Explain supported web-search options neutrally: Tavily, Exa, Brave, Firecrawl, Serper, runtime_websearch, or configure_later.
     - Run `multi-agent-brief sources decide --config $ARGUMENTS/config.yaml`.
     - Review `$ARGUMENTS/source_candidates.yaml`.
     - Run `multi-agent-brief sources decide --config $ARGUMENTS/config.yaml --merge` after source approval.
   - Treat `source_candidates.yaml` as a source plan only, not source evidence. Scout must extract candidate claims from actual source content or search results.
   - If runtime WebSearch is used and it reports `Did 0 searches`, or every query returns an empty result set, stop and request human review. Do not switch to source-planner and continue with stale or old sources.
   - If the workspace uses a configured non-`llm_decide` source profile, verify the configured `sources.yaml` source surface instead of running source proposal.
   - Check the expected artifact or source configuration evidence.
   - Run `multi-agent-brief state stage-complete --workspace $ARGUMENTS --stage source-discovery --reason "Source discovery source surface was resolved."`.
   - If the transaction fails, stop and report the failure. Do not invoke the next specialist or tool.
   - Local input-only mode can proceed to input governance only after the `source-discovery` transaction succeeds.

5. **Input governance gate (if available):**
   - Run `multi-agent-brief inputs classify --config $ARGUMENTS/config.yaml`.
   - Give the scout subagent the evidence input list.
   - Give user feedback, instructions, and context files to the relevant subagents as guidance rather than factual evidence.
   - Check the expected artifact.
   - Run `multi-agent-brief state stage-complete --workspace $ARGUMENTS --stage input-governance --reason "Input governance classified reader inputs."`.
   - If the transaction fails, stop and report the failure. Do not invoke the next specialist or tool.

6. Invoke the **scout** subagent:
   - Read approved workspace sources, evidence inputs, and cached packages.
   - Extract candidate reportable items.
   - Write `$ARGUMENTS/output/intermediate/candidate_claims.json`.
   - Check the expected artifact.
   - Run `multi-agent-brief state stage-complete --workspace $ARGUMENTS --stage scout --reason "Candidate claims were extracted."`.
   - If the transaction fails, stop and report the failure. Do not invoke the next specialist.

7. Invoke the **screener** subagent:
   - Rank, deduplicate, freshness-check, and capacity-cap candidate items.
   - Follow `config.yaml` freshness settings exactly.
   - Do not tell Screener that older sources may be retained unless the config contains an explicit freshness override.
   - If too few eligible items remain under the configured freshness window, stop and report the mismatch instead of relaxing the rule.
   - Write `$ARGUMENTS/output/intermediate/screened_candidates.json`.
   - Check the expected artifact.
   - Run `multi-agent-brief state stage-complete --workspace $ARGUMENTS --stage screener --reason "Candidate claims were screened and ranked."`.
   - If the transaction fails, stop and report the failure. Do not invoke the next specialist.

8. Invoke the **claim-ledger** subagent:
   - Convert screened candidates into stable, source-grounded claims.
   - Write `$ARGUMENTS/output/intermediate/claim_ledger.json`.
   - Check the expected artifact.
   - Run `multi-agent-brief state stage-complete --workspace $ARGUMENTS --stage claim-ledger --reason "Claim Ledger was built from screened candidates."`.
   - If the transaction fails, stop and report the failure. Do not invoke the next specialist.

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
   - Check the expected artifact.
   - Run `multi-agent-brief state stage-complete --workspace $ARGUMENTS --stage analyst --reason "Auditable brief was drafted from the Claim Ledger."`.
   - If the transaction fails, stop and report the failure. Do not invoke the next specialist.

11. Invoke the **editor** subagent:
    - Polish for management or research-team readability.
    - Clean invalid citation markers and process residue.
    - Preserve valid `[src:CLAIM_ID]` citations in `audited_brief.md`.
    - Check the expected artifact.
    - Run `multi-agent-brief state stage-complete --workspace $ARGUMENTS --stage editor --reason "Auditable brief was edited without changing evidence."`.
    - If the transaction fails, stop and report the failure. Do not invoke the next specialist.

12. Invoke the **auditor** subagent:
    - Audit `$ARGUMENTS/output/intermediate/audited_brief.md` against `$ARGUMENTS/output/intermediate/claim_ledger.json`.
    - Check citation support, numbers, dates, advice language, and process residue.
    - Write or update `$ARGUMENTS/output/intermediate/audit_report.json`.
    - Check the expected artifact.
    - Do not invoke formatter/finalize yet. The auditor stage is complete only after the quality-gate transaction below succeeds.

13. Run deterministic quality gates and refresh runtime state before finalize:
    - Confirm quality gate selection in `control_selections.json`, or record it with `multi-agent-brief controls select --workspace $ARGUMENTS --control quality_gates --selection enable --reason "Use quality gates before finalize."`.
    - Run `multi-agent-brief gates check --workspace $ARGUMENTS`.
    - Run `multi-agent-brief state check --workspace $ARGUMENTS --strict`.
    - If state is not blocked, run `multi-agent-brief state stage-complete --workspace $ARGUMENTS --stage auditor --reason "Audit and quality gates passed."`.
    - If the transaction fails, stop and report the failure. Do not invoke formatter/finalize.
    - If state is blocked, choose `delegate_repair`, `request_human_review`, or `block_run`; do not finalize.
    - Repair guidance is bounded runtime guidance, not an automatic trajectory regulator: if the same stage has already needed roughly three retry/repair rounds, prefer `request_human_review` or `block_run`; if a repair would touch more than two sections, narrow the scope before delegating or request human review.

14. Invoke the **formatter** subagent / finalize tool only after the gates/state completion path passes:
    - Run `multi-agent-brief finalize --config $ARGUMENTS/config.yaml`.
    - Remember: `finalize` is not a quality-gate executor.
    - After finalize writes delivery artifacts, run `multi-agent-brief state finalize-complete --workspace $ARGUMENTS --reason "Reader-facing artifacts passed finalize checks."`.
    - Confirm `$ARGUMENTS/output/delivery/brief.md` is reader-facing.
    - Confirm `$ARGUMENTS/output/delivery/<named>.docx` exists if DOCX is configured.
    - Confirm `$ARGUMENTS/output/source_appendix.md` remains an audit/control copy when configured and does not expose raw claim IDs, source IDs, evidence text, local paths, or `file://` URLs.
    - Do not present Claim Ledger, Audit Report, Audited Brief, named Markdown, or source appendix audit copy as user delivery files.

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
