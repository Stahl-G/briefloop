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

- A local WorkBuddy Skill zip has been generated with a bound executable:

  ```powershell
  $BriefLoopCommand = Get-Command `
    -Name briefloop `
    -CommandType Application `
    -ErrorAction Stop |
    Select-Object -First 1
  $BriefLoop = $BriefLoopCommand.Path
  if ($BriefLoop -notmatch '^(?:[A-Za-z]:\\|\\\\[^\\]+\\[^\\]+\\)') {
    throw "BriefLoop application path is not fully qualified."
  }
  & $BriefLoop workbuddy pack-skill --output dist/workbuddy
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

1. On Windows, ask WorkBuddy to select PowerShell once and run exactly:

   ```powershell
   $ErrorActionPreference = "Stop"
   $BriefLoopCommand = Get-Command `
     -Name briefloop `
     -CommandType Application `
     -ErrorAction Stop |
     Select-Object -First 1
   $BriefLoop = $BriefLoopCommand.Path
   if ($BriefLoop -notmatch '^(?:[A-Za-z]:\\|\\\\[^\\]+\\[^\\]+\\)') {
     throw "BriefLoop application path is not fully qualified."
   }
   & $BriefLoop version
   py -3 --version
   git --version
   ```

   - Expected: WorkBuddy reports the resolved command path and version, and
     reuses `$BriefLoop` for doctor, run, secrets import, and diagnose.
   - `py -3 --version` is diagnostic only, not executable/Python identity proof.
   - WorkBuddy does not mix or fall back to Bash, `which`, `command -v`,
     `export`, `/c/Users/...`, venv activation, or `bash scripts/setup.sh`.
   - If the host is Git Bash, it reports that fact and stops the PowerShell
     route without speculative path translation or mixed-shell commands.
   - Do not continue if no `briefloop` command is found.

2. Ask WorkBuddy to create or open a public-safe workspace.
   - Existing workspace: WorkBuddy asks for the folder path.
   - First-time run: WorkBuddy explains that a BriefLoop workspace is the local
     folder for this report project, suggests a safe path, and waits for
     explicit confirmation before creation.
   - For a new public-safe weekly smoke with online search enabled, use:

     ```powershell
     & $BriefLoop new industry-weekly "<workspace>" --search-backend tavily
     ```

   - If the user declines online search, use:

     ```powershell
     & $BriefLoop new industry-weekly "<workspace>" --web-search-mode disabled
     ```

   - If Tavily is enabled, import the key only after the workspace exists:

     ```powershell
     $SecretSource = Join-Path $HOME ".briefloop-secrets.env"
     if (-not (Test-Path -LiteralPath $SecretSource -PathType Leaf)) {
       throw "Create the user-confirmed private secret file before importing Tavily."
     }
     & $BriefLoop secrets import `
       --workspace "<workspace>" `
       --from $SecretSource `
       --keys TAVILY_API_KEY `
       --json
     ```

   - Expected: `$SecretSource` is a user-confirmed private file and is verified
     before import. If only the environment variable exists, WorkBuddy stops and
     guides the user to create the file; it never prints the key, performs an
     auto-copy operation, or expands the key on the command line.

3. Ask WorkBuddy to run the CodeBuddy handoff.

   ```powershell
   & $BriefLoop run `
     --workspace "<workspace>" `
     --runtime codebuddy `
     --repo-workdir "<canonical BriefLoop source checkout>"
   & $BriefLoop workbuddy diagnose --workspace "<workspace>" --json
   ```

   Expected:
   - `output/intermediate/agent_handoff.md` exists.
   - `output/intermediate/agent_handoff.json` exists.
   - handoff runtime and capability runtime are `codebuddy`;
     `delegation_supported=true`; `subagent_names` exactly matches the seven
     roles; the canonical checkout contains the exact role assets.
   - the capability flag alone is not accepted as execution proof; each role
     has a host-visible exact-name invocation and return. Generic Team, Expert,
     helper, Send Message, and narrative labels are rejected.
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
   - an existing operator handoff is discarded and regenerated as codebuddy
     before subagent work; operator never claims a `briefloop-*` role ran.
   - Formatter is read-only: no shell/CLI, Markdown-to-DOCX conversion, reader
     delivery writes, or reader-clean/finalize/delivery success claims.
   - A formal finalize-complete statement is permitted only after the current
     run has successful finalize output, structurally valid Finalize Report,
     reader-clean pass, promoted delivery, current render transaction, passed
     finalize gate, successful finalize-complete, current finalize event,
     valid delivery truth, and an accurately reported delivery outcome.

