"""run / start / handoff / prepare — runtime handoff launcher commands."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from multi_agent_brief.orchestrator.handoff import (
    RUNTIME_RECIPE_FAST_RERUN,
    VALID_RUNTIMES,
    VALID_RUNTIME_RECIPES,
    build_handoff,
    legacy_runtime_alias_warning,
    render_handoff_cli,
    write_handoff_and_state,
)
from multi_agent_brief.orchestrator.fact_layer_import import require_fast_rerun_handoff_ready
from multi_agent_brief.orchestrator_contract import resolve_repo_workdir


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
        default="auto",
        choices=list(VALID_RUNTIMES),
        help="Target runtime for handoff (default: auto, resolves to hermes; manual is a legacy alias for operator).",
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
        help="[legacy] Replaced by 'multi-agent-brief run'.",
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
        default="auto",
        choices=list(VALID_RUNTIMES),
        help="Target runtime for handoff (default: auto, resolves to hermes; manual is a legacy alias for operator).",
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
        default="auto",
        choices=list(VALID_RUNTIMES),
        help="Target runtime for handoff (default: auto, resolves to hermes; manual is a legacy alias for operator).",
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
    if args.command == "prepare":
        return _run_prepare(args)
    if args.command == "handoff":
        return _run_handoff(args)
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
        print("  multi-agent-brief onboard")
        print("  multi-agent-brief init <workspace> --from-onboarding onboarding.json")
        print()
        print("For a demo only:")
        print("  multi-agent-brief init <workspace> --demo")
        return 1

    try:
        repo_workdir = resolve_repo_workdir(
            getattr(args, "repo_workdir", None),
            workspace=workspace_path,
        )
    except ValueError as exc:
        print(f"{prefix} {exc}")
        return 1

    recipe = getattr(args, "recipe", "full")
    if recipe == RUNTIME_RECIPE_FAST_RERUN:
        try:
            require_fast_rerun_handoff_ready(workspace_path)
        except ValueError as exc:
            print(f"{prefix} {exc}")
            return 1

    try:
        handoff = build_handoff(
            workspace=workspace_path,
            repo_workdir=repo_workdir,
            runtime=args.runtime,
            recipe=recipe,
            venv=getattr(args, "venv", None),
            run_doctor=not getattr(args, "skip_doctor", False),
        )
    except ValueError as exc:
        print(f"{prefix} {exc}")
        return 1
    _print_runtime_alias_warning(prefix, getattr(args, "runtime", ""))

    written = write_handoff_and_state(
        handoff=handoff,
        workspace=workspace_path,
        repo_workdir=repo_workdir,
        prefix=prefix,
    )
    if written is None:
        return 1
    md_path, json_path = written
    print(render_handoff_cli(handoff))
    print(f"{prefix} Handoff written: {md_path}")
    print(f"{prefix} Handoff JSON:  {json_path}")
    return 0


def _run_prepare(args: argparse.Namespace) -> int:
    """[legacy] prepare — replaced by the runtime handoff launcher."""
    print(
        "[legacy] prepare has been replaced by:"
        " multi-agent-brief run --workspace <workspace>"
    )
    return 1


def _run_handoff(args: argparse.Namespace) -> int:
    """handoff — generate runtime handoff from workspace config."""
    config_path = Path(args.config).resolve()
    if not config_path.exists():
        print(f"[error] config.yaml not found: {config_path}")
        return 1
    workspace = config_path.parent
    try:
        repo_workdir = resolve_repo_workdir(
            getattr(args, "repo_workdir", None),
            workspace=workspace,
        )
    except ValueError as exc:
        print(f"[handoff] {exc}")
        return 1

    recipe = getattr(args, "recipe", "full")
    if recipe == RUNTIME_RECIPE_FAST_RERUN:
        try:
            require_fast_rerun_handoff_ready(workspace)
        except ValueError as exc:
            print(f"[handoff] {exc}")
            return 1

    try:
        handoff = build_handoff(
            workspace=workspace,
            repo_workdir=repo_workdir,
            runtime=args.runtime,
            recipe=recipe,
            venv=getattr(args, "venv", None),
            run_doctor=not getattr(args, "skip_doctor", False),
        )
    except ValueError as exc:
        print(f"[handoff] {exc}")
        return 1
    _print_runtime_alias_warning("[handoff]", getattr(args, "runtime", ""))

    written = write_handoff_and_state(
        handoff=handoff,
        workspace=workspace,
        repo_workdir=repo_workdir,
        prefix="[handoff]",
    )
    if written is None:
        return 1
    md_path, json_path = written
    print(render_handoff_cli(handoff))
    print(f"[handoff] Written: {md_path}")
    print(f"[handoff] JSON:   {json_path}")
    return 0


def _print_runtime_alias_warning(prefix: str, runtime: str) -> None:
    warning = legacy_runtime_alias_warning(runtime)
    if warning:
        print(f"{prefix} WARNING: {warning}", file=sys.stderr)
