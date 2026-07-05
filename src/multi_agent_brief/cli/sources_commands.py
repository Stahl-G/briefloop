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


def handle_sources(args: argparse.Namespace) -> int:
    """Dispatch sources subcommands."""
    if args.sources_action == "decide":
        return _sources_decide(args)
    if args.sources_action == "materialize-pack":
        return _sources_materialize_pack(args)
    if args.sources_action == "add-file":
        return _sources_add_file(args)
    if args.sources_action == "add-rss":
        return _sources_add_rss(args)
    if args.sources_action == "add-web-search":
        return _sources_add_web_search(args)
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
            "[hint] Run `briefloop init` first to create the"
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
        try:
            result = merge_candidates_to_sources(sources_path, candidates_path)
        except SourceCandidatesError as exc:
            print(f"[error] {exc.error_code}: {exc}")
            return 1
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
        f"  briefloop sources decide --config {args.config}"
        " --merge"
    )
    return 0


def _sources_materialize_pack(args: argparse.Namespace) -> int:
    try:
        result = materialize_source_evidence_pack(
            config_path=args.config,
            force=bool(args.force),
        )
    except SourceEvidencePackError as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, sort_keys=True))
        else:
            print(f"[error] {exc}")
        return 1

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    print(
        "[sources] Materialized durable source evidence pack:"
        f" {result['record_count']} records"
    )
    print(f"[sources] Manifest: {result['manifest_path']}")
    if result.get("error_count"):
        print(f"[sources] Provider errors recorded: {result['error_count']}")
    print(
        "[sources] Boundary: source evidence records are durable inputs, not"
        " semantic support proof or delivery approval."
    )
    return 0


def _sources_add_file(args: argparse.Namespace) -> int:
    try:
        workspace = _workspace_from_sources_args(args)
        result = add_file_sources(
            workspace=workspace,
            values=list(args.paths),
            source_category=args.category,
            language=args.language,
            name=args.name,
        )
    except (OSError, SourceHubError, yaml.YAMLError) as exc:
        return _print_sourcehub_error("sources.add-file", args, exc)
    return _print_sourcehub_result("sources.add-file", args, result)


def _sources_add_rss(args: argparse.Namespace) -> int:
    try:
        workspace = _workspace_from_sources_args(args)
        result = add_rss_feed(
            workspace=workspace,
            url=args.url,
            name=args.name,
            source_category=args.category,
            language=args.language,
        )
    except (OSError, SourceHubError, yaml.YAMLError) as exc:
        return _print_sourcehub_error("sources.add-rss", args, exc)
    return _print_sourcehub_result("sources.add-rss", args, result)


def _sources_add_web_search(args: argparse.Namespace) -> int:
    try:
        workspace = _workspace_from_sources_args(args)
        result = add_web_search_handoff(
            workspace=workspace,
            query=args.query,
            domains=list(args.domains or []),
            max_results=int(args.max_results),
            recency_days=args.recency_days,
        )
    except (OSError, SourceHubError, yaml.YAMLError) as exc:
        return _print_sourcehub_error("sources.add-web-search", args, exc)
    return _print_sourcehub_result("sources.add-web-search", args, result)


def _add_workspace_selector(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--workspace",
        help="BriefLoop workspace path. Defaults to the current directory.",
    )
    parser.add_argument(
        "--config",
        help="Path to config.yaml. Overrides --workspace when provided.",
    )


def _workspace_from_sources_args(args: argparse.Namespace) -> Path:
    config = getattr(args, "config", None)
    if config:
        return Path(config).expanduser().resolve().parent
    workspace = getattr(args, "workspace", None)
    if workspace:
        return Path(workspace).expanduser().resolve()
    return Path.cwd().resolve()


def _print_sourcehub_error(label: str, args: argparse.Namespace, exc: Exception) -> int:
    payload = {
        "ok": False,
        "action": label,
        "error": str(exc),
        "boundary": "sourcehub_lite_source_setup_only",
    }
    if getattr(args, "json", False):
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"[error] {exc}")
    return 1


def _print_sourcehub_result(label: str, args: argparse.Namespace, result: dict) -> int:
    if getattr(args, "json", False):
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    print(f"[{label}] ok: {result.get('ok')}")
    if label == "sources.add-file":
        print(f"registered_files: {result.get('source_count')}")
        for source in result.get("sources", []):
            print(f"- {source.get('path')}")
        print("boundary: source setup only; no collection, span extraction, or support judgment")
    elif label == "sources.add-rss":
        feed = result.get("feed") if isinstance(result.get("feed"), dict) else {}
        print(f"feed: {feed.get('name')} <{feed.get('url')}>")
        print("boundary: feed registration only; no fetch was run")
    elif label == "sources.add-web-search":
        task = result.get("task") if isinstance(result.get("task"), dict) else {}
        print(f"query: {task.get('query')}")
        print("boundary: runtime web-search handoff only; no Python search was run")
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
