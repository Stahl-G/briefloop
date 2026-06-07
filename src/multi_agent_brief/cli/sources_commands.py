"""sources and doctor — source discovery and health-check commands."""

from __future__ import annotations

import argparse
from pathlib import Path

from multi_agent_brief.sources.decider import (
    load_source_discovery,
    build_search_queries,
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

    # Default: generate source_candidates.yaml
    queries = build_search_queries(discovery)
    print(
        f"[sources] Source discovery for:"
        f" {discovery.get('company', 'N/A')}"
        f" ({discovery.get('industry', 'N/A')})"
    )
    print(f"[sources] Generated {len(queries)} search queries:")
    for i, q in enumerate(queries, 1):
        print(f"  {i}. {q}")

    search_results = None
    if args.search:
        # Check if a real search backend is configured
        ws_config = (
            load_sources_config(sources_path)
            if sources_path.exists()
            else None
        )
        has_backend = (
            ws_config
            and ws_config.web_search.get("enabled")
            and ws_config.web_search.get("backend")
            and ws_config.web_search.get("backend") != "mock"
        )
        if not has_backend:
            print(
                "[error] --search requires a configured search backend."
            )
            print(
                "        Supported backends: tavily, exa, brave, firecrawl,"
                " serper."
            )
            print(
                "        Enable web_search in sources.yaml with a real"
                " backend and API key,"
            )
            print(
                "        or run without --search to generate template"
                " candidates."
            )
            return 1

        # Actually execute searches via the configured backend
        from multi_agent_brief.sources.web_search import (
            WebSearchProvider,
            backend_api_key_env,
        )

        provider = WebSearchProvider()
        try:
            backend = provider._get_backend(ws_config.web_search)
        except Exception as exc:
            print(
                f"[error] Failed to initialize search backend: {exc}"
            )
            return 1

        if not backend.is_available():
            api_key_env = backend_api_key_env(
                backend, ws_config.web_search
            )
            key_hint = f" Set {api_key_env}." if api_key_env else ""
            print(
                f"[error] Search backend '{backend.name}' is configured"
                f" but not available.{key_hint}"
            )
            return 1

        print(
            f"[sources] Executing {len(queries)} search queries via"
            f" backend: {backend.name}"
        )
        search_results = []
        max_results = ws_config.web_search.get("max_results", 10)
        for q in queries:
            try:
                results = backend.search(q, max_results=max_results)
                search_results.append(
                    {
                        "query": q,
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
                print(f"  [{len(results)} results] {q}")
            except Exception as exc:
                print(f"  [error] Search failed for '{q}': {exc}")
                # Continue with remaining queries; errors are surfaced to user

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
