# v0.4.0 Implementation Plan — Knowledge & Governance Contracts

This plan supersedes the old Capability Center v0.4 plan. Capability Center has already landed; v0.4.0 now means **data contracts, Claim semantics, audit status, run traceability, and release consistency**.

The goal is not to add more features. The goal is to make the existing workflow stable enough to become the v1.0 baseline and the future v2.0 MAS Runtime comparison engine.

## Non-Negotiables

- Do not start MAS Runtime work in v0.4.0.
- Do not add more search backends, topic modules, or delivery channels.
- Do not weaken Screener, Claim Ledger, deterministic audit, final quality gates, or human-review boundaries.
- Do not make experimental capabilities look supported.
- Do not introduce private/company-specific examples.
- Preserve Windows native PowerShell support.
- Every behavior change needs focused tests.

## Target Outcome

At the end of v0.4.0:

- Claims can distinguish fact, case, interpretation, hypothesis, action, and to-verify items.
- Evidence can distinguish direct, comparable, historical analogy, and background support.
- Core artifacts have explicit schema versions and validation paths.
- Every formal pipeline run produces a run manifest with artifact hashes.
- Semantic audit cannot report "pass" when it was not configured or not run.
- Audit findings identify who should repair them and whether they block release.
- Release consistency drift is caught before merge.

## Suggested Worktree Setup

Use independent worktrees so multiple agents can work in parallel from `origin/main`.

```bash
git fetch origin main
mkdir -p ../mabw-v040

git worktree add ../mabw-v040-claim-schema -b codex/v040-claim-schema origin/main
git worktree add ../mabw-v040-contracts -b codex/v040-contracts origin/main
git worktree add ../mabw-v040-run-manifest -b codex/v040-run-manifest origin/main
git worktree add ../mabw-v040-semantic-audit -b codex/v040-semantic-audit origin/main
git worktree add ../mabw-v040-audit-taxonomy -b codex/v040-audit-taxonomy origin/main
git worktree add ../mabw-v040-release-gate -b codex/v040-release-gate origin/main
```

Recommended PR order:

```text
PR A claim-schema
→ PR C semantic-audit
→ PR D audit-taxonomy
→ PR B contracts
→ PR E run-manifest
→ PR F release-gate
```

`run-manifest` can start in parallel, but should rebase after contracts if it imports schema constants or validators.

## PR A — Claim Schema v2

Branch:

```text
codex/v040-claim-schema
```

Primary files:

- `src/multi_agent_brief/core/schemas.py`
- `src/multi_agent_brief/core/claim_ledger.py`
- `src/multi_agent_brief/core/selection.py`
- `removed Python runtime scout module`
- `src/multi_agent_brief/audit/deterministic.py`
- `src/multi_agent_brief/audit/harness.py`
- `tests/test_claim_ledger.py`
- `tests/test_deterministic_audit.py`
- new `tests/test_claim_schema_v2.py`

Implementation:

- Add `schema_version: str = "claim/v2"` to `Claim`.
- Add `epistemic_type`.
- Add `evidence_relation`.
- Add `applicability_reason`.
- Add `limitations`.
- Preserve `claim_type` for compatibility.
- Add compatibility migration in `Claim.from_dict()`.
- Make `Claim.to_dict()` export new fields.
- Map legacy `claim_type` values conservatively:
  - `fact`, `number`, `date` -> `FACT` + `DIRECT`
  - `interpretation`, `risk`, `forecast` -> `INTERPRETATION` or `HYPOTHESIS` + `DIRECT`
  - `needs_recrawl` metadata should not become a supported epistemic type.

Suggested literals:

```python
EpistemicType = Literal[
    "FACT",
    "CASE",
    "INTERPRETATION",
    "HYPOTHESIS",
    "ACTION",
    "TO_VERIFY",
]

EvidenceRelation = Literal[
    "DIRECT",
    "COMPARABLE",
    "HISTORICAL_ANALOGY",
    "BACKGROUND",
]
```

Audit rules:

- `COMPARABLE` and `HISTORICAL_ANALOGY` claims need either `applicability_reason` or at least one `limitations` entry.
- `ACTION` claims need evidence and should be high-friction in audits.
- `HYPOTHESIS` must not be phrased as confirmed fact in final output.
- `BACKGROUND` should not be described as current-period news.

Acceptance tests:

- Old claim JSON imports successfully.
- New claim JSON round-trips.
- Comparable claim without applicability/limitations creates an audit finding.
- Action claim without evidence creates an audit finding.
- Claim Ledger export remains readable by existing source map and formatter.

Agent instruction:

```text
Implement Claim Schema v2 without breaking old claim ledgers. Preserve claim_type compatibility, add explicit epistemic_type and evidence_relation, and add deterministic audit checks for misuse. Do not change pipeline order or add new features.
```

