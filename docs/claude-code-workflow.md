# Claude Code Agent Workflow

## Two-Layer Architecture

This repository has two distinct layers:

### Layer 1: Python CLI (Deterministic Preparation)

The Python CLI provides deterministic, testable, API-key-optional preparation tools:

```text
multi-agent-brief init → /generate-brief (Claude Code) → output artifacts
```

- **Deterministic pipeline**: Scout → Screener → Claim Ledger → Analyst → Editor → Auditor → Formatter
- **Audit harness**: DeterministicAuditAgent, QualityHarnessAuditAgent, CompositeAuditAgent
- **Output contract**: `brief.md` for readers, plus `output/intermediate/audited_brief.md`, `claim_ledger.json`, `audit_report.json`, and `source_map.md` for audit/review
- **No API keys required**: runs entirely with local Python
- **Testable**: `python -m pytest -q` covers all pipeline stages

The Python CLI produces intermediate artifacts (draft brief, claim ledger, audit report). The final user-facing brief is written by Claude Code / Codex subagents using these artifacts.

### Layer 2: Claude Code Subagent Orchestration

Claude Code subagents provide interactive, model-assisted workflow orchestration:

```text
source-planner → scout → analyst → editor → auditor → formatter
```

- **Subagent definitions**: `.claude/agents/*.md` (Markdown + YAML frontmatter)
- **Interactive orchestration**: Claude Code spawns subagents as needed
- **Model-assisted judgment**: subagents use LLM for extraction, analysis, editing
- **Source grounding**: all subagents respect Claim Ledger and citation contracts
- **No SDK dependency**: subagents are pure Markdown prompts, not Python code

Claude Code subagents complement the Python CLI — they do not replace it.

## How The Layers Interact

```text
┌─────────────────────────────────────────────────────┐
│  Claude Code Session                                │
│                                                     │
│  User: "Generate a weekly brief for my company"     │
│         │                                           │
│         ▼                                           │
│  source-planner subagent                            │
│    → reads user.md, config.yaml, sources.yaml       │
│    → generates source_candidates.yaml               │
│         │                                           │
│         ▼                                           │
│  /generate-brief (Claude Code subagent workflow)  │
│    → Python CLI executes deterministic pipeline     │
│    → produces reader brief + intermediate audit set │
│         │                                           │
│         ▼                                           │
│  analyst subagent                                   │
│    → reads claim_ledger.json and user.md            │
│    → improves brief sections                        │
│    → preserves all [src:CLAIM_ID] citations         │
│         │                                           │
│         ▼                                           │
│  editor subagent                                    │
│    → improves readability and management tone       │
│    → preserves all citations                        │
│         │                                           │
│         ▼                                           │
│  auditor subagent                                   │
│    → reviews audited_brief.md against ledger        │
│    → runs python deterministic audit commands       │
│    → reports findings and recommends fixes          │
│                                                     │
│  formatter/final handoff                            │
│    → strips [src:CLAIM_ID] only for brief.md        │
└─────────────────────────────────────────────────────┘
```

## Available Subagents

| Subagent | Role | Purpose |
|----------|------|---------|
| `source-planner` | coordination | Generate/refine source_candidates.yaml and search_tasks |
| `source-provider` | coordination | Configure and collect sources from providers |
| `scout` | pipeline | Extract candidate reportable items from sources |
| `screener` | pipeline | Filter, rank, deduplicate candidates |
| `claim-ledger` | pipeline | Convert candidates to source-grounded claims |
| `analyst` | pipeline | Draft management-ready brief sections |
| `editor` | pipeline | Improve readability without adding facts |
| `auditor` | pipeline | Review final brief against ledger and audit report |
| `formatter` | pipeline | Write final output artifacts |
| `orchestrator` | coordination | Coordinate multi-step pipeline work |
| `draft-audit-harness` | harness | Draft-level audit checks |
| `final-quality-harness` | harness | Final text quality gates |
| `rendered-output-harness` | harness | DOCX/PDF rendering fidelity checks |

## Key Design Decisions

### Why Two Layers?

1. **Testability**: The Python CLI has 100+ tests covering every pipeline stage. Subagent behavior is inherently non-deterministic and harder to test.
2. **Portability**: The Python CLI runs anywhere Python 3.9+ is available, without Claude Code.
3. **Auditability**: Deterministic audit gates in Python are repeatable. Model-assisted checks are complementary.
4. **No SDK lock-in**: Subagents are pure Markdown — no Anthropic SDK dependency in the Python package.

### Why Not Embed Model Calls in Python?

- Model calls inside the Python pipeline would make tests non-deterministic.
- API key management would complicate the CLI.
- The Python pipeline should remain runnable without any API keys.
- Claude Code subagents handle model interaction externally.

### What Stays in Python

- Pipeline orchestration logic
- Deterministic audit checks
- Output file formatting and validation
- Source provider configuration and collection
- Claim Ledger data structure
- CLI commands (init, prepare, doctor, audit, sources decide, competitors init/list/merge)

### What Stays in Subagents

- Source planning and discovery
- Claim extraction from unstructured text
- Brief section drafting
- Prose editing and readability improvement
- Final review and recommendation

## Usage Patterns

### Pattern 1: Full Agent-Assisted Workflow

```text
1. User asks Claude Code to generate a brief
2. source-planner creates/refines sources
3. Python CLI runs the pipeline
4. analyst improves the draft
5. editor polishes the prose
6. auditor verifies the final output
```

### Pattern 2: Python CLI Only

```text
1. User runs multi-agent-brief init
2. User adds source files to input/
3. User runs /generate-brief in Claude Code
4. User reviews output/ artifacts
```

### Pattern 3: Hybrid

```text
1. User runs multi-agent-brief init (Python CLI)
2. User asks Claude Code source-planner to refine sources
3. User runs /generate-brief in Claude Code
4. User asks Claude Code auditor to review output
```

## See Also

- [docs/claude-code-quickstart.md](claude-code-quickstart.md) — Command guide with sample prompts
- [docs/agents/claude-code.md](agents/claude-code.md) — Subagent configuration reference
- [docs/architecture.md](architecture.md) — Pipeline architecture
- [docs/harness.md](harness.md) — Audit harness design
