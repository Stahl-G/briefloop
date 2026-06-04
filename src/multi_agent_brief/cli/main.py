from __future__ import annotations

import argparse
import json
from pathlib import Path

from multi_agent_brief import __version__
from multi_agent_brief.audit.deterministic import run_deterministic_audit
from multi_agent_brief.cli.init_wizard import build_profile_from_args, create_demo_workspace, create_workspace
from multi_agent_brief.onboarding.io import load_onboarding_result
from multi_agent_brief.onboarding.mapper import map_onboarding_to_profile
from multi_agent_brief.sources.decider import (
    load_source_discovery,
    build_search_queries,
    generate_source_candidates,
    merge_candidates_to_sources,
)
from multi_agent_brief.sources.doctor import run_doctor, format_doctor_report
from multi_agent_brief.sources.registry import load_sources_config
from multi_agent_brief.core.claim_ledger import ClaimLedger
from multi_agent_brief.core.config import build_run_settings, load_config
from multi_agent_brief.core.pipeline import BriefPipeline
from multi_agent_brief.core.schemas import PipelineContext


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="multi-agent-brief")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run deterministic brief preparation pipeline (produces draft artifacts, not final brief).")
    run_parser.add_argument("input_dir", nargs="?", help="Directory containing .md, .txt, or .json input files.")
    run_parser.add_argument("--config", help="YAML config file. Provides input/output/project settings.")
    run_parser.add_argument("--output", help="Output directory.")
    run_parser.add_argument("--name", help="Brief title.")
    run_parser.add_argument("--language")
    run_parser.add_argument("--audience")
    run_parser.add_argument("--industry", help="Industry for source planning, e.g. manufacturing.")
    run_parser.add_argument("--days", type=int, help="Source recency in days.")

    audit_parser = subparsers.add_parser("audit", help="Run deterministic audit on an existing Markdown brief.")
    audit_parser.add_argument("brief", help="Markdown brief to audit.")
    audit_parser.add_argument("--ledger", required=True, help="Claim Ledger JSON file.")
    audit_parser.add_argument("--output", help="Optional path for audit_report.json.")
    audit_parser.add_argument("--report-date", default="", help="Report date for stale-source checks, e.g. 2026-06-02.")
    audit_parser.add_argument("--max-source-age-days", type=int, help="Maximum allowed source age.")
    audit_parser.add_argument("--fail-on-stale-source", action="store_true", help="Treat stale sources as high-severity findings.")

    init_parser = subparsers.add_parser("init", help="Create a reusable brief workspace.")
    init_parser.add_argument("target", nargs="?", default="brief-workspace", help="Target workspace directory.")
    init_parser.add_argument("--demo", action="store_true", help="Create the existing synthetic demo workspace.")
    init_parser.add_argument("--force", action="store_true", help="Overwrite existing init files.")
    init_parser.add_argument("--language", choices=["en-US", "zh-CN", "bilingual"], help="Wizard/interface language.")
    init_parser.add_argument("--output-language", choices=["en-US", "zh-CN", "bilingual"], help="Generated brief language.")
    init_parser.add_argument("--company", help="Company or organization name.")
    init_parser.add_argument("--role", help="User role, e.g. strategy_office.")
    init_parser.add_argument("--industry", help="Industry slug, e.g. manufacturing.")
    init_parser.add_argument("--title", help="Brief title.")
    init_parser.add_argument("--audience", help="Target reader group.")
    init_parser.add_argument("--focus-areas", help="Comma-separated focus areas.")
    init_parser.add_argument("--cadence", choices=["weekly", "biweekly", "monthly", "ad_hoc"], help="Reporting cadence.")
    init_parser.add_argument("--selector-max-items", type=int, help="Maximum selected items per brief.")
    init_parser.add_argument("--rag", choices=["on", "off"], help="Enable or disable retrieval settings.")
    init_parser.add_argument("--retrieval-provider", choices=["ollama", "gemini"], help="Retrieval provider.")
    init_parser.add_argument("--output-formats", help="Comma-separated output formats.")
    init_parser.add_argument("--source-profile", choices=["conservative", "research", "aggressive_signal", "custom", "llm_decide"], help="Source collection profile.")
    init_parser.add_argument("--tavily", action="store_true", help="Enable Tavily live web search backend.")
    init_parser.add_argument("--from-onboarding", help="Path to onboarding.json for conversational init.")

    doctor_parser = subparsers.add_parser("doctor", help="Check source configuration health.")
    doctor_parser.add_argument("--config", required=True, help="Path to config.yaml in the workspace.")

    # sources subcommand
    sources_parser = subparsers.add_parser("sources", help="Source discovery and management.")
    sources_sub = sources_parser.add_subparsers(dest="sources_action", required=True)

    decide_parser = sources_sub.add_parser("decide", help="Resolve llm_decide profile into concrete source candidates.")
    decide_parser.add_argument("--config", required=True, help="Path to config.yaml in the workspace.")
    decide_parser.add_argument("--search", action="store_true", help="Run web search to discover sources (requires search backend).")
    decide_parser.add_argument("--merge", action="store_true", help="Merge approved source_candidates.yaml into sources.yaml.")
    decide_parser.add_argument("--candidates", help="Path to source_candidates.yaml (for --merge).")

    subparsers.add_parser("version", help="Print package version.")
    return parser


