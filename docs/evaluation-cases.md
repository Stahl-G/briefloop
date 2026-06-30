# Public-Safe Evaluation Cases

v0.6.4 introduced packaged public-safe evaluation cases for developer and CI
regression checks. v0.6.5 extended the packaged suite with provenance
projection coverage. v0.7.0 adds Improvement Memory control cases. The
pre-release v0.11.1 issue-closure line adds synthetic Product OS blocker cases
for durable source evidence pack validation and event-linked release readiness
reports.

These cases validate control-surface behavior across:

- deterministic quality gates
- feedback issue triage and repair planning controls
- runtime state blockers
- provenance projection controls
- Improvement Ledger / Memory materialization controls
- durable source evidence pack manifest validation
- event-linked release-readiness projection validation
- Hermes guidance invariants

They are not benchmark scores and are not workflow artifacts.

## Evaluation Claim Boundary

Evaluation cases prove deterministic control behavior, not model output quality. A passing case can show that an approved Improvement Memory entry was materialized into a frozen snapshot and referenced by handoff, or that an invalid optional control artifact stays invalid in the artifact registry. It does not prove that a runtime model followed guidance well, that a source supports a claim, or that a release is authorized.

For v0.8 planning, guidance evaluation should distinguish two future measurements:

- **Guidance manifestation rate**: when relevant approved guidance is materialized, whether the output or run trace shows observable evidence that the guidance affected the brief.
- **Guidance regression rate**: whether materialized guidance causes overfitting, factual weakening, omission, formatting harm, or conflict with evidence and contracts.

These metrics require real runtime traces and baseline comparisons. They are not asserted by the v0.7 public-safe eval suite.

## Commands

```bash
multi-agent-brief eval-cases list
multi-agent-brief eval-cases validate
multi-agent-brief eval-cases run --json
```

Use `--case-id <id>` to run one case and `--cases-dir <path>` for a custom fixture directory.

## Demo Scripts

Two source-clone demo scripts exercise the public-safe deterministic control
surface without network access, LLM calls, private fixtures, or tracked-file
changes:

```bash
bash scripts/demo.sh
bash scripts/demo-deep-dive.sh
```

`scripts/demo.sh` validates and runs packaged eval cases, then highlights the
three Improvement Memory cases and the `materialized_entry_ids` manifest
assertion. `scripts/demo-deep-dive.sh` creates a temporary workspace, records
and approves synthetic audience guidance, rebuilds `improvement/memory.md`, runs
handoff preparation, and prints the `runtime_manifest.json.improvement` block.

These scripts demonstrate deterministic control behavior only. They do not
prove model output quality improvement.

## Boundaries

Evaluation cases:

- use synthetic public-safe fixtures
- dispatch structured allowlisted actions
- prepare temporary runtime state from explicit `initial_stage`
- compare only stable partial assertions and optional `expected_actions`
- validate structured runtime manifest fields such as `runtime_manifest.json.improvement`
- validate selected artifact-registry statuses for explicit synthetic blockers
- do not infer workflow stage from files
- do not execute workflow stages
- do not run subagents
- do not call LLM judges
- do not fetch sources or live market data
- do not treat source candidates, source-pack manifests, or release-readiness
  reports as support proof or delivery approval
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
