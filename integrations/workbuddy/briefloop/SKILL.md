---
name: briefloop-workbuddy
description: Operate BriefLoop from WorkBuddy through CodeBuddy-compatible role subagents and deterministic CLI transactions. Use for requests like "跑周报", "生成行业简报", "运行简报", "briefloop", "industry weekly", or "market brief".
---

# BriefLoop WorkBuddy Skill

> Legacy mirror only. This English mirror is retained for compatibility and is
> not the operating source of truth. The canonical WorkBuddy Skill is
> `.agents/skills/briefloop-workbuddy/` (Chinese), packaged by
> `& $BriefLoop workbuddy pack-skill`. For delivery-truth semantics
> (`finalize_report.json` `delivery_promotion`, the Store-native status
> projection `& $BriefLoop status --workspace "<workspace>" --json`
> `package_ready` / `delivered` fields), follow the
> canonical skill.

## Scope

Use this Skill when a WorkBuddy user wants to create, open, inspect, run, repair,
or summarize a BriefLoop workspace. Also use it for natural-language requests
such as "跑周报", "生成行业简报", "运行简报", or "帮我做市场简报" when the user
expects a BriefLoop-backed briefing workflow.

This Skill is a WorkBuddy-facing adapter around BriefLoop's CLI, CodeBuddy
project role agents, and workspace artifacts. It is not a new BriefLoop runtime
authority layer. It does not prove semantic truth, approve delivery, run gates
by itself, or claim that role subagents ran without actual WorkBuddy/CodeBuddy
delegation. This source bundle is available from a BriefLoop source checkout;
Python wheel/sdist package installs do not include it until a packaging command
is added.

## Purpose

Help WorkBuddy users operate BriefLoop through confirmed local workspaces,
CodeBuddy-compatible role subagents, `briefloop` CLI transactions, and generated
handoff artifacts without turning WorkBuddy prose into runtime authority.

## Use When

Use when a WorkBuddy conversation asks for a weekly brief, industry brief,
market brief, document review, existing BriefLoop workspace inspection, repair,
status, quality summary, or delivery preparation. If the user is only changing
BriefLoop source code, use the repository development skill instead.

## Inputs

- The user's report topic or existing workspace path.
- The active `briefloop` command path and version.
- Current workspace files, status output, and generated handoff artifacts when
  they exist.
- Explicit user confirmation before creating a new workspace or delivering.

## Outputs

- BriefLoop CLI commands that the user can inspect.
- Deterministic progress summaries based on the current handoff and
  `& $BriefLoop status --workspace "<workspace>" --json` and `& $BriefLoop runtime next --workspace "<workspace>"`.
- Role-subagent draft artifacts only where the handoff assigns them.
- No direct edits to Python-owned control files or frozen artifacts.

## First Checks

Before operating a workspace:

1. For Windows WorkBuddy, use PowerShell only and bind one absolute CLI path:

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

   Reuse `$BriefLoop` for bootstrap, doctor, run, secrets import, status, and runtime next.
   `py -3 --version` is diagnostic only. Do not mix or fall back to `bash`,
   `which`, `command -v`, `export`, `/c/Users/...`,
   `source .venv/bin/activate`, or `bash scripts/setup.sh`. If the actual shell
   is Git Bash, report it and stop the PowerShell route; do not guess path
   translations or mix shells. This route does not claim Git Bash support.

2. Report the resolved binary path and version to the user.
3. If `briefloop` is not available, ask the user to activate the source-clone
   virtual environment or finish setup before continuing.
4. Use the first-run search default correctly:
   - default BriefLoop first run does not require live web search;
   - missing search API keys do not make setup incomplete;
   - if the user asks for live web search or API setup, use Tavily as the
     default provider and check `TAVILY_API_KEY` first;
   - mention Exa, Brave, Firecrawl, or Serper only when the user asks for a
     different provider;
   - never print API key values; report only whether the expected env key is
     present.
5. If no workspace path is provided, do not ask only "where is the workspace?"
   First classify:
   - existing workspace: ask for the folder path;
   - first-time run: offer to create one.
