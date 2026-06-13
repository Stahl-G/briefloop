"""sources and doctor — source discovery and health-check commands."""

from __future__ import annotations

import argparse
from pathlib import Path

from multi_agent_brief.sources.decider import (
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


def register_doctor(subparsers: argparse._SubParsersAction) -> None:
    """Register the doctor subparser."""
    doctor_parser = subparsers.add_parser(
        "doctor", help="Check source configuration health."
    )
    doctor_parser.add_argument(
        "--config", required=True, help="Path to config.yaml in the workspace."
    )


def handle_sources(args: argparse.Namespace) -> int:
    """Dispatch sources subcommands."""
    if args.sources_action == "decide":
        return _sources_decide(args)
    return 1


def handle_doctor(args: argparse.Namespace) -> int:
    """Run doctor health check."""
    return _doctor(args)


def _doctor(args: argparse.Namespace) -> int:
    results = run_doctor(config_path=args.config)
    print(format_doctor_report(results))
    errors = sum(1 for r in results if r.status == "ERROR")
    return 1 if errors else 0


def _sources_decide(args: argparse.Namespace) -> int:
    config_path = Path(args.config)
    workspace = config_path.parent
    sources_path = workspace / "sources.yaml"

    if not sources_path.exists():
        print(f"[error] sources.yaml not found: {sources_path}")
        print(
            "[hint] Run `multi-agent-brief init` first to create the"
            " workspace."
        )
        return 1

    discovery = load_source_discovery(sources_path)
    if not discovery:
        print(
            "[error] No source_discovery section found in sources.yaml."
        )
        print(
            "[hint] Re-init with --source-profile llm_decide to generate"
            " discovery policy."
        )
        return 1

    # --merge: merge candidates into sources.yaml
    if args.merge:
        candidates_path = (
            Path(args.candidates)
            if args.candidates
            else workspace / "source_candidates.yaml"
        )
        if not candidates_path.exists():
            print(
                f"[error] source_candidates.yaml not found:"
                f" {candidates_path}"
            )
            return 1
        result = merge_candidates_to_sources(sources_path, candidates_path)
        added_local = result.get("added_local", 0)
        local_part = (
            f" + {added_local} local signal tasks" if added_local else ""
        )
        print(
            f"[sources] Merged {result['added_manual']} manual +"
            f" {result['added_rss']} RSS{local_part} into sources.yaml"
        )
        return 0

    ws_config = load_sources_config(sources_path) if sources_path.exists() else None
    web_search_config = (
        {**ws_config.web_search, "_workspace_dir": str(workspace)}
        if ws_config
        else {}
    )
    backfill_config = web_search_config.get("initial_news_backfill") or {}
    use_daily_backfill = bool(args.daily_news_backfill) or bool(
        backfill_config.get("enabled")
    )
    backfill_days = int(
        args.backfill_days
        or backfill_config.get("days")
        or 7
    )
    daily_max_results = int(
        args.daily_max_results
        or backfill_config.get("daily_max_results")
        or 20
    )

    # Default: generate source_candidates.yaml
    search_tasks = (
        build_daily_news_search_tasks(
            discovery,
            days=backfill_days,
            daily_max_results=daily_max_results,
        )
        if use_daily_backfill
        else _build_standard_search_tasks(discovery)
    )
    queries = [str(task.get("query", "")) for task in search_tasks if task.get("query")]
    print(
        f"[sources] Source discovery for:"
        f" {discovery.get('company', 'N/A')}"
        f" ({discovery.get('industry', 'N/A')})"
    )
    if use_daily_backfill:
        print(
            "[sources] Initial daily news backfill enabled:"
            f" {backfill_days} days x {daily_max_results} results/day"
        )
    preferred_domains, excluded_domains = build_news_domain_preferences(discovery)
    if preferred_domains:
        print(
            "[sources] Preferred news domains:"
            f" {', '.join(preferred_domains)}"
        )
    if excluded_domains:
        print(
            "[sources] Excluded news domains:"
            f" {', '.join(excluded_domains)}"
        )
    print(f"[sources] Generated {len(queries)} search queries:")
    for i, q in enumerate(queries, 1):
        print(f"  {i}. {q}")

    search_results = None
    if args.search:
        from multi_agent_brief.sources.web_search import (
            WebSearchProvider,
            backend_api_key_env,
            temporary_workspace_api_key_env,
        )

        provider = WebSearchProvider()
        validation_errors = provider.validate_config(web_search_config)
        if web_search_config.get("enabled") is not True:
            validation_errors.append(
                "--search requires web_search.enabled: true; disabled web_search is a no-op."
            )
        if web_search_config.get("mode") != "external_api":
            validation_errors.append(
                "--search requires web_search.mode: external_api; runtime_tool/configure_later/disabled modes are not Python-searchable."
            )
        if validation_errors:
            print(
                "[error] --search requires a valid external_api web_search configuration."
            )
            for error in validation_errors:
                print(f"        {error}")
            print(
                "        Supported backends: tavily, exa, brave, firecrawl,"
                " serper."
            )
            print(
                "        Enable web_search in sources.yaml with mode:"
                " external_api, a real backend, and API key,"
            )
            print(
                "        or run without --search to generate template"
                " candidates."
            )
            return 1

        # Actually execute searches via the configured backend
        try:
            backend = provider._get_backend(web_search_config)
        except Exception as exc:
            print(
                f"[error] Failed to initialize search backend: {exc}"
            )
            return 1

        with temporary_workspace_api_key_env(backend, web_search_config):
            if not backend.is_available():
                api_key_env = backend_api_key_env(
                    backend, web_search_config
                )
                key_hint = f" Set {api_key_env}." if api_key_env else ""
                print(
                    f"[error] Search backend '{backend.name}' is configured"
                    f" but not available.{key_hint}"
                )
                return 1

            print(
                f"[sources] Executing {len(search_tasks)} search queries via"
                f" backend: {backend.name}"
            )
            search_results = []
            attempted_searches = 0
            successful_searches = 0
            total_result_count = 0
            max_results = web_search_config.get("max_results", 10)
            for task in search_tasks:
                q = str(task.get("query", ""))
                domains = task.get("domains") or None
                task_max_results = int(task.get("max_results") or max_results)
                attempted_searches += 1
                try:
                    search_kwargs = {"domains": domains}
                    for key in ("topic", "vertical", "tbs"):
                        if task.get(key):
                            search_kwargs[key] = task[key]
                    if use_daily_backfill:
                        search_kwargs["days"] = backfill_days
                    elif web_search_config.get("recency_days"):
                        search_kwargs["days"] = web_search_config["recency_days"]
                    results = backend.search(
                        q,
                        max_results=task_max_results,
                        **search_kwargs,
                    )
                    search_results.append(
                        {
                            "query": q,
                            "metadata": {
                                key: value
                                for key, value in task.items()
                                if key not in ("query", "domains")
                            },
                            "results": [
                                {
                                    "title": r.title,
                                    "url": r.url,
                                    "snippet": r.snippet,
                                    "published_at": r.published_at,
                                    "source_name": r.source_name,
                                }
                                for r in results
                            ],
                        }
                    )
                    successful_searches += 1
                    total_result_count += len(results)
                    print(f"  [{len(results)} results] {q}")
                except Exception as exc:
                    print(f"  [error] Search failed for '{q}': {exc}")
                    # Continue with remaining queries, but do not write normal
                    # candidates if every backend request failed.
            if attempted_searches and successful_searches == 0:
                print(
                    "[error] All configured search queries failed; "
                    "source_candidates.yaml was not generated."
                )
                return 1
            if attempted_searches and total_result_count == 0:
                print(
                    "[error] All configured search queries returned zero results; "
                    "source_candidates.yaml was not generated."
                )
                return 1

    candidates = generate_source_candidates(discovery, search_results)
    candidates_path = workspace / "source_candidates.yaml"
    try:
        from multi_agent_brief.sources.decider import _save_yaml

        _save_yaml(candidates_path, candidates)
    except Exception as e:
        print(f"[error] Failed to write source_candidates.yaml: {e}")
        return 1

    print(
        f"[sources] Generated source_candidates.yaml at {candidates_path}"
    )

    # Generate collector_tasks.json if local signal tasks exist
    from multi_agent_brief.sources.local_signal_planner import (
        write_collector_tasks_json,
    )

    collector_path = (
        workspace / "output" / "intermediate" / "collector_tasks.json"
    )
    collector_tasks = write_collector_tasks_json(discovery, collector_path)
    if collector_tasks:
        print(
            f"[sources] Generated collector_tasks.json at"
            f" {collector_path}"
        )
        print(
            f"[sources] {len(collector_tasks['tasks'])} local signal"
            " collection tasks ready"
        )

    print(
        "[sources] Review and enable/disable sources, then run:"
    )
    print(
        f"  multi-agent-brief sources decide --config {args.config}"
        " --merge"
    )
    return 0


def _build_standard_search_tasks(discovery: dict) -> list[dict]:
    preferred_domains, excluded_domains = build_news_domain_preferences(discovery)
    tasks: list[dict] = []
    for query in build_search_queries(discovery):
        task = {"query": query, "domains": preferred_domains or None}
        if preferred_domains:
            task["preferred_domains"] = preferred_domains
        if excluded_domains:
            task["excluded_domains"] = excluded_domains
        tasks.append(task)
    return tasks
