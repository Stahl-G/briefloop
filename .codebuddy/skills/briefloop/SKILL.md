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

1. Resolve the active CLI:

   ```bash
   BRIEFLOOP_CLI="$(command -v briefloop)"
   test -n "$BRIEFLOOP_CLI"
   "$BRIEFLOOP_CLI" version
   ```

2. Report only the resolved command path and version.
3. Treat BriefLoop's first-run default as local/no live web search. Empty
   optional search-provider keys do not make setup incomplete. If the user asks
   for external web search, default to Tavily and verify only that
   `TAVILY_API_KEY` is present without printing the key value.
4. Classify the workspace path:
   - existing workspace: ask for the folder path;
   - first-time run: explain that a BriefLoop workspace is the local folder for
     this report project, suggest a safe path, and ask for confirmation before
     creating it.

## Role Delegation

Do not perform Scout, Claim Ledger, Analyst, Editor, Auditor, or Formatter work
in the main conversation. For role-owned artifact work, explicitly invoke the
matching project CodeBuddy sub-agent:

- `briefloop-scout`
- `briefloop-claim-ledger`
- `briefloop-analyst`
- `briefloop-editor`
- `briefloop-auditor`
- `briefloop-formatter`

Project role-agent files live in:

```text
.codebuddy/agents/briefloop-scout.md
.codebuddy/agents/briefloop-claim-ledger.md
.codebuddy/agents/briefloop-analyst.md
.codebuddy/agents/briefloop-editor.md
.codebuddy/agents/briefloop-auditor.md
.codebuddy/agents/briefloop-formatter.md
```

Role sub-agents may draft only handoff-assigned role artifacts. They must not
run `briefloop` or `multi-agent-brief` CLI commands. They return artifact paths
or readiness summaries to the main CodeBuddy session.

## Deterministic Transactions

The main CodeBuddy session owns deterministic CLI transactions. After a role
sub-agent returns, run the appropriate BriefLoop CLI validation, gate,
stage-complete, repair, finalize, delivery, or quality command only when the
current handoff and user intent allow it.

Before every role delegation and after every deterministic CLI transaction,
re-read:

```text
output/intermediate/agent_handoff.md
output/intermediate/agent_handoff.json
```

Report progress only when visible in CLI output, `status`, `workflow_state.json`,
`event_log.jsonl`, or generated artifacts.

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
