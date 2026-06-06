# Claude Code Quick Start

This guide shows how to use Claude Code subagents with `multi-agent-brief-workflow`.

## Prerequisites

1. Claude Code installed and configured
2. Repository cloned and set up:

```bash
cd ~/Developer/multi-agent-brief-workflow
bash scripts/setup.sh
source .venv/bin/activate
```

3. A brief workspace initialized:

```bash
multi-agent-brief init ../mabw-workspace
```

Answer the interactive onboarding questions before running the pipeline. In an agent workflow, create `onboarding.json` from natural-language answers and run `multi-agent-brief init ../mabw-workspace --from-onboarding onboarding.json`.

## Reference Workflow Demo

The `examples/reference_workflow_demo/` workspace exercises the full official path without API keys. It uses synthetic public-safe data and a conservative (manual-only) source profile.

```bash
# Run the CI smoke test
python scripts/ci/smoke_reference_workflow.py examples/reference_workflow_demo

# Or run the pipeline directly
multi-agent-brief prepare --config examples/reference_workflow_demo/config.yaml
```

PowerShell:

```powershell
python scripts\ci\smoke_reference_workflow.py examples\reference_workflow_demo

multi-agent-brief prepare --config examples\reference_workflow_demo\config.yaml
```

Expected artifacts after a successful run:

```text
examples/reference_workflow_demo/output/
  brief.md                              # reader-facing, no [src:] markers
  intermediate/
    audited_brief.md                    # internal, retains [src:CLAIM_ID] citations
    claim_ledger.json                   # source-grounded claims
    audit_report.json                   # audit findings
    source_map.md                       # source mapping
    run_manifest.json                   # run metadata and artifact hashes
```

The smoke script (`scripts/ci/smoke_reference_workflow.py`) validates the artifact contract and exits non-zero on any failure.

## Sample Prompts

### 1. Source Planning

Use the `source-planner` subagent to create or refine sources for your workspace:

```text
Use the source-planner subagent to create sources for the workspace at ../mabw-workspace.
Read user.md and config.yaml, then generate source_candidates.yaml with public, citable sources
for the manufacturing industry.
```

The subagent will:
- Read `user.md`, `config.yaml`, and `sources.yaml`
- Generate `source_candidates.yaml` with public, citable, timestamped sources
- Align sources with your industry, role, and focus areas

### 2. Claim Extraction

Use the `scout` subagent to extract claims from search results:

```text
Use the scout subagent to extract claims from the latest search results in ../mabw-workspace/input/.
Filter boilerplate and navigation text. Extract structured claims with statement, evidence_text,
source_url, published_at, topic, claim_type, and confidence.
```

The subagent will:
- Read source files in `input/`
- Filter boilerplate, cookies, privacy text, ads
- Extract structured claims with full metadata
- Mark vague or low-confidence items

### 3. Run the Pipeline

Run the deterministic Python pipeline:

```bash
# Then use /generate-brief in Claude Code
```

PowerShell:

```powershell
# Then use /generate-brief in Claude Code
```

This produces:
- `output/brief.md` — reader-facing Markdown without internal claim IDs
- `output/intermediate/audited_brief.md` — auditable Markdown with `[src:CLAIM_ID]`
- `output/intermediate/claim_ledger.json` — source-grounded claims
- `output/intermediate/audit_report.json` — audit findings
- `output/intermediate/source_map.md` — source mapping

### 4. Analyst Improvement

Use the `analyst` subagent to improve the auditable brief while preserving citations:

```text
Use the analyst subagent to improve the brief at ../mabw-workspace/output/intermediate/audited_brief.md.
Read output/intermediate/claim_ledger.json and user.md. Draft management-ready sections.
Preserve every [src:CLAIM_ID] citation. Write in Chinese according to the workspace language.
```

The subagent will:
- Read `claim_ledger.json` and `user.md`
- Draft clear, management-ready sections
- Preserve all `[src:CLAIM_ID]` citations
- Write in the workspace language (Chinese or English)

### 5. Editor Polish

Use the `editor` subagent to improve readability:

