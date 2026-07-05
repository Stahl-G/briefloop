# BriefLoop Operator Skill Changelog

## briefloop-operator-skill-v0.1.12 — 2026-07-05

- Reclassified MABW-080 / BriefLoop-090 as archived experimental measurement
  surfaces for reference-run reproduction, scorecard audit, and explicit
  experiment work only.
- Clarified that `experiments 080` must not be used as a normal product
  workspace path, WorkBuddy first-user flow, or launch claim.

## briefloop-operator-skill-v0.1.11 — 2026-07-03

- Clarified the Evidence Extract MinerU-derived Markdown bridge:
  `briefloop extract` can bind an already-present adjacent `.mineru.md`
  representation for PDF/binary sources, while the original source bytes remain
  the root source-lock object.
- Reiterated that `briefloop extract` does not run MinerU automatically, parse
  PDFs by itself, perform rendered-page visual inspection, judge support,
  generate CSM rows, or approve delivery/release.

## briefloop-operator-skill-v0.1.10 — 2026-07-03

- Added Evidence Extract source-lock guidance for
  `output/intermediate/evidence_extract_source_lock.json`,
  `output/intermediate/evidence_extract_page_inventory.json`, and their audit
  copies.
- Clarified that `briefloop extract` records scope, copied source bytes,
  source-lock hashes, UTF-8 logical-page seeds, and UTF-8 text-span seeds only;
  it does not parse binary documents, render pages for visual inspection,
  extract tables/figures, judge semantic support, generate CSM rows, or approve
  delivery/release.

## briefloop-operator-skill-v0.1.9 — 2026-07-02

- Updated Trajectory Regulation guidance from read-only recommendation to
  deterministic current-stage decision narrowing: exhausted retry,
  repair-cycle, or repeated-blocker budgets can narrow
  `workflow_state.next_allowed_decisions` to `request_human_review` and
  `block_run`.
- Clarified that trajectory narrowing records control state and event-log
  evidence only; it does not execute repair, run gates, approve delivery,
  decide release readiness, or let Python perform agent work.

## briefloop-operator-skill-v0.1.8 — 2026-07-02

- Added Citation Profile Split guidance for `reader_contract.citation_profile`
  values (`executive`, `analyst`, `audit`) and the corresponding
  `finalize_report.json` / bundle manifest fields.
- Clarified that citation profiles split reader-safe source labels from audit
  trace retention only; they do not prove support, relax gates, remove audit
  trace, approve delivery, or decide release readiness.

## briefloop-operator-skill-v0.1.7 — 2026-07-01

- Added Support-Calibrated Wording guidance for warning-only
  `support_wording` diagnostics over reader Markdown, Claim Ledger metadata,
  source taxonomy, and valid Claim-Support Matrix policy signals.
- Clarified that Support-Calibrated Wording is deterministic lexical projection
  only; it does not judge claim truth, generate or accept support rows, run
  gates, block delivery, approve release, or create a quality score.

## briefloop-operator-skill-v0.1.6 — 2026-07-01

- Added Reader Template Conformance v1 guidance for `reader_contract`
  diagnostics over finalized reader Markdown.
- Clarified that `report_template_conformance` may surface
  `reader_block_warnings` through status, handoff, `finalize_report.json`, and
  Quality Panel, but remains warning-only and does not rewrite content, parse
  DOCX, run gates, block delivery, approve release, score prose quality, or
  prove semantic correctness.

## briefloop-operator-skill-v0.1.5 — 2026-07-01

- Added Materiality Selection diagnostic guidance for status / Quality Panel
  projections over excluded or deprioritized screened candidates that match
  explicit PolicyProfile materiality terms or workspace focus terms.
- Clarified that Materiality Selection is deterministic keyword diagnostics
  only; it does not judge semantic importance, mutate screening output,
  resurrect candidates, alter the Claim Ledger, run gates, approve delivery,
  decide release readiness, or score quality.

## briefloop-operator-skill-v0.1.4 — 2026-07-01

- Added Guidance Manifestation diagnostic guidance for
  `guidance_manifestation_report.json`, including the allowed labels
  `explicitly_reflected`, `partially_reflected`, `contradicted`, and
  `not_observable`.
- Clarified that manifestation labels are human/imported diagnostics surfaced
  through status / Quality Panel only; they do not mutate Improvement Memory,
  approve guidance, create a quality score, run gates, approve delivery, or
  decide release readiness.

## briefloop-operator-skill-v0.1.3 — 2026-07-01

- Added Trajectory Regulation operator guidance: status / Quality Panel can
  surface read-only retry, repair-cycle, and repeated-blocker projections from
  `workflow_state.json` and `event_log.jsonl`.
- Clarified that trajectory recommendations may suggest `request_human_review`
  or `block_run`, but do not write workflow state, execute repair, run gates,
  approve delivery, decide release readiness, or claim output quality.

## briefloop-operator-skill-v0.1.2 — 2026-06-30

- Added coverage/omission gate guidance for selected screened-candidate
  continuity from `screened_candidates.json` to Claim Ledger metadata and cited
  brief references, with explicit no-full-recall / no-semantic-proof boundary.
- Added Quality Panel / Quality Summary / static HTML operator guidance,
  including `briefloop quality summarize`, SHA-bound summary/HTML projections,
  and audit-bundle-only boundaries.
- Added internal release-mode approval guidance for `approval init`,
  `approval record`, `release check`, event-log linkage, and the distinction
  between internal readiness and public release authority.
- Added v0.11 product-baseline readiness checks to the repo-development
  validation checklist.
- Documented product-facing ReportPack entry aliases while preserving canonical
  internal pack ids in control artifacts.
- Documented README canonicalization: `README.md` and `README.zh-CN.md` are the
  long-form public README bodies; `README_en.md` is a compatibility pointer.
- Added new Python-owned projection/control artifacts to the control-record map:
  `quality_panel.json`, `quality_summary.md`, `quality_panel.html`,
  `human_approval_ledger.json`, and `release_readiness_report.json`.

## briefloop-operator-skill-v0.1.1 — 2026-06-19

- Clarified that MABW-080 is the current experiment command surface.
- Clarified that BriefLoop-090 is a future readiness/fresh-rerun label, not a
  current CLI namespace.

## briefloop-operator-skill-v0.1 — 2026-06-19

- Added canonical repo-local BriefLoop operator protocol skill.
- Added mode classifier for runtime workspace, 080/090 experiment,
  repo-development, and public-claims work.
- Added auditable_brief vs delivery_brief operating boundaries.
- Added repair, gates/status, public-claim, and naming compatibility references.
- Added red lines against direct frozen-artifact edits, prompt-only control, and
  output-quality overclaims.
