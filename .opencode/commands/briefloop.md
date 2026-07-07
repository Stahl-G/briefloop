---
description: Generate a real source-grounded and audited brief
agent: brief-orchestrator
subtask: false
---

You are the Orchestrator main agent generating a real user-facing brief for workspace: $ARGUMENTS

BriefLoop uses an Orchestrator-led external subagent workflow. Python CLI commands provide setup,
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
   - Run: `briefloop run --workspace $ARGUMENTS --runtime opencode --skip-doctor`
   - Read `$ARGUMENTS/output/intermediate/agent_handoff.md`.
   - Read `$ARGUMENTS/output/intermediate/audience_profile_snapshot.md`.
   - Read `$ARGUMENTS/output/intermediate/orchestrator_control_switchboard.json`.
   - Summarize relevant taste guidance for delegated roles.
   - Do not treat `audience_profile.md` as source evidence; mid-run profile edits apply to the next run.
   - Record control choices with `briefloop controls select`; selection is not execution.
   - Do not call `briefloop run` again mid-pipeline to refresh handoff or state. Use `briefloop status`, `state show`, `gates check`, `state check`, and repair commands instead.

2. Read `$ARGUMENTS/config.yaml`, `$ARGUMENTS/sources.yaml`, `$ARGUMENTS/user.md`, and workspace inputs.

3. **Source discovery gate (llm_decide only):**
   If `sources.yaml` has `source.mode: llm_decide` and `source_candidates.yaml` does not exist or has not been merged:
   - Run: `briefloop sources decide --config $ARGUMENTS/config.yaml`
   - Review `$ARGUMENTS/source_candidates.yaml`.
   - Run: `briefloop sources decide --config $ARGUMENTS/config.yaml --merge`

4. **Doctor gate:**
   - Run: `briefloop doctor --config $ARGUMENTS/config.yaml`
   - Fix any issues before proceeding.

5. **Input governance gate:**
   - Run: `briefloop inputs classify --config $ARGUMENTS/config.yaml`
   - Pass only evidence inputs to the scout subagent.

6. Read `configs/policy_packs/default.yaml` and apply role topology:
   - `default`: Scout performs discovery + screening and writes both `candidate_claims.json` and `screened_candidates.json`.
     Do not delegate Screener and do not call `state stage-complete --stage screener` in default topology.
   - `strict`: Scout writes only `candidate_claims.json`; then Screener writes `screened_candidates.json`.
   - In all modes both artifacts are required before Claim Ledger.
   - Optional chunk parallelism is parent-side only: chunk outputs are scratch/intermediate runtime material, not workflow artifacts.
   - If Scout work is split across chunks or child agents, the parent must join chunks deterministically before writing `candidate_claims.json`, using source identity, source path or URL, source date, topic, and evidence text rather than completion order.
   - Source identity must preserve `source_url` only for HTTP(S) URLs or `source_path` for local/package sources, plus source title/name, publisher when known, source_category, provider source_type, source dates, and evidence text.
   - Never put titles, source names, source IDs, search queries, or local paths in `source_url`.
   - Do not append to `candidate_claims.json` from chunk workers, and do not silently drop duplicate or near-duplicate chunk outputs.

7. Delegate the **brief-scout** subagent:
   - Read approved source materials, evidence inputs, and cached packages.
   - Extract candidate reportable items.
   - Write `$ARGUMENTS/output/intermediate/candidate_claims.json`.
   - In default topology, screen candidates and write `$ARGUMENTS/output/intermediate/screened_candidates.json` before recording `stage-complete --stage scout`.
   - Do not replay Screener delegation or `stage-complete --stage screener` in default topology.

8. Strict topology only: check `candidate_claims.json`, then delegate the **brief-screener** subagent:
   - Dedupe, rank, freshness-check, and cap candidates.
   - Write `$ARGUMENTS/output/intermediate/screened_candidates.json`.

9. Check `screened_candidates.json`, then delegate the **brief-claim-ledger** subagent:
   - Convert screened candidates into source-grounded claim drafts without claim_id fields.
   - Preserve source URL/path, source title/name, publisher, source_category, provider source_type, published/retrieved dates, topic, claim type, confidence, and evidence text.
   - Never put titles, source names, source IDs, search queries, or local paths in `source_url`.
   - Write `$ARGUMENTS/output/intermediate/claim_drafts.json`.
   - Run: `briefloop state freeze-claim-ledger --workspace $ARGUMENTS`.
   - Confirm freeze produced `$ARGUMENTS/output/intermediate/claim_ledger.json` before `stage-complete --stage claim-ledger`.

10. Read `$ARGUMENTS/output/intermediate/claim_ledger.json` and `$ARGUMENTS/user.md`.

11. Check `claim_ledger.json`, then delegate the **brief-analyst** subagent:
   - Write the Analyst working draft from `claim_ledger.json` and `user.md`.
   - Use only `claim_ledger.json` as source evidence.
   - If `atomic_claim_graph.json` is present and valid, use it only as an optional experimental structural decomposition aid for frozen Claim Ledger claims; it is not source evidence or proof of support.
   - Preserve all valid [src:<claim_id>] citations that use real Claim Ledger IDs.
   - Do not cite atom IDs in reader-facing prose.
   - Do not introduce material atoms absent from the frozen Claim Ledger and, when present and valid, `atomic_claim_graph.json`.
   - Do not create, edit, rewrite, repair, or extend `$ARGUMENTS/output/intermediate/atomic_claim_graph.json`; if it is absent or invalid, do not repair it.
   - Write the working auditable brief to `$ARGUMENTS/output/intermediate/audited_brief.md`.
   - Do not write `$ARGUMENTS/output/intermediate/analyst_draft_snapshot.md`; Python freezes it during analyst stage-complete.