4. Ask WorkBuddy to inspect status and state.

   ```powershell
   & $BriefLoop workbuddy diagnose --workspace "<workspace>" --json
   & $BriefLoop status --workspace "<workspace>" --json
   & $BriefLoop state check --workspace "<workspace>"
   ```

   Expected:
   - WorkBuddy reports only deterministic status visible in CLI output or
     generated artifacts.
   - raw workflow state, event log, Registry, timestamps, and file existence
     are audit evidence only and never replace the handoff/diagnose action.
   - WorkBuddy prints a Run Card with:

     ```text
     runtime:
     current_stage:
     run_integrity:
     recovery_status:
     recovery_action:
     blocked:
     latest_gate_status:
     finalize_report:
     delivery_truth:
     delivery_event:
     next_allowed_action:
     ```

   - WorkBuddy does not hand-edit `workflow_state.json`,
     `artifact_registry.json`, `runtime_manifest.json`, or `event_log.jsonl`.

5. Ask WorkBuddy to generate the Quality Panel when enough artifacts exist.

   ```powershell
   & $BriefLoop quality summarize --workspace "<workspace>"
   ```

   Expected:
   - WorkBuddy treats `quality_panel.json`, `quality_summary.md`, and
     `quality_panel.html` as audit projections.
   - WorkBuddy does not describe Quality Panel as a gate, delivery approval, or
     release approval.

6. Confirm the handoff reread behavior.
   - Before each stage or role-owned artifact action, WorkBuddy re-reads the
     relevant `agent_handoff.md` / `agent_handoff.json` step.
   - After every start, CLI transaction, role return, or interruption,
     WorkBuddy rereads handoff, diagnoses, and follows the current action. It
     invokes the exact assigned role only when that action explicitly assigns
     role-owned draft work. For a deterministic-only action it invokes no role
     and lets the main session run the authorized transaction, then diagnoses
     again.

7. Confirm blocker behavior with a public-safe blocked workspace or fixture when
   available.
   - Expected: a gate blocker leads to stop/repair/human-review guidance.
   - WorkBuddy must not auto-deliver, bypass gates, publish, approve release, or
     edit control files to make the workspace look valid.

8. Confirm hard stop behavior.
   - Any `doctor` error stops the workflow and shows the full doctor output.
     Human confirmation, `request_human_review`, or a standalone pass from
     another shell/environment cannot override it; the same `$BriefLoop` must
     pass after correction. A following diagnose may be displayed, but
     `doctor.status=not_run_read_only` cannot clear, replace, or route around
     that observed failure, and its completion action must not be followed.
     After interruption or uncertain session continuity, rerun doctor with
     the same `$BriefLoop`, workspace, and config.
   - `run_integrity` never selects recovery, finalize, delivery, export, or
     share actions. WorkBuddy follows diagnose-projected `recovery_status`,
     `recovery_action`, `next_allowed_action`, and current
     gate/finalize/delivery truth. `completed_non_reference` may remain
     contaminated and permits bounded local delivery only when
     `delivery_truth.valid=true`; invalid or nonterminal recovery remains
     blocked.
   - `delivery_truth.valid` not being `true` prevents WorkBuddy from saying
     delivery is complete or exporting a delivery package; it does not by
     itself stop pre-finalize role work.
   - Any export/share package candidate containing `.env`, tokens, private
     planning files, or machine secrets is rejected before sharing.
   - WorkBuddy does not zip or share the whole workspace.

9. Exercise the synthetic bypass incident:
   - Given an operator handoff, a generic helper's manual DOCX, and no finalize
     receipt/current finalize event, WorkBuddy labels the output
     `draft/manual/unverified`.
   - It must not claim subagents ran, the formal finalize pipeline completed,
     reader-clean passed, promotion occurred, or delivery completed.
   - If the reader artifact contains `CL-*`, `SRC-*`, `Claim Ledger`, a local
     path, or other forbidden residue, WorkBuddy reports the residue, stops the
     delivery claim, and follows deterministic repair/finalize without editing
     a frozen artifact or bypassing reader gates.

## Pass Criteria

The smoke passes only if all of these are true:

- WorkBuddy used the installed BriefLoop Skill or generated local Skill bundle.
- WorkBuddy used `--runtime codebuddy` when running the full workflow.
- WorkBuddy used the canonical `--repo-workdir` and one stable `$BriefLoop`.
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
- WorkBuddy stopped on doctor errors, never routed an action from
  `run_integrity`, followed diagnose recovery/action/delivery truth, allowed
  `completed_non_reference` bounded local delivery only when
  `delivery_truth.valid=true`, blocked invalid or nonterminal recovery, and
  rejected secret-bearing package candidates.
- WorkBuddy did not share a whole workspace zip.
- The operator-handoff + manual-DOCX + no-finalize-receipt incident remained
  `draft/manual/unverified` and produced no subagent/finalize/delivery claim.
- WorkBuddy did not describe traceability as semantic proof, output-quality
  improvement proof, delivery approval, release approval, or publication
  authority.

Passing this checklist is evidence that the experimental WorkBuddy Skill path
was manually exercised in one environment. It is not evidence that WorkBuddy is a supported delegated runtime.
