---
name: briefloop-workbuddy
description: Operate BriefLoop from WorkBuddy through CodeBuddy-compatible role subagents and deterministic CLI transactions. Use for requests like "跑周报", "生成行业简报", "运行简报", "briefloop", "industry weekly", or "market brief".
---

# BriefLoop WorkBuddy Skill

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
- Deterministic progress summaries based on status, workflow state, event log,
  or generated artifacts.
- Role-subagent draft artifacts only where the handoff assigns them.
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

Run full BriefLoop workspaces through the CodeBuddy runtime:

```bash
briefloop run --workspace <workspace> --runtime codebuddy
```

The WorkBuddy main session owns deterministic CLI transactions. It must invoke
the matching CodeBuddy-compatible role subagent for role-owned draft artifact
work, then return to the main session for validation, gate, state, finalize,
delivery, and quality commands.

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

If the current WorkBuddy environment cannot invoke those role subagents, stop
before full workflow execution. You may still run deterministic setup,
`status`, `state check`, `quality summarize`, or `demo` commands, but do not
fall back to hand-authoring BriefLoop JSON artifacts and do not silently switch
to `--runtime operator`.

Before each stage or role-owned artifact action, and after each BriefLoop CLI
command, re-open the relevant step in `output/intermediate/agent_handoff.md`
and `output/intermediate/agent_handoff.json` before continuing. Do not skip
handoff steps or claim that a subagent ran unless WorkBuddy/CodeBuddy actually
delegated and recorded that role.

After each deterministic CLI transaction, summarize progress to the user. Only
report completed states that are visible in `status`, `workflow_state.json`,
`event_log.jsonl`, or generated artifacts.

## Run Card Protocol

After every key CLI command, role return, repair action, gate check, finalize
attempt, quality summary, or bundle/export request, print a machine-fact Run
Card. Do not replace the Run Card with a free-form "completed" summary.

Use exactly these fields and fill unknown values with `unknown` rather than
guessing:

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

Read the values from `briefloop status --workspace <workspace> --json`,
`briefloop state check --workspace <workspace> --json`,
`workflow_state.json`, `event_log.jsonl`, and file existence checks. If
`output/intermediate/finalize_report.json` and `output/delivery/` are missing,
the Run Card must say the run has a draft only, not completed delivery.

## Hard Stop Rules

Stop immediately and show the machine evidence only for conditions that make
the requested action unsafe. Do not turn normal pre-finalize state into a
workflow stop.

1. `briefloop doctor` reports any error. Show the full doctor output, actual
   workspace path, current user, output path existence/writability check, and
   platform permission/ACL output. Do not downgrade the error in prose and do
   not mark doctor complete unless the user explicitly confirms the evidence.
2. For finalize, delivery, export, or share requests: if `run_integrity` is not
   clean, or it is `contaminated`, `stale_or_invalid`, or unknown, stop that
   action. Do not run finalize or delivery. The next safe action is fresh run,
   controlled repair, or human review. For early-stage draft work, report the
   Run Card and continue only with non-delivery workflow steps allowed by the
   handoff.
3. For delivery, export, share, or completion claims: if
   `output/intermediate/finalize_report.json` or `output/delivery/` is missing,
   stop that action. Do not say "delivered", "交付完成", or "delivery complete".
   Say only that a draft exists, if a draft artifact exists. Continue earlier
   role-work stages only when the handoff and Run Card allow them.
4. Any export, share, package, zip, or attachment candidate contains
   `.env`, tokens, private planning files, or machine secrets. Stop, tell the
   user to remove the package, and recommend rotating any exposed key. Never
   share a whole workspace zip.

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
- Do not say "delivered" unless `output/intermediate/finalize_report.json`,
  `output/delivery/`, and the relevant finalize / delivery events exist.
- Do not zip or share the whole workspace. Use BriefLoop-generated delivery
  or audit bundles when present; never include `.env`. If support is needed,
  run `briefloop workbuddy diagnose --json` and share the redacted output
  manually.
- Stop and ask when the workspace path, active binary, gate status, or delivery
  intent is unclear.