## PR B — Core Contracts Package

Branch:

```text
codex/v040-contracts
```

Primary files:

- new `src/multi_agent_brief/contracts/`
- new `src/multi_agent_brief/contracts/schemas/`
- new `tests/contracts/`
- `src/multi_agent_brief/core/schemas.py`
- `src/multi_agent_brief/sources/base.py`
- `src/multi_agent_brief/analysis_modules/base.py`

Implementation:

- Add contract package:

```text
src/multi_agent_brief/contracts/
  __init__.py
  validators.py
  migrations.py
  errors.py
  versions.py
  schemas/
    claim.v2.json
    source_item.v1.json
    candidate_item.v1.json
    audit_report.v1.json
    analysis_pack.v1.json
    run_manifest.v1.json
```

- Keep validators lightweight and dependency-free unless a JSON schema dependency already exists.
- Prefer explicit Python validators for MVP if adding `jsonschema` would expand install surface.
- Define unknown-field policy:
  - Claim: preserve unknowns under `metadata` only when safe.
  - Audit report: reject missing required fields.
  - Source item: reject missing `source_id`, `title`, or source content fields.
- Add fixtures:

```text
tests/contracts/fixtures/
  claim_v2_minimal.json
  claim_v2_comparable.json
  source_item_v1.json
  audit_report_v1.json
  analysis_pack_v1.json
  run_manifest_v1.json
```

Acceptance tests:

- All fixtures validate.
- Required fields fail with useful errors.
- Unknown-field policy is tested.
- Legacy claim fixture migrates to `claim/v2`.

Agent instruction:

```text
Create a dependency-light contracts package for v0.4.0. Do not rewrite the whole app around it yet. Add schemas, validators, fixtures, and contract tests that future PRs can import.
```

## PR C — Semantic Audit Status Semantics

Branch:

```text
codex/v040-semantic-audit
```

Primary files:

- `src/multi_agent_brief/audit/semantic.py`
- `src/multi_agent_brief/audit/interfaces.py`
- `src/multi_agent_brief/audit/harness.py`
- `src/multi_agent_brief/audit/deterministic.py`
- `src/multi_agent_brief/core/schemas.py`
- `tests/test_auditor_interface.py`
- new `tests/test_semantic_audit_status.py`

Implementation:

- Add semantic audit status values:

```text
not_configured
not_run
pass
warning
fail
error
```

- `NoOpSemanticAuditAgent` must not return `pass`.
- If semantic audit is unavailable, report `not_configured`.
- Composite audit metadata must make deterministic and semantic status separate.
- Do not require API keys for MVP operation.
- Do not make `not_configured` fail ordinary deterministic runs.
- Make release-facing docs clear that semantic audit was not performed.

Acceptance tests:

- NoOp semantic audit returns `not_configured`, not `pass`.
- Composite audit with deterministic pass + semantic not configured is transparent in metadata.
- Existing no-key test path still passes.
- Configured semantic audit exceptions become `error`, not empty pass.

Agent instruction:

```text
Fix semantic audit status semantics. No-op or missing semantic audit must be visible as not_configured/not_run, never pass. Preserve no-API-key local usability.
```

## PR D — Audit Finding Taxonomy

Branch:

```text
codex/v040-audit-taxonomy
```

Primary files:

- `src/multi_agent_brief/core/schemas.py`
- `src/multi_agent_brief/audit/deterministic.py`
- `src/multi_agent_brief/audit/harness.py`
- `src/multi_agent_brief/audit/final_quality.py`
- `src/multi_agent_brief/analysis_modules/market_competitor/auditor.py`
- `tests/test_deterministic_audit.py`
- `tests/test_quality_harness.py`
- `tests/test_final_quality_audit.py`
- new `tests/test_audit_finding_taxonomy.py`

Implementation:

- Add fields to `AuditFinding`:

```python
blocking_level: str = "analyst_blocking"
repair_owner: str = "analyst"
```

- Suggested blocking levels:

```text
editor_fixable
analyst_blocking
source_blocking
configuration_error
rendering_error
safety_blocking
```

- Suggested repair owners:

```text
editor
analyst
source_provider
auditor
formatter
user
maintainer
```

- Backfill existing findings with conservative defaults.
- Final-quality render/DOCX issues should use `rendering_error` / `formatter`.
- Missing sources and unsupported claims should use `source_blocking` or `analyst_blocking`.
- Redaction/privacy issues should use `safety_blocking`.

Acceptance tests:

- Every finding exported to JSON has `blocking_level` and `repair_owner`.
- Existing tests updated without weakening severity.
- Final auditor can summarize repair ownership in metadata.
- Unknown legacy finding data imports with defaults.

Agent instruction:

```text
Add repair ownership and blocking level to AuditFinding. Backfill existing audit agents conservatively. Do not lower severities or turn blocking issues into warnings.
```

