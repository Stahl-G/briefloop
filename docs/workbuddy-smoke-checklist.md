# WorkBuddy Integration Smoke Checklist

This checklist records a manual WorkBuddy integration smoke path while
WorkBuddy has no stable automatable CLI or test harness for this repository.
It is an experimental integration smoke: not runtime proof, delegated-agent proof, output-quality proof, semantic proof, delivery approval, or release approval.

Use public-safe or synthetic materials only. Do not paste private company data,
tokens, private planning notes, or machine-specific secrets into this checklist
or any issue/PR comment that reports the result.

## Preconditions

- BriefLoop source checkout is available locally.
- The WorkBuddy Skill package guard passes:

  ```bash
  python3 scripts/check_workbuddy_skill_pack.py
  ```

- A local WorkBuddy Skill zip has been generated:

  ```bash
  briefloop workbuddy pack-skill --output dist/workbuddy
  ```

- The generated Skill zip or the source Skill folder has been installed into
  WorkBuddy through WorkBuddy's local Skill import flow. If installing from a
  folder, use `.agents/skills/briefloop-workbuddy/`, not the repo operator
  protocol skill at `.agents/skills/briefloop/`.
- For CodeBuddy project-level discovery, the source checkout exposes the
  project Skill and role-agent assets:

  ```text
  .codebuddy/skills/briefloop/
  .codebuddy/agents/briefloop-*.md
  ```

  The CodeBuddy Skill must run in the main session. It must not use
  `context: fork`.

## Smoke Path

1. Ask WorkBuddy to locate the active BriefLoop CLI.
   - Expected: WorkBuddy reports the resolved command path and version.
   - Do not continue if no `briefloop` command is found.

2. Ask WorkBuddy to create or open a public-safe workspace.
   - Existing workspace: WorkBuddy asks for the folder path.
   - First-time run: WorkBuddy explains that a BriefLoop workspace is the local
     folder for this report project, suggests a safe path, and waits for
     explicit confirmation before creation.
   - For a new public-safe weekly smoke with online search enabled, use:

     ```bash
     briefloop new industry-weekly <workspace> --search-backend tavily
     ```

   - If the user declines online search, use:

     ```bash
     briefloop new industry-weekly <workspace> --web-search-mode disabled
     ```

3. Ask WorkBuddy to run the CodeBuddy handoff.

   ```bash
   briefloop run --workspace <workspace> --runtime codebuddy
   ```

   Expected:
   - `output/intermediate/agent_handoff.md` exists.
   - `output/intermediate/agent_handoff.json` exists.
   - role delegation is explicit and uses the checked-in project sub-agents:

   ```text
   briefloop-scout
   briefloop-screener
   briefloop-claim-ledger
   briefloop-analyst
   briefloop-editor
   briefloop-auditor
   briefloop-formatter
   ```

   Expected:
   - role agents draft only handoff-assigned artifacts;
   - role agents do not run BriefLoop CLI commands;
   - the main CodeBuddy session runs deterministic validation, stage, gate,
     finalize, delivery, and quality commands when allowed.
   - if role agents are unavailable, WorkBuddy stops before full workflow
     execution instead of hand-authoring workflow JSON artifacts or silently
     switching to `--runtime operator`.

4. Ask WorkBuddy to inspect status and state.

   ```bash
   briefloop status --workspace <workspace>
   briefloop state check --workspace <workspace>
   ```

   Expected:
   - WorkBuddy reports only deterministic status visible in CLI output or
     generated artifacts.
   - WorkBuddy prints a Run Card with:

     ```text
     runtime:
     current_stage:
     run_integrity:
     blocked:
     latest_gate_status:
     finalize_report:
     delivery_dir:
     next_allowed_action:
     ```

   - WorkBuddy does not hand-edit `workflow_state.json`,
     `artifact_registry.json`, `runtime_manifest.json`, or `event_log.jsonl`.

5. Ask WorkBuddy to generate the Quality Panel when enough artifacts exist.

   ```bash
   briefloop quality summarize --workspace <workspace>
   ```

   Expected:
   - WorkBuddy treats `quality_panel.json`, `quality_summary.md`, and
     `quality_panel.html` as audit projections.
   - WorkBuddy does not describe Quality Panel as a gate, delivery approval, or
     release approval.

6. Confirm the handoff reread behavior.
   - Before each stage or role-owned artifact action, WorkBuddy re-reads the
     relevant `agent_handoff.md` / `agent_handoff.json` step.
   - After each deterministic CLI transaction, WorkBuddy reports progress only
     if it is visible in status, workflow state, event log, or generated
     artifacts.

7. Confirm blocker behavior with a public-safe blocked workspace or fixture when
   available.
   - Expected: a gate blocker leads to stop/repair/human-review guidance.
   - WorkBuddy must not auto-deliver, bypass gates, publish, approve release, or
     edit control files to make the workspace look valid.

8. Confirm hard stop behavior.
   - Any `doctor` error stops the workflow and shows the full doctor output.
   - Non-clean, contaminated, stale, or unknown `run_integrity` stops
     finalize, delivery, export, and share, but does not by itself stop earlier
     handoff-assigned stages.
   - Missing `output/intermediate/finalize_report.json` or `output/delivery/`
     prevents WorkBuddy from saying delivery is complete or exporting a
     delivery package; it does not by itself stop pre-finalize role work.
   - Any export/share package candidate containing `.env`, tokens, private
     planning files, or machine secrets is rejected before sharing.
   - WorkBuddy does not zip or share the whole workspace.

## Pass Criteria

The smoke passes only if all of these are true:

- WorkBuddy used the installed BriefLoop Skill or generated local Skill bundle.
- WorkBuddy used `--runtime codebuddy` when running the full workflow.
- WorkBuddy reported the active BriefLoop CLI path and version.
- WorkBuddy created or opened only a confirmed workspace path.
- WorkBuddy did not hand-edit Python-owned control files or frozen artifacts.
- WorkBuddy did not claim delegated role execution without actual WorkBuddy
  delegation.
- CodeBuddy project role agents, when used, did not run CLI transactions or
  edit Python-owned control files.
- WorkBuddy printed machine-fact Run Cards instead of free-form completion
  claims.
- WorkBuddy did not silently fall back to `--runtime operator` for full
  workflow execution.
- WorkBuddy stopped on doctor errors, stopped finalize/delivery/export/share
  on contaminated or unknown run integrity, did not claim delivery when
  finalize/delivery artifacts were missing, and rejected secret-bearing package
  candidates.
- WorkBuddy did not share a whole workspace zip.
- WorkBuddy did not describe traceability as semantic proof, output-quality
  improvement proof, delivery approval, release approval, or publication
  authority.

Passing this checklist is evidence that the experimental WorkBuddy Skill path
was manually exercised in one environment. It is not evidence that WorkBuddy is a supported delegated runtime.
