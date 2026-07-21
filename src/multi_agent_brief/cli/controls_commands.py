"""Orchestrator control switchboard CLI commands."""

from __future__ import annotations

import argparse


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
    """Fail-closed stub for the retired public CLI surface.

    The parser registration is retained so the authority guard can return
    the typed rejection for workspace invocations; any no-workspace bypass
    lands here instead of executing legacy code.
    """

    print("runtime_command_unsupported")
    return 1

# NOTE: the public command surface of this module is retired. The
# SQLite ControlStore is the sole runtime authority; only the parser
# registration (typed rejections) and the stub below remain.
