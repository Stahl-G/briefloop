# BriefLoop Skill Version Matrix

Skill contract version: `briefloop-operator-skill-v0.1.2`
Last verified against BriefLoop runtime: `v0.10.7`
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
- Experimental product-layer contracts:
  - `multi-agent-brief packs list`
  - `multi-agent-brief packs show <pack_id>`
  - `multi-agent-brief packs templates`
  - `multi-agent-brief packs bundle --workspace <workspace>`
  - `multi-agent-brief validate-report-spec <report_spec.yaml>`
  - `multi-agent-brief extract --workspace <workspace> --scope <text>
    --source <file>` for `evidence_extract` source/scope registration and
    deterministic text-span seed registry generation for UTF-8 text sources
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
    - internal review readiness only; no public release authority
  - Quality Panel / Summary / static HTML projections:
    - `briefloop quality summarize --workspace <workspace>`
    - artifacts: `quality_panel.json`, `quality_summary.md`,
      `quality_panel.html`
    - quality summary and HTML are SHA-bound projections of
      `quality_panel.json`
    - audit bundle inclusion is allowed when present and valid; delivery bundle
      inclusion is not
    - projection only; no gate execution, quality score, repair, delivery
      approval, or release authority
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
