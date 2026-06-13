# Claude Code Quickstart

This quickstart shows the first-class five-verb writer path for MABW in Claude
Code. The verbs are:

```text
/mabw new
/mabw run <workspace>
/mabw status <workspace>
/mabw feedback <workspace> [text-or-file]
/mabw deliver <workspace>
```

`/mabw` is the writer-facing entrypoint. `/generate-brief` remains the
compatibility command for the full delegated subagent workflow.

For Chinese writer-facing operation notes, see:

- `docs/golden-path.zh-CN.md`
- `docs/weekly-use.zh-CN.md`

## Five Writer Verbs

| Verb | Product meaning |
|---|---|
| `/mabw new` | Start a new brief workspace by answering who it is for, what this issue covers, and what to watch. |
| `/mabw run <workspace>` | Create or refresh this run's handoff without executing specialist agents or marking stages complete. |
| `/mabw status <workspace>` | See where the run stands. Strictly read-only. |
| `/mabw feedback <workspace> [text-or-file]` | Record what feels wrong; triage, repair, improvement proposals, and approvals require explicit confirmation. |
| `/mabw deliver <workspace>` | Deliver only after gates, the reader-final gate, and `state finalize-complete` pass. |

`doctor` is still available for diagnostics, but it is not a sixth writer verb.

## 1. Create a workspace

```bash
multi-agent-brief init ../mabw-workspace --demo
```

PowerShell:

```powershell
multi-agent-brief init ..\mabw-workspace --demo
```

## 2. Use The Writer Entrypoint In Claude Code

Run this slash command inside the Claude Code CLI or the Claude Desktop Code
tab with this repository selected as the project folder, so
`.claude/commands/mabw.md` is loaded:

```text
/mabw run ../mabw-workspace
/mabw status ../mabw-workspace
```

If Claude Code returns `Unknown command: /mabw`, the current session
has not discovered this project command. Confirm the project folder is the MABW
repository root, type `/` to inspect available commands, or install the command
for user-level discovery:

```bash
multi-agent-brief claude install --repo-workdir .
```

You can also use the standard CLI handoff instead:

```bash
multi-agent-brief run --workspace ../mabw-workspace
```

To execute the full delegated workflow from Claude Code after handoff, use:

```text
/generate-brief ../mabw-workspace
```

The delegated workflow follows this sequence:

```text
source discovery -> doctor -> scout -> screener -> claim-ledger -> analyst -> editor -> auditor -> finalize
```

## What `/mabw status` Means

`/mabw status <workspace>` is a read-only dashboard. It should help a writer
understand four things without exposing a schema inventory:

| Question | What status should tell you |
|---|---|
| What stage this run is in | Current stage, missing artifacts, blockers, and the next safe action. |
| Source-trail surface readiness | Whether Claim Ledger, audit, gate, and source appendix artifacts are present or stale. To trace a specific number, open those source-trail files. |
| What reader preferences were approved | Whether Improvement Memory was materialized for this run, and which snapshot is frozen. |
| What checks are guarding delivery | Gate status, reader-final cleanliness, feedback/repair blockers, and finalize readiness. |

Hard rule: `status` is read-only. It does not run `state check`, refresh the
artifact registry, initialize runtime state, refresh the switchboard, append
events, or write a status file. If records may be stale, it reports that and
names the explicit command the operator can run.

## How `/mabw feedback` Is Routed

`/mabw feedback <workspace> [text-or-file]` records feedback first. Recording
feedback is allowed immediately; acting on it is not automatic.

Downstream actions still require explicit confirmation:

- run-local repair: create or update feedback issues / repair plan, then repair explicitly;
- cross-run preference: create an Improvement Ledger proposal, then approve explicitly;
- resolved issue: mark it resolved only after the operator confirms the repair or review result.

Fact and source problems are not long-term preferences. A stale number, missing
source, unsupported claim, or broken citation should stay in the feedback/repair
or gate path. Fixed format requirements should be promoted to a template or
delivery standard, not softened into memory.

Use this wording when the requested behavior is already enforced:

