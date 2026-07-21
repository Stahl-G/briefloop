"""Provenance projection CLI commands."""

from __future__ import annotations

import argparse


def register(subparsers: argparse._SubParsersAction) -> None:
    provenance_parser = subparsers.add_parser(
        "provenance",
        help="Build and inspect deterministic workspace provenance projections.",
    )
    actions = provenance_parser.add_subparsers(dest="provenance_action", required=True)

    build_parser = actions.add_parser(
        "build",
        help="Build output/intermediate/provenance_graph.json from existing control files.",
    )
    build_parser.add_argument("--workspace", required=True, help="Path to workspace directory.")
    build_parser.add_argument("--repo-workdir", help="Repository or packaged contract base.")
    build_parser.add_argument("--strict", action="store_true", help="Fail when provenance warnings exist.")
    build_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")

    show_parser = actions.add_parser(
        "show",
        help="Show provenance graph summary.",
    )
    show_parser.add_argument("--workspace", required=True, help="Path to workspace directory.")
    show_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")

    validate_parser = actions.add_parser(
        "validate",
        help="Validate output/intermediate/provenance_graph.json.",
    )
    validate_parser.add_argument("--workspace", required=True, help="Path to workspace directory.")
    validate_parser.add_argument("--strict", action="store_true", help="Fail when provenance warnings exist.")
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
