# HERMES.md

This repository is the BriefLoop source repo, not a brief workspace.

Hermes-specific runtime path:

1. Run `bash scripts/setup.sh` if `.venv/` is missing.
2. Install the Hermes plugin:
   `briefloop hermes install-plugin`
3. **Always run `mabw_env_doctor` FIRST.** Follow `next_action` in the result. Never assume the environment is ready.
4. For a new Hermes brief, run the Hermes plugin command `/mabw new`.
   It is a plugin compatibility command, not the `briefloop new` ReportPack initializer. Then collect onboarding fields in chat and call tools:
   `mabw_create_onboarding` → `mabw_init_workspace` → `mabw_run_handoff`.
5. For an existing workspace: `briefloop run --workspace <workspace> --runtime hermes` — env check + handoff in one step.
6. To resume: `briefloop run --workspace <workspace> --runtime hermes`.
7. Read `<workspace>/output/intermediate/agent_handoff.md`.
8. Read `<workspace>/output/intermediate/audience_profile_snapshot.md` as frozen runtime taste context. Do not treat `audience_profile.md` as source evidence, an artifact gate, or provenance proof.
9. Read `<workspace>/output/intermediate/orchestrator_control_switchboard.json`; use `briefloop controls select` to record enable/defer/reject choices. Selection is not execution.
10. Continue with Hermes delegate_task:
   scout → screener → claim-ledger → analyst → editor → auditor.
11. Before finalize, run:
   `briefloop gates check --workspace <workspace>`
   `briefloop state check --workspace <workspace> --strict`
   `briefloop state stage-complete --workspace <workspace> --stage auditor --reason "Audit and quality gates passed."`
12. Then run `briefloop finalize --config <workspace>/config.yaml`. Finalize is transactional: a failed reader-clean does not promote delivery and leaves any prior delivery unchanged.
13. Only after `finalize_report.json` reports `delivery_promotion: "promoted"`, run:
   `briefloop gates check --workspace <workspace> --stage finalize --brief <workspace>/output/brief.md`
   `briefloop state finalize-complete --workspace <workspace> --reason "Reader-facing artifacts passed finalize checks."`
   Then confirm the Store-native status projection (`briefloop status --workspace <workspace> --json`) reports `delivered=true` for the current run before claiming delivery; audit/gate status or artifact existence alone is not a delivery claim. The legacy `workbuddy diagnose` surface is retired.
14. `finalize` alone is not a quality-gate executor; do not skip gates/state completion checks when quality gates are required.
15. Optional audit/debug trace: run `briefloop provenance build --workspace <workspace>` and `briefloop provenance validate --workspace <workspace>` after runtime state exists. This projection is not semantic proof and is not required to finalize.
16. Report `output/brief.md`, `brief.docx`, `claim_ledger.json`, `audit_report.json`, `quality_gate_report.json`, audience snapshot context, switchboard selections, and optional `provenance_graph.json` when created.
17. Never treat README, docs, examples, or repo files as evidence for the brief.
