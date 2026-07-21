"""sources and doctor — source discovery and health-check commands."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

from multi_agent_brief.sources.evidence_pack import (
    SourceEvidencePackError,
    materialize_source_evidence_pack,
)
from multi_agent_brief.sources.sourcehub import (
    SourceHubError,
    add_file_sources,
    add_rss_feed,
    add_web_search_handoff,
)
from multi_agent_brief.sources.decider import (
    SourceCandidatesError,
    load_source_discovery,
    build_search_queries,
    build_daily_news_search_tasks,
    build_news_domain_preferences,
    generate_source_candidates,
    merge_candidates_to_sources,
)
from multi_agent_brief.sources.doctor import run_doctor, format_doctor_report
from multi_agent_brief.sources.registry import load_sources_config


def register_sources(subparsers: argparse._SubParsersAction) -> None:
    """Register the sources subcommand group."""
    sources_parser = subparsers.add_parser(
        "sources", help="Source discovery and management."
    )
    sources_sub = sources_parser.add_subparsers(
        dest="sources_action", required=True
    )

    decide_parser = sources_sub.add_parser(
        "decide",
        help="Resolve llm_decide profile into concrete source candidates.",
    )
    decide_parser.add_argument(
        "--config", required=True, help="Path to config.yaml in the workspace."
    )
    decide_parser.add_argument(
        "--search",
        action="store_true",
        help="Run web search to discover sources (requires search backend).",
    )
    decide_parser.add_argument(
        "--daily-news-backfill",
        action="store_true",
        help=(
            "Run one user-need-customized news search per day for the"
            " recent backfill window."
        ),
    )
    decide_parser.add_argument(
        "--backfill-days",
        type=int,
        help="Number of past days for --daily-news-backfill. Default: 7.",
    )
    decide_parser.add_argument(
        "--daily-max-results",
        type=int,
        help="Maximum search results per day for daily news backfill. Default: 20.",
    )
    decide_parser.add_argument(
        "--merge",
        action="store_true",
        help="Merge approved source_candidates.yaml into sources.yaml.",
    )
    decide_parser.add_argument(
        "--candidates",
        help="Path to source_candidates.yaml (for --merge).",
    )

    materialize_parser = sources_sub.add_parser(
        "materialize-pack",
        help="Materialize explicit durable source records into input/sources/.",
    )
    materialize_parser.add_argument(
        "--config", required=True, help="Path to config.yaml in the workspace."
    )
    materialize_parser.add_argument(
        "--force",
        action="store_true",
        help="Replace existing generated source evidence records.",
    )
    materialize_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON.",
    )

    add_file_parser = sources_sub.add_parser(
        "add-file",
        help="Copy local text evidence files into the workspace and register them.",
    )
    _add_workspace_selector(add_file_parser)
    add_file_parser.add_argument(
        "paths",
        nargs="+",
        help="Local text source files or glob patterns (.md, .txt, .json).",
    )
    add_file_parser.add_argument(
        "--name",
        help="Reader-facing source name. Only valid with one file.",
    )
    add_file_parser.add_argument(
        "--category",
        default="other",
        help="Reader-facing source category. Defaults to other.",
    )
    add_file_parser.add_argument(
        "--language",
        default="en",
        help="Source language hint. Defaults to en.",
    )
    add_file_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON.",
    )

    add_rss_parser = sources_sub.add_parser(
        "add-rss",
        help="Register an RSS/Atom feed in sources.yaml.",
    )
    _add_workspace_selector(add_rss_parser)
    add_rss_parser.add_argument("url", help="RSS/Atom feed URL.")
    add_rss_parser.add_argument("--name", help="Feed display name.")
    add_rss_parser.add_argument(
        "--category",
        default="news_media",
        help="Reader-facing source category. Defaults to news_media.",
    )
    add_rss_parser.add_argument(
        "--language",
        default="en",
        help="Feed language hint. Defaults to en.",
    )
    add_rss_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON.",
    )

    add_web_parser = sources_sub.add_parser(
        "add-web-search",
        help="Register a runtime web-search handoff task without executing search.",
    )
    _add_workspace_selector(add_web_parser)
    add_web_parser.add_argument(
        "--query",
        required=True,
        help="Search query for the runtime handoff.",
    )
    add_web_parser.add_argument(
        "--domain",
        action="append",
        dest="domains",
        default=[],
        help="Preferred domain for the handoff task. Repeatable.",
    )
    add_web_parser.add_argument(
        "--max-results",
        type=int,
        default=10,
        help="Maximum results for the runtime handoff. Defaults to 10.",
    )
    add_web_parser.add_argument(
        "--recency-days",
        type=int,
        help="Optional recency window for the runtime handoff.",
    )
    add_web_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON.",
    )


def register_doctor(subparsers: argparse._SubParsersAction) -> None:
    """Register the doctor subparser."""
    doctor_parser = subparsers.add_parser(
        "doctor", help="Check source configuration health."
    )
    doctor_parser.add_argument(
        "--config", required=True, help="Path to config.yaml in the workspace."
    )


def handle_doctor(args: argparse.Namespace) -> int:
    """Run doctor health check."""
    return _doctor(args)


def _doctor(args: argparse.Namespace) -> int:
    results = run_doctor(config_path=args.config)
    print(format_doctor_report(results))
    errors = sum(1 for r in results if r.status == "ERROR")
    return 1 if errors else 0


def _add_workspace_selector(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--workspace",
        help="BriefLoop workspace path. Defaults to the current directory.",
    )
    parser.add_argument(
        "--config",
        help="Path to config.yaml. Overrides --workspace when provided.",
    )


def handle_sources(args: argparse.Namespace) -> int:
    """Fail-closed stub for the retired public `sources` command group.

    The parser registration is retained so the authority guard can return
    the typed rejection for workspace invocations; any no-workspace bypass
    lands here instead of executing legacy code. `doctor` stays active.
    """

    print("runtime_command_unsupported")
    return 1

# NOTE: the public `sources` command group (decide/materialize-pack/add-*)
# is retired; `sources decide` retirement is by design. The SQLite ControlStore
# is the sole runtime authority; `doctor` remains an active command.