```text
Use the editor subagent to improve the readability of ../mabw-workspace/output/intermediate/audited_brief.md.
Improve management tone and reduce repetition.
Preserve all [src:CLAIM_ID] citations exactly. Do not add new facts.
```

The subagent will:
- Improve clarity and management tone
- Reduce repetition
- Preserve all `[src:CLAIM_ID]` citations
- Not add new claims or facts

### 6. Auditor Review

Use the `auditor` subagent to verify the final output:

```text
Use the auditor subagent to review ../mabw-workspace/output/intermediate/audited_brief.md
against output/intermediate/claim_ledger.json and output/intermediate/audit_report.json.
Check for unsupported facts, missing citations, orphan citations, stale sources,
and investment-advice language. Recommend fixes.
```

The subagent will:
- Review the brief against `claim_ledger.json` and `audit_report.json`
- Check for unsupported facts, missing/orphan citations
- Check for stale sources and investment-advice language
- Recommend fixes
- Run `python` deterministic audit commands where available

After audit passes, regenerate `output/brief.md` as the reader-facing copy with `[src:CLAIM_ID]` markers stripped.

### 7. Doctor Check

Check source configuration health:

```bash
multi-agent-brief doctor --config ../mabw-workspace/config.yaml
```

PowerShell:

```powershell
multi-agent-brief doctor --config ..\mabw-workspace\config.yaml
```

### 8. Source Discovery (llm_decide profile)

```bash
# Generate candidate sources
multi-agent-brief sources decide --config ../mabw-workspace/config.yaml

# Review candidates
cat ../mabw-workspace/source_candidates.yaml

# Merge into sources
multi-agent-brief sources decide --config ../mabw-workspace/config.yaml --merge
```

PowerShell:

```powershell
multi-agent-brief sources decide --config ..\mabw-workspace\config.yaml

Get-Content ..\mabw-workspace\source_candidates.yaml

multi-agent-brief sources decide --config ..\mabw-workspace\config.yaml --merge
```

## Complete Workflow Example

```text
User: I need to create a weekly brief for my solar manufacturing company.

Claude Code:
  1. Uses source-planner to generate sources for solar manufacturing
  2. Runs multi-agent-brief init with the right settings
  3. Runs /generate-brief to produce the reader brief and audit artifacts
  4. Uses analyst to improve the audited brief sections
  5. Uses editor to polish the prose
  6. Uses auditor to verify the audited brief
  7. Regenerates and shows the user the final brief.md
```

To validate the workflow end-to-end without API keys, use the reference demo:

```bash
python scripts/ci/smoke_reference_workflow.py
```

PowerShell:

```powershell
python scripts\ci\smoke_reference_workflow.py
```

## Subagent Reference

| Subagent | When to Use |
|----------|-------------|
| `source-planner` | Planning source discovery, generating search tasks |
| `source-provider` | Configuring and collecting sources from providers |
| `scout` | Extracting candidate items from source content |
| `screener` | Filtering, ranking, deduplicating candidates |
| `claim-ledger` | Converting candidates to source-grounded claims |
| `analyst` | Drafting management-ready brief sections |
| `editor` | Improving readability without adding facts |
| `auditor` | Reviewing final brief against ledger and audit report |
| `formatter` | Writing final output artifacts |
| `orchestrator` | Coordinating multi-step pipeline work |

## Tips

- **Preserve citations internally**: Tell analyst/editor/auditor subagents to preserve `[src:CLAIM_ID]` in `audited_brief.md`; strip them only when regenerating reader-facing `brief.md`.
- **Use Python CLI for determinism**: The Python pipeline is repeatable and testable.
- **Use subagents for judgment**: Subagents are best for extraction, analysis, and editing.
- **Check source health**: Run `multi-agent-brief doctor` before running the pipeline.
- **Review audit output**: Always check `output/intermediate/audit_report.json` before distributing a brief.

## See Also

- [docs/claude-code-workflow.md](claude-code-workflow.md) — Two-layer architecture explanation
- [docs/agents/claude-code.md](agents/claude-code.md) — Subagent configuration reference
- [CLAUDE.md](../CLAUDE.md) — Project-level Claude Code instructions
