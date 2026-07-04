---
name: briefloop-workbuddy
description: Operate BriefLoop from WorkBuddy through the host-agnostic operator runtime and deterministic CLI transactions. Use for requests like "跑周报", "生成行业简报", "运行简报", "briefloop", "industry weekly", or "market brief".
---

# BriefLoop WorkBuddy Skill

## Scope

Use this Skill when a WorkBuddy user wants to create, open, inspect, run, repair,
or summarize a BriefLoop workspace. Also use it for natural-language requests
such as "跑周报", "生成行业简报", "运行简报", or "帮我做市场简报" when the user
expects a BriefLoop-backed briefing workflow.

This Skill is a WorkBuddy-facing adapter around BriefLoop's CLI and artifacts.
It is not a new BriefLoop runtime authority layer. It does not prove semantic
truth, approve delivery, run gates by itself, or claim that role subagents ran.
This source bundle is available from a BriefLoop source checkout; Python
wheel/sdist package installs do not include it until a packaging command is
added.

## First Checks

Before operating a workspace:

1. Locate the active BriefLoop command:

   ```bash
   BRIEFLOOP_CLI="$(command -v briefloop || command -v multi-agent-brief)"
   test -n "$BRIEFLOOP_CLI"
   "$BRIEFLOOP_CLI" version
   ```

2. Report the resolved binary path and version to the user.
3. If no workspace path is provided, do not ask only "where is the workspace?"
   First classify:
   - existing workspace: ask for the folder path;
   - first-time run: offer to create one.
4. Explain that a BriefLoop workspace is the local folder for this report
   project. Before creating it, ask for explicit confirmation of the target
   path.
5. If creating a workspace, use a product entry:

   ```bash
   briefloop new industry-weekly <workspace>
   briefloop new management-monthly <workspace>
   briefloop new document-review <workspace>
   briefloop new solar-periodic <workspace>
   ```

`solar-periodic` is an experimental product entry. Say that before using it.

## Operating Mode

Run existing workspaces through the operator runtime:

```bash
multi-agent-brief run --workspace <workspace> --runtime operator
```

Operator runtime means a host-agnostic compact operator workflow. It does not
assume WorkBuddy delegated Scout, Analyst, Editor, Auditor, or Formatter roles.
It does not assume WorkBuddy delegated any role.
If WorkBuddy has not explicitly delegated and recorded a role, do not claim that
the role ran as a subagent.

Before each stage or role-owned artifact action, and after each BriefLoop CLI
command, re-open the relevant step in `output/intermediate/agent_handoff.md`
and `output/intermediate/agent_handoff.json` before continuing. Do not skip
handoff steps or claim that a subagent ran unless WorkBuddy actually delegated
and recorded that role.

After each deterministic CLI transaction, summarize progress to the user. Only
report completed states that are visible in `status`, `workflow_state.json`,
`event_log.jsonl`, or generated artifacts.

## Required References

Read the relevant reference before acting:

- `references/quickstart.md`
- `references/workspace-workflow.md`
- `references/artifact-boundary.md`
- `references/status-and-gates.md`
- `references/repair-protocol.md`
- `references/workbuddy-safety.md`

## Hard Boundaries

- Do not directly edit `workflow_state.json`, `artifact_registry.json`,
  `runtime_manifest.json`, `event_log.jsonl`, gate reports, release reports, or
  frozen artifacts.
- Do not use WorkBuddy prose as a substitute for BriefLoop transactions.
- Do not run delivery unless the user explicitly asks and current gates allow it.
- Do not approve releases, human approval ledgers, or memory entries.
- Do not present traceability as semantic proof or output-quality improvement.
- Do not say "Analyst is complete" or "Auditor passed" unless the matching
  artifact, event, status, or transaction is present.
- Stop and ask when the workspace path, active binary, gate status, or delivery
  intent is unclear.
