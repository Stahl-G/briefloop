# Public-Safe Evaluation Cases

v0.6.4 introduces packaged public-safe evaluation cases for developer and CI regression checks.

These cases validate control-surface behavior across:

- deterministic quality gates
- feedback issue triage and repair planning controls
- runtime state blockers
- Hermes guidance invariants

They are not benchmark scores and are not workflow artifacts.

## Commands

```bash
multi-agent-brief eval-cases list
multi-agent-brief eval-cases validate
multi-agent-brief eval-cases run --json
```

Use `--case-id <id>` to run one case and `--cases-dir <path>` for a custom fixture directory.

## Boundaries

Evaluation cases:

- use synthetic public-safe fixtures
- dispatch structured allowlisted actions
- prepare temporary runtime state from explicit `initial_stage`
- compare only stable partial assertions and optional `expected_actions`
- do not infer workflow stage from files
- do not execute workflow stages
- do not run subagents
- do not call LLM judges
- do not fetch sources or live market data
- do not execute repair or rewrite briefs
- do not add `evaluation_report.json` to runtime artifact contracts

## Fixture Safety

The packaged fixture scanner rejects common leakage patterns:

- non-synthetic manifests
- shell-string commands
- local user paths and `file://` references
- real URLs outside public-safe allowlists
- email domains outside public-safe examples
- token-shaped values and private-key markers
- raw prompt labels
- non-synthetic claim/source IDs in claim ledger fixtures

`contains_text.file` assertions must stay inside the selected evaluation case root or repository root. Absolute paths and `..` traversal are rejected during fixture validation.

`--keep-workspaces` copies temporary debug workspaces into `.mabw-eval-cases/`; that directory is ignored by Git.

Detailed private benchmark cases, commercial scenario design, prompt notes, and failure taxonomies stay out of the public repository until they are stable and safe to publish.
