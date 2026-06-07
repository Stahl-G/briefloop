# HERMES.md

This repository is the MABW source repo, not a brief workspace.

Primary Hermes path:

1. Run `bash scripts/setup.sh` if `.venv/` is missing.
2. Install the Hermes plugin:
   `multi-agent-brief hermes install-plugin`
3. For a real brief, do not use demo/example workspaces unless the user explicitly names one.
4. If no workspace is provided, collect onboarding fields:
   company_or_org, industry_or_theme, task_objective, audience, language, cadence, source_style, output_style, must_watch, forbidden_sources, web_search_mode.
5. Use tools in this order:
   `mabw_create_onboarding` → `mabw_init_workspace` → `mabw_run_handoff`.
6. Read `<workspace>/output/intermediate/agent_handoff.md`.
7. Continue with Hermes delegate_task:
   scout → screener → claim-ledger → analyst → editor → auditor.
8. Run `multi-agent-brief finalize --config <workspace>/config.yaml`.
9. Report `output/brief.md`, `brief.docx`, `claim_ledger.json`, and `audit_report.json`.
10. Never treat README, docs, examples, or repo files as evidence for the brief.
