# Quickstart

This quickstart is for WorkBuddy users operating BriefLoop locally. The Skill
bundle is source-clone-only in this release; Python wheel/sdist package installs
do not include the WorkBuddy files yet.

## 1. Confirm The Active CLI

For Windows WorkBuddy, select PowerShell once and reuse one absolute CLI path:

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

Report the resolved command path and version before making changes.
`py -3 --version` is diagnostic only; it does not prove which Python runs
`$BriefLoop`. Do not automatically mix or fall back to `bash`, `which`,
`command -v`, `export`, `/c/Users/...`, `source .venv/bin/activate`, or
`bash scripts/setup.sh`. If the actual host shell is Git Bash, report it and
stop this PowerShell route. Do not guess path translations or mix the shells;
this route does not claim Git Bash support.

## 2. Use The First-Run Search Default

BriefLoop's first-run default is local/no live web search. A user can create a
workspace, inspect status, and generate CodeBuddy handoff without any search API
key. Empty search-provider keys in `.env` do not mean setup failed.

If the user asks for live web search or says they want to configure an API key,
use Tavily as the default provider. Record that choice first. A new workspace
must be created successfully in the next section before importing workspace
secrets; verify that an existing workspace exists before importing there.
Never run `secrets import` before `& $BriefLoop new`.

## 3. Create A Workspace

If the user asks "跑周报" and has no workspace:

1. Explain in one sentence: a BriefLoop workspace is the local folder for this
   report project.
2. Suggest a safe local folder outside the BriefLoop source checkout, for
   example `~/Documents/BriefLoop/workspaces/<topic-slug>` on macOS/Linux or
   `C:\Users\<User>\Documents\BriefLoop\workspaces\<topic-slug>` on Windows.
3. Ask for explicit confirmation before creating it. Suggest only; do not create
   the folder or workspace silently.
4. Choose the product entry from the user's plain-language request:
   - weekly, industry, market, competitor, 周报, 行业, or 竞品 ->
     `industry-weekly`
   - management monthly, 管理月报, or 月报 -> `management-monthly`
   - file review, PDF review, document review, 文件, PDF, or 审阅 ->
     `document-review`
5. Run `& $BriefLoop new ...` only after the user confirms the target path.

Use one product entry and persist the user's recorded search choice. There is
no executable undecided `new` route after the user answers the search question:

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

`industry-weekly`, `management-monthly`, and `document-review` are the baseline
supported product entries. `solar-periodic` is experimental.

If the user enabled Tavily, import the key only after workspace creation:

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
mutate PATH, or inject a key into one command. Check
only whether `TAVILY_API_KEY` is present. Do not print or commit the key value.

## 4. Run CodeBuddy Handoff

Run:

```powershell
& $BriefLoop run `
  --workspace "<workspace>" `
  --runtime codebuddy `
  --repo-workdir "<canonical BriefLoop source checkout>"
```

Then immediately run diagnose and read both handoff files:

```powershell
& $BriefLoop workbuddy diagnose --workspace "<workspace>" --json
```

Before role work, verify that handoff runtime and runtime-capability runtime are
`codebuddy`, `delegation_supported=true`, `subagent_names` exactly matches the
seven `briefloop-*` roles, and the canonical checkout contains the exact
`.codebuddy/agents/briefloop-*.md` assets. Declared delegation support is not
role-run evidence. Only a host-visible exact role invocation and return count;
generic Team, Expert, helper, Send Message, or narrative labels do not.

After handoff, report only deterministic progress from handoff/diagnose, for example:

```text
已创建工作区。
已生成 CodeBuddy handoff。
当前状态：等待 source/scout artifact。
```

Say `briefloop-analyst` or `briefloop-auditor` role returned only after a
host-visible exact-role invocation and return in the current handoff step.
Analyst stage completion requires current deterministic stage/transaction
truth; audit/gate success requires the current deterministic verdict/status.
A matching artifact, stale event, manual file, or prior transaction proves
none of those facts by itself.

After every key command or role return, print this Run Card from machine facts:

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

If `doctor` reports any error, stop and show the complete doctor output. Fix
the environment/config and rerun with the same `$BriefLoop`; user confirmation
or a standalone pass elsewhere cannot turn it into pass. A following
diagnose's `doctor.status=not_run_read_only` cannot clear, replace, or route
around that failure, and its completion action must not be followed. After an
interruption or uncertain session continuity, rerun doctor with the same
`$BriefLoop`, workspace, and config. Read recovery and
delivery fields from `& $BriefLoop workbuddy diagnose --workspace
"<workspace>" --json`; do not infer recovery from
`run_integrity`. `delivery_truth.valid=true` permits a delivery action but does
not prove delivery. Report `delivery_bundle_prepared` as local ready and
`delivery_draft_created` as draft created; claim delivered only for
`delivery_event=delivery_succeeded`. Say the run has a role draft only when
`output/intermediate/audited_brief.md` exists;
otherwise say no draft or delivery exists yet. Continue earlier stages only
when the handoff allows them.

The WorkBuddy main session must invoke the matching role subagent for
handoff-assigned draft work:

```text
briefloop-scout
briefloop-screener
briefloop-claim-ledger
briefloop-analyst
briefloop-editor
briefloop-auditor
briefloop-formatter
```

If these role subagents are not available, stop before full workflow execution.
Do not fall back to hand-writing BriefLoop JSON artifacts or silently switching
to `--runtime operator`.

If an operator handoff exists but the user requests subagents, stop using it,
regenerate the codebuddy handoff with `--repo-workdir`, and reread both handoff
files. Never keep running operator while promising later dispatch, and never
claim a `briefloop-*` role ran under an operator handoff.

After every start, CLI command, role return, or interruption: reread handoff,
run diagnose, and follow its current action. Invoke only the exact assigned role
when that action explicitly assigns role-owned draft work. For a
deterministic-only action, invoke no role and let the main session run the
authorized transaction. Then diagnose again. Raw
workflow state, event log, Registry, timestamps, and file existence are audit
evidence only, not next-action/gate/finalize/delivery truth.

Formatter reports readiness only. Hand-written Markdown/DOCX is always
`draft/manual/unverified`, never formal finalize or delivery. Claim formal
finalize completion only when actual finalize, valid Finalize Report,
reader-clean/promoted/current-render truth, finalize gate, successful
finalize-complete, current finalize event, valid delivery truth, and literal
delivery outcome all exist. Report `CL-*`, `SRC-*`, `Claim Ledger`, local-path,
or other residue and follow deterministic repair/finalize.

## 5. Summarize Quality

When the workspace has enough artifacts to summarize:

```powershell
& $BriefLoop quality summarize --workspace "<workspace>"
```

Open `output/intermediate/quality_panel.html` for the static audit view.
Quality Panel is traceability and process accountability, not semantic proof,
delivery approval, or release authorization.

## 6. Share Outputs Safely

Do not zip or share the whole workspace. Whole workspaces can contain `.env`,
tokens, private planning notes, control files, and unfinished artifacts. Use
BriefLoop-generated delivery or audit bundles when present. If a package or
attachment candidate contains `.env`, stop, remove the package, and recommend
rotating any exposed key.
