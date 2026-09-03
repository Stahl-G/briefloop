"""Split orchestration for agent-rollout evaluation.

The rollout callable is injected (``RolloutFn``) so orchestration is a pure
function of ``(corpus, rollout)`` and is fully testable offline, without
model credentials.  The real Codex adapter arrives with Phase 2 and plugs in
here unchanged; anything performance-dependent -- retries, timeouts,
concurrency, randomness -- belongs to that adapter, never to this module.

Failure rules:

* An empty split is a hard ``ValueError``: running nothing must never
  silently score nothing.
* An outcome whose ``case_id`` does not name the case it was given is a hard
  ``ValueError`` naming both ids, raised on the spot so a misrouted rollout
  cannot contaminate the score.
* An unknown split name surfaces as ``CorpusError`` from ``corpus.select``:
  split-name validation is owned by the corpus layer, not duplicated here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, TypeAlias

from multi_agent_brief.evaluation_v2.contracts import (
    CorpusScore,
    EvaluationCase,
    RolloutOutcome,
)
from multi_agent_brief.evaluation_v2.corpus import Corpus
from multi_agent_brief.evaluation_v2.scoring import score_corpus

RolloutFn: TypeAlias = Callable[[EvaluationCase], RolloutOutcome]


@dataclass(frozen=True)
class SplitResult:
    """One scored split plus the per-case outcomes it was computed from."""

    split: str
    outcomes: tuple[RolloutOutcome, ...]
    score: CorpusScore


def run_split(corpus: Corpus, split: str, rollout: RolloutFn) -> SplitResult:
    """Run every case in ``split``, in corpus order, and score the results.

    Exactly one rollout call per selected case; the collected outcomes are
    passed untouched to ``score_corpus`` and returned alongside the score, so
    a result can always be audited down to the raw records it was computed
    from.
    """
    cases = corpus.select(split)
    if not cases:
        raise ValueError(f"split {split!r} has no cases")

    outcomes: list[RolloutOutcome] = []
    for case in cases:
        outcome = rollout(case)
        if outcome.case_id != case.case_id:
            raise ValueError(
                f"rollout for {case.case_id!r} returned outcome for "
                f"{outcome.case_id!r}"
            )
        outcomes.append(outcome)

    return SplitResult(
        split=split,
        outcomes=tuple(outcomes),
        score=score_corpus(cases, outcomes),
    )
