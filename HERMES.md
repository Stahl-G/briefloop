# HERMES.md

This repository is the MABW source repo, not a brief workspace.

Primary Hermes path:

1. Run `bash scripts/setup.sh` if `.venv/` is missing.
2. Install the Hermes plugin:
   `multi-agent-brief hermes install-plugin`
3. **Always run `mabw_env_doctor` FIRST.** Follow `next_action` in the result. Never assume the environment is ready.
4. For a new brief: `/mabw new` — then collect onboarding fields in chat, then call tools:
   `mabw_create_onboarding` → `mabw_init_workspace` → `mabw_run_handoff`.
5. For an existing workspace: `/mabw run <workspace>` — env check + handoff in one step.
6. To resume: `/mabw continue <workspace>`.
7. Read `<workspace>/output/intermediate/agent_handoff.md`.
8. Continue with Hermes delegate_task:
   scout → screener → claim-ledger → analyst → editor → auditor.
9. Before finalize, run:
   `multi-agent-brief gates check --workspace <workspace>`
   `multi-agent-brief state check --workspace <workspace> --strict`
   `multi-agent-brief state decide --workspace <workspace> --stage auditor --decision continue --reason "Audit and quality gates passed."`
10. Then run `multi-agent-brief finalize --config <workspace>/config.yaml`.
11. `finalize` alone is not a quality-gate executor; do not skip gates/state decisions when quality gates are required.
12. Report `output/brief.md`, `brief.docx`, `claim_ledger.json`, `audit_report.json`, and `quality_gate_report.json`.
13. Never treat README, docs, examples, or repo files as evidence for the brief.
