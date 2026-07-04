# BriefLoop Skill Version Matrix

Skill contract version: `briefloop-operator-skill-v0.1.2`
Last verified against BriefLoop runtime: `v0.11.9`
Public project name: BriefLoop
Historical implementation name: MABW

## Supported Current Surfaces

- CLI: `multi-agent-brief`
- Shell CLI alias: `briefloop`
- Claude writer command: `/briefloop`
- Compatibility Claude command: `/mabw`
- BriefLoop skill is an agent protocol surface, not the `/briefloop` slash
  command implementation.
- Python package/module path: `multi_agent_brief`
- Distribution package name: `multi-agent-brief-workflow`
- Assessment targets:
  - `delivery_brief`
  - `auditable_brief`
- Experimental optional artifacts:
  - Atomic Claim Graph: `atomic_claim_graph.json`
  - Evidence Span Registry: `evidence_span_registry.json`
  - Claim-Support Matrix: `claim_support_matrix.json`
    - schema and vocabulary validation
    - cross-artifact reference validation
    - read-only status projection and quality-gate findings from explicit rows
  - Semantic Assessment Report: `semantic_assessment_report.json`
    - schema and reference validation
    - proposal-only Claim-Support Matrix delta projection
    - read-only status visibility
- Quality-gate surfaces:
  - `coverage_omission` detects selected high-priority screened candidates that
    do not carry into Claim Ledger metadata or, for auditable briefs, cited
    internal `[src:<claim_id>]` references unless an explicit limitation /
    omission reason exists
  - reader-facing finalize checks do not require delivery Markdown to retain
    internal Claim Ledger reference markers
  - `final_abstract_quality` surfaces deterministic final-abstract risk
    patterns as warning-only findings and feeds normal Quality Panel / Quality
    Summary warning counts
  - this is selected-item continuity only, not full-world recall, semantic
    support proof, prose-quality scoring, release authority, or
    source-discovery completeness
