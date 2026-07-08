# Public Claim Boundaries

Read this before writing README text, release notes, HN/GitHub posts, demos,
research summaries, or public experiment conclusions.

## Allowed Claim Shapes

- BriefLoop records where claims entered the workflow.
- BriefLoop provides deterministic gates, ledgers, events, repair transactions,
  and archived control records.
- MABW-080 scorecards can record deterministic readiness fields and imported
  human or llm-assisted-human assessment. BriefLoop-090 reference runs may use
  this archived tooling only when a fresh experiment is explicitly run and
  documented.
- A run or experiment observed a specific pattern under stated controls.

## Forbidden Claim Shapes

Do not say:

- BriefLoop proves truth.
- BriefLoop eliminates hallucinations.
- BriefLoop makes reports automatically ready to send.
- Improvement Memory improves output quality as a general fact.
- Python judged prose quality, semantic manifestation, or factual regression.
- Planned support-sufficiency structures are implemented.

## Required Boundary Language

Use wording like:

- "traceability, not semantic proof"
- "measurement infrastructure"
- "imported external assessment"
- "pilot-level observation"
- "formal denominator excludes invalid or unbound runs"
- "not a management-ready delivery claim" for `auditable_brief`

## RC-Phase Wording

While `docs/v1-pilot-evidence.md` reports `not_satisfied`, allowed:

- "BriefLoop is in v1.0 release-candidate hardening."
- "We are validating the full first-user workflow and failure/recovery paths
  before calling it v1.0."

Forbidden while the gate is unsatisfied:

- "BriefLoop v1.0 is ready."
- "The WorkBuddy/CodeBuddy first-user path is stable."
- "BriefLoop can safely recover contaminated runs." (supersede recovery
  preserves contamination and keeps the run non-reference-eligible; it is not
  a clean-recovery claim)
