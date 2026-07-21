"""Human approval ledger CLI commands."""

from __future__ import annotations

import argparse


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
