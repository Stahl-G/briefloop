"""Hidden fresh-v2 intake CLI; not connected to active runtime adapters."""

from __future__ import annotations

import argparse
import json

from multi_agent_brief.intake_v2.errors import IntakeError, IntakeResult
from multi_agent_brief.intake_v2.service import IntakeService


_LANES = ("source", "candidate", "screened", "claim-drafts", "audit")


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "intake-v2",
        help="Internal fresh-v2 intake; not the active runtime path.",
        description="Internal fresh-v2 intake; not the active runtime path.",
    )
    lanes = parser.add_subparsers(dest="intake_v2_lane", required=True)
    for lane in _LANES:
        lane_parser = lanes.add_parser(lane)
        lane_parser.add_argument("--workspace", required=True)
        lane_parser.add_argument("--request", required=True)
        lane_parser.add_argument(
            "--json",
            action="store_true",
            required=True,
            help="Emit exactly one machine-readable result object.",
        )


def handle(args: argparse.Namespace) -> int:
    try:
        service = IntakeService(args.workspace)
        if args.intake_v2_lane == "source":
            result = service.submit_source(args.request)
        else:
            result = service.submit_proposal(args.intake_v2_lane, args.request)
    except IntakeError as exc:
        result = IntakeResult(
            status="failed_uncommitted",
            error_code=exc.code,
        )
    print(json.dumps(result.to_dict(), sort_keys=True, separators=(",", ":")))
    return result.exit_code


__all__ = ["handle", "register"]
