#!/usr/bin/env python3
"""Fail-closed reward gate for the B'' evaluation loop.

Usage:
    python3 scripts/check_reward_gate.py --candidate 0.62 [--ledger PATH] [--min-margin 0.0]

Exit codes:
    0  candidate strictly exceeds best recorded val reward + margin
    1  refused: no measurement exists, the ledger is malformed, or the
       candidate does not clear the bar

The gate NEVER invents a decision without a measurement: an empty or
missing ledger is a refusal, not a pass.  ``--min-margin`` exists because
"strictly greater than" freezes into noise-chasing when the retest spread
of the metric is unknown; set the margin from the measured variance
(first recorded in docs/evaluation-results/first-reward.md) before gating
anything that matters.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from multi_agent_brief.evaluation_v2.reward_ledger import (  # noqa: E402
    DEFAULT_LEDGER_PATH,
    best_reward,
    load_records,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--candidate",
        type=float,
        required=True,
        help="candidate val reward to gate (0..1)",
    )
    parser.add_argument(
        "--ledger",
        type=Path,
        default=DEFAULT_LEDGER_PATH,
        help=f"reward ledger path (default: {DEFAULT_LEDGER_PATH})",
    )
    parser.add_argument(
        "--min-margin",
        type=float,
        default=0.0,
        help="minimum improvement over the best recorded reward (default: 0)",
    )
    parser.add_argument(
        "--split", default="val", choices=["train", "val"], help="split to gate on"
    )
    args = parser.parse_args(argv)

    if not 0.0 <= args.candidate <= 1.0:
        print(f"[reward-gate] candidate {args.candidate} outside [0, 1]", file=sys.stderr)
        return 1
    if args.min_margin < 0:
        print("[reward-gate] --min-margin must be >= 0", file=sys.stderr)
        return 1

    try:
        records = load_records(args.ledger)
    except ValueError as exc:
        print(f"[reward-gate] {exc}", file=sys.stderr)
        return 1

    best = best_reward(records, split=args.split)
    if best is None:
        print(
            f"[reward-gate] no measured {args.split} reward in {args.ledger}; "
            "refusing to gate — measure first",
            file=sys.stderr,
        )
        return 1

    bar = best + args.min_margin
    verdict = "PASS" if args.candidate > bar else "FAIL"
    print(
        f"[reward-gate] {verdict}: candidate {args.candidate:.4f} vs best "
        f"{best:.4f} + margin {args.min_margin:.4f} (bar {bar:.4f}, "
        f"{len(records)} records, split {args.split})"
    )
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
