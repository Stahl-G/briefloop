"""Paired reward for agent-rollout evaluation.

    R = defect_recall * true_negative_rate

The product, not the mean.  A role that blocks everything drives the second
term to zero; a role that passes everything drives the first to zero.  This
is what makes the red line "Precision-only quality gate ... can reward
omission" structurally satisfied rather than merely promised.

POLARITY: R measures DETECTION only, over the four-type auditor detection
vocabulary (``FINDING_TYPES``).  Generation quality -- the finalize-family
types in ``GENERATION_DEFECT_TYPES``, where FEWER findings is better -- is a
separate metric with inverted polarity and is deliberately not folded into
R; it has no corpus representation yet and is future work.

Scoring is a pure function of ``(cases, outcomes)``: nothing about the role
or its measured performance changes corpus composition or the measurements
taken from it.

Measurement rules:

* ``defect_recall`` is computed over every seeded defect, blocking or warning.
  A seeded defect counts as detected iff the case's outcome carries a
  ``ReportedFinding`` with the same ``finding_type`` AND the same ``locator``
  (double match).  A ``blocking_level`` disagreement (the role reports a
  warning where the truth expects blocking) still counts as detection; that
  disagreement is captured by ``block_agreement``, which is reported but never
  enters ``R``.
* ``true_negative_rate`` is computed over the ``clean_claims`` of non-blocking
  cases only (cases whose derived ``must_block`` is False -- warning-only and
  defect-free cases).  A clean claim counts as flagged iff any reported
  finding's locator equals it -- regardless of that finding's type: a false
  positive is a false positive whatever type vocabulary it was filed under.
  Noncompliant findings carry no locator at all (they never entered
  ``findings``), so they cannot flag a clean claim.
* ``format_compliance`` is the aggregate share of reported findings that are
  vocabulary-legal AND anchored:
  ``sum(len(outcome.findings)) / sum(len(outcome.findings)
  + outcome.noncompliant_finding_count)``.  An empty denominator yields 1.0.
  It is reported in ``CorpusScore`` but NEVER enters ``R``.
* An empty denominator yields 1.0: a split with zero seeded defects or zero
  in-scope clean claims must not crash or zero the product.
* Every case must have exactly one outcome.  Duplicate outcomes, outcomes
  whose case id is not in the split, and cases without an outcome all raise
  ``ValueError``.

Design choice: matches are computed from ``RolloutOutcome.findings`` and
``blocked`` only.  ``found_defect_ids`` and ``flagged_claim_locators`` are
adapter-provided convenience views over the same findings; this module
deliberately does not read them, so a score can never drift from the raw
record the adapter produced.
"""

from __future__ import annotations

from typing import Iterable, Sequence

from multi_agent_brief.evaluation_v2.contracts import (
    CorpusScore,
    EvaluationCase,
    RolloutOutcome,
)


def _index_outcomes(outcomes: Iterable[RolloutOutcome]) -> dict[str, RolloutOutcome]:
    indexed: dict[str, RolloutOutcome] = {}
    for outcome in outcomes:
        if outcome.case_id in indexed:
            raise ValueError(f"duplicate rollout outcome for case {outcome.case_id}")
        indexed[outcome.case_id] = outcome
    return indexed


def score_corpus(
    cases: Sequence[EvaluationCase],
    outcomes: Iterable[RolloutOutcome],
) -> CorpusScore:
    """Score one corpus split.  Every case must have exactly one outcome."""
    indexed = _index_outcomes(outcomes)

    known_case_ids = {case.case_id for case in cases}
    for case_id in indexed:
        if case_id not in known_case_ids:
            raise ValueError(f"unknown rollout outcome for case {case_id}")

    seeded_total = 0
    seeded_detected = 0
    clean_total = 0
    clean_flagged = 0
    block_matches = 0
    compliant_findings = 0
    reported_findings = 0

    for case in cases:
        outcome = indexed.get(case.case_id)
        if outcome is None:
            raise ValueError(f"missing rollout outcome for case {case.case_id}")

        reported_pairs = {(finding.finding_type, finding.locator) for finding in outcome.findings}
        seeded_total += len(case.seeded_defects)
        seeded_detected += sum(
            1
            for defect in case.seeded_defects
            if (defect.finding_type, defect.locator) in reported_pairs
        )

        # TNR scope: clean claims of non-blocking cases only.  The flag test
        # is locator-equality alone -- deliberately blind to finding_type.
        if not case.must_block:
            reported_locators = {finding.locator for finding in outcome.findings}
            clean_total += len(case.clean_claims)
            clean_flagged += sum(
                1 for locator in case.clean_claims if locator in reported_locators
            )

        if outcome.blocked is case.must_block:
            block_matches += 1

        compliant_findings += len(outcome.findings)
        reported_findings += len(outcome.findings) + outcome.noncompliant_finding_count

    defect_recall = 1.0 if seeded_total == 0 else seeded_detected / seeded_total
    true_negative_rate = (
        1.0 if clean_total == 0 else 1.0 - clean_flagged / clean_total
    )
    block_agreement = 1.0 if not cases else block_matches / len(cases)
    format_compliance = 1.0 if reported_findings == 0 else compliant_findings / reported_findings

    return CorpusScore(
        defect_recall=defect_recall,
        true_negative_rate=true_negative_rate,
        reward=defect_recall * true_negative_rate,
        seeded_total=seeded_total,
        seeded_detected=seeded_detected,
        clean_total=clean_total,
        clean_flagged=clean_flagged,
        block_agreement=block_agreement,
        format_compliance=format_compliance,
        case_count=len(cases),
    )