6. Explain that a BriefLoop workspace is the local folder for this report
   project. Before creating it, ask for explicit confirmation of the target
   path.
7. If creating a workspace, persist the user's search choice in the product
   entry. Do not create an undecided workspace after the user has enabled or
   declined search:

   ```powershell
   # user enables online search; strongly recommend Tavily
   & $BriefLoop new industry-weekly "<workspace>" --search-backend tavily
   & $BriefLoop new management-monthly "<workspace>" --search-backend tavily
   & $BriefLoop new document-review "<workspace>" --search-backend tavily
   & $BriefLoop new solar-periodic "<workspace>" --search-backend tavily

   # user declines online search
   & $BriefLoop new industry-weekly "<workspace>" --web-search-mode disabled
   & $BriefLoop new management-monthly "<workspace>" --web-search-mode disabled
   & $BriefLoop new document-review "<workspace>" --web-search-mode disabled
   & $BriefLoop new solar-periodic "<workspace>" --web-search-mode disabled
   ```

`solar-periodic` is an experimental product entry. Say that before using it.

## Search Default

BriefLoop's first-run product default is local/no live web search. A workspace
can be created, inspected, and handed off with no search API key. Do not tell
the user BriefLoop is unfinished only because `.env` has empty search-provider
keys.

If the user wants external web search, persist Tavily through workspace secrets:

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
auto-copy, or expand the key on the command line. Do not temporarily export,
mutate PATH, or inject a key into one command. Then
verify only that `TAVILY_API_KEY` is present. Do not display or commit the key. Do
not ask the user to choose among Tavily, Exa, Brave, Firecrawl, and Serper
unless they explicitly ask for alternatives.

## Operating Mode

Run full BriefLoop workspaces through the CodeBuddy runtime:

```powershell
& $BriefLoop run `
  --workspace "<workspace>" `
  --runtime codebuddy `
  --repo-workdir "<canonical BriefLoop source checkout>"
