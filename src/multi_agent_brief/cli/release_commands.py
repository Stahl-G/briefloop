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
