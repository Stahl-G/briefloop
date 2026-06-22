---
description: BriefLoop writer command for Claude Code
argument-hint: "new | run <workspace> | status <workspace> | feedback <workspace> [text-or-file] | deliver <workspace>"
---

You are the Claude Code first-class BriefLoop writer command.

`/briefloop` and `/mabw` are compatibility aliases for the same five-verb
writer surface. Use the same operational contract as `.claude/commands/mabw.md`.
When showing writer examples from this command, prefer `/briefloop`; when
explaining compatibility, mention that `/mabw` remains supported.

This command is not a second workflow engine. Python remains the deterministic
setup, validation, control, and rendering layer; Claude Code remains the
Orchestrator runtime.

## First-Screen Writer Help

If `$ARGUMENTS` is empty or the first token is unknown, show only these writer
verbs first:

```text
/briefloop new
  Start a new brief. Answer who it is for, what this issue covers, and what to watch.
  BriefLoop creates the workspace and prepares the run rules and handoff.

/briefloop run <workspace>
  Create or refresh this run's handoff. It prepares evidence/accountability surfaces,
  but it does not execute specialist agents or mark stages complete.

/briefloop status <workspace>
  See where the run stands. Strictly read-only: it never changes files,
  refreshes state, or appends events.

/briefloop feedback <workspace> [text-or-file]
  Tell BriefLoop what feels wrong. Feedback is recorded first; triage, repair,
  Improvement Ledger proposals, and approvals require explicit confirmation.

/briefloop deliver <workspace>
  Final delivery. It must pass gates, the reader-final gate, and
  state finalize-complete before reader artifacts are treated as delivered.
```

Do not put `doctor`, `runtime install`, `eval-cases`, release checks, generated
asset checks, or low-level state commands in first-screen writer help.
`doctor` remains a diagnostic/maintainer command, not a sixth writer verb.

## Routing

Parse the first token in `$ARGUMENTS` as the verb. Parse the rest as the
workspace path and optional text/file argument.

For the detailed verb contract, follow `.claude/commands/mabw.md` exactly:

- `new`
- `run <workspace>`
- `status <workspace>`
- `feedback <workspace> [text-or-file]`
- `deliver <workspace>`

When executing deterministic CLI commands, use `multi-agent-brief` or its shell
alias `briefloop`; both invoke the same Python entrypoint. Keep all gate,
repair, status, finalize, delivery, and human-approval boundaries unchanged.
