# Workspace Workflow

BriefLoop workspaces are advanced by deterministic CLI transactions and
role-owned draft artifacts. WorkBuddy may help the user operate the loop, but it
must not hand-edit control files.

## Normal Loop

1. Confirm the workspace path.
2. Run operator handoff:

   ```bash
   multi-agent-brief run --workspace <workspace> --runtime operator
   ```

3. Read `output/intermediate/agent_handoff.md` and
   `output/intermediate/agent_handoff.json`.
4. Before each stage or role-owned artifact action, re-read the relevant
   `agent_handoff.md` / `agent_handoff.json` step.
5. Perform only the role-owned artifact work that the handoff assigns.
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

Allowed examples:

```text
已创建工作区。
已生成 operator handoff。
当前状态：等待 source/scout artifact。
Quality Panel 已生成。
```

Do not say `Analyst 已经分析完成` or `Auditor 已通过` unless the matching
artifact, event, transaction, or status output exists.

## Role Delegation

The operator runtime does not assume delegation. If WorkBuddy provides a real
delegate or child-agent tool and the user chooses to use it, delegation must be
explicit. Otherwise, say that one WorkBuddy operator prepared the artifact work.

Do not claim Scout, Screener, Claim Ledger, Analyst, Editor, Auditor, or
Formatter subagents ran unless WorkBuddy actually delegated those roles.

If the user is chatting in Chinese, explain the next action in Chinese when
useful, but follow the English operator handoff literally. Preserve command
names, artifact names, and handoff obligations exactly. Translation must not
drop steps, soften gate/blocker language, or turn operator work into a claimed
subagent run.
