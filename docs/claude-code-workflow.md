# Claude Code Agent Workflow

MABW is a subagent-first workflow toolkit. Python CLI commands provide setup,
source discovery, input governance, audit checks, and final rendering. The actual
brief is produced by external subagents.

## Architecture

```text
Python tools
  init / sources decide / doctor / inputs extract / inputs classify / audit / finalize

External subagents
  source-planner -> scout -> screener -> claim-ledger -> analyst -> editor -> auditor -> formatter
```

## CLI Tool Layer

The Python CLI keeps deterministic, testable support functions in code:

- `briefloop init` — create a workspace
- `briefloop sources decide` — resolve `llm_decide` source policy
- `briefloop doctor` — check configuration and source health
- `briefloop inputs extract` — convert supported PDF/DOCX/image inputs to adjacent `.mineru.md` files with MinerU
- `briefloop inputs classify` — classify evidence and instruction inputs
- `briefloop audit` — run deterministic audit checks where applicable
- `briefloop finalize` — render reader-facing Markdown and DOCX from `audited_brief.md`

These commands are tools. They provide contracts, validation, and rendering for
the subagent workflow.

## Subagent Runtime Layer

Claude Code subagents handle model-assisted judgment and handoffs:

```text
source-planner -> scout -> screener -> claim-ledger -> analyst -> editor -> auditor -> formatter
```

- **source-planner** proposes public, citable sources.
- **scout** extracts candidate reportable items.
- **screener** ranks, deduplicates, freshness-checks, and capacity-caps candidates.
- **claim-ledger** writes stable source-grounded claim entries.
- **analyst** writes `output/intermediate/audited_brief.md`.
- **editor** improves readability while preserving citations.
- **auditor** checks the auditable brief against `claim_ledger.json`.
- **formatter/finalize** produces reader-facing artifacts.

## Execution Flow

```text
User request
  |
  v
Read workspace: config.yaml, sources.yaml, user.md, input/
  |
  v
Source discovery if llm_decide is enabled
  |
  v
doctor
  |
  v
inputs extract when non-text inputs exist
  |
  v
inputs classify
  |
  v
scout subagent -> candidate_claims.json
  |
  v
screener subagent -> screened_candidates.json
  |
  v
claim-ledger subagent -> claim_ledger.json
  |
  v
analyst subagent -> audited_brief.md
  |
  v
editor subagent -> polished audited_brief.md
  |
  v
auditor subagent -> audit_report.json
  |
  v
finalize -> brief.md / DOCX
```

## Generated Agent Files

| Role | Purpose |
|---|---|
| `source-planner` | Plan public, citable source discovery |
| `scout` | Extract candidate reportable items |
| `screener` | Rank, deduplicate, and freshness-check candidates |
| `claim-ledger` | Convert screened candidates into stable source-grounded claims |
| `analyst` | Draft the auditable brief from Claim Ledger evidence |
| `editor` | Improve clarity and structure while preserving citations |
| `auditor` | Verify the auditable brief against Claim Ledger evidence |
| `formatter` | Coordinate final output handoff and rendering |

## Design Principle

Prompt and skill files describe positive workflow, role boundaries, inputs,
outputs, and handoffs.

Harnesses, validators, CI checks, and audit rules own hard gates, schema checks,
regression tests, and failure conditions.

## Usage Pattern

```text
1. Initialize or open a workspace.
2. Resolve source discovery when configured.
3. Run doctor.
4. Invoke the subagents in workflow order.
5. Run finalize after `audited_brief.md` exists.
6. Review artifact paths, audit status, and limitations.
```

## Related Docs

- [Claude Code quickstart](claude-code-quickstart.md)
- [Agent configuration reference](agents/claude-code.md)
- [Architecture overview](architecture.md)
- [Harness design](harness.md)
