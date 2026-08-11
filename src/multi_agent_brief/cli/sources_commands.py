"""sources and doctor — source discovery and health-check commands."""

from __future__ import annotations

import argparse

from multi_agent_brief.sources.doctor import run_doctor, format_doctor_report


_RETIRED_SOURCES_DESCRIPTION = (
    "Retired compatibility command. It is unavailable and always returns "
    "runtime_command_unsupported. Configure public-web sources through init-web, "
    "then follow the Store-derived `briefloop runtime continue --workspace "
    "<workspace>` action."
)


def register_sources(subparsers: argparse._SubParsersAction) -> None:
    """Register the sources subcommand group."""
    sources_parser = subparsers.add_parser(
        "sources",
        help="Retired source compatibility commands; always unavailable.",
        description=_RETIRED_SOURCES_DESCRIPTION,
    )
    sources_sub = sources_parser.add_subparsers(
        dest="sources_action", required=True
    )

    decide_parser = sources_sub.add_parser(
        "decide",
        help="Retired/unavailable candidate command (compatibility only).",
        description=_RETIRED_SOURCES_DESCRIPTION,
    )
    decide_parser.add_argument(
        "--config",
        required=True,
        help="Compatibility argument only; no source candidates are resolved.",
    )
    decide_parser.add_argument(
        "--search",
        action="store_true",
        help="Compatibility flag only; no web search is executed.",
    )
    decide_parser.add_argument(
        "--daily-news-backfill",
        action="store_true",
        help="Compatibility flag only; no daily news search is executed.",
    )
    decide_parser.add_argument(
        "--backfill-days",
        type=int,
        help="Compatibility value only; no backfill is executed.",
    )
    decide_parser.add_argument(
        "--daily-max-results",
        type=int,
        help="Compatibility value only; no search results are requested.",
    )
    decide_parser.add_argument(
        "--merge",
        action="store_true",
        help="Compatibility flag only; no source candidates are merged.",
    )
    decide_parser.add_argument(
        "--candidates",
        help="Compatibility path only; the file is not read or merged.",
    )

    materialize_parser = sources_sub.add_parser(
        "materialize-pack",
        help="Retired/unavailable materialization command (compatibility only).",
        description=_RETIRED_SOURCES_DESCRIPTION,
    )
    materialize_parser.add_argument(
        "--config",
        required=True,
        help="Compatibility argument only; no source pack is materialized.",
    )
    materialize_parser.add_argument(
        "--force",
        action="store_true",
        help="Compatibility flag only; no source evidence is replaced.",
    )
    materialize_parser.add_argument(
        "--json",
        action="store_true",
        help="Compatibility flag only; the command remains unavailable.",
    )

    add_file_parser = sources_sub.add_parser(
        "add-file",
        help="Retired/unavailable local-file command (compatibility only).",
        description=_RETIRED_SOURCES_DESCRIPTION,
    )
    _add_workspace_selector(add_file_parser)
    add_file_parser.add_argument(
        "paths",
        nargs="+",
        help="Compatibility paths only; no file is read, copied, or registered.",
    )
    add_file_parser.add_argument(
        "--name",
        help="Compatibility value only; no source name is recorded.",
    )
    add_file_parser.add_argument(
        "--category",
        default="other",
        help="Compatibility value only; no source category is recorded.",
    )
    add_file_parser.add_argument(
        "--language",
        default="en",
        help="Compatibility value only; no source language is recorded.",
    )
    add_file_parser.add_argument(
        "--json",
        action="store_true",
        help="Compatibility flag only; the command remains unavailable.",
    )

    add_rss_parser = sources_sub.add_parser(
        "add-rss",
        help="Retired/unavailable RSS command (compatibility only).",
        description=_RETIRED_SOURCES_DESCRIPTION,
    )
    _add_workspace_selector(add_rss_parser)
    add_rss_parser.add_argument(
        "url", help="Compatibility URL only; no feed is registered."
    )
    add_rss_parser.add_argument(
        "--name", help="Compatibility value only; no feed name is recorded."
    )
    add_rss_parser.add_argument(
        "--category",
        default="news_media",
        help="Compatibility value only; no feed category is recorded.",
    )
    add_rss_parser.add_argument(
        "--language",
        default="en",
        help="Compatibility value only; no feed language is recorded.",
    )
    add_rss_parser.add_argument(
        "--json",
        action="store_true",
        help="Compatibility flag only; the command remains unavailable.",
    )

    add_web_parser = sources_sub.add_parser(
        "add-web-search",
        help="Retired/unavailable web-search command (compatibility only).",
        description=_RETIRED_SOURCES_DESCRIPTION,
    )
    _add_workspace_selector(add_web_parser)
    add_web_parser.add_argument(
        "--query",
        required=True,
        help="Compatibility value only; no search or handoff is created.",
    )
    add_web_parser.add_argument(
        "--domain",
        action="append",
        dest="domains",
        default=[],
        help="Compatibility value only; no preferred domain is recorded.",
    )
    add_web_parser.add_argument(
        "--max-results",
        type=int,
        default=10,
        help="Compatibility value only; no search results are requested.",
    )
    add_web_parser.add_argument(
        "--recency-days",
        type=int,
        help="Compatibility value only; no recency window is recorded.",
    )
    add_web_parser.add_argument(
        "--json",
        action="store_true",
        help="Compatibility flag only; the command remains unavailable.",
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
        help="Compatibility path only; no workspace source state is changed.",
    )
    parser.add_argument(
        "--config",
        help="Compatibility path only; the config is not used for source actions.",
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
