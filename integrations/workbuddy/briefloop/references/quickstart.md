# Quickstart

This quickstart is for WorkBuddy users operating BriefLoop locally. The Skill
bundle is source-clone-only in this release; Python wheel/sdist package installs
do not include the WorkBuddy files yet.

## 1. Confirm The Active CLI

Run:

```bash
BRIEFLOOP_CLI="$(command -v briefloop || command -v multi-agent-brief)"
test -n "$BRIEFLOOP_CLI"
"$BRIEFLOOP_CLI" version
```

Report the resolved command path and version before making changes. If neither
command exists, stop and ask the user to install BriefLoop or open the source
checkout.

## 2. Use The First-Run Search Default

BriefLoop's first-run default is local/no live web search. A user can create a
workspace, inspect status, and generate CodeBuddy handoff without any search API
key. Empty search-provider keys in `.env` do not mean setup failed.

If the user asks for live web search or says they want to configure an API key,
use Tavily as the default provider:

```text
TAVILY_API_KEY=<user-provided-key>
```

Check only whether `TAVILY_API_KEY` is present. Do not print the key value. Do
not ask the user to choose Exa, Brave, Firecrawl, or Serper unless they ask for
a non-Tavily provider.

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
5. Run `briefloop new ...` only after the user confirms the target path.

Use one product entry:

```bash
briefloop new industry-weekly <workspace>
briefloop new management-monthly <workspace>
briefloop new document-review <workspace>
briefloop new solar-periodic <workspace>
```

`industry-weekly`, `management-monthly`, and `document-review` are the baseline
supported product entries. `solar-periodic` is experimental.

## 4. Run CodeBuddy Handoff

Run:

```bash
briefloop run --workspace <workspace> --runtime codebuddy
```

Then inspect:

```bash
briefloop status --workspace <workspace>
briefloop state check --workspace <workspace>
```

After handoff, report only deterministic progress that is visible in files or
CLI output, for example:

```text
已创建工作区。
已生成 CodeBuddy handoff。
当前状态：等待 source/scout artifact。
```

Do not say `Analyst 已经分析完成` or `Auditor 已通过` unless the matching
artifact, event, transaction, or status output exists.

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

If `doctor` reports any error, stop and show the complete doctor output before
continuing. Read recovery and delivery fields from `briefloop workbuddy
diagnose --workspace <workspace> --json`; do not infer recovery from
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

## 5. Summarize Quality

When the workspace has enough artifacts to summarize:

```bash
briefloop quality summarize --workspace <workspace>
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