& $BriefLoop status --workspace "<workspace>" --json
```

Use `--runtime codebuddy` only when the source checkout contains
`.codebuddy/skills/briefloop/SKILL.md` and
`.codebuddy/agents/briefloop-*.md`. The local WorkBuddy Skill zip alone does
not install those CodeBuddy project assets.

Full execution requires the CodeBuddy/WorkBuddy main session itself to have a
command-execution capability that can invoke `briefloop`; the host may present
it as a terminal, shell, or equivalent tool rather than naming it `Bash`. Read
`references/workbuddy-delegation.md` before role work. A CodeBuddy or WorkBuddy
host that has loaded the project assets uses the same checked-in
`.codebuddy/agents/briefloop-*.md` role definitions and invokes them by exact
name. After a role returns, the main session runs validation, gate, state,
finalize, delivery, and quality commands.

The drafting roles declare `Read, Write, Grep, Glob`; the read-only Formatter
declares `Read, Grep, Glob`. All intentionally omit `Bash`, so deterministic
CLI transactions stay in the command-capable main session. The role and main
session are separate permission domains.

Use these role names exactly when the handoff assigns the corresponding stage:

- `briefloop-scout`
- `briefloop-screener`
- `briefloop-claim-ledger`
- `briefloop-analyst`
- `briefloop-editor`
- `briefloop-auditor`
- `briefloop-formatter`

The checked-in role definitions live under:

```text
.codebuddy/agents/briefloop-*.md
```

Invoke a role explicitly, for example: `Use the briefloop-scout subagent for
the Scout work assigned by the current BriefLoop handoff, then return the
written artifact paths to the main session.` Verify the host actually invoked
and returned from that exact role; a generic helper or narrative claim is not
delegation evidence.

Before role work, read both handoff files and verify the handoff runtime,
capability runtime, `delegation_supported=true`, exact seven-role
`subagent_names`, and exact project assets. The capability flag is not
execution proof. Generic Team, Expert, helper, Send Message, and narrative
labels are not role-run evidence.

If the main session cannot invoke `briefloop`, stop before role work or state
advancement and use a CodeBuddy/WorkBuddy environment with command execution.
If the host can run CLI commands but cannot invoke those project roles, stop
before full workflow execution. You may still run deterministic setup, `status`, `state
check`, `quality summarize`, `doctor`, or demo commands, but do not fall back
to hand-authoring role artifacts under the codebuddy handoff and do not
silently switch runtime. The user must explicitly choose either a
CodeBuddy/WorkBuddy session with project-role dispatch or a regenerated
`--runtime operator` handoff.

If an operator handoff exists but the user requests subagents, stop using it,
regenerate the codebuddy handoff with the canonical `--repo-workdir`, and reread
both handoff files. Do not continue operator while promising later dispatch;
operator must never claim that a `briefloop-*` role ran.

Before each stage or role-owned artifact action, and after each BriefLoop CLI
command, re-open the relevant step in `output/intermediate/agent_handoff.md`
and `output/intermediate/agent_handoff.json` before continuing. Do not skip
handoff steps or claim that a subagent ran unless WorkBuddy/CodeBuddy actually
delegated and recorded that role.

After every start, CLI command, role return, or interruption: reread both
handoff files, read the Store-native status projection
(`& $BriefLoop status --workspace "<workspace>" --json`) and
`& $BriefLoop runtime next --workspace "<workspace>"`, and follow the current
action. Invoke only the exact
assigned role when that action explicitly assigns role-owned draft work. For a
deterministic-only action, invoke no role and let the main session run the
authorized transaction. Then read the status projection again. Raw workflow
state, event log,
Registry, timestamps, and file existence
are audit evidence only; they do not reconstruct next action, gate, finalize,
or delivery truth.

## Run Card Protocol

After every key CLI command, role return, repair action, gate check, finalize
attempt, quality summary, or bundle/export request, print a machine-fact Run
Card. Do not replace the Run Card with a free-form "completed" summary.

Use exactly these fields and fill unknown values with `unknown` rather than
guessing:

```text
runtime:
current_stage:
terminal_state:
package_ready:
delivered:
store_revision:
next_action:
```

Read these values only from `& $BriefLoop status --workspace
"<workspace>" --json` (the Store-native status projection; its
`terminal_state`, `package_ready`, and `delivered` fields are receipt-bound
via `projection_source`) and `& $BriefLoop runtime next --workspace
"<workspace>"` (workflow progression truth). The legacy completion projection /
`workbuddy diagnose` surface is retired; do not call it.
`package_ready=true` means the current run's reader
package is ready for a delivery decision; it does not mean delivery occurred.
Only `delivered=true` for the current run permits a completed-delivery claim;
`terminal_state=draft_created` means a draft exists and is not delivered.

## Hard Stop Rules

Stop immediately and show the machine evidence only for conditions that make
the requested action unsafe. Do not turn normal pre-finalize state into a
workflow stop.

1. `& $BriefLoop doctor` reports any error. Show the full doctor output, actual
   workspace path, current user, output path existence/writability check, and
   platform permission/ACL output. Do not downgrade the error in prose and do
   keep it failed until the environment/config is corrected and doctor reruns
   successfully with the same `$BriefLoop`. `request_human_review`, user
   confirmation, or a standalone pass in another environment cannot override
   it. After an
   interruption or uncertain session continuity, rerun doctor with the same
   `$BriefLoop`, workspace, and config before continuing.
2. Do not infer recovery progress from `run_integrity`. Follow only the
   current action reported by `runtime next` and the handoff; a recovered run remains
   contaminated and non-reference. Do not deliver while recovery is
   nonterminal or invalid. For `completed_non_reference`, do not rerun
   finalize; local delivery still requires `package_ready=true` in the status
   projection.
3. For delivery actions, require `package_ready=true` in the status
   projection. For completion
   claims, also require `delivered=true` for the current run. Do not say
   "delivered", "交付完成", or "delivery complete" for
   `package_ready=true` or `terminal_state=draft_created`.
   Say only that a draft exists when `output/intermediate/audited_brief.md` exists; otherwise say no draft or delivery exists yet.
   Continue earlier role-work stages only when the handoff and Run Card allow
   them.
4. Any export, share, package, zip, or attachment candidate contains
   `.env`, tokens, private planning files, or machine secrets. Stop, tell the
   user to remove the package, and recommend rotating any exposed key. Never
   share a whole workspace zip.

## Formatter And Finalize

`briefloop-formatter` is a read-only finalize-readiness reporter. It must not
run Bash, PowerShell, or CLI; convert Markdown to DOCX; write reader delivery
artifacts; or claim reader-clean, gate/finalize success, or delivery. A role
return is not a stage pass. Only the main session may run the handoff-authorized
finalize, finalize gate, and finalize-complete transactions.

Do not say the formal finalize pipeline completed unless the current run has
all of these deterministic observations: handoff/status-projection authorized
and the
current workspace-config-bound finalize transaction succeeded; structurally
valid `finalize_report.json`; reader-clean passed;
`delivery_promotion == promoted`; current `render_transaction_id`; passed
finalize quality gate; handoff/status-projection authorized and the
current-workspace-bound
finalize-complete transaction with a recorded reason succeeded;
the Store-native status projection reports `package_ready=true`; and
`delivered` / `terminal_state` is reported literally. `package_ready=true` is
only
eligibility, not evidence that delivery occurred. Only the status projection
reporting `delivered=true` for the current run can support a delivery claim.

Markdown or DOCX written outside the deterministic finalize lifecycle by
WorkBuddy or a generic helper is `draft/manual/unverified`. Do not move, rename,
or describe it as a formal BriefLoop delivery. Do not claim internal IDs or
reader residue were cleaned, template rendering occurred, or a later verbal
response repaired it. If a reader artifact contains `CL-*`, `SRC-*`,
`Claim Ledger`, local paths, or other forbidden residue, report it, stop the
delivery claim, and follow deterministic repair/finalize. Never hand-edit a
frozen artifact or bypass reader gates.

## Work

Classify the request, confirm or create the workspace path, run deterministic
BriefLoop commands, invoke role subagents only for handoff-assigned draft work,
read the current handoff before stage or artifact work, and stop when gates,
status, role-agent availability, or user intent are unclear. Use reference
files for details instead of expanding authority in this entrypoint.

## Handoff

Treat `output/intermediate/agent_handoff.md` and
`output/intermediate/agent_handoff.json` as the workspace-specific execution
contract. Re-read the relevant step before each stage or role-owned artifact
action and after each deterministic CLI transaction.

## Required References

Read the relevant reference before acting:

- `references/quickstart.md`
- `references/workspace-workflow.md`
- `references/artifact-boundary.md`
- `references/status-and-gates.md`
- `references/repair-protocol.md`
- `references/workbuddy-safety.md`
- `references/workbuddy-delegation.md`

## Hard Boundaries

- Do not directly edit `workflow_state.json`, `artifact_registry.json`,
  `runtime_manifest.json`, `event_log.jsonl`, gate reports, release reports, or
  frozen artifacts.
- Do not use WorkBuddy prose as a substitute for BriefLoop transactions.
- Do not run delivery unless the user explicitly asks and current gates allow it.
- Do not approve releases, human approval ledgers, or memory entries.
- Do not present traceability as semantic proof or output-quality improvement.
- Say `briefloop-analyst` or `briefloop-auditor` role returned only after a
  host-visible exact-role invocation and return in the current handoff step.
  Analyst stage completion requires current deterministic stage/transaction
  truth; audit/gate success requires the current deterministic verdict/status.
  A matching artifact, stale event, manual file, or prior transaction proves
  none of role execution, stage completion, or audit success by itself.
- Do not say "delivered" unless the Store-native status projection
  (`& $BriefLoop status --workspace "<workspace>" --json`) reports
  `delivered=true` for the current run.
- Do not zip or share the whole workspace. Use BriefLoop-generated delivery
  or audit bundles when present; never include `.env`. If support is needed,
  share only manually reviewed, non-secret excerpts from `& $BriefLoop status
  --json` or doctor output.
- Stop and ask when the workspace path, active binary, gate status, or delivery
  intent is unclear.