- Experimental product-layer contracts:
  - `multi-agent-brief packs list`
  - `multi-agent-brief packs show <pack_id>`
  - `multi-agent-brief packs templates`
  - `multi-agent-brief packs bundle --workspace <workspace>`
  - `multi-agent-brief validate-report-spec <report_spec.yaml>`
  - `multi-agent-brief extract --workspace <workspace> --scope <text>
    --source <file>` for `evidence_extract` source/scope registration and
    deterministic source-lock / logical-page seed / text-span seed registry
    generation for copied source files and UTF-8 text sources; PDF/binary
    sources can use already-present adjacent MinerU-derived `.mineru.md` text
    representations, with original source bytes still bound in source lock
  - `multi-agent-brief sources add-file <path>`
  - `multi-agent-brief sources add-rss <url>`
  - `multi-agent-brief sources add-web-search --query <text>`
  - `multi-agent-brief new <pack> <workspace>` / `briefloop new <pack> <workspace>`
    for conservative local-first workspace skeletons
  - product-facing ReportPack entries:
    - `industry-weekly` -> canonical ReportPack `market_weekly`
    - `management-monthly` -> canonical ReportPack `management_monthly`
    - `document-review` -> canonical ReportPack `evidence_extract`
    - `solar-periodic` -> canonical ReportPack `solar_industry_periodic`
  - packaged ReportPacks: `market_weekly`, `management_monthly`,
    `solar_industry_periodic`, `evidence_extract`
  - packaged ReportTemplates: `market_weekly`, `management_monthly`,
    `solar_industry_periodic`, `evidence_extract`
  - packaged PolicyProfiles: `manufacturing_default`,
    `solar_manufacturing_default`, `evidence_extract_default`,
    `finance_default`, `internet_default`
  - ReportPack default policy profile binding and optional ReportSpec
    `policy_profile` override validation
  - `briefloop new` / `multi-agent-brief new` deterministic `--industry`
    resolver that writes the selected profile and resolution source into
    `report_spec.yaml`, with explicit `--policy-profile` override
  - resolved PolicyProfile projection in `validate-report-spec`, read-only
    status, and generated handoff artifacts
  - resolved ReportTemplate section-order projection in read-only status and
    generated handoff artifacts
  - read-only ReportTemplate section-conformance diagnostics in status and
    generated handoff artifacts for existing audited/final reader Markdown
  - Reader Template Conformance v1:
    - packaged ReportTemplates may declare `reader_contract` fields for
      required reader blocks, Markdown table slots, executive-summary length,
      and Source Appendix position
    - status, handoff, `finalize_report.json`, and Quality Panel can surface
      `report_template_conformance` with `reader_block_warnings`
    - warning-only projection; no gate execution, delivery block, rewrite,
      DOCX parsing, quality score, release authority, or semantic proof
  - Citation Profile Split:
    - packaged ReportTemplates may declare
      `reader_contract.citation_profile`
    - allowed profiles: `executive`, `analyst`, `audit`
    - `finalize_report.json` and `report_bundle_manifest.json` record the
      resolved citation profile, source, reader citation style, and audit trace
      level
    - reader delivery stays on reader-safe source labels and must not expose
      Claim Ledger IDs, span IDs, local paths, or hashes
    - audit bundles retain trace artifacts when present
    - citation-surface metadata only; no support proof, gate relaxation, audit
      trace removal, delivery approval, release authority, or quality score
  - limited PolicyProfile deterministic gate adapter for existing gate
    strictness and reader-final forbidden-phrase checks
  - SourceHub Lite setup for local text files, RSS feed registration, and
    runtime web-search handoff tasks
  - internal release-mode approval records:
    - `multi-agent-brief approval init`
    - `multi-agent-brief approval record`
    - `multi-agent-brief release check`
    - artifacts: `human_approval_ledger.json`,
      `release_readiness_report.json`
    - event-log linkage is required before approval records are trusted
    - `release_readiness_report.json` includes configured `branding_context`
      metadata when `release.branding` is present, and required institution
      branding authorization context can block internal readiness
    - internal review readiness only; no public release authority
  - Quality Panel / Summary / static HTML projections:
    - `briefloop quality summarize --workspace <workspace>`
    - artifacts: `quality_panel.json`, `quality_summary.md`,
      `quality_panel.html`
    - `quality_panel_closeout` in finalize/status is a post-finalize
      recommendation to run `briefloop quality summarize --workspace
      <workspace>`, not an automatic writer
    - quality summary and HTML are SHA-bound projections of
      `quality_panel.json`
    - audit bundle inclusion is allowed when present and valid; delivery bundle
      inclusion is not
    - projection only; no gate execution, quality score, repair, delivery
      approval, or release authority
  - Trajectory Regulation read-only projection:
    - surfaced through `multi-agent-brief status --workspace <workspace>
      --json` and Quality Panel recommended actions
    - reads existing `workflow_state.json` and `event_log.jsonl`
    - summarizes retry-stage events, repair starts/completions, repeated
      blockers, and exhausted attempt budgets
    - may suggest `request_human_review` or `block_run` for the operator
    - projection only; no workflow-state write, repair execution, gate
      execution, delivery approval, release readiness decision, or quality
      score
  - Guidance Manifestation diagnostic projection:
    - artifact: `guidance_manifestation_report.json`
    - surfaced through `multi-agent-brief status --workspace <workspace>
      --json` and Quality Panel
    - allowed labels: `explicitly_reflected`, `partially_reflected`,
      `contradicted`, `not_observable`
    - labels are human/imported diagnostics for approved guidance entries
      already materialized into the current run
    - Python validates and counts labels; it does not judge manifestation,
      mutate Improvement Memory, approve guidance, run gates, approve delivery,
      decide release readiness, or claim output-quality improvement
  - Materiality Selection diagnostic projection:
    - surfaced through `multi-agent-brief status --workspace <workspace>
      --json` and Quality Panel
    - reads valid `screened_candidates.json`, resolved PolicyProfile
      `materiality_terms`, and workspace focus terms
    - surfaces excluded/deprioritized candidates with capacity/scope reason
      codes that match explicit materiality or focus terms
    - may suggest `request_human_review` or
      `review_materiality_exclusions` for the operator
    - deterministic keyword diagnostics only; no semantic-importance judgment,
      screening mutation, candidate resurrection, Claim Ledger mutation, gate
      authority, delivery approval, release readiness decision, or quality
      score
  - Support-Calibrated Wording warning projection:
    - surfaced through `multi-agent-brief status --workspace <workspace>
      --json` and Quality Panel as `support_wording`
    - reads existing reader Markdown, Claim Ledger metadata, source taxonomy,
      and valid Claim-Support Matrix policy signals when present
    - surfaces warning-only risks such as weak support with strong wording,
      inference without framing, unsupported claims in reader text, and
      media/report-style source classes written with strong unattributed wording
    - deterministic lexical projection only; no claim-truth judgment,
      support-row generation or acceptance, gate execution, delivery block,
      release authority, or quality score
  - Packaged synthetic eval fixtures include a trajectory retry-budget case
    that proves repeated retry decisions narrow
    `workflow_state.next_allowed_decisions` to `request_human_review` and
    `block_run` without adding decision vocabulary, executing repair, running
    gates, approving delivery, or deciding release readiness, plus a guidance
    manifestation `not_observable` case that keeps the result diagnostic-only.
  - v0.11.0 product-baseline readiness guard:
    - `scripts/check_product_baseline.py`
    - release consistency runs the baseline guard before release prep
    - verifies stable product entries, README boundary wording,
      `README_en.md` compatibility-pointer shape, and forbidden public
      overclaims
  - no binary/PDF span extraction from `extract`, no stage execution from Product OS commands, publication approval,
    web-search execution, section-conformance gate, gate bypass, semantic support
    assessment, semantic truth proof, or second gate engine
- MABW-080 experiment operations:
  - `validate-case`
  - `scaffold-condition`
  - `register-run`
  - `score-run`
  - `export-blind-pack`
  - `import-assessment`
  - `summarize`

## Compatibility Rules

- Do not rename runtime surfaces unless the task is explicitly a compatibility
  migration.
- Keep `multi-agent-brief`, `briefloop`, `/briefloop`, and `/mabw` as
  compatibility surfaces unless the task is explicitly a breaking migration.
- Do not describe deferred semantic-governance surfaces or v0.10 Product OS
  roadmap goals as completed unless the support matrix and current CLI expose
  the exact surface.
- BriefLoop-090 is a future readiness/fresh-rerun label, not a current CLI namespace or supported command surface. Current experiment commands remain under `multi-agent-brief experiments 080`.
- If runtime behavior conflicts with this skill, prefer:
  - `docs/architecture-status.md`
  - `docs/support-matrix.md`
  - current CLI help
  - the workspace's generated runtime handoff

## Planned / Not Yet Authoritative

These are roadmap directions unless current code, tests, and support matrix say
otherwise:

- Finding Candidate System
- Release Eligibility Scorecard
- semantic support scoring
- support-sufficiency gates
- human adjudication queues
- semantic regression harnesses
