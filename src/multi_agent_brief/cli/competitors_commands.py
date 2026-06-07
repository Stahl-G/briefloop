"""competitors — market & competitor universe management commands."""

from __future__ import annotations

import argparse
from pathlib import Path

from multi_agent_brief.analysis_modules.market_competitor.config import (
    load_competitor_candidates,
    save_competitor_candidates,
    merge_candidates_to_universe,
    load_competitor_universe,
    generate_candidates_template,
)


def register(subparsers: argparse._SubParsersAction) -> None:
    """Register the competitors subcommand group."""
    comp_parser = subparsers.add_parser(
        "competitors",
        help="Market & competitor universe management.",
    )
    comp_sub = comp_parser.add_subparsers(
        dest="competitors_action", required=True
    )

    comp_init = comp_sub.add_parser(
        "init",
        help="Create empty competitor_candidates.yaml template.",
    )
    comp_init.add_argument(
        "--config", required=True, help="Path to config.yaml in the workspace."
    )

    comp_list = comp_sub.add_parser(
        "list", help="List pending competitor candidates."
    )
    comp_list.add_argument(
        "--config", required=True, help="Path to config.yaml in the workspace."
    )

    comp_merge = comp_sub.add_parser(
        "merge",
        help="Merge approved candidates into competitor_universe.yaml.",
    )
    comp_merge.add_argument(
        "--config", required=True, help="Path to config.yaml in the workspace."
    )
    comp_merge.add_argument(
        "--candidates", help="Path to competitor_candidates.yaml."
    )


def handle(args: argparse.Namespace) -> int:
    """Dispatch competitors subcommands."""
    if args.competitors_action == "init":
        return _competitors_init(args)
    if args.competitors_action == "list":
        return _competitors_list(args)
    if args.competitors_action == "merge":
        return _competitors_merge(args)
    return 1


def _competitors_init(args: argparse.Namespace) -> int:
    """Create competitor_candidates.yaml template.

    Writes an empty template for manual editing.  LLM-assisted competitor
    discovery is available via slash command ``/propose-competitors
    <workspace>`` in Claude Code / Codex.
    """
    config_path = Path(args.config)
    workspace = config_path.parent
    candidates_path = workspace / "competitor_candidates.yaml"

    if candidates_path.exists():
        candidates = load_competitor_candidates(candidates_path)
        if candidates:
            print(
                "[competitors] competitor_candidates.yaml already exists"
                " with candidates."
            )
            print(
                "              Run 'competitors list' to review, then"
                " 'competitors merge' to confirm."
            )
            return 0

    template = generate_candidates_template()
    save_competitor_candidates(template["candidates"], candidates_path)
    print(
        f"[competitors] Created empty competitor_candidates.yaml at"
        f" {candidates_path}"
    )
    print(
        "[competitors] Add candidate competitors to this file, then run:"
    )
    print(f"  multi-agent-brief competitors merge --config {args.config}")
    print(
        "[hint] For LLM-assisted discovery, use /propose-competitors"
        " <workspace> in Claude Code."
    )
    return 0


def _competitors_list(args: argparse.Namespace) -> int:
    """List pending competitor candidates."""
    config_path = Path(args.config)
    workspace = config_path.parent
    candidates_path = workspace / "competitor_candidates.yaml"

    candidates = load_competitor_candidates(candidates_path)
    if not candidates:
        print(
            "[competitors] No pending candidates. Run 'competitors init'"
            " first."
        )
        return 0

    pending = [c for c in candidates if not c.get("approved", False)]
    approved = [c for c in candidates if c.get("approved", False)]

    if pending:
        print(f"Pending ({len(pending)}):")
        for c in pending:
            print(
                f"  [{c.get('entity_id', '?')}] {c.get('name', '?')}  "
                f"relation={c.get('relation', '?')}  "
                f"suggested_by={c.get('suggested_by', '?')}"
            )
    if approved:
        print(f"Approved ({len(approved)}):")
        for c in approved:
            print(f"  [{c.get('entity_id', '?')}] {c.get('name', '?')}")
    if not pending and not approved:
        print("[competitors] Candidate list is empty.")

    return 0


def _competitors_merge(args: argparse.Namespace) -> int:
    """Merge approved candidates into competitor_universe.yaml."""
    config_path = Path(args.config)
    workspace = config_path.parent
    candidates_path = (
        Path(args.candidates)
        if args.candidates
        else workspace / "competitor_candidates.yaml"
    )
    universe_path = workspace / "competitor_universe.yaml"

    if not candidates_path.exists():
        print(
            f"[error] competitor_candidates.yaml not found:"
            f" {candidates_path}"
        )
        return 1

    if not universe_path.exists():
        print(
            f"[error] competitor_universe.yaml not found:"
            f" {universe_path}"
        )
        print(
            "[hint] Re-run 'multi-agent-brief init' to regenerate workspace"
            " files."
        )
        return 1

    added = merge_candidates_to_universe(candidates_path, universe_path)
    print(
        f"[competitors] Merged {added} entities into"
        " competitor_universe.yaml"
    )

    universe = load_competitor_universe(universe_path)
    if universe.entities:
        print(
            f"[competitors] Tracking {len(universe.entities)} entities: "
            f"{', '.join(e.name for e in universe.entities)}"
        )
    return 0