12. After analyst stage-complete freezes `analyst_draft_snapshot.md`, delegate the **brief-editor** / Delivery Editor subagent:
    - Read `$ARGUMENTS/output/intermediate/analyst_draft_snapshot.md` as the frozen factual boundary.
    - Own the Editor-owned final auditable brief at `$ARGUMENTS/output/intermediate/audited_brief.md`.
    - Polish for management / research team readability.
    - Do not add new facts, numbers, named entities, dates, causal claims, or citations.
    - If `$ARGUMENTS/output/intermediate/atomic_claim_graph.json` is present and valid, use it only as an optional experimental structural decomposition aid; if it is absent or invalid, do not repair it.
    - Do not create, edit, rewrite, repair, or extend `$ARGUMENTS/output/intermediate/atomic_claim_graph.json`.
    - Do not introduce material atoms absent from the frozen Claim Ledger and, when present and valid, `atomic_claim_graph.json`.
    - Do not cite atom IDs in reader-facing prose.
    - Preserve valid [src:<claim_id>] in `audited_brief.md` that use real Claim Ledger IDs.

13. Check edited `audited_brief.md`, then delegate the **brief-auditor** subagent:
    - Audit `$ARGUMENTS/output/intermediate/audited_brief.md` against `$ARGUMENTS/output/intermediate/claim_ledger.json`.

14. Check `audit_report.json`, then run quality gates and refresh runtime state before finalize:
    - Confirm quality gate selection in `control_selections.json`, or record it with `briefloop controls select --workspace $ARGUMENTS --control quality_gates --selection enable --reason "Use quality gates before finalize."`
    - Run: `briefloop gates check --workspace $ARGUMENTS --stage auditor`
    - Run: `briefloop state check --workspace $ARGUMENTS --strict`
    - If state is not blocked, run: `briefloop state stage-complete --workspace $ARGUMENTS --stage auditor --reason "Audit and quality gates passed."`
    - If state is blocked, do not edit artifacts directly and do not finalize.
    - Do not edit frozen artifacts directly. Direct edits will mark the run contaminated and non-reference-eligible.
    - Run: `briefloop gates show --workspace $ARGUMENTS --json` and follow its required_commands.
    - Current-gate repair start must be scoped with `--gate-stage` and `--gate-artifact`; do not use unscoped repair start for current-gate blockers.
    - For non-gate owner-stage repair routes from audit_report, finalize_report, artifact_registry, or transaction_integrity, run: `briefloop repair route --workspace $ARGUMENTS --json`.
    - Start the selected non-gate route with `--finding-id <finding_id>` or `--route-index <route_index>`; do not use bare `repair start --workspace $ARGUMENTS`.
    - If the current gate has an owner-stage repair route:
      1. Run the scoped repair start command from `gates show` required_commands.
      2. Delegate only the reported repair_owner role.
      3. Allow edits only to the reported allowed_artifacts.
      4. Do not edit blocked_direct_edits or any frozen artifact outside allowed_artifacts.
      5. After the owner role finishes, run: `briefloop repair complete --workspace $ARGUMENTS --reason "<reason>" --json`
      6. Resume from must_rerun_from. If must_rerun_from is auditor, rerun Auditor and then gates/state check.
    - If no deterministic current-gate repair route is available, choose request_human_review or block_run.
    - Never use state decide delegate_repair to authorize artifact edits.
    - Never manually update artifact_registry.json or frozen hashes.

15. Finalize only after the gates/state completion path passes:
    - Run: `briefloop finalize --config $ARGUMENTS/config.yaml`
    - After finalize writes delivery artifacts, run: `briefloop gates check --workspace $ARGUMENTS --stage finalize --brief $ARGUMENTS/output/brief.md`.
    - Then run: `briefloop state finalize-complete --workspace $ARGUMENTS --reason "Reader-facing artifacts passed finalize checks."`
    - Confirm `output/delivery/brief.md` strips [src:<claim_id>].
    - Confirm `output/delivery/<named>.docx` exists if DOCX is configured.
    - Confirm `output/source_appendix.md` remains an audit/control copy when configured and does not expose raw claim IDs, source IDs, evidence text, local paths, or file:// URLs.
    - Do not present Claim Ledger, Audit Report, Audited Brief, named Markdown, or source appendix audit copy as user delivery files.
    - Remember: finalize is not a quality-gate executor.

16. Optional audit/debug provenance projection after runtime state exists:
    - Run: `briefloop provenance build --workspace $ARGUMENTS`
    - Run: `briefloop provenance show --workspace $ARGUMENTS --json`
    - Run: `briefloop provenance validate --workspace $ARGUMENTS`
    - Treat provenance as citation/control projection, not semantic proof.

16. **Final response:**
    - Report artifact paths.
    - Report audit status.
    - Report quality gate status.
    - Report switchboard selections.
    - Report optional provenance graph path when created.
    - Report success when audit status supports delivery.
