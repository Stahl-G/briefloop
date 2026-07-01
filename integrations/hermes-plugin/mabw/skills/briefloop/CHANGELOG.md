# BriefLoop Operator Skill Changelog

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
