# Claude Code Quickstart

This quickstart shows the subagent-first path for creating a real source-grounded brief.

## 1. Create a workspace

```bash
multi-agent-brief init ../mabw-workspace --demo
```

PowerShell:

```powershell
multi-agent-brief init ..\mabw-workspace --demo
```

## 2. Generate the brief in Claude Code

Run this slash command inside the Claude Code CLI or the Claude Desktop Code
tab with this repository selected as the project folder, so
`.claude/commands/generate-brief.md` is loaded:

```text
/generate-brief ../mabw-workspace
```

If Claude Code returns `Unknown command: /generate-brief`, the current session
has not discovered this project command. Confirm the project folder is the MABW
repository root, type `/` to inspect available commands, or use the standard
CLI handoff instead:

```bash
multi-agent-brief run --workspace ../mabw-workspace
```

The command follows this workflow:

```text
source discovery -> doctor -> scout -> screener -> claim-ledger -> analyst -> editor -> auditor -> finalize
```

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

## 6. Finalize

After `audited_brief.md` exists:

```bash
multi-agent-brief finalize --config ../mabw-workspace/config.yaml
```

PowerShell:

```powershell
multi-agent-brief finalize --config ..\mabw-workspace\config.yaml
```

This produces reader-facing output such as:

- `output/brief.md`
- configured named Markdown output
- `output/brief.docx` when DOCX output is configured

## Complete Workflow Example

```text
User: I need to create a weekly brief for my solar manufacturing company.

Claude Code:
  1. Uses source-planner to resolve source discovery.
  2. Runs doctor.
  3. Runs /generate-brief inside Claude Code for the subagent workflow.
  4. Uses auditor findings to report artifact status and limitations.
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

- Preserve `[src:CLAIM_ID]` citations inside `audited_brief.md`.
- Use CLI tools for deterministic setup, validation, audit, and rendering.
- Use subagents for source extraction, screening, analysis, editing, and final review.
- Check `output/intermediate/audit_report.json` before distributing a brief.
