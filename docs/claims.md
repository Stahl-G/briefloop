# What BriefLoop Claims — And What It Does Not

Nothing on this page is new: every statement already appears in `README.md`,
`docs/15-minute-pilot.md`, `docs/red-lines-and-anti-patterns.md`,
`docs/architecture-status.md`, or `docs/support-matrix.md`. This page collects
those boundaries in one place without softening them.

## What BriefLoop Is Not

BriefLoop is not:

- a semantic proof engine;
- an automatic truth checker;
- a replacement for human review;
- a report publisher or delivery approval system;
- evidence that output quality improved.

In short, BriefLoop is not a semantic proof engine, not an automatic truth
checker, and not a replacement for human review.

It is also not a learning system: even approved guidance has no later-run
effect until a Human explicitly starts a compatible successor with reuse
enabled, and no experiment reports that this reuse improves output. Provenance
projection is not semantic proof: it records citation and control
relationships, and semantic support still needs audit or human review.

## Evidence Boundary

The Claim Ledger, quality gates, audits, and Quality Panel attest
traceability and structural control: claims keep stable identities and freeze
records, gates write deterministic findings that can block unsafe continue or
finalize decisions, and panels summarize control integrity from existing
artifacts. `Supported` means deterministic commands, contracts, and regression
tests are present, not output-quality validation. These surfaces do not prove
semantic truth, create a quality score, prove full recall, or measure utility.

## What Is Not Measured

- Defect detection by agent roles (recall and false-flag rate on seeded-defect corpora): MEASURED on the synthetic 40-case val corpus via real codex rollouts — recall 1.000 (24/24 seeded defects, all three runs); true-negative rate 0.896–0.958 (mean reward 0.931, spread 0.063); see `docs/evaluation-results/first-reward.md`. Measurement variance still exceeds the 2.5-point gating threshold, so no reward gate is enabled yet.
- Guidance reuse utility: approval alone has no later-run effect, and whether
  explicit successor reuse improves output is NOT MEASURED.
- Experimental Tavily acquisition and end-to-end performance: live usefulness,
  reliability, cost, coverage, success rate, and acquisition-to-`finalized_local`
  performance are NOT MEASURED.
- Advisory LAJ evaluator utility and efficacy: NOT MEASURED.

## Supported Versus Experimental

`docs/support-matrix.md` is the authoritative capability table. Experimental
surfaces are functional but may change without notice and are not production
guarantees. Check that table before relying on any capability.