## PR E — Run Manifest

Branch:

```text
codex/v040-run-manifest
```

Primary files:

- new `src/multi_agent_brief/core/run_manifest.py`
- `src/multi_agent_brief/core/pipeline.py`
- `removed Python runtime formatter module`
- `src/multi_agent_brief/cli/main.py`
- `tests/test_pipeline.py`
- `tests/test_formatter_docx_metadata.py`
- new `tests/test_run_manifest.py`

Implementation:

- Generate `output/intermediate/run_manifest.json`.
- Include:

```json
{
  "schema_version": "run-manifest/v1",
  "run_id": "...",
  "workflow_version": "...",
  "started_at": "...",
  "completed_at": "...",
  "status": "pass|warning|fail|error",
  "config_hash": "...",
  "enabled_providers": [],
  "enabled_modules": [],
  "source_count": 0,
  "candidate_count": 0,
  "claim_count": 0,
  "audit_status": "...",
  "audit_score": 0,
  "errors": [],
  "warnings": [],
  "artifacts": {},
  "artifact_hashes": {}
}
```

- Hash artifacts with SHA-256.
- Capture provider/module errors from existing metadata/artifacts.
- Ensure fatal source collection still leaves a manifest if output dir is known.
- Avoid changing final reader-facing text.

Acceptance tests:

- Successful prepare/run path writes manifest.
- Fatal collection path writes manifest with `status=fail` or `error`.
- Artifact hashes match file contents.
- Manifest artifact list includes brief, audited brief, ledger, audit report, and source map when present.

Agent instruction:

```text
Add run_manifest.json as a traceability artifact. It should summarize what happened, not alter pipeline behavior. Include artifact hashes and provider/module/audit status.
```

## PR F — Release Consistency Gate

Branch:

```text
codex/v040-release-gate
```

Primary files:

- new `scripts/check_release_consistency.py`
- `.github/workflows/tests.yml`
- `tests/test_release_consistency.py` if appropriate
- `README.md`
- `README_en.md`
- `CHANGELOG.md`
- `docs/roadmap.md`
- `docs/roadmap.zh-CN.md`

Implementation:

- Check:

```text
pyproject.toml version
src/multi_agent_brief/__init__.py __version__
CHANGELOG latest release heading or unreleased policy
README current version strings
README_en current version strings
generated agent configs status
docs/impl-plan-v0.4.0.md current milestone title
```

- Keep failure messages actionable.
- Add CI step after tests or docs checks.
- Do not force tag existence on ordinary PRs unless release mode is enabled.
- Support:

```bash
python scripts/check_release_consistency.py
python scripts/check_release_consistency.py --release
```

Acceptance tests:

- Script passes on current tree.
- A temporary mismatched fixture or monkeypatched version fails clearly.
- CI invokes the non-release mode.

Agent instruction:

```text
Create a release consistency gate that catches version/docs/agent-config drift without making ordinary PRs require a Git tag. Keep release-only checks behind --release.
```

## Parallelization Guidance

Can run immediately in parallel:

- PR A Claim Schema v2
- PR C Semantic Audit Status
- PR F Release Consistency Gate

Can start in parallel but should rebase before finalizing:

- PR B Contracts Package, because it may want Claim v2 names from PR A.
- PR D Audit Finding Taxonomy, because it overlaps audit files with PR C.
- PR E Run Manifest, because it may import contracts from PR B.

Suggested merge sequence:

```text
1. PR A Claim Schema v2
2. PR C Semantic Audit Status
3. PR D Audit Finding Taxonomy
4. PR B Contracts Package
5. PR E Run Manifest
6. PR F Release Consistency Gate
```

If conflicts become heavy, merge PR B before PR D only if contracts are dependency-light and do not modify audit files.

## Common Test Commands

All agents should run:

```bash
python -m pytest -q
git diff --check
```

When touching agent configs:

```bash
python scripts/generate_agent_configs.py --check
```

When touching capability catalog:

```bash
python scripts/check_capabilities.py
```

When touching release/version docs after PR F:

```bash
python scripts/check_release_consistency.py
```

Windows compatibility must remain native PowerShell; do not instruct Windows users to rely on WSL/Git Bash.

## Definition of Done for v0.4.0

- All six PRs are merged.
- Full CI passes on Linux, macOS, and Windows.
- No public docs present v2.0 MAS Runtime as the current main path.
- No experimental connector/output is described as fully supported.
- New schemas and manifests are public-safe and fixture-backed.
- README, README_en, CHANGELOG, roadmap, and implementation plan agree on v0.4.0 scope.

## One-Line Agent Directive

In v0.4.0, do not expand the product surface. Stabilize Claim semantics, contracts, audit status, run traceability, and release consistency so v1.0 can become the future MAS Runtime baseline.
