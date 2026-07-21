"""Repair routing CLI commands."""

from __future__ import annotations

import argparse


def register(subparsers: argparse._SubParsersAction) -> None:
    repair_parser = subparsers.add_parser(
        "repair",
        help="Route and execute deterministic owner-stage repair transactions.",
    )
    actions = repair_parser.add_subparsers(dest="repair_action", required=True)
    route_parser = actions.add_parser(
        "route",
        help="Show allowed repair owner/artifacts for the current workspace issue.",
    )
    route_parser.add_argument("--workspace", required=True, help="Path to workspace directory.")
    route_parser.add_argument(
        "--route-index",
        type=int,
        help="0-based route index to inspect explicitly.",
    )
    route_parser.add_argument(
        "--finding-id",
        help="Finding ID to inspect explicitly.",
    )
    route_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    start_parser = actions.add_parser(
        "start",
        help="Start the deterministic owner-stage repair transaction for the current route.",
    )
    start_parser.add_argument("--workspace", required=True, help="Path to workspace directory.")
    start_parser.add_argument(
        "--repo-workdir",
        help="Repository or packaged contract base (default: auto-detect).",
    )
    start_parser.add_argument(
        "--actor",
        default="orchestrator",
        choices=("cli", "orchestrator", "runtime", "system"),
        help="Actor recorded in event_log.jsonl.",
    )
    start_parser.add_argument(
        "--route-index",
        type=int,
        help="0-based route index from `repair route --json` to start explicitly.",
    )
    start_parser.add_argument(
        "--finding-id",
        help="Finding ID from `repair route --json` to start explicitly.",
    )
    start_parser.add_argument(
        "--gate-stage",
        help="Current quality-gate stage ID for scoped repair start.",
    )
    start_parser.add_argument(
        "--gate-artifact",
        help="Current quality-gate report artifact ID for scoped repair start.",
    )
    start_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    complete_parser = actions.add_parser(
        "complete",
        help="Complete the active owner-stage repair transaction.",
    )
    complete_parser.add_argument("--workspace", required=True, help="Path to workspace directory.")
    complete_parser.add_argument("--reason", required=True, help="Short repair completion reason summary.")
    complete_parser.add_argument(
        "--repo-workdir",
        help="Repository or packaged contract base (default: auto-detect).",
    )
    complete_parser.add_argument(
        "--actor",
        default="orchestrator",
        choices=("cli", "orchestrator", "runtime", "system"),
        help="Actor recorded in event_log.jsonl.",
    )
    complete_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    supersede_parser = actions.add_parser(
        "supersede-stage",
        help="Record a contaminated owner-stage artifact revision and require downstream rerun.",
    )
    supersede_parser.add_argument("--workspace", required=True, help="Path to workspace directory.")
    supersede_parser.add_argument("--stage", required=True, help="Owner stage that produced the superseded artifact.")
    supersede_parser.add_argument(
        "--artifact",
        required=True,
        help="Workspace-relative artifact path or artifact id to supersede.",
    )
    supersede_parser.add_argument("--reason", required=True, help="Human/operator reason for the supersede.")
    supersede_parser.add_argument(
        "--repo-workdir",
        help="Repository or packaged contract base (default: auto-detect).",
    )
    supersede_parser.add_argument(
        "--actor",
        default="orchestrator",
        choices=("cli", "orchestrator", "runtime", "system"),
        help="Actor recorded in event_log.jsonl.",
    )
    supersede_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")


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
