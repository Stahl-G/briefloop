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
4. Perform only the role-owned artifact work that the handoff assigns.
5. Use the owning CLI transaction when an artifact is ready.
6. After every CLI command, re-read the relevant handoff step before continuing.
7. Re-check status before repair, finalize, quality summary, or delivery.

## Common Inspection Commands

```bash
multi-agent-brief status --workspace <workspace>
multi-agent-brief status --workspace <workspace> --json
multi-agent-brief state check --workspace <workspace>
multi-agent-brief quality summarize --workspace <workspace>
```

## Role Delegation

The operator runtime does not assume delegation. If WorkBuddy provides a real
delegate or child-agent tool and the user chooses to use it, delegation must be
explicit. Otherwise, say that one WorkBuddy operator prepared the artifact work.

Do not claim Scout, Screener, Claim Ledger, Analyst, Editor, Auditor, or
Formatter subagents ran unless WorkBuddy actually delegated those roles.

If the user is chatting in Chinese, explain the next action in Chinese when
useful, but follow the English operator handoff literally. Translation must not
drop steps, soften gate/blocker language, or turn operator work into a claimed
subagent run.
