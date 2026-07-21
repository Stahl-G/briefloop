"""Semantic support proposal adjudication CLI."""

from __future__ import annotations

import argparse


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "semantic-support",
        help="Record human adjudication for Semantic Support Auditor proposals.",
    )
    actions = parser.add_subparsers(dest="semantic_support_action", required=True)

    bind = actions.add_parser(
        "bind",
        help="Seal semantic_assessment_report.json checked_inputs once before human adjudication.",
    )
    bind.add_argument("--workspace", required=True, help="Path to workspace directory.")
    bind.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")

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