def _print_tavily_guidance() -> None:
    """Print setup guidance for Tavily live search."""
    print()
    print("Tavily live search is enabled. To use it, set the TAVILY_API_KEY")
    print("environment variable before running the pipeline.")
    print()
    print("  Do not paste API keys into chat, config files, README, or GitHub.")
    print("  Keys should be stored in environment variables only.")
    print()
    print("  Check configuration: multi-agent-brief doctor --config <workspace>/config.yaml")


def run_pipeline_from_args(args: argparse.Namespace) -> int:
    print()
    print("NOTE: This command prepares intermediate artifacts only.")
    print("It does not generate a final user-facing brief.")
    print("Use Claude Code / Codex agents to produce the final brief from these artifacts.")
    print()

    config = load_config(args.config) if args.config else None
    settings = build_run_settings(
        config=config,
        input_dir=args.input_dir,
        output_dir=args.output,
        name=args.name,
        language=args.language,
        audience=args.audience,
    )

    # Handle --industry and --days: create SourceConfig and attach to context
    industry = getattr(args, "industry", None) or (config or {}).get("project", {}).get("industry", "")
    days = getattr(args, "days", None) or (config or {}).get("report", {}).get("max_source_age_days")

    context = PipelineContext(**settings)

    # Determine source config: check sources.yaml existence as primary signal
    config_dir = Path(config.get("_config_dir", ".")) if config else Path(".")
    sources_path = config_dir / "sources.yaml"

    has_source_settings = bool(
        config and (
            config.get("source")
            or config.get("source_strategy")
            or sources_path.exists()
        )
    )

    if has_source_settings:
        from multi_agent_brief.sources.base import SourceConfig

        if sources_path.exists():
            source_config = load_sources_config(sources_path)
            # Ensure industry from project config is available even if sources.yaml
            # was generated by an older version that omitted it from source_strategy.
            if not source_config.industry and industry:
                source_config.industry = industry
        else:
            # Build from args/config
            enabled = ["manual"]
            if industry:
                enabled.append("rss")
            source_config = SourceConfig(
                profile="research",
                industry=industry or "",
                enabled_providers=enabled,
                manual={"enabled": True, "sources": [{"name": "Local Input Directory", "path": settings["input_dir"], "category": "local_files", "enabled": True}]},
                rss={"enabled": bool(industry), "feeds": []},
            )
        if days:
            context.max_source_age_days = days
        context.metadata["source_config"] = source_config

        # Read source_discovery for query enhancement
        if sources_path.exists():
            try:
                import yaml as _yaml
                _sy = _yaml.safe_load(sources_path.read_text(encoding="utf-8")) or {}
                _discovery = _sy.get("source_discovery", {})
                if _discovery:
                    context.metadata["source_discovery"] = _discovery
            except Exception:
                pass

    # Pre-flight check: llm_decide workspace without resolved sources
    _config_dir = Path(config.get("_config_dir", ".")) if config else Path(".")
    _sources_yaml = _config_dir / "sources.yaml"
    if _sources_yaml.exists():
        try:
            import yaml as _yaml
            _sy = _yaml.safe_load(_sources_yaml.read_text(encoding="utf-8")) or {}
            _ss = _sy.get("source_strategy", {}) or {}
            if _ss.get("requires_agent_resolution") and _ss.get("decision_mode") == "agent_decide":
                _candidates = _config_dir / "source_candidates.yaml"
                if not _candidates.exists():
                    print("[WARN] Workspace uses llm_decide source profile but source_candidates.yaml has not been generated.")
                    print("       Run: multi-agent-brief sources decide --config <config.yaml>")
                    print("       The pipeline will continue with local/manual sources only.")
        except Exception:
            pass

    # Pre-flight check: if Tavily is enabled, verify API key exists
    _source_config = context.metadata.get("source_config")
    if _source_config and hasattr(_source_config, "web_search"):
        _ws = _source_config.web_search
        if _ws.get("enabled") and _ws.get("backend") == "tavily":
            import os
            api_key_env = _ws.get("api_key_env", "TAVILY_API_KEY")
            if not os.environ.get(api_key_env):
                print(f"[ERROR] Tavily live search is enabled, but {api_key_env} is not set.")
                print("  Set it as an environment variable before running the pipeline.")
                print("  Do not paste API keys into chat, config files, README, or GitHub.")
                print("  Aborting. Either set the key or disable web_search in sources.yaml.")
                return 1

    outputs = BriefPipeline().run(context)
    for output in outputs:
        print(f"[{output.agent_name}] {output.summary}")

    audit_report = context.report_state.audit_report
    if audit_report and audit_report.audit_status == "fail":
        print("Audit failed. Review audit_report.json for details.")
        return 2
    return 0


