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

6. Read `configs/policy_packs/default.yaml` and apply role topology:
   - `default`: Scout performs discovery + screening and writes both `candidate_claims.json` and `screened_candidates.json`.
   - `strict`: Scout writes only `candidate_claims.json`; then Screener writes `screened_candidates.json`.
   - In all modes both artifacts are required before Claim Ledger.
   - Optional chunk parallelism is parent-side only: chunk outputs are scratch/intermediate runtime material, not workflow artifacts.
   - If Scout work is split across chunks or child agents, the parent must join chunks deterministically before writing `candidate_claims.json`, using source identity, source path or URL, source date, topic, and evidence text rather than completion order.
   - Do not append to `candidate_claims.json` from chunk workers, and do not silently drop duplicate or near-duplicate chunk outputs.

7. Delegate the **brief-scout** subagent:
   - Read approved source materials, evidence inputs, and cached packages.
   - Extract candidate reportable items.
   - Write `$ARGUMENTS/output/intermediate/candidate_claims.json`.
   - In default topology, screen candidates and write `$ARGUMENTS/output/intermediate/screened_candidates.json` before recording `stage-complete --stage scout`.

8. Strict topology only: check `candidate_claims.json`, then delegate the **brief-screener** subagent:
   - Dedupe, rank, freshness-check, and cap candidates.
   - Write `$ARGUMENTS/output/intermediate/screened_candidates.json`.

9. Check `screened_candidates.json`, then delegate the **brief-claim-ledger** subagent:
   - Convert screened candidates into source-grounded claim drafts without claim_id fields.
   - Write `$ARGUMENTS/output/intermediate/claim_drafts.json`.
   - Run: `multi-agent-brief state freeze-claim-ledger --workspace $ARGUMENTS`.
   - Confirm freeze produced `$ARGUMENTS/output/intermediate/claim_ledger.json` before `stage-complete --stage claim-ledger`.

10. Read `$ARGUMENTS/output/intermediate/claim_ledger.json` and `$ARGUMENTS/user.md`.

11. Check `claim_ledger.json`, then delegate the **brief-analyst** subagent:
   - Write the Analyst working draft from `claim_ledger.json` and `user.md`.
   - Use only `claim_ledger.json` as source evidence.
   - Preserve all valid [src:<claim_id>] citations that use real Claim Ledger IDs.
   - Write the working auditable brief to `$ARGUMENTS/output/intermediate/audited_brief.md`.
   - Do not write `$ARGUMENTS/output/intermediate/analyst_draft_snapshot.md`; Python freezes it during analyst stage-complete.

12. After analyst stage-complete freezes `analyst_draft_snapshot.md`, delegate the **brief-editor** / Delivery Editor subagent:
    - Read `$ARGUMENTS/output/intermediate/analyst_draft_snapshot.md` as the frozen factual boundary.
    - Own the Editor-owned final auditable brief at `$ARGUMENTS/output/intermediate/audited_brief.md`.
    - Polish for management / research team readability.
    - Do not add new facts, numbers, named entities, dates, causal claims, or citations.
    - Preserve valid [src:<claim_id>] in `audited_brief.md` that use real Claim Ledger IDs.

13. Check edited `audited_brief.md`, then delegate the **brief-auditor** subagent:
    - Audit `$ARGUMENTS/output/intermediate/audited_brief.md` against `$ARGUMENTS/output/intermediate/claim_ledger.json`.

14. Check `audit_report.json`, then run quality gates and refresh runtime state before finalize:
    - Confirm quality gate selection in `control_selections.json`, or record it with `multi-agent-brief controls select --workspace $ARGUMENTS --control quality_gates --selection enable --reason "Use quality gates before finalize."`
    - Run: `multi-agent-brief gates check --workspace $ARGUMENTS --stage auditor`
    - Run: `multi-agent-brief state check --workspace $ARGUMENTS --strict`
    - If state is not blocked, run: `multi-agent-brief state stage-complete --workspace $ARGUMENTS --stage auditor --reason "Audit and quality gates passed."`
    - If state is blocked, choose delegate_repair, request_human_review, or block_run; do not finalize.

15. Finalize only after the gates/state completion path passes:
    - Run: `multi-agent-brief finalize --config $ARGUMENTS/config.yaml`
    - After finalize writes delivery artifacts, run: `multi-agent-brief gates check --workspace $ARGUMENTS --stage finalize --brief $ARGUMENTS/output/brief.md`.
    - Then run: `multi-agent-brief state finalize-complete --workspace $ARGUMENTS --reason "Reader-facing artifacts passed finalize checks."`
    - Confirm `output/delivery/brief.md` strips [src:<claim_id>].
    - Confirm `output/delivery/<named>.docx` exists if DOCX is configured.
    - Confirm `output/source_appendix.md` remains an audit/control copy when configured and does not expose raw claim IDs, source IDs, evidence text, local paths, or file:// URLs.
    - Do not present Claim Ledger, Audit Report, Audited Brief, named Markdown, or source appendix audit copy as user delivery files.
    - Remember: finalize is not a quality-gate executor.

16. Optional audit/debug provenance projection after runtime state exists:
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
