"""Human approval ledger CLI commands."""

from __future__ import annotations

import argparse
import json
from typing import Any

from multi_agent_brief.orchestrator.runtime_state.errors import RuntimeStateError
from multi_agent_brief.product.release_approval import (
    ReleaseApprovalError,
    initialize_approval_ledger,
    record_human_approval,
    release_modes_payload,
)


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "approval",
        help="Record human approval decisions for internal release modes.",
    )
    actions = parser.add_subparsers(dest="approval_action", required=True)

    modes_parser = actions.add_parser("modes", help="List supported internal release modes.")
    modes_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")

    init_parser = actions.add_parser("init", help="Initialize the human approval ledger for a mode.")
    init_parser.add_argument("--workspace", required=True, help="Path to workspace directory.")
    init_parser.add_argument("--mode", required=True, help="Release mode, for example ir_draft.")
    init_parser.add_argument("--by", default="human", help="Human/operator label for the initialization.")
    init_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")

    record_parser = actions.add_parser("record", help="Append a human approval decision.")
    record_parser.add_argument("--workspace", required=True, help="Path to workspace directory.")
    record_parser.add_argument("--mode", help="Release mode. Required when multiple modes are initialized.")
    record_parser.add_argument("--role", required=True, help="Approval role, for example evidence_reviewer.")
    record_parser.add_argument(
        "--decision",
        required=True,
        choices=["approve", "reject", "request_changes"],
        help="Human decision for this role.",
    )
    record_parser.add_argument("--reason", required=True, help="Short human rationale.")
    record_parser.add_argument("--by", default="human", help="Human/operator label.")
    record_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")


def handle(args: argparse.Namespace) -> int:
    action = getattr(args, "approval_action", "")
    try:
        if action == "modes":
            payload = release_modes_payload()
            _print_payload("approval modes", payload, as_json=getattr(args, "json", False))
            return 0
        if action == "init":
            result = initialize_approval_ledger(
                workspace=args.workspace,
                mode=args.mode,
                actor=getattr(args, "by", "human"),
            )
            payload = {
                "ok": True,
                "mode": args.mode,
                "ledger_path": "output/intermediate/human_approval_ledger.json",
                "event_id": result.event.get("event_id") if result.event else "",
                "boundary": "internal_review_approval_records_only_not_public_release_authorization",
            }
            _print_payload("approval init", payload, as_json=getattr(args, "json", False))
            return 0
        if action == "record":
            result = record_human_approval(
                workspace=args.workspace,
                mode=getattr(args, "mode", None),
                role=args.role,
                decision=args.decision,
                reason=args.reason,
                actor_id=getattr(args, "by", "human"),
            )
            payload = {
                "ok": True,
                "mode": getattr(args, "mode", None) or "resolved_from_ledger",
                "role": args.role,
                "decision": args.decision,
                "ledger_path": "output/intermediate/human_approval_ledger.json",
                "event_id": result.event.get("event_id") if result.event else "",
                "boundary": "internal_review_approval_records_only_not_public_release_authorization",
            }
            _print_payload("approval record", payload, as_json=getattr(args, "json", False))
            return 0
    except (ReleaseApprovalError, RuntimeStateError, OSError, json.JSONDecodeError) as exc:
        payload = {"ok": False, "error": str(exc)}
        _print_payload("approval", payload, as_json=getattr(args, "json", False))
        return 1
    raise AssertionError(f"Unhandled approval action: {action}")


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
