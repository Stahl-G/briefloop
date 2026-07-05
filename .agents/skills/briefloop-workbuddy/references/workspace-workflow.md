# Workspace Workflow

BriefLoop workspaces are advanced by deterministic CLI transactions and
role-owned draft artifacts. WorkBuddy may help the user operate the loop, but it
must not hand-edit control files.

## Normal Loop

1. Confirm the workspace path.
2. Run CodeBuddy handoff:

   ```bash
   briefloop run --workspace <workspace> --runtime codebuddy
   ```

3. Read `output/intermediate/agent_handoff.md` and
   `output/intermediate/agent_handoff.json`.
4. Before each stage or role-owned artifact action, re-read the relevant
   `agent_handoff.md` / `agent_handoff.json` step.
5. Invoke the matching role subagent for role-owned artifact work assigned by
   the handoff.
6. Use the owning CLI transaction when an artifact is ready.
7. After every CLI command, re-read the relevant handoff step before continuing.
8. Re-check status before repair, finalize, quality summary, or delivery.

## Common Inspection Commands

```bash
multi-agent-brief status --workspace <workspace>
multi-agent-brief status --workspace <workspace> --json
multi-agent-brief state check --workspace <workspace>
multi-agent-brief quality summarize --workspace <workspace>
```

## Progress Updates

After each deterministic CLI transaction, summarize progress to the user. Only
report completed states that are visible in `status`, `workflow_state.json`,
`event_log.jsonl`, or generated artifacts.

Use a Run Card after every key CLI command, role return, repair action, gate
check, finalize attempt, quality summary, or bundle/export request:

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

Read these fields from `briefloop status --workspace <workspace> --json`,
`briefloop state check --workspace <workspace> --json`, `workflow_state.json`,
`event_log.jsonl`, and file existence checks. Do not infer them from prose.

Allowed examples:

```text
已创建工作区。
已生成 CodeBuddy handoff。
当前状态：等待 source/scout artifact。
Quality Panel 已生成。
```

Do not say `Analyst 已经分析完成` or `Auditor 已通过` unless the matching
artifact, event, transaction, or status output exists.

Do not say `交付完成`, `delivered`, or `delivery complete` unless
`output/intermediate/finalize_report.json`, `output/delivery/`, and the
relevant finalize / delivery events exist.

## Hard Stops

- If `doctor` reports any error, stop. Show the full doctor output, workspace
  path, current user, output path existence/writability result, and permission
  or ACL output. Do not downgrade the error yourself.
- If `run_integrity` is not clean, stop finalize, delivery, export, and share
  actions. Do not run finalize or delivery. For early-stage role work, report
  the Run Card and continue only with non-delivery workflow steps allowed by
  the handoff.
- If finalize report or delivery directory is missing, report draft-only status
  and do not claim delivery or export a delivery package. This is normal before
  finalize and must not block earlier handoff-assigned stages by itself.
- If a zip, export, or attachment candidate contains `.env` or secrets, stop.
  Do not share it; recommend rotating any exposed key.

## Role Delegation

The WorkBuddy main session must delegate role-owned draft work explicitly. Use
the checked-in CodeBuddy-compatible role names exactly:

```text
briefloop-scout
briefloop-screener
briefloop-claim-ledger
briefloop-analyst
briefloop-editor
briefloop-auditor
briefloop-formatter
```

Role subagents draft only handoff-assigned artifacts. They do not run
BriefLoop CLI commands, edit control files, run gates, approve delivery, or
authorize release. The main WorkBuddy session runs deterministic CLI
transactions after a role returns.

Do not claim Scout, Screener, Claim Ledger, Analyst, Editor, Auditor, or
Formatter subagents ran unless WorkBuddy actually delegated those roles.

If role subagents are not available, stop before full workflow execution. You
may still run deterministic setup, `status`, `state check`, `quality summarize`,
or demo commands, but you must not hand-author workflow JSON artifacts.

If the user is chatting in Chinese, explain the next action in Chinese when
useful, but follow the generated handoff literally. Preserve command
names, artifact names, and handoff obligations exactly. Translation must not
drop steps, soften gate/blocker language, or turn main-session work into a claimed
subagent run.
