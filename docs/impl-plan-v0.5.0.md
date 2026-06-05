# v0.5.0 Implementation Plan - Production Reference Workflow

v0.5.0 starts after the v0.4.0 contract foundation. v0.4.0 made claims, audit findings, contracts, manifests, and semantic audit status more explicit. v0.5.0 should turn those foundations into one official, reproducible, publishable reference workflow.

The goal is not to add more providers or make the system look bigger. The goal is to make the current workflow reliable enough that a new user can install the tool, initialize a workspace, run preparation, use the agent workflow, pass final delivery gates, and produce Markdown/DOCX outputs that are ready for human review.

## Version Thesis

v0.5.0 is the "reference workflow" release.

At the end of v0.5.0, the project should have:

- One documented official path from onboarding to human review.
- One public-safe synthetic demo that exercises that path.
- Final Clean gates for reader-facing output.
- Audience Profiles that shape structure, tone, required sections, and audit thresholds.
- Basic DOCX templates with deterministic rendered-output validation.
- Editorial Governance gates for factual density, comparable cases, source coverage, and research-gap separation.
- A Policy & Regulatory Risk Module as the second analysis module after Market & Competitor.
- A minimal HistoryStore for previous briefs and previous claim ledgers.
- Effort Budgets that tune runtime limits without introducing model routing.

## Non-Negotiables

- Preserve the pipeline order:

```text
Scout -> Screener -> Claim Ledger -> Analyst -> Editor -> Auditor -> Formatter
```

- Do not bypass Screener.
- Do not bypass Claim Ledger.
- Do not weaken deterministic audit, final quality gates, rendered-output checks, or human-review boundaries.
- Do not make experimental capabilities look supported.
- Do not add more search backends, delivery channels, or large topic-module families.
- Do not start MAS Runtime, Shared World, Event Store, TaskBoard, or AgentMessage work.
- Do not build full RAG or a vector database.
- Do not build the full Event Completeness engine or missing-fact retrieval loop in v0.5.0.
- Do not hard-code Vietnam, TikTok, Shopee, Lazada, or any other specific market/platform into core coverage rules.
- Preserve Windows native PowerShell setup, test, and smoke guidance.
- Every behavior change needs focused tests.
- Public fixtures and examples must use public or synthetic data only.

## Official Workflow Contract

The v0.5.0 official path is:

```text
interactive init
-> sources decide and user/source confirmation
-> doctor
-> prepare
-> Analyst
-> Editor
-> Final Auditor
-> Formatter
-> Markdown / DOCX
-> Human Review
```

The reference workflow must produce these artifacts:

```text
output/brief.md
output/brief.docx                       # if docx is enabled
output/<configured-name>.md              # if named outputs are enabled
output/<configured-name>.docx            # if docx and named outputs are enabled
output/intermediate/audited_brief.md
output/intermediate/claim_ledger.json
output/intermediate/audit_report.json
output/intermediate/source_map.md
output/intermediate/run_manifest.json
```

v0.5.0 may add these artifacts:

```text
output/intermediate/final_clean_report.json
output/intermediate/final_quality_report.json
output/intermediate/rendered_output_report.json
output/intermediate/source_coverage_report.json
output/intermediate/research_gaps.md
output/intermediate/history_report.json
```

Reader-facing `brief.md` must not expose internal `[src:CLAIM_ID]` citations. The audited intermediate Markdown must preserve valid citations. The reader-facing Markdown must be a clean projection of the audited final Markdown, not a separate unaudited rewrite.

## Scope Boundaries

Must ship in v0.5.0:

- Official workflow harness and synthetic demo.
- Final Clean gate.
- Audience Profiles.
- DOCX templates and rendered-output validation.
- Editorial Governance rule packs.
- Source Coverage Report and `research_gaps.md`.
- Policy & Regulatory Risk Module.
- Minimal HistoryStore.
- Effort Budgets.
- Release readiness docs and CI checks.

Experimental or interface-only in v0.5.0:

