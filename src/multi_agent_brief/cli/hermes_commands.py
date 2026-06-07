"""hermes — Hermes Agent skill / cron plan / prompt commands."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from multi_agent_brief.hermes import (
    build_hermes_cron_plan,
    install_hermes_skill,
    render_hermes_cron_commands,
    render_hermes_cron_markdown,
    render_hermes_prompt,
    render_hermes_skill,
)
from multi_agent_brief.hermes.adapter import sync_cached_package_source, write_json


def register(subparsers: argparse._SubParsersAction) -> None:
    """Register the hermes subcommand group."""
    hermes_parser = subparsers.add_parser(
        "hermes",
        help="Generate Hermes Agent skill and cron plans for scheduled briefs.",
    )
    hermes_sub = hermes_parser.add_subparsers(
        dest="hermes_action", required=True
    )

    hermes_skill = hermes_sub.add_parser(
        "skill", help="Write the Hermes SKILL.md for MABW cron jobs."
    )
    hermes_skill.add_argument(
        "--output",
        help="Output SKILL.md path"
        " (default: .agents/hermes-skills/multi-agent-brief-hermes/SKILL.md).",
    )

    hermes_plan = hermes_sub.add_parser(
        "cron-plan",
        help="Generate a Hermes cron plan JSON/Markdown for a workspace.",
    )
    hermes_plan.add_argument(
        "--config", required=True, help="Path to workspace config.yaml."
    )
    hermes_plan.add_argument(
        "--repo-workdir",
        help="Repository workdir for Hermes cron --workdir"
        " (default: current directory).",
    )
    hermes_plan.add_argument(
        "--cadence",
        help="Comma-separated cadences: weekly,monthly,daily."
        " Defaults to config cadence or weekly.",
    )
    hermes_plan.add_argument(
        "--deliver",
        default="local",
        help="Hermes delivery target, e.g. local, feishu, telegram.",
    )
    hermes_plan.add_argument(
        "--profile",
        default="",
        help="Optional existing Hermes profile name.",
    )
    hermes_plan.add_argument(
        "--output",
        help="Output JSON path"
        " (default: workspace/output/intermediate/hermes_cron_plan.json).",
    )
    hermes_plan.add_argument(
        "--markdown", help="Optional Markdown output path."
    )

    hermes_commands = hermes_sub.add_parser(
        "cron-commands",
        help="Print Hermes cron create commands for a workspace.",
    )
    hermes_commands.add_argument(
        "--config", required=True, help="Path to workspace config.yaml."
    )
    hermes_commands.add_argument(
        "--repo-workdir",
        help="Repository workdir for Hermes cron --workdir"
        " (default: current directory).",
    )
    hermes_commands.add_argument(
        "--cadence",
        help="Comma-separated cadences: weekly,monthly,daily."
        " Defaults to config cadence or weekly.",
    )
    hermes_commands.add_argument(
        "--deliver",
        default="local",
        help="Hermes delivery target, e.g. local, feishu, telegram.",
    )
    hermes_commands.add_argument(
        "--profile",
        default="",
        help="Optional existing Hermes profile name.",
    )

    hermes_sync = hermes_sub.add_parser(
        "sync-sources",
        help="Enable cached_package input/hermes_cache in workspace"
        " sources.yaml.",
    )
    hermes_sync.add_argument(
        "--config", required=True, help="Path to workspace config.yaml."
    )
    hermes_sync.add_argument(
        "--cache-dir",
        default="input/hermes_cache",
        help="Cache path written into sources.yaml.",
    )
    hermes_sync.add_argument(
        "--dry-run",
        action="store_true",
        help="Show changes without writing.",
    )

    hermes_install = hermes_sub.add_parser(
        "install-skill",
        help="Install the MABW Hermes skill to a Hermes-discoverable"
        " directory.",
    )
    hermes_install.add_argument(
        "--target",
        help="Target skill directory (auto-detected if omitted).",
    )

    hermes_prompt = hermes_sub.add_parser(
        "prompt",
        help="Generate a Hermes run prompt for a workspace.",
    )
    hermes_prompt.add_argument(
        "--config", required=True, help="Path to workspace config.yaml."
    )
    hermes_prompt.add_argument(
        "--repo-workdir",
        help="Repository workdir (default: current directory).",
    )
    hermes_prompt.add_argument(
        "--venv",
        help="Virtual env path"
        " (default: <repo>/.venv/{bin,Scripts}/activate).",
    )


def handle(args: argparse.Namespace) -> int:
    """Dispatch hermes subcommands."""
    if args.hermes_action == "skill":
        return _hermes_skill(args)
    if args.hermes_action == "cron-plan":
        return _hermes_cron_plan(args)
    if args.hermes_action == "cron-commands":
        return _hermes_cron_commands(args)
    if args.hermes_action == "sync-sources":
        return _hermes_sync_sources(args)
    if args.hermes_action == "install-skill":
        return _hermes_install_skill(args)
    if args.hermes_action == "prompt":
        return _hermes_prompt(args)
    return 1


def _build_hermes_plan_from_args(args: argparse.Namespace):
    from multi_agent_brief.core.config import load_config

    config_path = Path(args.config).resolve()
    if not config_path.exists():
        print(f"[error] config.yaml not found: {config_path}")
        return None
    config = load_config(config_path)
    cadences = None
    if getattr(args, "cadence", None):
        cadences = [
            c.strip() for c in args.cadence.split(",") if c.strip()
        ]
    repo_workdir = (
        Path(args.repo_workdir).resolve()
        if getattr(args, "repo_workdir", None)
        else Path.cwd().resolve()
    )
    return build_hermes_cron_plan(
        config=config,
        workspace=config_path.parent,
        repo_workdir=repo_workdir,
        cadences=cadences,
        deliver=getattr(args, "deliver", "local"),
        profile=getattr(args, "profile", ""),
    )


def _hermes_skill(args: argparse.Namespace) -> int:
    output = (
        Path(args.output)
        if args.output
        else Path(
            ".agents/hermes-skills/multi-agent-brief-hermes/SKILL.md"
        )
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_hermes_skill(), encoding="utf-8")
    print(f"[hermes] Wrote Hermes skill: {output}")
    print(
        "[hint] Run 'multi-agent-brief hermes install-skill' to install to"
        " a Hermes-discoverable directory."
    )
    return 0


def _hermes_cron_plan(args: argparse.Namespace) -> int:
    plan = _build_hermes_plan_from_args(args)
    if plan is None:
        return 1
    workspace = Path(plan.workspace)
    output = (
        Path(args.output)
        if args.output
        else workspace / "output" / "intermediate" / "hermes_cron_plan.json"
    )
    write_json(output, plan.to_dict())
    print(f"[hermes] Wrote cron plan: {output}")
    if args.markdown:
        md_path = Path(args.markdown)
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(
            render_hermes_cron_markdown(plan), encoding="utf-8"
        )
        print(f"[hermes] Wrote cron plan Markdown: {md_path}")
    print(
        "[hint] Run 'multi-agent-brief hermes cron-commands"
        " --config <workspace>/config.yaml' to print install commands."
    )
    return 0


def _hermes_cron_commands(args: argparse.Namespace) -> int:
    plan = _build_hermes_plan_from_args(args)
    if plan is None:
        return 1
    print(render_hermes_cron_commands(plan), end="")
    return 0


def _hermes_sync_sources(args: argparse.Namespace) -> int:
    config_path = Path(args.config).resolve()
    sources_path = config_path.parent / "sources.yaml"
    try:
        result = sync_cached_package_source(
            sources_path=sources_path,
            cache_dir=args.cache_dir,
            dry_run=args.dry_run,
        )
    except Exception as exc:
        print(f"[error] {exc}")
        return 1
    action = (
        "would update"
        if args.dry_run and result["changed"]
        else "updated"
        if result["changed"]
        else "already configured"
    )
    print(f"[hermes] sources.yaml {action}: {sources_path}")
    print(f"[hermes] cached_package path: {result['cache_dir']}")
    print(
        "[hermes] enabled providers:"
        f" {', '.join(result['enabled_providers'])}"
    )
    return 0


def _hermes_install_skill(args: argparse.Namespace) -> int:
    result = install_hermes_skill(target_dir=args.target)
    print(f"[hermes] Installed skill: {result['skill_path']}")
    if result["auto_detected"]:
        print(
            "[hermes] Auto-detected Hermes skill directory:"
            f" {result['skill_dir']}"
        )
    if result["hint"]:
        print(f"[hint] {result['hint']}")
    print()
    print(
        "[hermes] Next: generate a run prompt for your workspace:"
    )
    print(
        "  multi-agent-brief hermes prompt"
        " --config <workspace>/config.yaml"
    )
    print(
        "[hermes] Then paste the prompt into Hermes to start the delegated"
        " brief workflow."
    )
    return 0


def _hermes_prompt(args: argparse.Namespace) -> int:
    config_path = Path(args.config).resolve()
    workspace = config_path.parent
    repo_workdir = (
        Path(args.repo_workdir).resolve()
        if getattr(args, "repo_workdir", None)
        else Path.cwd().resolve()
    )
    if args.venv:
        venv_activate = str(Path(args.venv).resolve())
    else:
        venv = repo_workdir / ".venv"
        if sys.platform == "win32":
            venv_activate = str(venv / "Scripts" / "activate")
        else:
            venv_activate = str(venv / "bin" / "activate")
    prompt = render_hermes_prompt(
        workspace=workspace,
        repo_workdir=repo_workdir,
        venv_path=venv_activate,
    )
    print(prompt, end="")
    return 0
