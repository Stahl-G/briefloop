"""Read-only CLI access to registered contract schemas and examples."""

from __future__ import annotations

import argparse
import json

from multi_agent_brief.contracts import SchemaRegistry, V2_CONTRACT_IDS


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "contract",
        help="Inspect strict versioned BriefLoop contracts.",
    )
    actions = parser.add_subparsers(dest="contract_action", required=True)
    show = actions.add_parser("show", help="Show a v2 contract schema or valid example.")
    show.add_argument("contract_id", choices=V2_CONTRACT_IDS)
    output = show.add_mutually_exclusive_group(required=True)
    output.add_argument("--schema", action="store_true", help="Print JSON Schema.")
    output.add_argument(
        "--example",
        choices=("minimal", "full"),
        help="Print an embedded valid example.",
    )


def handle(args: argparse.Namespace) -> int:
    if args.contract_action != "show":
        return 1
    if args.schema:
        payload = SchemaRegistry.json_schema(args.contract_id)
    else:
        payload = SchemaRegistry.example(args.contract_id, args.example)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0