- Event family concepts.
- Event field completeness contracts.
- Missing fact status labels.
- Field-level provenance beyond existing Claim evidence.

Deferred beyond v0.5.0:

- Full Event Completeness engine.
- Automatic missing-fact retrieval loop.
- Full operating-data diagnostics.
- More search backends.
- Full model routing.
- Full RAG or vector memory.
- MAS Runtime.

## Suggested Worktree Setup

Use independent worktrees so multiple agents can work in parallel from `origin/main`.

```bash
git fetch origin main
mkdir -p ../mabw-v050

git worktree add ../mabw-v050-workflow -b codex/v050-official-workflow origin/main
git worktree add ../mabw-v050-final-clean -b codex/v050-final-clean origin/main
git worktree add ../mabw-v050-audience -b codex/v050-audience-profiles origin/main
git worktree add ../mabw-v050-docx -b codex/v050-docx-templates origin/main
git worktree add ../mabw-v050-editorial -b codex/v050-editorial-governance origin/main
git worktree add ../mabw-v050-coverage -b codex/v050-source-coverage origin/main
git worktree add ../mabw-v050-policy -b codex/v050-policy-regulatory-module origin/main
git worktree add ../mabw-v050-history -b codex/v050-history-store origin/main
git worktree add ../mabw-v050-budgets -b codex/v050-effort-budgets origin/main
git worktree add ../mabw-v050-release -b codex/v050-release-readiness origin/main
```

Recommended merge order:

```text
PR A official-workflow
-> PR B final-clean
-> PR C audience-profiles
-> PR D docx-templates
-> PR E editorial-governance
-> PR F source-coverage
-> PR G policy-regulatory-module
-> PR H history-store
-> PR I effort-budgets
-> PR J release-readiness
```

Parallelization guidance:

- PR A should merge first. It defines the workflow contract and test fixture shape.
- PR B, PR C, and PR D can start immediately but should rebase after PR A.
- PR E depends on PR B and PR C because it consumes Final Clean and Audience Profile thresholds.
- PR F can start after PR A and should rebase before PR E if it shares audit metadata.
- PR G can start after PR A because it uses the existing AnalysisModule Registry.
- PR H can start after PR A but must not change current-period fact semantics.
- PR I can start after PR C because effort budgets should inherit profile defaults.
- PR J merges last.

## PR A - Official Workflow Harness and Synthetic Demo

Branch:

```text
codex/v050-official-workflow
```

Primary files:

- `.claude/commands/generate-brief.md`
- `.opencode/commands/generate-brief.md`
- `docs/claude-code-workflow.md`
- `docs/claude-code-quickstart.md`
- `docs/windows-powershell.md`
- `examples/reference_workflow_demo/`
- `scripts/ci/smoke_reference_workflow.py`
- `tests/test_reference_workflow.py`
- `tests/test_cli.py`

Implementation:

- Add one public-safe `examples/reference_workflow_demo/` workspace.
- Include synthetic input files with enough facts to produce a non-empty brief, claim ledger, audit report, source map, run manifest, and optional DOCX.
- Add a smoke script that validates the official path without requiring API keys.
- Ensure the workflow runs with local/manual input only.
- Ensure `sources decide` is skipped or handled explicitly when the demo is local-input-only.
- Ensure `doctor` passes or produces actionable non-fatal warnings.
- Ensure `prepare` produces deterministic artifacts.
- Ensure `/generate-brief` documentation still calls the correct deterministic command.
- Define a single artifact contract for `brief.md`, `audited_brief.md`, `claim_ledger.json`, `audit_report.json`, `source_map.md`, and `run_manifest.json`.
- Add Windows PowerShell guidance for the same smoke path.

Acceptance tests:

- The synthetic demo runs without API keys.
- The smoke script exits non-zero if any expected artifact is missing.
- `brief.md` is reader-facing and does not contain `[src:`.
- `output/intermediate/audited_brief.md` retains valid `[src:CLAIM_ID]` citations.
- `claim_ledger.json` contains every cited claim from `audited_brief.md`.
- `run_manifest.json` lists all produced artifacts and hashes.
- Windows-compatible command examples are present.

