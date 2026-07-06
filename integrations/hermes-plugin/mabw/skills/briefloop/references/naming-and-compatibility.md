# Naming And Compatibility

Read this when the task touches BriefLoop/MABW naming, public links, release
notes, or compatibility claims.

## Current Names

- Public project name: BriefLoop
- Historical implementation lineage: MABW
- Public CLI: `briefloop`; compatibility CLI: `multi-agent-brief`
- Claude command: `/briefloop`; deprecated compatibility command: `/mabw`
- Python module: `multi_agent_brief`
- Package/distribution: `briefloop`
- Experiment namespace: MABW-080
- Public README bodies:
  - `README.md` is the canonical English README.
  - `README.zh-CN.md` is the canonical Chinese README.
  - `README_en.md` is only a short compatibility pointer to `README.md`.
- Product-facing ReportPack entries:
  - `industry-weekly` -> internal/canonical `market_weekly`
  - `management-monthly` -> internal/canonical `management_monthly`
  - `document-review` -> internal/canonical `evidence_extract`
  - `solar-periodic` -> internal/canonical `solar_industry_periodic`

## Compatibility Rules

- Do not rename runtime surfaces by accident.
- Do not change workspace artifact names for public framing only.
- Do not write product-facing aliases into control artifacts that require
  canonical ids unless the implementation explicitly supports that alias layer.
- When public docs say BriefLoop, keep compatibility notes for
  `briefloop`, `multi-agent-brief`, `/briefloop`, `/mabw`, package/module
  paths, and MABW experiment IDs.
- Do not present `/generate-brief` as a recommended first-user writer path.
  It is the supported Claude delegated stage-workflow command when a generated
  `briefloop run --runtime claude` handoff or advanced Claude operation tells
  the operator to continue stage execution. `/briefloop run` creates or
  refreshes handoff files only; it does not execute specialists or complete
  stages.
- Keep README canonicalization intact. `README_en.md` should remain a pointer,
  not a third long-form README body.
- BriefLoop-090 is an archived experiment/readiness label; v0.9.0 is a semver
  release. `experiments 090` is not a current CLI namespace. Do not conflate
  them.

## Current Authority

Use `docs/briefloop-naming.md`, `docs/architecture-status.md`, and
`docs/support-matrix.md` for current public compatibility wording.