> This is already enforced: before each delivery, MABW checks the reader-final
> output for internal IDs, source residue, local paths, and delivery gate
> failures. If the check fails, delivery is not marked complete. You can see the
> result in `output/intermediate/finalize_report.json`.

## 3. Source Discovery

When the workspace uses `llm_decide`, resolve sources before Scout:

```bash
multi-agent-brief sources decide --config ../mabw-workspace/config.yaml
cat ../mabw-workspace/source_candidates.yaml
multi-agent-brief sources decide --config ../mabw-workspace/config.yaml --merge
```

PowerShell:

```powershell
multi-agent-brief sources decide --config ..\mabw-workspace\config.yaml
Get-Content ..\mabw-workspace\source_candidates.yaml
multi-agent-brief sources decide --config ..\mabw-workspace\config.yaml --merge
```

## 4. Doctor Check

```bash
multi-agent-brief doctor --config ../mabw-workspace/config.yaml
```

PowerShell:

```powershell
multi-agent-brief doctor --config ..\mabw-workspace\config.yaml
```

## 5. Subagent Handoff

Claude Code subagents create the auditable artifacts:

| Subagent | Output |
|---|---|
| `scout` | `output/intermediate/candidate_claims.json` |
| `screener` | `output/intermediate/screened_candidates.json` |
| `claim-ledger` | `output/intermediate/claim_ledger.json` |
| `analyst` | `output/intermediate/audited_brief.md` |
| `editor` | polished `audited_brief.md` |
| `auditor` | `output/intermediate/audit_report.json` |

## 6. Deliver

After `audited_brief.md` exists and the auditor/quality gates are ready, use
the writer-facing delivery verb:

```text
/mabw deliver ../mabw-workspace
```

It runs the deterministic finalize path, verifies completion with
`state finalize-complete`, then shows or sends the delivery bundle. The lower-level
generation command is still available:

```bash
multi-agent-brief finalize --config ../mabw-workspace/config.yaml
```

PowerShell:

```powershell
multi-agent-brief finalize --config ..\mabw-workspace\config.yaml
```

This produces reader-facing output such as:

- `output/delivery/brief.md`
- `output/delivery/<named>.docx` when DOCX output is configured

Internal audit/control records remain under `output/intermediate/` and
`output/source_appendix.md`. When source appendix output is configured, the
appendix is already appended inside the delivery files; do not hand the
standalone audit/control copy to the reader as an extra artifact.

To show the finalized local delivery bundle directly:

```bash
multi-agent-brief deliver --workspace ../mabw-workspace --target local
```

To send the bundle to Feishu, choose a channel and recipient explicitly:

```bash
multi-agent-brief deliver --workspace ../mabw-workspace --target feishu --channel doc --recipient <folder_token>
```

## Complete Workflow Example

```text
User: I need to create a weekly brief for my solar manufacturing company.

Claude Code:
  1. Uses source-planner to resolve source discovery.
  2. Runs /mabw run to create handoff/control files.
  3. Runs /generate-brief inside Claude Code for the delegated subagent workflow.
  4. Runs /mabw deliver after audit and gates pass.
  5. Uses status and auditor findings to report artifact status and limitations.
```

## Subagent Reference

| Subagent | When to Use |
|---|---|
| `source-planner` | Planning source discovery and search tasks |
| `source-provider` | Configuring and collecting sources from providers |
| `scout` | Extracting candidate items from source content |
| `screener` | Filtering, ranking, and deduplicating candidates |
| `claim-ledger` | Converting candidates to source-grounded claims |
| `analyst` | Drafting management-ready brief sections |
| `editor` | Improving readability while preserving citations |
| `auditor` | Reviewing the auditable brief against ledger and audit report |
| `formatter` | Coordinating final output artifacts |

## Tips

- Preserve `[src:<claim_id>]` citations inside `audited_brief.md`, using real Claim Ledger IDs.
- Use CLI tools for deterministic setup, validation, audit, and rendering.
- Use subagents for source extraction, screening, analysis, editing, and final review.
- Check `output/intermediate/audit_report.json` before distributing a brief.
- `/mabw status` calls `multi-agent-brief status --workspace <workspace> --json`;
  it reports stale control files instead of refreshing them.
