---
name: briefloop
description: Operate BriefLoop workspaces from the main CodeBuddy session using deterministic CLI transactions and project role sub-agents.
---

# BriefLoop CodeBuddy Skill

## Scope

This is the project-level CodeBuddy Skill adapter for BriefLoop first-user and
workspace operation. It is a main-session orchestration protocol, not a forked
Skill and not a new BriefLoop authority layer.

Do not add `context: fork` to this Skill. CodeBuddy forked Skills run inside an
isolated sub-agent context, and CodeBuddy sub-agents cannot spawn other
sub-agents. The BriefLoop Skill must stay in the main CodeBuddy session so it
can invoke role sub-agents and then run deterministic BriefLoop CLI
transactions.

Canonical WorkBuddy/CodeBuddy instructions remain in:

```text
.agents/skills/briefloop-workbuddy/SKILL.md
.agents/skills/briefloop-workbuddy/references/
```

Read those files before operating a workspace. This adapter exists so
CodeBuddy's official project Skill discovery can find BriefLoop at:

```text
.codebuddy/skills/briefloop/SKILL.md
```

## First Checks

1. On Windows WorkBuddy, select PowerShell once and bind one absolute CLI path:

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

   Reuse `$BriefLoop` for bootstrap, doctor, run, secrets import, and diagnose.
   `py -3 --version` is diagnostic only. Do not mix or fall back to `bash`,
   `which`, `command -v`, `export`, `/c/Users/...`,
   `source .venv/bin/activate`, or `bash scripts/setup.sh`. If the actual shell
   is Git Bash, report it and stop the PowerShell route; do not guess path
   translations or mix shells. This contract does not claim Git Bash support.

2. Report only the resolved command path and version.
3. If the main session cannot resolve and invoke `briefloop` or
   `multi-agent-brief`, stop before workspace creation, role work, fallback, or
   state advancement. Regenerating an operator handoff does not supply missing
   command execution and is not a fallback for a missing CLI.
4. Ask whether the user wants online search enabled. If yes, strongly recommend
   Tavily and record the choice. Never run workspace `secrets import` before a
   new workspace is created; verify an existing workspace before importing.
   Never print or commit the key, temporarily export it, mutate PATH, or inject
   it into one command. If no, explicitly disable web search.
5. Classify the workspace path:
   - existing workspace: ask for the folder path;
   - first-time run: explain that a BriefLoop workspace is the local folder for
     this report project, suggest a safe path, and ask for confirmation before
     creating it.
6. When creating a workspace, make the search choice explicit:

   ```powershell
   # user enables online search; strongly recommend Tavily
   & $BriefLoop new industry-weekly "<workspace>" --search-backend tavily

   # user declines online search
   & $BriefLoop new industry-weekly "<workspace>" --web-search-mode disabled
   ```

7. If Tavily was enabled, import the key only after the workspace exists:

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

   `$SecretSource` must be a user-confirmed private file. If only the environment
   variable exists, stop and guide the user to create the file; never print,
   auto-copy, or expand the key on the command line.

## Role Delegation

Read `.agents/skills/briefloop-workbuddy/references/workbuddy-delegation.md`
for the shared CodeBuddy/WorkBuddy invocation contract.

Do not perform Scout, Screener, Claim Ledger, Analyst, Editor, Auditor, or
Formatter work in the main conversation. In either a CodeBuddy or WorkBuddy
host that has loaded the project assets, explicitly invoke the matching project
sub-agent by its exact name:

- `briefloop-scout`
- `briefloop-screener`
- `briefloop-claim-ledger`
- `briefloop-analyst`
- `briefloop-editor`
- `briefloop-auditor`
- `briefloop-formatter`

Project role-agent files live in:

```text
.codebuddy/agents/briefloop-scout.md
.codebuddy/agents/briefloop-screener.md
.codebuddy/agents/briefloop-claim-ledger.md
.codebuddy/agents/briefloop-analyst.md
.codebuddy/agents/briefloop-editor.md
.codebuddy/agents/briefloop-auditor.md
.codebuddy/agents/briefloop-formatter.md
```

Generate the delegated handoff only with the bound CLI and canonical checkout:

```powershell
& $BriefLoop run `
  --workspace "<workspace>" `
  --runtime codebuddy `
  --repo-workdir "<canonical BriefLoop source checkout>"