def run_audit_from_args(args: argparse.Namespace) -> int:
    brief_path = Path(args.brief)
    if not brief_path.exists():
        raise FileNotFoundError(f"Brief not found: {brief_path}")
    ledger = ClaimLedger.import_json(args.ledger)
    report = run_deterministic_audit(
        brief_path.read_text(encoding="utf-8"),
        ledger,
        report_date=args.report_date,
        max_source_age_days=args.max_source_age_days,
        fail_on_stale_source=args.fail_on_stale_source,
    )
    payload = json.dumps(report.to_dict(), ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(payload, encoding="utf-8")
    else:
        print(payload)
    return 0 if report.audit_status != "fail" else 2


def _apply_cli_overrides(profile, args: argparse.Namespace) -> None:
    """Apply explicit CLI args onto a profile built from onboarding.

    Only overrides fields where the CLI arg is non-None / truthy.
    """
    from multi_agent_brief.cli.init_wizard import normalize_language, parse_list_arg, apply_rag_args

    if getattr(args, "language", None):
        lang = normalize_language(args.language)
        profile.interface_language = lang
        profile.output_language = lang
    if getattr(args, "output_language", None):
        profile.output_language = normalize_language(args.output_language)
    if getattr(args, "company", None):
        profile.company = args.company
    if getattr(args, "role", None):
        profile.role = args.role
    if getattr(args, "industry", None):
        profile.industry = args.industry
    if getattr(args, "title", None):
        profile.brief_title = args.title
    if getattr(args, "audience", None):
        profile.audience = args.audience
    focus = parse_list_arg(getattr(args, "focus_areas", None))
    if focus:
        profile.focus_areas = focus
    if getattr(args, "cadence", None):
        profile.cadence = args.cadence
    if getattr(args, "selector_max_items", None):
        profile.selector_max_items = args.selector_max_items
    apply_rag_args(profile, getattr(args, "rag", None), getattr(args, "retrieval_provider", None))
    formats = parse_list_arg(getattr(args, "output_formats", None))
    if formats:
        profile.output_formats = formats
    if getattr(args, "source_profile", None):
        profile.source_profile = args.source_profile
    if getattr(args, "tavily", False):
        profile.tavily_enabled = True


def init_workspace_from_args(args: argparse.Namespace) -> int:
    # Priority: explicit CLI target > onboarding.target > default "brief-workspace"
    if args.demo:
        target = Path(args.target)
        create_demo_workspace(target, force=args.force)
        print(f"Created demo workspace: {target}")
        print(f"Prepare draft artifacts: multi-agent-brief run --config {target / 'config.yaml'}")
        return 0

    from_onboarding = getattr(args, "from_onboarding", None)
    if from_onboarding:
        onboarding = load_onboarding_result(from_onboarding)
        profile = map_onboarding_to_profile(onboarding)
        # Apply any explicit CLI overrides on top of onboarding values
        _apply_cli_overrides(profile, args)
        # CLI target overrides onboarding.target
        cli_target = args.target
        default_target = "brief-workspace"
        if cli_target and cli_target != default_target:
            target = Path(cli_target)
        elif onboarding.target and onboarding.target != "brief-workspace":
            target = Path(onboarding.target)
        else:
            target = Path(default_target)
    else:
        target = Path(args.target)
        profile = build_profile_from_args(args)

    create_workspace(target, profile, force=args.force)
    print(f"Created brief workspace: {target}")
    print(f"Prepare draft artifacts: multi-agent-brief run --config {target / 'config.yaml'}")

    # Print Tavily setup guidance if enabled
    if profile.tavily_enabled:
        _print_tavily_guidance()

    return 0


def run_doctor_from_args(args: argparse.Namespace) -> int:
    results = run_doctor(config_path=args.config)
    print(format_doctor_report(results))
    errors = sum(1 for r in results if r.status == "ERROR")
    return 1 if errors else 0


def run_sources_decide_from_args(args: argparse.Namespace) -> int:
    config_path = Path(args.config)
    workspace = config_path.parent
    sources_path = workspace / "sources.yaml"

    if not sources_path.exists():
        print(f"[error] sources.yaml not found: {sources_path}")
        print("[hint] Run `multi-agent-brief init` first to create the workspace.")
        return 1

    discovery = load_source_discovery(sources_path)
    if not discovery:
        print("[error] No source_discovery section found in sources.yaml.")
        print("[hint] Re-init with --source-profile llm_decide to generate discovery policy.")
        return 1

    # --merge: merge candidates into sources.yaml
    if args.merge:
        candidates_path = Path(args.candidates) if args.candidates else workspace / "source_candidates.yaml"
        if not candidates_path.exists():
            print(f"[error] source_candidates.yaml not found: {candidates_path}")
            return 1
        result = merge_candidates_to_sources(sources_path, candidates_path)
        print(f"[sources] Merged {result['added_manual']} manual + {result['added_rss']} RSS sources into sources.yaml")
        return 0

    # Default: generate source_candidates.yaml
    queries = build_search_queries(discovery)
    print(f"[sources] Source discovery for: {discovery.get('company', 'N/A')} ({discovery.get('industry', 'N/A')})")
    print(f"[sources] Generated {len(queries)} search queries:")
    for i, q in enumerate(queries, 1):
        print(f"  {i}. {q}")

    search_results = None
    if args.search:
        # Check if a real search backend is configured
        ws_config = load_sources_config(sources_path) if sources_path.exists() else None
        has_backend = (
            ws_config
            and ws_config.web_search.get("enabled")
            and ws_config.web_search.get("backend")
            and ws_config.web_search.get("backend") != "mock"
        )
        if not has_backend:
            print("[error] --search requires a configured search backend (e.g. Tavily).")
            print("        Enable web_search in sources.yaml with a real backend and API key,")
            print("        or run without --search to generate template candidates.")
            return 1

        # Actually execute searches via the configured backend
        from multi_agent_brief.sources.web_search import WebSearchProvider
        provider = WebSearchProvider()
        try:
            backend = provider._get_backend(ws_config.web_search)
        except Exception as exc:
            print(f"[error] Failed to initialize search backend: {exc}")
            return 1

        if not backend.is_available():
            print("[error] Search backend is configured but not available (API key missing?).")
            return 1

        print(f"[sources] Executing {len(queries)} search queries via backend: {backend.name}")
        search_results = []
        max_results = ws_config.web_search.get("max_results", 10)
        for q in queries:
            try:
                results = backend.search(q, max_results=max_results)
                search_results.append({
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
                })
                print(f"  [{len(results)} results] {q}")
            except Exception as exc:
                print(f"  [error] Search failed for '{q}': {exc}")
                # Continue with remaining queries; errors are surfaced to user

    candidates = generate_source_candidates(discovery, search_results)
    candidates_path = workspace / "source_candidates.yaml"
    try:
        import yaml
        with open(candidates_path, "w", encoding="utf-8") as f:
            yaml.dump(candidates, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    except Exception as e:
        # Fallback: write as JSON
        import json
        candidates_path = workspace / "source_candidates.json"
        candidates_path.write_text(json.dumps(candidates, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[sources] Generated source_candidates.yaml at {candidates_path}")
    print("[sources] Review and enable/disable sources, then run:")
    print(f"  multi-agent-brief sources decide --config {args.config} --merge")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "run":
        return run_pipeline_from_args(args)
    if args.command == "audit":
        return run_audit_from_args(args)
    if args.command == "init":
        return init_workspace_from_args(args)
    if args.command == "doctor":
        return run_doctor_from_args(args)
    if args.command == "sources":
        if args.sources_action == "decide":
            return run_sources_decide_from_args(args)
    if args.command == "version":
        print(__version__)
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
