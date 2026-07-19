"""Read-only workspace status CLI."""

from __future__ import annotations

import argparse
import json

from multi_agent_brief.runtime_host_v2.errors import RuntimeHostError
from multi_agent_brief.status import build_workspace_status, format_workspace_status


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "status",
        help="Show a read-only workspace operator dashboard.",
    )
    parser.add_argument(
        "--workspace", required=True, help="Path to workspace directory."
    )
    parser.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON."
    )


def handle(args: argparse.Namespace) -> int:
    try:
        status = build_workspace_status(args.workspace)
    except (OSError, RuntimeHostError) as exc:
        status = {
            "ok": False,
            "workspace": str(args.workspace),
            "read_only": True,
            "error": str(exc),
        }
    if getattr(args, "json", False):
        print(json.dumps(status, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(format_workspace_status(status))
    return 0 if status.get("ok") else 1
