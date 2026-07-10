# Claude Code Quickstart

This quickstart shows the first-class five-verb writer path for BriefLoop in
Claude Code. The primary command is `/briefloop`; `/mabw` remains a
compatibility alias during the BriefLoop transition. The verbs are:

```text
/briefloop new
/briefloop run <workspace>
/briefloop status <workspace>
/briefloop feedback <workspace> [text-or-file]
/briefloop deliver <workspace>
```

`/briefloop` is the BriefLoop writer command. Legacy project commands may still
exist for compatibility, but they are not the first path for new writers.

For writer-facing operation notes, see:

- `docs/golden-path.md`
- `docs/weekly-use.md`

Chinese versions:

- `docs/golden-path.zh-CN.md`
- `docs/weekly-use.zh-CN.md`

## Five Writer Verbs

| Verb | Product meaning |
|---|---|
| `/briefloop new` | Start a new brief workspace by answering who it is for, what this issue covers, and what to watch. |
| `/briefloop run <workspace>` | Create or refresh this run's handoff without executing specialist agents or marking stages complete. |
| `/briefloop status <workspace>` | See where the run stands. Strictly read-only. |
| `/briefloop feedback <workspace> [text-or-file]` | Record what feels wrong; triage, repair, improvement proposals, and approvals require explicit confirmation. |
| `/briefloop deliver <workspace>` | Deliver only after gates, the reader-final gate, and `state finalize-complete` pass. |

`doctor` is still available for diagnostics, but it is not a sixth writer verb.

## 1. Create a workspace

```bash
briefloop new industry-weekly ../weekly-brief
```

PowerShell:

```powershell
briefloop new industry-weekly ..\weekly-brief
```

Other product entries include `management-monthly` for recurring management
reviews and `document-review` for document evidence review workspaces. These
entries write canonical internal ReportPack ids into `report_spec.yaml`.

## 2. Use The Writer Entrypoint In Claude Code

Run this slash command inside the Claude Code CLI or the Claude Desktop Code
tab with this repository selected as the project folder, so
`.claude/commands/briefloop.md` and `.claude/commands/mabw.md` are loaded:

```text
/briefloop run ../weekly-brief
/briefloop status ../weekly-brief
```

If Claude Code returns `Unknown command: /briefloop`, the current session
has not discovered this project command. Confirm the project folder is the BriefLoop
repository root, type `/` to inspect available commands, or install the command
for user-level discovery:

```bash
briefloop claude install --repo-workdir .
```

You can also use the standard CLI handoff instead:

```bash
briefloop run --workspace ../briefloop-workspace
```

## Advanced: Continue From The Handoff

Most writers should stay on `/briefloop`. After `/briefloop run`, inspect:

```text
output/intermediate/agent_handoff.md
output/intermediate/agent_handoff.json
```

Continue only by following the generated handoff, stage artifacts, and
deterministic transactions. The delegated workflow follows this sequence:

```text
source discovery -> doctor -> scout -> screener -> claim-ledger -> analyst -> editor -> auditor -> finalize
```

If the generated Claude handoff instructs the Orchestrator main agent to
continue with `/generate-brief <workspace>`, that command is the Claude
delegated stage-workflow entrypoint. It is not the first writer command for new
users. `/briefloop run <workspace>` creates or refreshes handoff files only; it
does not execute specialists or complete stages.

## What `/briefloop status` Means

`/briefloop status <workspace>` is a read-only dashboard. It should help a writer
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

## How `/briefloop feedback` Is Routed

`/briefloop feedback <workspace> [text-or-file]` records feedback first. Recording
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

> This is already enforced: finalize only promotes delivery when
> `finalize_report.json` reports `delivery_promotion: "promoted"`, and delivery
> truth comes from `briefloop workbuddy diagnose --json`
> (`delivery_truth.valid=true`), never from file existence. Before each
> delivery, BriefLoop checks the reader-final
> output for internal IDs, source residue, local paths, and delivery gate
> failures. If the check fails, delivery is not marked complete. You can see the
> result in `output/intermediate/finalize_report.json`.

## 3. Source Discovery

When the workspace uses `llm_decide`, resolve sources before Scout:

```bash
briefloop sources decide --config ../briefloop-workspace/config.yaml
cat ../briefloop-workspace/source_candidates.yaml
briefloop sources decide --config ../briefloop-workspace/config.yaml --merge
```

PowerShell:

```powershell
briefloop sources decide --config ..\briefloop-workspace\config.yaml
Get-Content ..\briefloop-workspace\source_candidates.yaml
briefloop sources decide --config ..\briefloop-workspace\config.yaml --merge
```

## 4. Doctor Check

```bash
briefloop doctor --config ../briefloop-workspace/config.yaml
```

PowerShell:

```powershell
briefloop doctor --config ..\briefloop-workspace\config.yaml
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
/briefloop deliver ../briefloop-workspace
```

It runs the deterministic finalize path, verifies completion with
`state finalize-complete`, then shows or sends the delivery bundle. The lower-level
generation command is still available:

```bash
briefloop finalize --config ../briefloop-workspace/config.yaml
```

PowerShell:

```powershell
briefloop finalize --config ..\briefloop-workspace\config.yaml
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
briefloop deliver --workspace ../briefloop-workspace --target local
```

To send the bundle to Feishu, choose a channel and recipient explicitly:

```bash
briefloop deliver --workspace ../briefloop-workspace --target feishu --channel doc --recipient <folder_token>
```

## Complete Workflow Example

```text
User: I need to create a weekly brief for my solar manufacturing company.

Claude Code:
  1. Uses source-planner to resolve source discovery.
  2. Runs /briefloop run to create handoff/control files.
  3. Reads agent_handoff.md and follows the next safe action.
  4. Runs /briefloop deliver after audit and gates pass.
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
- `/briefloop status` calls `briefloop status --workspace <workspace> --json`;
  it reports stale control files instead of refreshing them.
