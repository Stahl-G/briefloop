"""Release readiness check CLI commands."""

from __future__ import annotations

import argparse
import json
from typing import Any

from multi_agent_brief.orchestrator.runtime_state.errors import RuntimeStateError
from multi_agent_brief.product.release_approval import (
    ReleaseApprovalError,
    check_release_readiness,
)


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "release",
        help="Check internal review readiness without authorizing public release.",
    )
    actions = parser.add_subparsers(dest="release_action", required=True)
    check_parser = actions.add_parser("check", help="Write a release readiness report.")
    check_parser.add_argument("--workspace", required=True, help="Path to workspace directory.")
    check_parser.add_argument("--mode", required=True, help="Release mode, for example ir_draft.")
    check_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")


def handle(args: argparse.Namespace) -> int:
    action = getattr(args, "release_action", "")
    try:
        if action == "check":
            result = check_release_readiness(workspace=args.workspace, mode=args.mode)
            payload = dict(result.payload)
            payload["ok"] = payload.get("status") == "pass"
            payload["report_path"] = "output/intermediate/release_readiness_report.json"
            _print_payload("release check", payload, as_json=getattr(args, "json", False))
            return 0 if payload["ok"] else 1
    except (ReleaseApprovalError, RuntimeStateError, OSError, json.JSONDecodeError) as exc:
        payload = {"ok": False, "error": str(exc)}
        _print_payload("release check", payload, as_json=getattr(args, "json", False))
        return 1
    raise AssertionError(f"Unhandled release action: {action}")


def _print_payload(label: str, payload: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return
    print(label)
    for key in (
        "ok",
        "mode",
        "status",
        "approval_required",
        "missing_roles",
        "blockers",
        "authorization",
        "next_step",
        "report_path",
    ):
        if key not in payload:
            continue
        value = payload[key]
        if isinstance(value, (dict, list)):
            print(f"{key}: {json.dumps(value, ensure_ascii=False, sort_keys=True)}")
        else:
            print(f"{key}: {value}")