Agent instruction:

```text
Create the v0.5 official reference workflow harness and a public-safe synthetic demo. Do not add new providers or model dependencies. The smoke path must run without API keys and must prove the artifact contract.
```

## PR B - Final Clean Gate

Branch:

```text
codex/v050-final-clean
```

Primary files:

- `src/multi_agent_brief/audit/final_quality.py`
- `src/multi_agent_brief/audit/rule_packs.py`
- `src/multi_agent_brief/agents/editor.py`
- `src/multi_agent_brief/agents/auditor.py`
- `src/multi_agent_brief/agents/formatter.py`
- `src/multi_agent_brief/core/schemas.py`
- `tests/test_final_quality_audit.py`
- `tests/test_editor_cleanup.py`
- `tests/test_b01_final_output_audit.py`
- new `tests/test_final_clean.py`

Implementation:

- Add a deterministic Final Clean pass that runs after Editor and before Formatter.
- Keep Final Clean separate from draft audit.
- Produce `output/intermediate/final_clean_report.json`.
- Block or flag:
  - invalid Claim IDs
  - empty source markers
  - raw `[src:CLAIM_ID]` in reader-facing `brief.md`
  - `[SRC:]`, `[SOURCE:]`, or similar process residue
  - template variables such as `{{...}}`, `${...}`, `<TODO>`, and unresolved placeholders
  - internal file paths and workspace paths
  - model/process phrases such as "as an AI", "agent should", "next run should", and workspace configuration notes
  - user feedback treated as a market fact
  - editorial comments treated as report conclusions
  - investment/trading recommendation wording
- Ensure the final audited Markdown remains citation-preserving.
- Ensure the reader-facing `brief.md` is generated by stripping citations from the audited final Markdown.
- Ensure `audit_report.json` records Final Clean status in metadata.
- Ensure fail/warning severities use the v0.4 AuditFinding taxonomy.

Acceptance tests:

- Final Clean catches internal process residue.
- Final Clean catches user feedback leakage.
- Final Clean catches invalid or empty citation markers.
- Reader-facing `brief.md` has no `[src:` markers.
- `audited_brief.md` retains valid citations and matches the text basis audited by the final auditor.
- Final Clean failure prevents "distribution-ready" status.
- Existing B01 final-output audit tests remain green.

Agent instruction:

```text
Implement Final Clean as a final delivery gate, not as a draft audit replacement. Preserve audited citations in intermediate Markdown, strip them only for reader-facing output, and fail on process residue or feedback contamination.
```

## PR C - Audience Profiles

Branch:

```text
codex/v050-audience-profiles
```

Primary files:

- new `src/multi_agent_brief/audience/`
- `src/multi_agent_brief/core/config.py`
- `src/multi_agent_brief/cli/init_wizard.py`
- `src/multi_agent_brief/onboarding/mapper.py`
- `src/multi_agent_brief/audit/final_quality.py`
- `configs/agent_roles.yaml`
- `.claude/agents/*.md`
- `.codex/agents/*.toml`
- `.opencode/agents/*.md`
- `tests/test_config_contract.py`
- `tests/test_init_from_onboarding.py`
- `tests/test_onboarding_mapper.py`
- new `tests/test_audience_profiles.py`

Implementation:

- Add Audience Profiles:
  - `management`
  - `research`
  - `ir`
  - `legal_compliance`
  - `default`
- Define profile fields:
  - `profile_id`
  - `display_name`
  - `required_sections`
  - `optional_sections`
  - `banned_phrases`
  - `required_disclaimers`
  - `min_markdown_chars`
  - `min_main_sections`
  - `summary_bullet_policy`
  - `citation_policy`
  - `docx_template`
  - `final_quality_thresholds`
  - `editorial_governance_thresholds`
