"""Semantic support proposal adjudication CLI."""

from __future__ import annotations

import argparse
import json
from typing import Any

from multi_agent_brief.orchestrator.runtime_state.errors import RuntimeStateError
from multi_agent_brief.orchestrator.runtime_state.semantic_support_acceptance import (
    record_semantic_support_adjudication,
)


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "semantic-support",
        help="Record human adjudication for Semantic Support Auditor proposals.",
    )
    actions = parser.add_subparsers(dest="semantic_support_action", required=True)

    adjudicate = actions.add_parser(
        "adjudicate",
        help="Record a human accept/reject decision for one semantic support proposal.",
    )
    adjudicate.add_argument("--workspace", required=True, help="Path to workspace directory.")
    adjudicate.add_argument("--proposal-id", required=True, help="Semantic assessment proposal row id.")
    adjudicate.add_argument(
        "--decision",
        required=True,
        choices=("accept", "reject"),
        help="Human decision for this proposal.",
    )
    adjudicate.add_argument("--reason", required=True, help="Short human rationale.")
    adjudicate.add_argument("--by", default="human", help="Human/operator label.")
    adjudicate.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")


def handle(args: argparse.Namespace) -> int:
    action = getattr(args, "semantic_support_action", "")
    try:
        if action == "adjudicate":
            payload = record_semantic_support_adjudication(
                workspace=args.workspace,
                proposal_id=args.proposal_id,
                decision=args.decision,
                reason=args.reason,
                actor_id=getattr(args, "by", "human"),
            )
            _print_payload("semantic-support adjudicate", payload, as_json=getattr(args, "json", False))
            return 0
    except (RuntimeStateError, OSError, json.JSONDecodeError) as exc:
        payload = exc.to_dict() if isinstance(exc, RuntimeStateError) else {"ok": False, "error": str(exc)}
        _print_payload("semantic-support", payload, as_json=getattr(args, "json", False))
        return 1
    raise AssertionError(f"Unhandled semantic-support action: {action}")


def _print_payload(label: str, payload: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return
    print(label)
    for key, value in payload.items():
        if isinstance(value, (dict, list)):
            print(f"{key}: {json.dumps(value, ensure_ascii=False, sort_keys=True)}")
        else:
            print(f"{key}: {value}")
