from __future__ import annotations

import argparse
import json
from pathlib import Path

from multi_agent_brief import __version__
from multi_agent_brief.audit.deterministic import run_deterministic_audit
from multi_agent_brief.cli.init_wizard import (
    InitOnboardingRequired,
    _is_interactive,
    build_profile_from_args,
    create_demo_workspace,
    create_workspace,
    has_direct_init_args,
    missing_required_direct_init_args,
)
from multi_agent_brief.onboarding.io import load_onboarding_result
from multi_agent_brief.onboarding.mapper import map_onboarding_to_profile
from multi_agent_brief.sources.decider import (
    load_source_discovery,
    build_search_queries,
    generate_source_candidates,
    merge_candidates_to_sources,
)
from multi_agent_brief.analysis_modules.market_competitor.config import (
    load_competitor_candidates,
    save_competitor_candidates,
    merge_candidates_to_universe,
    load_competitor_universe,
    generate_candidates_template,
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

    run_parser = subparsers.add_parser(
        "run",
        help="Deprecated — use 'prepare' instead. Prints migration guidance.",
    )
    run_parser.add_argument("input_dir", nargs="?", help="[ignored]")
    run_parser.add_argument("--config", help="[ignored]")
    run_parser.add_argument("--output", help="[ignored]")
    run_parser.add_argument("--name", help="[ignored]")
    run_parser.add_argument("--language")
    run_parser.add_argument("--audience")
    run_parser.add_argument("--industry", help="[ignored]")
    run_parser.add_argument("--days", type=int, help="[ignored]")

    prepare_parser = subparsers.add_parser("prepare", help="Run deterministic pipeline: source collection → Scout → Screener → Claim Ledger → draft artifacts.")
    prepare_parser.add_argument("--config", required=True, help="Path to config.yaml in the workspace.")
    prepare_parser.add_argument("--input", help="Override input directory.")
    prepare_parser.add_argument("--output", help="Override output directory.")

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

    # competitors subcommand group
    comp_parser = subparsers.add_parser("competitors", help="Market & competitor universe management.")
    comp_sub = comp_parser.add_subparsers(dest="competitors_action", required=True)

    comp_init = comp_sub.add_parser("init", help="Create empty competitor_candidates.yaml template.")
    comp_init.add_argument("--config", required=True, help="Path to config.yaml in the workspace.")

    comp_list = comp_sub.add_parser("list", help="List pending competitor candidates.")
    comp_list.add_argument("--config", required=True, help="Path to config.yaml in the workspace.")

    comp_merge = comp_sub.add_parser("merge", help="Merge approved candidates into competitor_universe.yaml.")
    comp_merge.add_argument("--config", required=True, help="Path to config.yaml in the workspace.")
    comp_merge.add_argument("--candidates", help="Path to competitor_candidates.yaml.")

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
    print("[notice] 'multi-agent-brief run' has been replaced by 'multi-agent-brief prepare'.")
    print("         'prepare' runs the same deterministic pipeline (source collection → Scout →")
    print("         Screener → Claim Ledger → draft artifacts) and is used by /generate-brief.")
    print("")
    print("  Instead of: multi-agent-brief run --config workspace/config.yaml")
    print("  Use:        multi-agent-brief prepare --config workspace/config.yaml")
    return 1


def run_prepare_from_args(args: argparse.Namespace) -> int:
    """Run the deterministic pipeline (source collection → Scout → Screener →
    Claim Ledger → draft artifacts).  Used by /generate-brief as step 3."""
    config_path = Path(args.config)
    config = load_config(str(config_path))

    input_dir = args.input
    output_dir = args.output

    settings = build_run_settings(
        config=config,
        input_dir=input_dir,
        output_dir=output_dir,
        name=None,
        language=None,
        audience=None,
    )
    context = PipelineContext(**settings)
    outputs = BriefPipeline().run(context)
    print(f"[prepare] Pipeline complete — {len(outputs)} stages run.")
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
        print(f"Run: /generate-brief {target} in Claude Code to generate a real brief.")
        return 0

    from_onboarding = getattr(args, "from_onboarding", None)
    if from_onboarding:
        onboarding = load_onboarding_result(from_onboarding)
        profile = map_onboarding_to_profile(onboarding)

        # Validate required fields — agent must not pass empty/sentinel values
        missing_onboarding: list[str] = []
        if not profile.company:
            missing_onboarding.append("company_or_subject")
        if not profile.industry_text:
            missing_onboarding.append("industry_or_theme")
        if not profile.task_objective and not profile.brief_title:
            missing_onboarding.append("brief_objective")
        if missing_onboarding:
            print("[error] Onboarding data is incomplete. Required fields are missing or empty:")
            for f in missing_onboarding:
                print(f"  - {f}")
            print("        Run the interactive onboarding wizard: multi-agent-brief init <workspace>")
            print("        Or provide all required fields in onboarding.json before using --from-onboarding.")
            return 1

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
        missing = missing_required_direct_init_args(args)
        if missing and has_direct_init_args(args):
            print("[error] Direct init with CLI args is incomplete.")
            print("        Start conversational onboarding first and run:")
            print("        multi-agent-brief init <workspace> --from-onboarding onboarding.json")
            print("        Developer-only direct init must provide all business fields:")
            print(f"        missing: {', '.join(missing)}")
            return 1
        if not _is_interactive() and missing:
            print("[error] Non-interactive init cannot create a workspace from defaults.")
            print("        Start conversational onboarding first and run:")
            print("        multi-agent-brief init <workspace> --from-onboarding onboarding.json")
            print("        Developer-only direct init must provide all business fields:")
            print(f"        missing: {', '.join(missing)}")
            return 1
        target = Path(args.target)
        try:
            profile = build_profile_from_args(args)
        except InitOnboardingRequired as exc:
            print(f"[error] {exc}")
            print("        Run init in an interactive terminal, or use --from-onboarding onboarding.json.")
            return 1

    create_workspace(target, profile, force=args.force)
    print(f"Created brief workspace: {target}")
    print(f"Run: /generate-brief {target} in Claude Code to generate a real brief.")

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
        from multi_agent_brief.sources.web_search import WebSearchProvider, backend_api_key_env
        provider = WebSearchProvider()
        try:
            backend = provider._get_backend(ws_config.web_search)
        except Exception as exc:
            print(f"[error] Failed to initialize search backend: {exc}")
            return 1

        if not backend.is_available():
            api_key_env = backend_api_key_env(backend, ws_config.web_search)
            key_hint = f" Set {api_key_env}." if api_key_env else ""
            print(f"[error] Search backend '{backend.name}' is configured but not available.{key_hint}")
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
        from multi_agent_brief.sources.decider import _save_yaml
        _save_yaml(candidates_path, candidates)
    except Exception as e:
        print(f"[error] Failed to write source_candidates.yaml: {e}")
        return 1

    print(f"[sources] Generated source_candidates.yaml at {candidates_path}")
    print("[sources] Review and enable/disable sources, then run:")
    print(f"  multi-agent-brief sources decide --config {args.config} --merge")
    return 0


def run_competitors_init_from_args(args: argparse.Namespace) -> int:
    """Create competitor_candidates.yaml template.

    Writes an empty template for manual editing.  LLM-assisted competitor
    discovery is available via slash command ``/propose-competitors <workspace>``
    in Claude Code / Codex.
    """
    config_path = Path(args.config)
    workspace = config_path.parent
    candidates_path = workspace / "competitor_candidates.yaml"

    if candidates_path.exists():
        candidates = load_competitor_candidates(candidates_path)
        if candidates:
            print("[competitors] competitor_candidates.yaml already exists with candidates.")
            print("              Run 'competitors list' to review, then 'competitors merge' to confirm.")
            return 0

    template = generate_candidates_template()
    save_competitor_candidates(template["candidates"], candidates_path)
    print(f"[competitors] Created empty competitor_candidates.yaml at {candidates_path}")
    print("[competitors] Add candidate competitors to this file, then run:")
    print(f"  multi-agent-brief competitors merge --config {args.config}")
    print("[hint] For LLM-assisted discovery, use /propose-competitors <workspace> in Claude Code.")
    return 0


def run_competitors_list_from_args(args: argparse.Namespace) -> int:
    """List pending competitor candidates."""
    config_path = Path(args.config)
    workspace = config_path.parent
    candidates_path = workspace / "competitor_candidates.yaml"

    candidates = load_competitor_candidates(candidates_path)
    if not candidates:
        print("[competitors] No pending candidates. Run 'competitors init' first.")
        return 0

    pending = [c for c in candidates if not c.get("approved", False)]
    approved = [c for c in candidates if c.get("approved", False)]

    if pending:
        print(f"Pending ({len(pending)}):")
        for c in pending:
            print(f"  [{c.get('entity_id', '?')}] {c.get('name', '?')}  "
                  f"relation={c.get('relation', '?')}  "
                  f"suggested_by={c.get('suggested_by', '?')}")
    if approved:
        print(f"Approved ({len(approved)}):")
        for c in approved:
            print(f"  [{c.get('entity_id', '?')}] {c.get('name', '?')}")
    if not pending and not approved:
        print("[competitors] Candidate list is empty.")

    return 0


def run_competitors_merge_from_args(args: argparse.Namespace) -> int:
    """Merge approved candidates into competitor_universe.yaml."""
    config_path = Path(args.config)
    workspace = config_path.parent
    candidates_path = Path(args.candidates) if args.candidates else workspace / "competitor_candidates.yaml"
    universe_path = workspace / "competitor_universe.yaml"

    if not candidates_path.exists():
        print(f"[error] competitor_candidates.yaml not found: {candidates_path}")
        return 1

    if not universe_path.exists():
        print(f"[error] competitor_universe.yaml not found: {universe_path}")
        print("[hint] Re-run 'multi-agent-brief init' to regenerate workspace files.")
        return 1

    added = merge_candidates_to_universe(candidates_path, universe_path)
    print(f"[competitors] Merged {added} entities into competitor_universe.yaml")

    universe = load_competitor_universe(universe_path)
    if universe.entities:
        print(f"[competitors] Tracking {len(universe.entities)} entities: "
              f"{', '.join(e.name for e in universe.entities)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "run":
        return run_pipeline_from_args(args)
    if args.command == "prepare":
        return run_prepare_from_args(args)
    if args.command == "audit":
        return run_audit_from_args(args)
    if args.command == "init":
        return init_workspace_from_args(args)
    if args.command == "doctor":
        return run_doctor_from_args(args)
    if args.command == "sources":
        if args.sources_action == "decide":
            return run_sources_decide_from_args(args)
    if args.command == "competitors":
        if args.competitors_action == "init":
            return run_competitors_init_from_args(args)
        if args.competitors_action == "list":
            return run_competitors_list_from_args(args)
        if args.competitors_action == "merge":
            return run_competitors_merge_from_args(args)
    if args.command == "version":
        print(__version__)
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