- Map free-text onboarding audience values to profiles conservatively.
- Preserve the raw audience text in config/user-facing context.
- Let config override profile selection explicitly.
- Expose profile metadata in `run_manifest.json`.
- Update generated agent instructions from `configs/agent_roles.yaml` rather than editing generated files by hand.
- Keep profile defaults public-safe and industry-neutral.

Acceptance tests:

- Free-text "management", "board", "executive", and Chinese equivalents map to `management`.
- Free-text "research analyst", "industry research", and Chinese equivalents map to `research`.
- "IR" and "investor relations" map to `ir`.
- "legal", "compliance", "regulatory", and Chinese equivalents map to `legal_compliance`.
- Unknown audience maps to `default` without silently changing the user's raw text.
- Final quality config inherits profile thresholds.
- Generated agent configs remain synchronized.

Agent instruction:

```text
Add Audience Profiles as deterministic configuration, not as a prompt-only style switch. Preserve raw audience text, map to a conservative profile, and let final quality gates consume profile thresholds.
```

## PR D - DOCX Templates and Rendered Output Validation

Branch:

```text
codex/v050-docx-templates
```

Primary files:

- `src/multi_agent_brief/outputs/ib_docx.py`
- `src/multi_agent_brief/outputs/docx.py`
- new `src/multi_agent_brief/outputs/templates/`
- `src/multi_agent_brief/agents/formatter.py`
- `src/multi_agent_brief/audit/final_quality.py`
- `docs/harness_matrix.md`
- `tests/test_docx_output.py`
- `tests/test_formatter_docx_metadata.py`
- new `tests/test_docx_templates.py`
- new `tests/test_rendered_output_validation.py`

Implementation:

- Add three DOCX templates:
  - `executive_brief`
  - `research_note`
  - `formal_internal_report`
- Allow `output.docx_template` in config.
- Let Audience Profiles choose a default DOCX template.
- Keep the existing default template behavior for backwards compatibility.
- Validate:
  - heading mapping
  - bullet/list rendering
  - table rendering or wide-table conversion warnings
  - text depth
  - footer fields
  - named output copies
  - missing `python-docx` dependency behavior
- Produce `output/intermediate/rendered_output_report.json` when rendered validation is enabled.
- Do not hide substantive content issues by changing report content in the renderer.

Acceptance tests:

- Each template creates a non-empty DOCX.
- Each template preserves heading hierarchy.
- Wide tables produce a finding or are converted according to documented behavior.
- Named DOCX output uses the same configured stem as named Markdown.
- Missing `python-docx` is surfaced as `docx_validation_dependency_missing` in production validation.
- Markdown-only runs do not require `python-docx`.

Agent instruction:

```text
Add basic DOCX templates and deterministic rendered-output validation. Keep rendering checks separate from content editing, preserve Markdown-only operation, and surface missing dependencies clearly.
```

## PR E - Editorial Governance Rule Packs

Branch:

```text
codex/v050-editorial-governance
```

Primary files:

- `src/multi_agent_brief/audit/rule_packs.py`
- `src/multi_agent_brief/audit/final_quality.py`
- new `src/multi_agent_brief/audit/editorial_governance.py`
- `src/multi_agent_brief/core/schemas.py`
- `.agents/skills/editor/SKILL.md`
- `.agents/skills/auditor/SKILL.md`
- `tests/test_rule_packs.py`
- `tests/test_final_quality_audit.py`
- new `tests/test_editorial_governance.py`

Implementation:

- Add Editorial Governance checks:
  - factual density
  - generic management implication
  - unsupported business advice
  - comparable case without applicability
  - comparable case without limitations
  - historical analogy presented as current fact
  - missing actor/action/date/object in main observations
  - must-preserve fact removed after editing
- Use existing Claim Schema v2 fields where available:
  - `epistemic_type`
  - `evidence_relation`
  - `applicability_reason`
  - `limitations`
  - optional `fact_role`
