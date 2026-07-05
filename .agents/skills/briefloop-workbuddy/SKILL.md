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

## Purpose

Help WorkBuddy users operate BriefLoop through confirmed local workspaces,
`briefloop` CLI transactions, and generated handoff artifacts without turning
WorkBuddy prose into runtime authority.

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
- Deterministic progress summaries based on status, workflow state, event log,
  or generated artifacts.
- Agent-authored draft artifacts only where the handoff allows them.
- No direct edits to Python-owned control files or frozen artifacts.

## First Checks

Before operating a workspace:

1. Locate the active BriefLoop command:

   ```bash
   BRIEFLOOP_CLI="$(command -v briefloop)"
   test -n "$BRIEFLOOP_CLI"
   "$BRIEFLOOP_CLI" version
   ```

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
7. If creating a workspace, use a product entry:

   ```bash
   briefloop new industry-weekly <workspace>
   briefloop new management-monthly <workspace>
   briefloop new document-review <workspace>
   briefloop new solar-periodic <workspace>
   ```

`solar-periodic` is an experimental product entry. Say that before using it.

## Search Default

BriefLoop's first-run product default is local/no live web search. A workspace
can be created, inspected, and handed off with no search API key. Do not tell
the user BriefLoop is unfinished only because `.env` has empty search-provider
keys.

If the user wants BriefLoop-hosted external web search, configure Tavily first:

```bash
TAVILY_API_KEY=<user-provided-key>
```

Then verify only that `TAVILY_API_KEY` is present. Do not display the key. Do
not ask the user to choose among Tavily, Exa, Brave, Firecrawl, and Serper
unless they explicitly ask for alternatives.

## Operating Mode

Run existing workspaces through the operator runtime:

```bash
briefloop run --workspace <workspace> --runtime operator
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

## Work

Classify the request, confirm or create the workspace path, run deterministic
BriefLoop commands, read the current handoff before stage or artifact work, and
stop when gates, status, or user intent are unclear. Use reference files for
details instead of expanding authority in this entrypoint.

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
