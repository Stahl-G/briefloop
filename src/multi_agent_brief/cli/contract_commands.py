"""Read-only CLI access to registered contract schemas and examples."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from multi_agent_brief.contracts import SchemaRegistry, V2_CONTRACT_IDS
from multi_agent_brief.contracts.json import (
    StrictJsonError,
    parse_strict_json_object,
)


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
    validate = actions.add_parser(
        "validate",
        help="Validate one JSON object without writing runtime state.",
    )
    validate.add_argument("contract_id", choices=V2_CONTRACT_IDS)
    validate.add_argument(
        "--input",
        required=True,
        type=Path,
        help="JSON file to validate against the selected strict contract.",
    )


def handle(args: argparse.Namespace) -> int:
    if args.contract_action == "show":
        if args.schema:
            payload = SchemaRegistry.json_schema(args.contract_id)
        else:
            payload = SchemaRegistry.example(args.contract_id, args.example)
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.contract_action != "validate":
        return 1
    try:
        payload = parse_strict_json_object(args.input.read_bytes())
    except OSError:
        result = {
            "schema_id": args.contract_id,
            "status": "invalid",
            "reason_code": "contract_input_unavailable",
            "violations": [],
        }
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 1
    except (StrictJsonError, TypeError, ValueError):
        result = {
            "schema_id": args.contract_id,
            "status": "invalid",
            "reason_code": "contract_input_invalid",
            "violations": [],
        }
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 1
    violations = SchemaRegistry.validate(args.contract_id, payload)
    errors = [item for item in violations if item.severity == "error"]
    result = {
        "schema_id": args.contract_id,
        "status": "invalid" if errors else "valid",
        "reason_code": "contract_validation_failed" if errors else None,
        "violations": [
            {
                "field": item.field,
                "reason": item.error,
                "severity": item.severity,
            }
            for item in violations
        ],
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 1 if errors else 0
