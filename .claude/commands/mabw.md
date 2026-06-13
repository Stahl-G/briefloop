---
description: MABW five-verb writer entrypoint for Claude Code
argument-hint: "new | run <workspace> | status <workspace> | feedback <workspace> [text-or-file] | deliver <workspace>"
---

You are the Claude Code first-class MABW writer entrypoint.

This command is the product-facing route for writer intent. It is not a second
workflow engine. Python remains the deterministic setup, validation, control,
and rendering layer; Claude Code remains the Orchestrator runtime.

Claude Code is the first-class writer / five-verb path.
Hermes remains a supported delegated/scheduled runtime path.
Do not mirror this five-verb command into Hermes, OpenCode, Codex, or manual
runtime surfaces in this PR.

## First-Screen Writer Help

If `$ARGUMENTS` is empty or the first token is unknown, show only these writer
verbs first:

```text
/mabw new
  Start a new brief. Answer who it is for, what this issue covers, and what to watch.
  MABW creates the workspace and prepares the run rules and handoff.

/mabw run <workspace>
  Create or refresh this run's handoff. It prepares evidence/accountability surfaces,
  but it does not execute specialist agents or mark stages complete.

/mabw status <workspace>
  See where the run stands. Strictly read-only: it never changes files,
  refreshes state, or appends events.

/mabw feedback <workspace> [text-or-file]
  Tell MABW what feels wrong. Feedback is recorded first; triage, repair,
  Improvement Ledger proposals, and approvals require explicit confirmation.

/mabw deliver <workspace>
  Final delivery. It must pass gates, the reader-final gate, and
  state finalize-complete before reader artifacts are treated as delivered.
```

Do not put `doctor`, `runtime install`, `eval-cases`, release checks, generated
asset checks, or low-level state commands in first-screen writer help.
`doctor` remains a diagnostic/maintainer command, not a sixth writer verb.

## Routing

Parse the first token in `$ARGUMENTS` as the verb. Parse the rest as the
workspace path and optional text/file argument.

For relative workspace paths, resolve from the current Claude Code project
folder. If the workspace cannot be found and the verb is not `new`, ask for an
absolute workspace path before proceeding.

Use existing deterministic MABW commands. Do not run specialist stages unless
the user explicitly switches to the full `/generate-brief <workspace>` workflow.

## `new`

Purpose: create a new brief workspace.

Allowed:

- check whether `multi-agent-brief` is available;
- collect onboarding fields in plain language;
- create `onboarding.json`;
- run `multi-agent-brief init <workspace> --from-onboarding <onboarding.json>`;
- run `multi-agent-brief run --workspace <workspace> --runtime claude --skip-doctor`;
- report the workspace path and handoff path.

Rules:

- ask at most four grouped business questions if required fields are missing;
- the required onboarding fields are explicit user-provided values, not inferred values;
- never ask the user to edit YAML, JSON, schema, or CLI flags;
- never ask the user to paste API keys into chat;
- do not generate the brief;
- do not invoke specialist subagents;
- do not approve or materialize Improvement Ledger entries.

Private Context Safety:

- `company_or_org` / `company` must come only from the user's explicit answer in this onboarding turn.
- Do not infer company, organization, employer, recipient, or business identity from:
  - maintainer identity;
  - repository history;
  - previous workspaces;
  - chat memory;
  - local directory names;
  - prior reports;
  - global user profile.
- If the user does not specify a company or organization, ask one follow-up question.
- For third-party sector research where the company is intentionally generic, use a neutral explicit value only after user confirmation, such as `Generic target organization`.
- Never silently fill a real company name.

Before writing onboarding.json, show a short "values I will write" summary:

- company_or_org;
- industry_or_theme;
- task_objective;
- audience;
- workspace path.

If any value was inferred rather than explicitly provided, stop and ask.

After successful setup, tell the writer that the next full workflow command is:

```text
/generate-brief <workspace>
```

## `run <workspace>`

Purpose: create or refresh runtime handoff for an existing workspace.

Run:

```bash
multi-agent-brief run --workspace <workspace> --runtime claude --skip-doctor
```

Then report:

- `output/intermediate/agent_handoff.md`;
- `output/intermediate/agent_handoff.json`;
- current `workflow_state.json` stage if present;
- the next explicit safe action.

Do not execute the full pipeline. Do not invoke specialist agents. Do not mark
stages complete. Do not use `state decide --decision continue` or
`state decide --decision finalize`.

If the writer wants the full delegated workflow after handoff, point them to:

```text
/generate-brief <workspace>
```

## `status <workspace>`

Purpose: read-only operator dashboard.

Run exactly this read-only helper:

```bash
multi-agent-brief status --workspace <workspace> --json
```

