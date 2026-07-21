"""Quality-gate CLI commands."""

from __future__ import annotations

import argparse


def register(subparsers: argparse._SubParsersAction) -> None:
    gates_parser = subparsers.add_parser(
        "gates",
        help="Run deterministic quality gates and inspect stage-scoped quality gate reports.",
    )
    actions = gates_parser.add_subparsers(dest="gates_action", required=True)

    check_parser = actions.add_parser(
        "check",
        help="Run material-fact, freshness, target-relevance, coverage/omission, and editor-new-fact gates.",
    )
    check_parser.add_argument("--workspace", required=True, help="Path to workspace directory.")
    check_parser.add_argument(
        "--brief",
        help="Brief path. Defaults to output/intermediate/audited_brief.md, or output/brief.md for --stage finalize.",
    )
    check_parser.add_argument("--ledger", help="Claim Ledger path. Defaults to output/intermediate/claim_ledger.json.")
    check_parser.add_argument("--report-date", default="", help="Report date, e.g. 2026-06-08.")
    check_parser.add_argument("--max-source-age-days", type=int, help="Maximum current-source age in days.")
    check_parser.add_argument(
        "--stage",
        help="Gate stage id. Defaults to auditor for audited_brief.md and finalize for output/brief.md.",
    )
    check_parser.add_argument(
        "--strict",
        action="store_true",
        help="Escalate high-severity freshness/material/editor-new-fact warnings into blocking findings.",
    )
    check_parser.add_argument("--repo-workdir", help="Repository or packaged contract base.")
    check_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")

    show_parser = actions.add_parser(
        "show",
        help="Show quality gate report state, including stage-scoped reports and legacy projection.",
    )
    show_parser.add_argument("--workspace", required=True, help="Path to workspace directory.")
    show_parser.add_argument("--repo-workdir", help="Repository or packaged contract base.")
    show_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")

    validate_parser = actions.add_parser(
        "validate",
        help="Validate stage-scoped quality gate reports.",
    )
    validate_parser.add_argument("--workspace", required=True, help="Path to workspace directory.")
    validate_parser.add_argument("--repo-workdir", help="Repository or packaged contract base.")
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
