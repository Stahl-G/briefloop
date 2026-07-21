"""Improvement Ledger CLI commands."""

from __future__ import annotations

import argparse


def register(subparsers: argparse._SubParsersAction) -> None:
    improve_parser = subparsers.add_parser(
        "improve",
        help="Manage the append-only Improvement Ledger.",
    )
    actions = improve_parser.add_subparsers(dest="improve_action", required=True)

    propose_parser = actions.add_parser(
        "propose",
        help="Append a proposed audience-guidance improvement.",
    )
    _add_workspace(propose_parser)
    propose_parser.add_argument("--guidance", required=True, help="Bounded audience guidance text.")
    propose_parser.add_argument("--category", required=True, help="Audience guidance category.")
    propose_parser.add_argument("--scope", required=True, help="Audience guidance scope.")
    propose_parser.add_argument("--source-summary", help="Required for explicit human proposals.")
    propose_parser.add_argument("--from-issue", help="Feedback issue id to freeze as source evidence.")
    propose_parser.add_argument("--supersedes", help="Approved materializable entry id replaced by this proposal.")
    _add_json(propose_parser)

    list_parser = actions.add_parser(
        "list",
        help="List current Improvement Ledger entries.",
    )
    _add_workspace(list_parser)
    list_parser.add_argument("--status", help="Filter by current status.")
    _add_json(list_parser)

    show_parser = actions.add_parser(
        "show",
        help="Show one Improvement Ledger entry and its revisions.",
    )
    _add_workspace(show_parser)
    show_parser.add_argument("--entry-id", required=True, help="Improvement entry id, such as AG-0001.")
    _add_json(show_parser)

    approve_parser = actions.add_parser(
        "approve",
        help="Approve a proposed Improvement Ledger entry without applying it.",
    )
    _add_workspace(approve_parser)
    approve_parser.add_argument("--entry-id", required=True, help="Improvement entry id.")
    approve_parser.add_argument("--by", required=True, help="Operator id approving this entry.")
    _add_json(approve_parser)

    reject_parser = actions.add_parser(
        "reject",
        help="Reject a proposed Improvement Ledger entry.",
    )
    _add_workspace(reject_parser)
    reject_parser.add_argument("--entry-id", required=True, help="Improvement entry id.")
    reject_parser.add_argument("--by", required=True, help="Operator id rejecting this entry.")
    reject_parser.add_argument("--reason", required=True, help="Short rejection reason.")
    _add_json(reject_parser)

    revert_parser = actions.add_parser(
        "revert",
        help="Revert an approved Improvement Ledger entry.",
    )
    _add_workspace(revert_parser)
    revert_parser.add_argument("--entry-id", required=True, help="Improvement entry id.")
    revert_parser.add_argument("--by", required=True, help="Operator id reverting this entry.")
    revert_parser.add_argument("--reason", required=True, help="Short revert reason.")
    _add_json(revert_parser)

    stats_parser = actions.add_parser(
        "stats",
        help="Summarize ledger-only Improvement counts.",
    )
    _add_workspace(stats_parser)
    _add_json(stats_parser)

    validate_parser = actions.add_parser(
        "validate",
        help="Validate the Improvement Ledger without writing files.",
    )
    _add_workspace(validate_parser)
    _add_json(validate_parser)

    rebuild_parser = actions.add_parser(
        "rebuild",
        help="Rebuild deterministic improvement/memory.md without touching runtime state.",
    )
    _add_workspace(rebuild_parser)
    _add_json(rebuild_parser)



def _add_workspace(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--workspace", required=True, help="Path to workspace directory.")



def _add_json(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")


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
