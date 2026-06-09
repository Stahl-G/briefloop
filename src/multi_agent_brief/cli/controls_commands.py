"""Orchestrator control switchboard CLI commands."""

from __future__ import annotations

import argparse
import json
from typing import Any

from multi_agent_brief.controls.contract import ControlSwitchboardError
from multi_agent_brief.controls.switchboard import (
    build_control_switchboard,
    select_control,
    show_control_switchboard,
    validate_control_switchboard,
)
from multi_agent_brief.orchestrator.runtime_state import RuntimeStateError


def register(subparsers: argparse._SubParsersAction) -> None:
    controls_parser = subparsers.add_parser(
        "controls",
        help="Build and inspect the Orchestrator control switchboard.",
    )
    actions = controls_parser.add_subparsers(dest="controls_action", required=True)

    build_parser = actions.add_parser(
        "build-switchboard",
        help="Build output/intermediate/orchestrator_control_switchboard.json.",
    )
    build_parser.add_argument("--workspace", required=True, help="Path to workspace directory.")
    build_parser.add_argument("--repo-workdir", help="Repository or packaged contract base.")
    build_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")

    show_parser = actions.add_parser(
        "show",
        help="Show switchboard and control selections.",
    )
    show_parser.add_argument("--workspace", required=True, help="Path to workspace directory.")
    show_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")

    select_parser = actions.add_parser(
        "select",
        help="Record an Orchestrator control selection without executing it.",
    )
    select_parser.add_argument("--workspace", required=True, help="Path to workspace directory.")
    select_parser.add_argument("--control", required=True, help="Control id.")
    select_parser.add_argument("--selection", required=True, choices=["enable", "defer", "reject"])
    select_parser.add_argument("--reason", required=True, help="Reason for the selection.")
    select_parser.add_argument("--approved-by-human", action="store_true", help="Record explicit human approval.")
    select_parser.add_argument("--human-approval-ref", help="Human approval reference.")
    select_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")

    validate_parser = actions.add_parser(
        "validate",
        help="Validate switchboard and control selections.",
    )
    validate_parser.add_argument("--workspace", required=True, help="Path to workspace directory.")
    validate_parser.add_argument("--strict", action="store_true", help="Fail when required controls lack selections.")
    validate_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")


def handle(args: argparse.Namespace) -> int:
    try:
        if args.controls_action == "build-switchboard":
            state = build_control_switchboard(
                workspace=args.workspace,
                repo_workdir=getattr(args, "repo_workdir", None),
                actor="cli",
            )
            _print_state("controls build-switchboard", state, as_json=getattr(args, "json", False))
            return 0 if state.get("ok") else 1

        if args.controls_action == "show":
            state = show_control_switchboard(workspace=args.workspace)
            _print_state("controls show", state, as_json=getattr(args, "json", False))
            return 0 if state.get("ok") else 1

        if args.controls_action == "select":
            state = select_control(
                workspace=args.workspace,
                control_id=args.control,
                selection=args.selection,
                reason=args.reason,
                approved_by_human=getattr(args, "approved_by_human", False),
                human_approval_ref=getattr(args, "human_approval_ref", None),
                actor="orchestrator",
            )
            _print_state("controls select", state, as_json=getattr(args, "json", False))
            return 0 if state.get("ok") else 1

        if args.controls_action == "validate":
            result = validate_control_switchboard(
                workspace=args.workspace,
                strict=getattr(args, "strict", False),
                actor="cli",
            )
            if getattr(args, "json", False):
                print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
            else:
                _print_validation(result)
            return 0 if result.get("ok") else 1
    except (ControlSwitchboardError, RuntimeStateError) as exc:
        _print_error(exc, as_json=getattr(args, "json", False))
        return 1
    return 1


def _print_state(label: str, state: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True))
        return
    validation = state.get("validation") or {}
    print(f"[{label}] ok: {state.get('ok')}")
    print(f"[{label}] switchboard: {state.get('control_switchboard_path')}")
    print(f"[{label}] selections: {state.get('control_selections_path')}")
    print(f"[{label}] controls: {validation.get('control_count', 0)}")
    print(f"[{label}] selections recorded: {validation.get('selection_count', 0)}")
    for error in validation.get("errors") or []:
        print(f"  - {error}")


def _print_validation(result: dict[str, Any]) -> None:
    print(f"[controls validate] ok: {result.get('ok')}")
    print(f"[controls validate] switchboard_present: {result.get('switchboard_present')}")
    print(f"[controls validate] selection_present: {result.get('selection_present')}")
    print(f"[controls validate] controls: {result.get('control_count', 0)}")
    print(f"[controls validate] selections: {result.get('selection_count', 0)}")
    for error in result.get("errors") or []:
        print(f"  - {error}")


def _print_error(exc: Exception, *, as_json: bool) -> None:
    payload = exc.to_dict() if hasattr(exc, "to_dict") else {"ok": False, "error": str(exc)}
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"[controls] {exc}")