- Keep event completeness out of scope. Do not require full `EventRecord` support.
- Add profile-aware thresholds from Audience Profiles.
- Store governance status in `audit_report.json` metadata.
- Add findings using v0.4 `blocking_level` and `repair_owner`.

Acceptance tests:

- Low factual density triggers warning or fail by profile.
- Unsupported business advice triggers a blocking finding.
- Comparable claims without applicability/limitations trigger findings.
- Historical analogy cannot be written as current-period fact.
- Editor removal of marked must-preserve facts triggers a finding.
- Quiet-week or sparse-source workflows can lower thresholds only when explicitly configured.

Agent instruction:

```text
Implement Editorial Governance as rule-pack driven final quality checks. Use Claim Schema v2 semantics, keep thresholds profile-aware, and do not build the full event completeness engine.
```

## PR F - Source Coverage Report and Research Gaps

Branch:

```text
codex/v050-source-coverage
```

Primary files:

- new `src/multi_agent_brief/sources/coverage.py`
- `src/multi_agent_brief/core/pipeline.py`
- `src/multi_agent_brief/agents/formatter.py`
- `src/multi_agent_brief/core/manifest.py`
- `src/multi_agent_brief/sources/base.py`
- `tests/test_source_collection.py`
- `tests/test_manifest.py`
- new `tests/test_source_coverage.py`

Implementation:

- Add `source_coverage_report.json`.
- Add `research_gaps.md`.
- Support configurable coverage dimensions:
  - `source_kind`
  - `source_tier`
  - `geography`
  - `language`
  - `platform`
  - `publisher_type`
  - `official_status`
  - `recency_bucket`
- Do not hard-code market/platform-specific dimensions into core logic.
- Let workspaces configure required or preferred coverage dimensions.
- Add source coverage summary to `run_manifest.json`.
- Ensure coverage gaps are not written into the formal reader-facing report body unless the Audience Profile explicitly wants a limitations section.
- Represent gaps as report metadata and `research_gaps.md`.

Acceptance tests:

- Coverage report is generated from manual/local sources.
- Missing optional dimensions are counted as `unknown`, not silently discarded.
- Required coverage gaps create audit findings or warnings by profile.
- `research_gaps.md` is generated when gaps exist.
- Reader-facing `brief.md` does not become a pipeline-improvement note.
- Manifest includes the coverage summary.

Agent instruction:

```text
Add configurable source coverage reporting and research gap separation. Keep dimensions generic and configurable; do not hard-code a specific country, platform, or industry into core support.
```

## PR G - Policy and Regulatory Risk Module

Branch:

```text
codex/v050-policy-regulatory-module
```

Primary files:

- new `src/multi_agent_brief/analysis_modules/policy_regulatory/`
- `src/multi_agent_brief/analysis_modules/registry.py`
- `src/multi_agent_brief/analysis_modules/base.py`
- `src/multi_agent_brief/core/schemas.py`
- `docs/modules/policy-regulatory.md`
- `docs/modules/policy-regulatory.zh-CN.md`
- `.agents/skills/`
- `configs/agent_roles.yaml`
- `tests/test_analysis_module_registry.py`
- new `tests/test_policy_regulatory_module.py`
- new `tests/test_policy_regulatory_audit.py`

Implementation:

- Add a second analysis module using the same AnalysisModule Registry pattern as Market & Competitor.
- Keep the module public-safe and generic.
- Produce intermediate artifacts:
  - `policy_events.json`
  - `risk_register.json`
  - `applicability_questions.json`
  - `policy_evidence_pack.json`
  - `policy_coverage_report.json`
- Suggested event fields:
  - `jurisdiction`
  - `authority`
  - `instrument_name`
  - `publication_date`
  - `effective_date`
  - `affected_entities`
  - `core_change`
  - `compliance_deadline`
  - `source_refs`
  - `limitations`
- Add module-specific audit checks:
  - official source missing
  - effective date missing
  - jurisdiction missing
  - applicability overclaim
  - compliance advice without basis
  - stale regulatory framing