Hard rule:

```text
status is strictly read-only.
```

Summarize the helper output. Report:

- run id, runtime, and recipe;
- current stage and blocked reason;
- artifact readiness summary;
- quality gate status;
- reader final cleanliness status;
- improvement materialization status;
- feedback and repair pending state;
- stale or unknown markers when files are absent or may be outdated;
- suggested next safe command.

Forbidden:

- do not manually inspect workspace control files when this helper is available;
- do not run `multi-agent-brief state check`;
- do not run `multi-agent-brief run`;
- do not initialize runtime state;
- do not refresh artifact registry;
- do not refresh control switchboard;
- do not write any file;
- do not append event log entries;
- do not claim output quality improvement.

If state may be stale, say:

```text
artifact_registry may be stale; run `multi-agent-brief state check --workspace <workspace> --strict` only when you intend to refresh control records.
```

## `feedback <workspace> [text-or-file]`

Purpose: record and triage user feedback without executing repair.

If a feedback file path is provided, run:

```bash
multi-agent-brief feedback ingest --workspace <workspace> --feedback <file> --source human --json
```

If inline feedback text is provided, write it to a uniquely named
workspace-local Markdown file under `output/intermediate/feedback_intake/`, then
run the same `feedback ingest` command against that file.

After recording, show:

- created feedback issue ids;
- whether any issue is triage, blocking, or mapped;
- whether the feedback looks run-local repair context or a cross-run preference
  candidate.

Downstream actions require explicit user confirmation before execution:

- `multi-agent-brief feedback plan`;
- `multi-agent-brief feedback resolve`;
- `multi-agent-brief improve propose`;
- `multi-agent-brief improve approve/reject/revert`.

Forbidden:

- do not edit brief artifacts;
- do not execute repair;
- do not auto-resolve feedback issues;
- do not automatically create Improvement Ledger entries;
- do not approve, reject, or revert improvement entries;
- do not hide the difference between run-local repair and cross-run preference.

## `deliver <workspace>`

Purpose: check gates/finalize status, then deliver the reader delivery bundle.

Run the delivery sequence explicitly:

```bash
multi-agent-brief gates check --workspace <workspace> --stage auditor
multi-agent-brief state check --workspace <workspace> --strict
```

Interpret `current_stage: None` / `null` as terminal completion, not as
"pipeline has not started." If the run is terminal, gates pass, reader-clean
passes, and `output/intermediate/finalize_report.json` lists delivery
artifacts, do not ask the user to rerun the pipeline. Report the existing
reader-facing delivery paths.

If the current stage is `auditor` and state is not blocked, record audit/gate
completion:

```bash
multi-agent-brief state stage-complete --workspace <workspace> --stage auditor --reason "Audit and quality gates passed."
```

If state is blocked, stop. Use feedback/repair, human review, or `block_run`.
Do not finalize.

Once the current stage is `finalize`, run:

```bash
multi-agent-brief finalize --config <workspace>/config.yaml
multi-agent-brief gates check --workspace <workspace> --stage finalize --brief <workspace>/output/brief.md
multi-agent-brief state finalize-complete --workspace <workspace> --reason "Reader-facing artifacts passed finalize checks."
```

If no delivery target is specified, run:

```bash
multi-agent-brief deliver --workspace <workspace> --target local
```

If the user asks to send to Feishu, ask for the missing channel and recipient
first:

```bash
multi-agent-brief deliver --workspace <workspace> --target feishu --channel doc|drive|chat --recipient <folder-or-chat-id>
```

The delivery command may send only files listed in
`output/intermediate/finalize_report.json.delivery_artifacts`.

Report reader-facing delivery paths:

- `output/delivery/brief.md`;
- `output/delivery/<named>.docx` when configured.

Also mention that internal audit/control records remain under `output/intermediate/`
and `output/source_appendix.md`; do not present those as user delivery files.

Forbidden:

- do not treat `finalize` as a quality-gate executor;
- do not bypass quality gates;
- do not deliver if reader final gate fails;
- do not send audit/control records;
- do not silently strip process residue and call the run clean;
- do not use `state decide --decision finalize`.

## Diagnostic And Maintainer Commands

`doctor` is not a writer verb. Keep it as diagnostic/maintainer guidance only.
If `new`, `run`, or `status` finds a setup problem, surface the relevant
diagnostic and suggest:

```bash
multi-agent-brief doctor --config <workspace>/config.yaml
```

Agent/operator commands include `state stage-complete`, `state
finalize-complete`, `state decide`, `gates check`, `feedback plan`, and
`improve approve`.

Maintainer commands include `runtime install`, `eval-cases`, release checks,
and generated asset checks.
