"""run / start — runtime handoff launcher command."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from multi_agent_brief.orchestrator.handoff import VALID_RUNTIME_RECIPES
from multi_agent_brief.orchestrator_contract import VALID_RUNTIMES


def register(subparsers: argparse._SubParsersAction) -> None:
    """Register run, start, handoff, and prepare subparsers."""

    run_parser = subparsers.add_parser(
        "run",
        help="Run a workspace through the selected agent runtime handoff.",
    )
    run_parser.add_argument(
        "--workspace", help="Path to workspace directory."
    )
    run_parser.add_argument(
        "--config",
        help="Path to workspace config.yaml (convenience alias for --workspace).",
    )
    run_parser.add_argument(
        "--runtime",
        required=True,
        choices=list(VALID_RUNTIMES),
        help="Exact target runtime identity for this handoff.",
    )
    run_parser.add_argument(
        "--recipe",
        default="full",
        choices=list(VALID_RUNTIME_RECIPES),
        help="Runtime handoff recipe: full or fast-rerun (guidance only, not a Python pipeline).",
    )
    run_parser.add_argument(
        "--repo-workdir",
        help="Repository workdir (default: auto-detect source repo).",
    )
    run_parser.add_argument(
        "--venv", help="Virtual env path (default: auto-detect)."
    )
    run_parser.add_argument(
        "--skip-doctor", action="store_true", help="Skip doctor check."
    )
    prepare_parser = subparsers.add_parser(
        "prepare",
        help="[legacy] Replaced by 'briefloop run'.",
    )
    prepare_parser.add_argument(
        "--config", required=True, help="Path to config.yaml in the workspace."
    )
    prepare_parser.add_argument("--input", help="Override input directory.")
    prepare_parser.add_argument("--output", help="Override output directory.")

    start_parser = subparsers.add_parser(
        "start",
        help="Alias for run: create runtime handoff for the current agent.",
    )
    start_parser.add_argument(
        "--workspace", help="Path to workspace directory."
    )
    start_parser.add_argument(
        "--runtime",
        required=True,
        choices=list(VALID_RUNTIMES),
        help="Exact target runtime identity for this handoff.",
    )
    start_parser.add_argument(
        "--recipe",
        default="full",
        choices=list(VALID_RUNTIME_RECIPES),
        help="Runtime handoff recipe: full or fast-rerun (guidance only, not a Python pipeline).",
    )
    start_parser.add_argument(
        "--repo-workdir",
        help="Repository workdir (default: auto-detect source repo).",
    )
    start_parser.add_argument(
        "--venv", help="Virtual env path (default: auto-detect)."
    )
    start_parser.add_argument(
        "--skip-doctor", action="store_true", help="Skip doctor check."
    )
    handoff_parser = subparsers.add_parser(
        "handoff",
        help="Generate a runtime handoff artifact from a workspace config.",
    )
    handoff_parser.add_argument(
        "--config", required=True, help="Path to workspace config.yaml."
    )
    handoff_parser.add_argument(
        "--runtime",
        required=True,
        choices=list(VALID_RUNTIMES),
        help="Exact target runtime identity for this handoff.",
    )
    handoff_parser.add_argument(
        "--recipe",
        default="full",
        choices=list(VALID_RUNTIME_RECIPES),
        help="Runtime handoff recipe: full or fast-rerun (guidance only, not a Python pipeline).",
    )
    handoff_parser.add_argument(
        "--repo-workdir",
        help="Repository workdir (default: auto-detect source repo).",
    )
    handoff_parser.add_argument(
        "--venv", help="Virtual env path (default: auto-detect)."
    )
    handoff_parser.add_argument(
        "--skip-doctor", action="store_true", help="Skip doctor check."
    )

def handle(args: argparse.Namespace) -> int:
    """Dispatch run / start / handoff / prepare commands."""
    if args.command in {"prepare", "handoff"}:
        # Retired surfaces: the authority guard rejects workspace invocations;
        # no-workspace bypasses land on this fail-closed stub.
        print("runtime_command_unsupported")
        return 1
    # run and start both use the launcher
    return _run_launcher(args)


def _resolve_workspace(args: argparse.Namespace) -> Path | None:
    """Resolve workspace path from --workspace, --config, or CWD auto-detect."""
    workspace = getattr(args, "workspace", None)
    config_path = getattr(args, "config", None)

    if config_path and not workspace:
        cp = Path(config_path).resolve()
        if cp.is_file():
            workspace = str(cp.parent)
        elif cp.is_dir():
            workspace = str(cp)

    if not workspace:
        cwd = Path.cwd()
        if (cwd / "config.yaml").exists() and (cwd / "user.md").exists():
            workspace = str(cwd)

    if not workspace:
        return None

    ws_path = Path(workspace).resolve()
    if not (ws_path / "config.yaml").exists():
        return None
    return ws_path


def _run_launcher(args: argparse.Namespace) -> int:
    """run — standard runtime handoff launcher."""
    prefix = (
        "[start]" if getattr(args, "command", None) == "start" else "[run]"
    )

    workspace_path = _resolve_workspace(args)
    if workspace_path is None:
        print(f"{prefix} No workspace found.")
        print()
        print("For a real workspace:")
        print("  briefloop onboard")
        print("  briefloop init <workspace> --from-onboarding onboarding.json")
        print()
        print("For a demo only:")
        print("  briefloop init <workspace> --demo")
        return 1

    from multi_agent_brief.cli.authority_guard import classify_workspace_authority

    authority = classify_workspace_authority(workspace_path)
    if authority.kind == "legacy":
        print(f"{prefix} legacy_workspace_unsupported")
        return 1
    if authority.kind == "invalid_sqlite":
        print(f"{prefix} control_store_integrity_invalid")
        return 1
    if getattr(args, "command", None) == "start":
        print(f"{prefix} runtime_command_unsupported")
        return 1
    if args.runtime != "codex":
        print(f"{prefix} runtime_adapter_unsupported")
        return 1
    if getattr(args, "skip_doctor", False):
        print(f"{prefix} runtime_command_unsupported")
        return 1
    from multi_agent_brief.runtime_host_v2.codex import load_codex_adapter_binding
    from multi_agent_brief.runtime_host_v2.errors import RuntimeHostError
    from multi_agent_brief.runtime_host_v2.service import RuntimeHostService

    try:
        action = RuntimeHostService(
            workspace_path,
            adapter_loader=load_codex_adapter_binding,
        ).next_action()
    except RuntimeHostError as exc:
        print(f"{prefix} {exc}")
        return 1
    print(
        json.dumps(
            action.model_dump(mode="json", exclude_unset=False),
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0
