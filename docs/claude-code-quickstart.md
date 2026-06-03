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
multi-agent-brief init ../mabw-workspace \
  --language zh-CN \
  --company "Your Company" \
  --industry manufacturing \
  --title "Weekly Brief" \
  --audience management \
  --source-profile research
```

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
multi-agent-brief run --config ../mabw-workspace/config.yaml
```

Or in PowerShell:

```powershell
multi-agent-brief run --config ../mabw-workspace\config.yaml
```

This produces:
- `output/brief.md` — the Markdown brief
- `output/claim_ledger.json` — source-grounded claims
- `output/audit_report.json` — audit findings
- `output/source_map.md` — source mapping

### 4. Analyst Improvement

Use the `analyst` subagent to improve the brief while preserving citations:

```text
Use the analyst subagent to improve the brief at ../mabw-workspace/output/brief.md.
Read claim_ledger.json and user.md. Draft management-ready sections.
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
Use the editor subagent to improve the readability of ../mabw-workspace/output/brief.md.
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
Use the auditor subagent to review the final brief at ../mabw-workspace/output/brief.md
against claim_ledger.json and audit_report.json.
Check for unsupported facts, missing citations, orphan citations, stale sources,
and investment-advice language. Recommend fixes.
```

The subagent will:
- Review the brief against `claim_ledger.json` and `audit_report.json`
- Check for unsupported facts, missing/orphan citations
- Check for stale sources and investment-advice language
- Recommend fixes
- Run `python` deterministic audit commands where available

### 7. Doctor Check

Check source configuration health:

```bash
multi-agent-brief doctor --config ../mabw-workspace/config.yaml
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

## Complete Workflow Example

```text
User: I need to create a weekly brief for my solar manufacturing company.

Claude Code:
  1. Uses source-planner to generate sources for solar manufacturing
  2. Runs multi-agent-brief init with the right settings
  3. Runs multi-agent-brief run to produce the initial brief
  4. Uses analyst to improve the brief sections
  5. Uses editor to polish the prose
  6. Uses auditor to verify the final output
  7. Shows the user the final brief.md
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

- **Preserve citations**: Always tell subagents to preserve `[src:CLAIM_ID]` citations.
- **Use Python CLI for determinism**: The Python pipeline is repeatable and testable.
- **Use subagents for judgment**: Subagents are best for extraction, analysis, and editing.
- **Check source health**: Run `multi-agent-brief doctor` before running the pipeline.
- **Review audit output**: Always check `audit_report.json` before distributing a brief.

## See Also

- [docs/claude-code-workflow.md](claude-code-workflow.md) — Two-layer architecture explanation
- [docs/agents/claude-code.md](agents/claude-code.md) — Subagent configuration reference
- [CLAUDE.md](../CLAUDE.md) — Project-level Claude Code instructions
