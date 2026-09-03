"""Experimental eval command: validate corpora, run splits once adapters land.

``validate`` is fully functional today: it loads a corpus, prints its factual
composition (total, per-split counts, blocking counts, per-finding-type
coverage), and enforces the production thresholds.  The packaged skeleton
corpus deliberately fails it -- reporting a reward-shaped verdict over an
empty corpus would be a public claim exceeding the artifacts.

``run`` fails closed: no rollout backend exists yet, and a placeholder reward
is never emitted.  ``_build_rollout`` is the single seam the codex rollout
adapter (later rollout task) plugs into; until that module exists the command
refuses before touching the corpus.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
import sys

from multi_agent_brief.evaluation_v2.contracts import FINDING_TYPES
from multi_agent_brief.evaluation_v2.corpus import (
    DEFAULT_CORPUS,
    SPLITS,
    Corpus,
    CorpusError,
    load_corpus,
    validate_corpus,
)
from multi_agent_brief.evaluation_v2.runner import RolloutFn, run_split

NO_ROLLOUT_ADAPTER_MESSAGE = (
    "no rollout adapter is available yet; the codex adapter lands with the "
    "rollout task"
)


class RolloutAdapterUnavailable(RuntimeError):
    """Raised when ``eval run`` has no rollout backend to drive."""


def register(subparsers: argparse._SubParsersAction) -> None:
    """Register the eval subparser."""
    parser = subparsers.add_parser(
        "eval",
        help="Experimental: score agent rollouts against a seeded-defect corpus.",
    )
    actions = parser.add_subparsers(dest="eval_action", required=True)

    validate_parser = actions.add_parser(
        "validate",
        help="Check corpus invariants at production thresholds; no rollouts.",
    )
    validate_parser.add_argument(
        "--corpus",
        default=str(DEFAULT_CORPUS),
        help="Path to a corpus manifest. Defaults to the packaged corpus.",
    )

    run_parser = actions.add_parser(
        "run",
        help="Run one split through a rollout adapter and report R.",
    )
    run_parser.add_argument(
        "--corpus",
        default=str(DEFAULT_CORPUS),
        help="Path to a corpus manifest. Defaults to the packaged corpus.",
    )
    run_parser.add_argument(
        "--split",
        required=True,
        choices=SPLITS,
        help="Corpus split to run.",
    )
    run_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit the score as machine-readable JSON.",
    )


def handle(args: argparse.Namespace) -> int:
    """Dispatch eval sub-actions."""
    if args.eval_action == "validate":
        return _handle_validate(args)
    if args.eval_action == "run":
        return _handle_run(args)
    return 1


def _handle_validate(args: argparse.Namespace) -> int:
    try:
        corpus = load_corpus(Path(args.corpus))
    except (CorpusError, ValueError) as exc:
        print(f"corpus invalid: {exc}", file=sys.stderr)
        return 1

    # Report the factual composition before the verdict: an invalid corpus
    # (the packaged skeleton today) still says what it contains, so the
    # failure is loud and quantified rather than a bare refusal.
    _print_summary(args.corpus, corpus)
    try:
        validate_corpus(corpus)
    except (CorpusError, ValueError) as exc:
        print(f"corpus invalid: {exc}", file=sys.stderr)
        return 1
    print("result: valid (production thresholds)")
    return 0


def _handle_run(args: argparse.Namespace) -> int:
    # Resolve the adapter first: while no rollout backend exists, refuse
    # before reading anything, so no partial work or placeholder score can
    # ever be produced.
    try:
        rollout = _build_rollout()
    except RolloutAdapterUnavailable as exc:
        print(f"eval run unavailable: {exc}", file=sys.stderr)
        return 1

    try:
        corpus = load_corpus(Path(args.corpus))
        validate_corpus(corpus)
    except (CorpusError, ValueError) as exc:
        print(f"corpus invalid: {exc}", file=sys.stderr)
        return 1

    result = run_split(corpus, args.split, rollout)
    score = result.score
    if args.json_output:
        payload = score.model_dump()
        payload["split"] = result.split
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"split            {result.split}")
        print(f"cases            {score.case_count}")
        print(
            f"seeded_defects   {score.seeded_total} (detected {score.seeded_detected})"
        )
        print(
            f"clean_claims     {score.clean_total} (flagged {score.clean_flagged})"
        )
        print(f"defect_recall    {score.defect_recall:.4f}")
        print(f"true_neg_rate    {score.true_negative_rate:.4f}")
        print(f"block_agreement  {score.block_agreement:.4f}")
        print(f"format_compliance {score.format_compliance:.4f}")
        print(f"R                {score.reward:.4f}")
    return 0


def _build_rollout() -> RolloutFn:
    """Single seam where the production rollout adapter plugs in.

    The codex adapter module lands with the rollout task; until it exists
    this raises ``RolloutAdapterUnavailable`` so ``eval run`` fails closed
    instead of reporting a fabricated reward.
    """
    try:
        from multi_agent_brief.evaluation_v2.codex_rollout import (
            build_codex_rollout,
        )
    except ImportError:
        raise RolloutAdapterUnavailable(NO_ROLLOUT_ADAPTER_MESSAGE) from None
    return build_codex_rollout()


def _print_summary(corpus_path: str, corpus: Corpus) -> None:
    """Print the compact composition summary validate reports against."""
    total = len(corpus.cases)
    blocking_total = sum(1 for case in corpus.cases if case.must_block)
    print(f"corpus: {corpus_path}")
    print(f"cases: {total}")
    for split in SPLITS:
        selected = corpus.select(split)
        blocking = sum(1 for case in selected if case.must_block)
        print(f"split {split}: {len(selected)} cases, {blocking} blocking")
    ratio = blocking_total / total if total else 0.0
    print(f"blocking ratio: {ratio:.2f}")
    counts = Counter(
        defect.finding_type
        for case in corpus.cases
        for defect in case.seeded_defects
    )
    for finding_type in sorted(FINDING_TYPES):
        print(f"finding {finding_type}: {counts.get(finding_type, 0)}")