& $BriefLoop workbuddy diagnose --workspace "<workspace>" --json
```

Read both handoff files and verify handoff runtime, capability runtime,
`delegation_supported=true`, exact seven-role `subagent_names`, and the role
assets above. The capability flag is not proof of execution. Only a host-visible
exact role invocation and return is evidence; generic Team, Expert, helper,
Send Message, or narrative labels are not.

Role sub-agents may draft only handoff-assigned role artifacts. They must not
run CLI, complete stages, run gates, freeze the Claim Ledger, finalize,
complete finalize, or approve/report delivery. A role return is not a stage
pass. They return artifact paths or readiness summaries to the main session.

The drafting roles use `Read, Write, Grep, Glob`; the read-only Formatter uses
`Read, Grep, Glob`. All intentionally omit `Bash`, so deterministic CLI
transactions stay in the main session.

Formatter is a read-only finalize-readiness reporter. It must not run Bash,
PowerShell, or CLI; convert Markdown to DOCX; write reader delivery artifacts;
or claim reader-clean, gate/finalize success, or delivery.

Formal finalize completion requires the existing deterministic lifecycle and
all current-run evidence: handoff/diagnose authorized and the current
workspace-config-bound finalize transaction succeeded; structurally
valid `finalize_report.json`; reader-clean pass;
`delivery_promotion == promoted`; current `render_transaction_id`; finalize
gate pass; handoff/diagnose authorized and the current-workspace-bound
finalize-complete transaction with a recorded reason succeeded; current finalize event in
diagnose; valid delivery truth; and literal delivery outcome. Eligibility is
not delivery occurrence.

Any Markdown/DOCX written outside that lifecycle is `draft/manual/unverified`,
never a formal BriefLoop delivery. Do not claim residue cleanup or rendering
from prose/file existence. Report `CL-*`, `SRC-*`, `Claim Ledger`, local paths,
or other forbidden reader residue and follow deterministic repair/finalize.

If the main session can invoke BriefLoop CLI commands but the host cannot
dispatch these project sub-agents (for example the Agent tool fails to honor
the frontmatter-restricted tool set), stop before full codebuddy workflow
execution. Do not draft role-owned artifacts in the main conversation under a
codebuddy handoff, and do not suggest editing the role agents' frontmatter
tools. With CLI execution already available, the only legal continuation is an
explicit user decision to regenerate the handoff with `--runtime operator`,
whose contract allows operator-authored artifact work and never claims
sub-agents ran.

If an operator handoff already exists and the user requests subagents, stop
using it, regenerate a codebuddy handoff with the canonical `--repo-workdir`,
and reread both handoff files. Never continue operator while promising later
dispatch, and never claim a `briefloop-*` role ran under operator.

## Deterministic Transactions

The main CodeBuddy session owns deterministic CLI transactions. After a role
sub-agent returns, run the appropriate BriefLoop CLI validation, gate,
stage-complete, repair, finalize, delivery, or quality command only when the
current handoff and user intent allow it.

At startup, after every CLI transaction, after every role return, and after any
interruption, re-read:

```text
output/intermediate/agent_handoff.md
output/intermediate/agent_handoff.json
```

Then run `& $BriefLoop workbuddy diagnose --workspace "<workspace>" --json`,
and follow the handoff/diagnose current action. Invoke only the exact assigned
role when that action explicitly assigns role-owned draft work. For a
deterministic-only action, invoke no role and let the main session run the
authorized transaction. Then diagnose again. Raw
workflow state, event log, Registry, timestamps, and file existence are audit
evidence only; never use them to reconstruct next action, gate, finalize, or
delivery truth.

Any doctor error remains an error until the environment/config is corrected and
doctor passes again with the same `$BriefLoop`. `request_human_review`, user
confirmation, or a standalone pass in another shell/environment cannot
override it. Diagnose may be displayed as evidence, but
`doctor.status=not_run_read_only` cannot clear, replace, or route around the
observed failure, and its completion action must not be followed. After an
interruption or uncertain session continuity, rerun doctor with the same
`$BriefLoop`, workspace, and config before continuing.

## Hard Boundaries

- Do not directly edit `workflow_state.json`, `artifact_registry.json`,
  `runtime_manifest.json`, `event_log.jsonl`, gate reports, release reports, or
  frozen artifacts.
- Do not say role sub-agents ran unless CodeBuddy actually invoked the matching
  project sub-agent.
- Do not treat traceability as semantic proof or output-quality proof.
- Do not approve delivery, authorize release, publish reports, or bypass gates.
- Do not let a role sub-agent spawn another sub-agent.
- Stop and ask when workspace path, handoff state, gate status, or delivery
  intent is unclear.