- Use `HYPOTHESIS` or `TO_VERIFY` for unconfirmed applicability.
- Make clear this is not legal advice.

Acceptance tests:

- Module can be disabled with zero impact.
- Module runs on a synthetic policy fixture.
- Registry can load both Market & Competitor and Policy & Regulatory modules.
- Unsupported compliance advice creates a finding.
- Missing official source creates a warning or blocking finding by profile.
- Module output never states legal conclusions as deterministic advice.

Agent instruction:

```text
Add a generic Policy & Regulatory Risk Module as the second AnalysisModule implementation. Use public-safe fixtures, preserve module-disabled zero impact, and avoid legal advice.
```

## PR H - Minimal HistoryStore

Branch:

```text
codex/v050-history-store
```

Primary files:

- new `src/multi_agent_brief/history/`
- `src/multi_agent_brief/core/previous.py`
- `src/multi_agent_brief/core/config.py`
- `src/multi_agent_brief/core/pipeline.py`
- `src/multi_agent_brief/core/manifest.py`
- `tests/test_pipeline.py`
- new `tests/test_history_store.py`

Implementation:

- Add a minimal HistoryStore interface with file-backed storage.
- Support:
  - previous brief Markdown
  - previous claim ledger JSON
  - previous source map
  - entity history JSONL
  - run manifest history
- Do not add vector search.
- Do not add model-based memory.
- Add repeat/novelty metadata for candidate and claim selection.
- Ensure historical claims are never treated as current-period facts unless supported by current sources.
- Add `history_report.json` summarizing loaded history and repeat/novelty counts.
- Keep current `previous` behavior compatible.

Acceptance tests:

- HistoryStore loads previous brief and ledger from configured paths.
- Missing history paths produce warnings, not crashes.
- Previous claims can mark repeats/novelty.
- Historical context cannot satisfy current source support by itself.
- Manifest records history status and loaded file hashes.

Agent instruction:

```text
Add a minimal file-backed HistoryStore for previous briefs and claim ledgers. It supports repeat/novelty checks only; it must not become RAG or silently turn history into current facts.
```

## PR I - Effort Budgets

Branch:

```text
codex/v050-effort-budgets
```

Primary files:

- `src/multi_agent_brief/core/config.py`
- `src/multi_agent_brief/sources/decider.py`
- `src/multi_agent_brief/core/pipeline.py`
- `src/multi_agent_brief/audit/semantic.py`
- `src/multi_agent_brief/core/manifest.py`
- `docs/onboarding.md`
- `tests/test_config_contract.py`
- `tests/test_source_decider.py`
- `tests/test_manifest.py`
- new `tests/test_effort_budgets.py`

Implementation:

- Add budget levels:
  - `low`
  - `medium`
  - `high`
  - `xhigh`
- Budgets expand only into deterministic runtime limits:
  - max sources
  - max search tasks
  - max claims
  - max candidates
  - max analysis modules
  - source recency window
  - semantic audit mode
  - timeout hints
  - retry limits
- Do not implement model routing.
- Do not add provider-specific paid-tier behavior.
- Validate numeric values strictly:
  - reject negative values
  - preserve explicit zero only where zero is meaningful
  - avoid implicit conversion of `0` to defaults unless documented
- Record resolved budget settings in `run_manifest.json`.

Acceptance tests:

- Each budget resolves to deterministic limits.
- Invalid budget names fail clearly.
- Negative numeric values fail validation.
- `0` behavior is explicit and tested.
- Budgets do not enable new providers by themselves.
- Manifest records the resolved budget.

Agent instruction:

```text
Implement effort budgets as deterministic runtime limits only. Do not add model routing, paid-provider behavior, or hidden provider enablement.
```

## PR J - v0.5.0 Release Readiness

Branch:

```text
codex/v050-release-readiness
```

Primary files:

- `pyproject.toml`
- `src/multi_agent_brief/__init__.py`
- `CHANGELOG.md`
- `README.md`
- `README_en.md`
- `docs/roadmap.md`
- `docs/roadmap.zh-CN.md`
- `docs/impl-plan-v0.5.0.md`
- `.github/workflows/tests.yml`
- `scripts/check_release_consistency.py`
- `tests/test_release_consistency.py`

Implementation:

- Update version to `0.5.0`.
- Add a `CHANGELOG.md` entry summarizing v0.5.0.
- Update README current-version text.
- Update roadmap status without making v0.6 or v1.0 look current.
- Run generated agent config checks if agent instructions changed.
- Run release consistency gate in non-release mode.
- Keep tag checks behind release mode.
- Add a v0.5 smoke checklist to docs.

Acceptance tests:

- `python scripts/check_release_consistency.py` passes.
- `python scripts/generate_agent_configs.py --check` passes if agent configs are touched.
- Full test suite passes on Linux/macOS/Windows CI.
- README, README_en, CHANGELOG, pyproject, and `__version__` agree.
- Experimental capabilities are labeled as Experimental or Interface Only.

Agent instruction:

```text
Prepare v0.5.0 release readiness after all feature PRs merge. Update version docs consistently, run release consistency checks, and do not mark deferred capabilities as supported.
```

## Integration Milestones

Milestone 1: workflow spine

- Merge PR A.
- The official local reference workflow runs without API keys.
- Artifacts are stable and documented.

Milestone 2: final delivery quality

- Merge PR B, PR C, and PR D.
- Final Clean, Audience Profiles, and DOCX validation work on the reference demo.
- Reader-facing output is clean while audited intermediate output remains traceable.

Milestone 3: editorial governance

- Merge PR E and PR F.
- Reports have fact-density checks, comparable-case checks, and source coverage reports.
- Research gaps are separated from formal business prose.

Milestone 4: module and memory baseline

- Merge PR G and PR H.
- Two different Analysis Modules use the same Registry.
- History supports repeat/novelty checks without becoming RAG.

Milestone 5: run controls and release

- Merge PR I and PR J.
- Effort budgets are deterministic and manifest-visible.
- Version and docs are synchronized.

## Common Test Commands

All agents should run:

```bash
python -m pytest -q
git diff --check
```

When touching generated agent configs:

```bash
python scripts/generate_agent_configs.py --check
```

When touching capability docs or catalog:

```bash
python scripts/check_capabilities.py
```

When touching version or release docs:

```bash
python scripts/check_release_consistency.py
```

Reference workflow smoke:

```bash
python scripts/ci/smoke_reference_workflow.py
```

Windows PowerShell equivalent:

```powershell
python -m pytest -q
python scripts/check_release_consistency.py
python scripts/ci/smoke_reference_workflow.py
```

## Definition of Done for v0.5.0

- A new user can follow README and complete the official workflow.
- The public-safe reference demo runs without API keys.
- Final Clean blocks reader-facing process residue, feedback contamination, invalid citations, and internal path leaks.
- Audience Profiles affect final quality gates and DOCX defaults deterministically.
- DOCX templates render non-empty documents and surface validation failures.
- Editorial Governance catches low factual density, unsupported business advice, and weak comparable-case framing.
- Source coverage and research gaps are reported outside the formal business prose.
- Policy & Regulatory Risk Module works through the shared AnalysisModule Registry.
- HistoryStore supports previous brief/ledger repeat checks without becoming RAG.
- Effort Budgets resolve to explicit runtime limits and are recorded in the manifest.
- README, README_en, CHANGELOG, roadmap, version files, and agent configs do not drift.
- Full CI passes on Linux, macOS, and Windows.

## One-Line Agent Directive

In v0.5.0, build the production reference workflow. Freeze one official path, enforce Final Clean and Audience Profile quality gates, validate DOCX output, add editorial governance and source coverage reports, add one policy/regulatory module, and keep HistoryStore and effort budgets minimal. Do not add new providers, full event completeness, RAG, model routing, or MAS Runtime.
