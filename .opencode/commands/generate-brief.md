---
description: Generate a real source-grounded and audited brief
agent: brief-orchestrator
subtask: false
---

You are the Orchestrator main agent generating a real user-facing brief for workspace: $ARGUMENTS

MABW uses an Orchestrator-led external subagent workflow. Python CLI commands provide setup,
source discovery, input governance, audit checks, validation helpers, and final rendering tools.

Read contract references before delegation:

- `configs/orchestrator_contract.yaml`
- `configs/stage_specs.yaml`
- `configs/artifact_contracts.yaml`
- `configs/policy_packs/default.yaml`

Use this Orchestrator loop for every stage:

1. Read workspace context, frozen audience profile snapshot, control switchboard, and contract references.
2. Identify the current stage and expected artifact.
3. Delegate the specialist role or run the Python tool.
4. Check the expected artifact before continuing.
5. Decide: continue, retry_stage, delegate_repair, request_human_review, block_run, or finalize.

Stage sequence:

1. Initialize runtime handoff/control context:
   - Run: `multi-agent-brief run --workspace $ARGUMENTS --runtime opencode --skip-doctor`
   - Read `$ARGUMENTS/output/intermediate/agent_handoff.md`.
   - Read `$ARGUMENTS/output/intermediate/audience_profile_snapshot.md`.
   - Read `$ARGUMENTS/output/intermediate/orchestrator_control_switchboard.json`.
   - Summarize relevant taste guidance for delegated roles.
   - Do not treat `audience_profile.md` as source evidence; mid-run profile edits apply to the next run.
   - Record control choices with `multi-agent-brief controls select`; selection is not execution.

2. Read `$ARGUMENTS/config.yaml`, `$ARGUMENTS/sources.yaml`, `$ARGUMENTS/user.md`, and workspace inputs.

3. **Source discovery gate (llm_decide only):**
   If `sources.yaml` has `source.mode: llm_decide` and `source_candidates.yaml` does not exist or has not been merged:
   - Run: `multi-agent-brief sources decide --config $ARGUMENTS/config.yaml`
   - Review `$ARGUMENTS/source_candidates.yaml`.
   - Run: `multi-agent-brief sources decide --config $ARGUMENTS/config.yaml --merge`

4. **Doctor gate:**
   - Run: `multi-agent-brief doctor --config $ARGUMENTS/config.yaml`
   - Fix any issues before proceeding.

5. **Input governance gate:**
   - Run: `multi-agent-brief inputs classify --config $ARGUMENTS/config.yaml`
   - Pass only evidence inputs to the scout subagent.

6. Delegate the **brief-scout** subagent:
   - Read approved source materials, evidence inputs, and cached packages.
   - Extract candidate reportable items.
   - Write `$ARGUMENTS/output/intermediate/candidate_claims.json`.

7. Check `candidate_claims.json`, then delegate the **brief-screener** subagent:
   - Dedupe, rank, freshness-check, and cap candidates.
   - Write `$ARGUMENTS/output/intermediate/screened_candidates.json`.

8. Check `screened_candidates.json`, then delegate the **brief-claim-ledger** subagent:
   - Convert screened candidates into source-grounded claims.
   - Write `$ARGUMENTS/output/intermediate/claim_ledger.json`.

9. Read `$ARGUMENTS/output/intermediate/claim_ledger.json` and `$ARGUMENTS/user.md`.

10. Check `claim_ledger.json`, then delegate the **brief-analyst** subagent:
   - Write the final brief from `claim_ledger.json` and `user.md`.
   - Use only `claim_ledger.json` as source evidence.
   - Preserve all valid [src:CLAIM_ID] citations.
   - Write the auditable brief to `$ARGUMENTS/output/intermediate/audited_brief.md`.

11. Check `audited_brief.md`, then delegate the **brief-editor** subagent:
    - Polish for management / research team readability.
    - Preserve valid [src:CLAIM_ID] in `audited_brief.md`.

12. Check edited `audited_brief.md`, then delegate the **brief-auditor** subagent:
    - Audit `$ARGUMENTS/output/intermediate/audited_brief.md` against `$ARGUMENTS/output/intermediate/claim_ledger.json`.

13. Check `audit_report.json`, then run quality gates and refresh runtime state before finalize:
    - Confirm quality gate selection in `control_selections.json`, or record it with `multi-agent-brief controls select --workspace $ARGUMENTS --control quality_gates --selection enable --reason "Use quality gates before finalize."`
    - Run: `multi-agent-brief gates check --workspace $ARGUMENTS`
    - Run: `multi-agent-brief state check --workspace $ARGUMENTS --strict`
    - If state is not blocked, run: `multi-agent-brief state decide --workspace $ARGUMENTS --stage auditor --decision continue --reason "Audit and quality gates passed."`
    - If state is blocked, choose delegate_repair, request_human_review, or block_run; do not finalize.

14. Finalize only after the gates/state decision path passes:
    - Run: `multi-agent-brief finalize --config $ARGUMENTS/config.yaml`
    - Confirm `output/brief.md` strips [src:CLAIM_ID].
    - Remember: finalize is not a quality-gate executor.

15. Optional audit/debug provenance projection after runtime state exists:
    - Run: `multi-agent-brief provenance build --workspace $ARGUMENTS`
    - Run: `multi-agent-brief provenance show --workspace $ARGUMENTS --json`
    - Run: `multi-agent-brief provenance validate --workspace $ARGUMENTS`
    - Treat provenance as citation/control projection, not semantic proof.

16. **Final response:**
    - Report artifact paths.
    - Report audit status.
    - Report quality gate status.
    - Report switchboard selections.
    - Report optional provenance graph path when created.
    - Report success when audit status supports delivery.
