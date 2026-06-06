from __future__ import annotations

import argparse
import json
import sys
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
from multi_agent_brief.hermes import (
    build_hermes_cron_plan,
    install_hermes_skill,
    render_hermes_cron_commands,
    render_hermes_cron_markdown,
    render_hermes_prompt,
    render_hermes_setup_success,
    render_hermes_skill,
)
from multi_agent_brief.hermes.adapter import sync_cached_package_source, write_json
from multi_agent_brief.cli.start_commands import (
    VALID_RUNTIMES,
    build_handoff,
    render_handoff_cli,
    write_handoff_artifacts,
)
from multi_agent_brief.core.claim_ledger import ClaimLedger
from multi_agent_brief.core.config import build_run_settings, load_config
from multi_agent_brief.outputs.finalize import finalize_reader_outputs
from multi_agent_brief.sources.registry import load_sources_config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="multi-agent-brief")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser(
        "run",
        help="Deprecated — use the subagent-first /generate-brief workflow instead.",
    )
    run_parser.add_argument("input_dir", nargs="?", help="[ignored]")
    run_parser.add_argument("--config", help="[ignored]")
    run_parser.add_argument("--output", help="[ignored]")
    run_parser.add_argument("--name", help="[ignored]")
    run_parser.add_argument("--language")
    run_parser.add_argument("--audience")
    run_parser.add_argument("--industry", help="[ignored]")
    run_parser.add_argument("--days", type=int, help="[ignored]")

    prepare_parser = subparsers.add_parser(
        "prepare",
        help="Deprecated — Python no longer runs the brief workflow. Use /generate-brief <workspace>.",
    )
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

    finalize_parser = subparsers.add_parser("finalize", help="Regenerate reader-facing Markdown/DOCX from output/intermediate/audited_brief.md.")
    finalize_parser.add_argument("--config", required=True, help="Path to config.yaml in the workspace.")
    finalize_parser.add_argument("--output", help="Override output directory.")

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
    init_parser.add_argument("--tavily", action="store_true", help="Legacy alias: enable Tavily live web search backend.")
    init_parser.add_argument("--web-search-mode", choices=["disabled", "runtime_tool", "external_api", "configure_later"], help="How web search is provided.")
    init_parser.add_argument("--search-backend", choices=["tavily", "exa", "brave", "firecrawl", "serper"], help="Search backend for --web-search-mode external_api.")
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

    # features subcommand
    features_parser = subparsers.add_parser("features", help="Show all available features and their status.")
    features_parser.add_argument("workspace", nargs="?", help="Optional workspace path to check provider status.")
    features_parser.add_argument("--info", metavar="ID", help="Show details for a specific capability.")
    features_parser.add_argument("--json", action="store_true", dest="json_output", help="Output as JSON.")

    # capability alias — user-facing synonym for features
    capability_parser = subparsers.add_parser("capability", help="Alias for 'features': show capabilities and setup status.")
    capability_parser.add_argument("workspace", nargs="?", help="Optional workspace path to check provider status.")
    capability_parser.add_argument("--info", metavar="ID", help="Show details for a specific capability.")
    capability_parser.add_argument("--json", action="store_true", dest="json_output", help="Output as JSON.")

    # recommend subcommand
    rec_parser = subparsers.add_parser("recommend", help="Recommend capabilities based on task description.")
    rec_parser.add_argument("workspace", nargs="?", help="Workspace path to check.")
    rec_parser.add_argument("--text", help="Task description text to scan for keywords.")
    rec_parser.add_argument("--json", action="store_true", dest="json_output", help="Output as JSON.")

    # setup subcommand
    setup_parser = subparsers.add_parser("setup", help="Apply recommended capabilities to a workspace.")
    setup_parser.add_argument("workspace", help="Workspace path to configure.")
    setup_parser.add_argument("--from-plan", help="Path to setup-plan.json to apply.")
    setup_parser.add_argument("--dry-run", action="store_true", help="Show what would change without writing.")

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

    # analysis-blocks subcommand
    ab_parser = subparsers.add_parser("analysis-blocks", help="Build analysis blocks from a claim ledger.")
    ab_parser.add_argument("--ledger", required=True, help="Path to claim_ledger.json.")
    ab_parser.add_argument("--output", help="Output path for analysis_blocks.json (default: same dir as ledger).")
    ab_parser.add_argument("--audience", default="default", choices=["management", "research", "default"], help="Heading style for markdown preview.")
    ab_parser.add_argument("--language", default="en-US", choices=["en-US", "zh-CN"], help="Language for labels.")
    ab_parser.add_argument("--markdown", action="store_true", help="Also print structured markdown to stdout.")

    # limitation-hygiene subcommand
    lh_parser = subparsers.add_parser("limitation-hygiene", help="Audit limitation hygiene from a claim ledger.")
    lh_parser.add_argument("--ledger", required=True, help="Path to claim_ledger.json.")
    lh_parser.add_argument("--output", help="Output path for limitation_hygiene_report.json (default: same dir as ledger).")

    # hermes subcommand group
    hermes_parser = subparsers.add_parser("hermes", help="Generate Hermes Agent skill and cron plans for scheduled briefs.")
    hermes_sub = hermes_parser.add_subparsers(dest="hermes_action", required=True)

    hermes_skill = hermes_sub.add_parser("skill", help="Write the Hermes SKILL.md for MABW cron jobs.")
    hermes_skill.add_argument("--output", help="Output SKILL.md path (default: .agents/hermes-skills/multi-agent-brief-hermes/SKILL.md).")

    hermes_plan = hermes_sub.add_parser("cron-plan", help="Generate a Hermes cron plan JSON/Markdown for a workspace.")
    hermes_plan.add_argument("--config", required=True, help="Path to workspace config.yaml.")
    hermes_plan.add_argument("--repo-workdir", help="Repository workdir for Hermes cron --workdir (default: current directory).")
    hermes_plan.add_argument("--cadence", help="Comma-separated cadences: weekly,monthly,daily. Defaults to config cadence or weekly.")
    hermes_plan.add_argument("--deliver", default="local", help="Hermes delivery target, e.g. local, feishu, telegram.")
    hermes_plan.add_argument("--profile", default="", help="Optional existing Hermes profile name.")
    hermes_plan.add_argument("--output", help="Output JSON path (default: workspace/output/intermediate/hermes_cron_plan.json).")
    hermes_plan.add_argument("--markdown", help="Optional Markdown output path.")

    hermes_commands = hermes_sub.add_parser("cron-commands", help="Print Hermes cron create commands for a workspace.")
    hermes_commands.add_argument("--config", required=True, help="Path to workspace config.yaml.")
    hermes_commands.add_argument("--repo-workdir", help="Repository workdir for Hermes cron --workdir (default: current directory).")
    hermes_commands.add_argument("--cadence", help="Comma-separated cadences: weekly,monthly,daily. Defaults to config cadence or weekly.")
    hermes_commands.add_argument("--deliver", default="local", help="Hermes delivery target, e.g. local, feishu, telegram.")
    hermes_commands.add_argument("--profile", default="", help="Optional existing Hermes profile name.")

    hermes_sync = hermes_sub.add_parser("sync-sources", help="Enable cached_package input/hermes_cache in workspace sources.yaml.")
    hermes_sync.add_argument("--config", required=True, help="Path to workspace config.yaml.")
    hermes_sync.add_argument("--cache-dir", default="input/hermes_cache", help="Cache path written into sources.yaml.")
    hermes_sync.add_argument("--dry-run", action="store_true", help="Show changes without writing.")

    hermes_install = hermes_sub.add_parser("install-skill", help="Install the MABW Hermes skill to a Hermes-discoverable directory.")
    hermes_install.add_argument("--target", help="Target skill directory (auto-detected if omitted).")

    hermes_prompt = hermes_sub.add_parser("prompt", help="Generate a Hermes run prompt for a workspace.")
    hermes_prompt.add_argument("--config", required=True, help="Path to workspace config.yaml.")
    hermes_prompt.add_argument("--repo-workdir", help="Repository workdir (default: current directory).")
    hermes_prompt.add_argument("--venv", help="Virtual env path (default: <repo>/.venv/{bin,Scripts}/activate).")

    # start — unified launcher (never generates brief)
    start_parser = subparsers.add_parser("start", help="Unified launcher: init, doctor, and runtime handoff. Never generates a brief.")
    start_parser.add_argument("--workspace", help="Path to workspace directory.")
    start_parser.add_argument("--runtime", default="hermes", choices=list(VALID_RUNTIMES),
                              help="Target runtime for handoff (default: hermes).")
    start_parser.add_argument("--repo-workdir", help="Repository workdir (default: current directory).")
    start_parser.add_argument("--venv", help="Virtual env path (default: auto-detect).")
    start_parser.add_argument("--skip-doctor", action="store_true", help="Skip doctor check.")

    # handoff — generate handoff artifact directly
    handoff_parser = subparsers.add_parser("handoff", help="Generate a runtime handoff artifact from a workspace config.")
    handoff_parser.add_argument("--config", required=True, help="Path to workspace config.yaml.")
    handoff_parser.add_argument("--runtime", default="hermes", choices=list(VALID_RUNTIMES),
                                help="Target runtime for handoff (default: hermes).")
    handoff_parser.add_argument("--repo-workdir", help="Repository workdir (default: current directory).")
    handoff_parser.add_argument("--venv", help="Virtual env path (default: auto-detect).")
    handoff_parser.add_argument("--skip-doctor", action="store_true", help="Skip doctor check.")

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


def _print_search_backend_guidance(profile) -> None:
    """Print safe, backend-agnostic web-search setup guidance."""
    backend = getattr(profile, "search_backend", "") or ("tavily" if getattr(profile, "tavily_enabled", False) else "")
    mode = getattr(profile, "web_search_mode", "disabled")
    backend_env = {
        "tavily": "TAVILY_API_KEY",
        "exa": "EXA_API_KEY",
        "brave": "BRAVE_SEARCH_API_KEY",
        "firecrawl": "FIRECRAWL_API_KEY",
        "serper": "SERPER_API_KEY",
    }
    if mode == "runtime_tool":
        print()
        print("Runtime web search is enabled. Make sure your execution runtime provides a web-search tool.")
        print("  Check configuration: multi-agent-brief doctor --config <workspace>/config.yaml")
        return
    if mode == "configure_later":
        print()
        print("Web search is marked for later configuration.")
        print("  Supported backends: tavily, exa, brave, firecrawl, serper")
        print("  Set one API key in .env or export it in your shell, then set web_search.backend in sources.yaml.")
        print("  Do not paste API keys into chat, config files, README, or GitHub.")
        return
    if mode == "external_api" and backend:
        env_var = backend_env.get(backend, "the backend API key env var")
        print()
        print(f"Web search backend '{backend}' is enabled. Set {env_var} in .env or your shell before running the pipeline.")
        print("  Supported backends: tavily, exa, brave, firecrawl, serper")
        print("  Do not paste API keys into chat, config files, README, or GitHub.")
        print("  Check configuration: multi-agent-brief doctor --config <workspace>/config.yaml")


def run_features_from_args(args: argparse.Namespace) -> int:
    """Show all available features with status indicators."""
    import json as json_mod

    from multi_agent_brief.capabilities.catalog import CAPABILITIES, get_capability, list_capabilities
    from multi_agent_brief.capabilities.detect import assess_capability

    # --info mode: single capability detail
    if args.info:
        cap = get_capability(args.info)
        if cap is None:
            print(f"[error] Unknown capability: {args.info}")
            print(f"        Available IDs: {', '.join(c.id for c in CAPABILITIES)}")
            return 1

        enabled_providers = None
        if args.workspace:
            from multi_agent_brief.sources.registry import load_sources_config
            sources_path = Path(args.workspace) / "sources.yaml"
            if sources_path.exists():
                sc = load_sources_config(sources_path)
                enabled_providers = set(sc.enabled_providers)

        status = assess_capability(cap.id, args.workspace, enabled_providers)

        if args.json_output:
            print(json_mod.dumps({
                "id": cap.id,
                "name": cap.name,
                "summary": cap.summary,
                "category": cap.category,
                "visibility": cap.visibility,
                "maturity": cap.maturity,
                "state": status.state,
                "notes": status.notes,
                "options": [{"id": o.id, "name": o.name, "description": o.description} for o in cap.options],
                "requirements": cap.requirements,
            }, ensure_ascii=False, indent=2))
        else:
            print(f"\n  {cap.name.get('en', cap.id)} ({cap.id})")
            print(f"  {cap.summary.get('en', '')}")
            print(f"  Category: {cap.category}  |  Visibility: {cap.visibility}  |  Maturity: {cap.maturity}")
            print(f"  Status: {status.state}")
            if status.notes:
                print(f"  Notes: {status.notes}")
            if cap.options:
                print(f"\n  Options:")
                for o in cap.options:
                    print(f"    {o.name:16s}  {o.description}")
            if cap.requirements:
                print(f"\n  Requirements:")
                for r in cap.requirements:
                    print(f"    - {r}")
            print()
        return 0

    # --json mode
    if args.json_output:
        enabled_providers = None
        if args.workspace:
            from multi_agent_brief.sources.registry import load_sources_config
            sources_path = Path(args.workspace) / "sources.yaml"
            if sources_path.exists():
                sc = load_sources_config(sources_path)
                enabled_providers = set(sc.enabled_providers)

        items = []
        for cap in CAPABILITIES:
            status = assess_capability(cap.id, args.workspace, enabled_providers)
            items.append({
                "id": cap.id,
                "name": cap.name.get("en", cap.id),
                "category": cap.category,
                "visibility": cap.visibility,
                "state": status.state,
                "notes": status.notes,
            })
        print(json_mod.dumps(items, ensure_ascii=False, indent=2))
        return 0

    # Default: human-readable table
    enabled_providers = None
    if args.workspace:
        from multi_agent_brief.sources.registry import load_sources_config
        sources_path = Path(args.workspace) / "sources.yaml"
        if sources_path.exists():
            sc = load_sources_config(sources_path)
            enabled_providers = set(sc.enabled_providers)

    symbols = {
        "ENABLED_READY": "✓",
        "ENABLED_NEEDS_SETUP": "!",
        "AVAILABLE": "○",
        "UNAVAILABLE": "—",
    }

    # Group by category
    categories: dict[str, list] = {}
    for cap in CAPABILITIES:
        categories.setdefault(cap.category, []).append(cap)

    category_labels = {
        "source": "Source Providers",
        "processing": "Processing",
        "analysis": "Analysis Modules",
        "output": "Output Formats",
        "integration": "Integration",
    }

    print()
    for cat, caps in categories.items():
        label = category_labels.get(cat, cat.title())
        print(f"━━━ {label} ━━━")
        for cap in caps:
            status = assess_capability(cap.id, args.workspace, enabled_providers)
            sym = symbols.get(status.state, "?")
            name = cap.name.get("en", cap.id)
            note = f"  — {status.notes}" if status.notes else ""
            print(f"  {sym} {name:40s}{note}")
        print()

    print("Run 'multi-agent-brief features --info <id>' for details on any feature.")
    print("Run 'multi-agent-brief features <workspace>' to check status against a workspace.")
    return 0


def run_recommend_from_args(args: argparse.Namespace) -> int:
    """Recommend capabilities based on task description or workspace config."""
    import json as json_mod

    from multi_agent_brief.capabilities.recommend import (
        recommend_from_config,
        recommend_from_input_dir,
        recommend_from_text,
        generate_setup_plan,
    )

    text_parts: list[str] = []

    # Collect text from --text flag
    if args.text:
        text_parts.append(args.text)

    # Collect from workspace
    workspace_dir = None
    enabled_providers = None
    if args.workspace:
        workspace_dir = Path(args.workspace)
        sources_path = workspace_dir / "sources.yaml"
        if sources_path.exists():
            from multi_agent_brief.sources.registry import load_sources_config
            sc = load_sources_config(sources_path)
            enabled_providers = set(sc.enabled_providers)

        config_path = workspace_dir / "config.yaml"
        if config_path.exists():
            from multi_agent_brief.core.config import load_config
            config = load_config(str(config_path))
            # Extract text from config
            proj = config.get("project", {})
            for key in ("name", "industry", "title"):
                if proj.get(key):
                    text_parts.append(str(proj[key]))

        # Scan input directory for file types
        input_dir = workspace_dir / "input"
        file_recs = recommend_from_input_dir(input_dir, enabled_providers)
    else:
        file_recs = []

    # Run text recommendations
    combined_text = " ".join(text_parts)
    text_recs = recommend_from_text(combined_text, enabled_providers) if combined_text else []

    all_recs = text_recs + file_recs

    if not all_recs:
        print("[recommend] No capability recommendations for this task.")
        print("[hint] Use --text to provide a task description, or specify a workspace.")
        return 0

    if args.json_output:
        plan = generate_setup_plan(all_recs, workspace_dir)
        print(json_mod.dumps(plan, ensure_ascii=False, indent=2))
    else:
        print(f"\nRecommendations ({len(all_recs)}):")
        print()
        for rec in all_recs:
            print(f"  → {rec.capability_id}")
            print(f"    Reason: {rec.reason}")
            print(f"    Rule: {rec.trigger_rule}")
            print()

    return 0


def run_setup_from_args(args: argparse.Namespace) -> int:
    """Apply a setup plan to a workspace (safe YAML merge)."""
    import json as json_mod

    workspace = Path(args.workspace)
    if not workspace.exists():
        print(f"[error] Workspace not found: {workspace}")
        return 1

    sources_path = workspace / "sources.yaml"
    config_path = workspace / "config.yaml"

    if not sources_path.exists():
        print(f"[error] sources.yaml not found in {workspace}")
        return 1

    # Load setup plan
    if args.from_plan:
        plan_path = Path(args.from_plan)
        if not plan_path.exists():
            print(f"[error] Setup plan not found: {plan_path}")
            return 1
        plan = json_mod.loads(plan_path.read_text(encoding="utf-8"))
    else:
        # Generate plan from workspace
        from multi_agent_brief.capabilities.recommend import (
            recommend_from_input_dir,
            recommend_from_text,
            generate_setup_plan,
        )
        text_parts = []
        if config_path.exists():
            from multi_agent_brief.core.config import load_config
            config = load_config(str(config_path))
            proj = config.get("project", {})
            for key in ("name", "industry", "title"):
                if proj.get(key):
                    text_parts.append(str(proj[key]))

        enabled_providers = None
        from multi_agent_brief.sources.registry import load_sources_config
        sc = load_sources_config(sources_path)
        enabled_providers = set(sc.enabled_providers)

        combined_text = " ".join(text_parts)
        recs = recommend_from_text(combined_text, enabled_providers) if combined_text else []
        recs += recommend_from_input_dir(workspace / "input", enabled_providers)
        plan = generate_setup_plan(recs, workspace)

    capabilities = plan.get("capabilities", [])
    if not capabilities:
        print("[setup] No capabilities to enable.")
        return 0

    # Show what would change
    if args.dry_run:
        print("[dry-run] Changes that would be applied:")
        for cap in capabilities:
            print(f"  → {cap['name']}: {cap.get('config_hint', 'enable in sources.yaml')}")
        print()
        print("[dry-run] No files modified.")
        return 0

    # Apply changes
    try:
        import yaml
    except ModuleNotFoundError:
        print("[error] PyYAML required for setup. Install: pip install pyyaml")
        return 1

    sources_data = yaml.safe_load(sources_path.read_text(encoding="utf-8")) or {}
    changes_made = 0

    for cap in capabilities:
        cap_id = cap["id"]
        if cap_id == "web_search":
            ws = sources_data.setdefault("web_search", {})
            if not ws.get("enabled"):
                ws["enabled"] = True
                # Keep legacy setup behavior deterministic, but surface that users may
                # switch backend to exa/brave/firecrawl/serper and the matching env var.
                ws.setdefault("backend", "tavily")
                ws.setdefault("api_key_env", "TAVILY_API_KEY")
                changes_made += 1
                print("  ✓ Enabled web_search in sources.yaml (default backend: tavily; alternatives: exa, brave, firecrawl, serper)")
        elif cap_id == "mineru":
            mu = sources_data.setdefault("mineru", {})
            if not mu.get("enabled"):
                mu["enabled"] = True
                changes_made += 1
                print(f"  ✓ Enabled mineru in sources.yaml")
        elif cap_id == "filing_resolver":
            fr = sources_data.setdefault("filing_resolver", {})
            if not fr.get("enabled"):
                fr["enabled"] = True
                changes_made += 1
                print(f"  ✓ Enabled filing_resolver in sources.yaml")
        elif cap_id == "feishu":
            fe = sources_data.setdefault("feishu", {})
            if not fe.get("enabled"):
                fe["enabled"] = True
                changes_made += 1
                print(f"  ✓ Enabled feishu in sources.yaml")
        elif cap_id == "rss":
            rss = sources_data.setdefault("rss", {})
            if not rss.get("enabled"):
                rss["enabled"] = True
                changes_made += 1
                print(f"  ✓ Enabled rss in sources.yaml")
        elif cap_id == "api_news":
            api = sources_data.setdefault("api", {})
            if not api.get("enabled"):
                api["enabled"] = True
                changes_made += 1
                print(f"  ✓ Enabled api in sources.yaml")
        elif cap_id == "market_competitor":
            if config_path.exists():
                config_text = config_path.read_text(encoding="utf-8")
                if "market_competitor" not in config_text:
                    config_data = yaml.safe_load(config_text) or {}
                    modules = config_data.setdefault("modules", {})
                    mc = modules.setdefault("market_competitor", {})
                    if not mc.get("enabled"):
                        mc["enabled"] = True
                        config_path.write_text(
                            yaml.safe_dump(config_data, sort_keys=False, default_flow_style=False),
                            encoding="utf-8",
                        )
                        changes_made += 1
                        print(f"  ✓ Enabled market_competitor in config.yaml")
        elif cap_id == "docx_output":
            if config_path.exists():
                config_text = config_path.read_text(encoding="utf-8")
                if "docx" not in config_text:
                    config_data = yaml.safe_load(config_text) or {}
                    output = config_data.setdefault("output", {})
                    formats = output.setdefault("formats", ["markdown"])
                    if "docx" not in formats:
                        formats.append("docx")
                        config_path.write_text(
                            yaml.safe_dump(config_data, sort_keys=False, default_flow_style=False),
                            encoding="utf-8",
                        )
                        changes_made += 1
                        print(f"  ✓ Added docx to output.formats in config.yaml")

    if changes_made:
        # Write updated sources.yaml
        sources_path.write_text(
            yaml.safe_dump(sources_data, sort_keys=False, default_flow_style=False),
            encoding="utf-8",
        )
        print(f"\n[setup] {changes_made} change(s) applied to {workspace}")
        print("[hint] Run 'multi-agent-brief doctor --config <workspace>/config.yaml' to verify.")
    else:
        print("[setup] All recommended capabilities are already enabled.")

    return 0


def run_pipeline_from_args(args: argparse.Namespace) -> int:
    print("[deprecated] multi-agent-brief run no longer runs the brief workflow.")
    print("MABW is subagent-first. Use /generate-brief <workspace> so external")
    print("Scout/Screener/Claim Ledger/Analyst/Editor/Auditor/Formatter subagents")
    print("execute the workflow.")
    print("")
    print("Python CLI commands remain available for tools such as:")
    print("  multi-agent-brief init")
    print("  multi-agent-brief doctor --config <workspace>/config.yaml")
    print("  multi-agent-brief sources decide --config <workspace>/config.yaml")
    print("  multi-agent-brief audit <brief.md> --ledger <claim_ledger.json>")
    print("  multi-agent-brief finalize --config <workspace>/config.yaml")
    return 1


def run_prepare_from_args(args: argparse.Namespace) -> int:
    """Deprecated wrapper for the removed Python brief-generation pipeline."""
    print("[deprecated] prepare no longer runs the brief workflow.")
    print("MABW is subagent-first. Python does not orchestrate Scout/Screener/")
    print("Analyst/Editor/Auditor/Formatter as a runtime pipeline.")
    print("")
    print("Use /generate-brief <workspace> so external subagents execute:")
    print("  Scout -> Screener -> Claim Ledger -> Analyst -> Editor -> Auditor -> Formatter")
    print("")
    print("Python CLI commands remain available as deterministic tools:")
    print("  multi-agent-brief doctor --config <workspace>/config.yaml")
    print("  multi-agent-brief sources decide --config <workspace>/config.yaml")
    print("  multi-agent-brief audit <brief.md> --ledger <claim_ledger.json>")
    print("  multi-agent-brief finalize --config <workspace>/config.yaml")
    return 1


def _determine_pipeline_exit_code(outputs: list, context) -> int:
    """Determine pipeline exit code based on outputs and context.

    Exit codes:
    - 0: pipeline completed and delivery gates passed/warning-accepted
    - 1: runtime/config/source fatal
    - 2: blocking quality/final-clean/rendered-output gate failed
    """
    # Check for source collection fatal errors
    source_output = next((o for o in outputs if o.agent_name == "source-collection"), None)
    if source_output:
        artifacts = source_output.artifacts or {}
        collection_errors = artifacts.get("collection_errors", [])
        if collection_errors:
            # Check for fatal errors (ConfigValidationError, ZeroUsableSources)
            fatal_errors = [
                e for e in collection_errors
                if e.get("error_type") in ("ConfigValidationError", "ZeroUsableSources", "NoSearchTasks")
            ]
            if fatal_errors:
                return 1  # runtime/config/source fatal

    # Check for Final Clean failures
    final_clean_report = context.report_state.final_clean_report
    if final_clean_report and final_clean_report.get("audit_status") == "fail":
        return 2  # blocking quality gate failed

    # Check for Audit failures
    audit_report = context.report_state.audit_report
    if audit_report and audit_report.audit_status == "fail":
        return 2  # blocking quality gate failed

    # Check for Rendered Output failures
    rendered_output_report = context.metadata.get("rendered_output_report")
    if rendered_output_report:
        rendered_status = getattr(rendered_output_report, "audit_status", None)
        if rendered_status == "fail":
            return 2  # blocking quality gate failed

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


def run_finalize_from_args(args: argparse.Namespace) -> int:
    """Regenerate final reader-facing artifacts from audited internal markdown.

    This is a deterministic delivery gate for agent-assisted workflows where
    analyst/editor/auditor subagents write output/intermediate/audited_brief.md
    before reader-facing artifacts are rendered.
    """
    config_path = Path(args.config).resolve()
    config = load_config(str(config_path))
    settings = build_run_settings(
        config=config,
        input_dir=None,
        output_dir=args.output,
        name=None,
        language=None,
        audience=None,
    )

    result = finalize_reader_outputs(
        output_dir=settings["output_dir"],
        project_name=settings["project_name"],
        output_formats=settings.get("output_formats", ["markdown"]),
        output_footer=settings.get("output_footer", ""),
        output_named_outputs=bool(settings.get("output_named_outputs", True)),
        output_filename_template=settings.get("output_filename_template", ""),
        output_filename_tokens=settings.get("output_filename_tokens", {}),
        docx_template=(config.get("output", {}) or {}).get("docx_template", "default"),
    )

    print(f"[finalize] Reader brief: {result.reader_brief}")
    if result.named_reader_brief:
        print(f"[finalize] Named reader brief: {result.named_reader_brief}")
    if result.reader_docx:
        print(f"[finalize] Reader DOCX: {result.reader_docx}")
    elif result.docx_generation != "not_requested":
        print(f"[finalize] DOCX generation: {result.docx_generation}")
    print("[finalize] Internal [src:CLAIM_ID] markers stripped from reader-facing artifacts.")
    return 0


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
    if getattr(args, "web_search_mode", None):
        profile.web_search_mode = args.web_search_mode
        profile.web_search_enabled = args.web_search_mode != "disabled"
    if getattr(args, "search_backend", None):
        profile.search_backend = args.search_backend
        profile.web_search_mode = "external_api"
        profile.web_search_enabled = True
        profile.tavily_enabled = args.search_backend == "tavily"
    if getattr(args, "tavily", False):
        profile.tavily_enabled = True
        profile.web_search_enabled = True
        profile.web_search_mode = "external_api"
        profile.search_backend = "tavily"


def init_workspace_from_args(args: argparse.Namespace) -> int:
    # Priority: explicit CLI target > onboarding.target > default "brief-workspace"
    if args.demo:
        target = Path(args.target)
        create_demo_workspace(target, force=args.force)
        print(f"Created demo workspace: {target}")
        print(f"Next: multi-agent-brief start --workspace {target}")
        print(f"  or: multi-agent-brief hermes prompt --config {target}/config.yaml")
        return 0

    from_onboarding = getattr(args, "from_onboarding", None)
    if from_onboarding:
        onboarding = load_onboarding_result(from_onboarding)
        profile = map_onboarding_to_profile(onboarding)

        # Validate required fields — agent must not pass empty/sentinel values
        missing_onboarding: list[str] = []
        if not profile.company:
            missing_onboarding.append("company_or_org")
        if not profile.industry_text:
            missing_onboarding.append("industry_or_theme")
        if not profile.task_objective and not profile.brief_title:
            missing_onboarding.append("task_objective")
        if missing_onboarding:
            print("[error] Onboarding data is incomplete. Required fields are missing or empty:")
            for f in missing_onboarding:
                print(f"  - {f}")
            print("        Supported field names: company_or_org, industry_or_theme, task_objective,")
            print("        audience_plain, language_plain, cadence_plain, source_style_plain,")
            print("        output_style_plain, must_watch, forbidden_sources, tavily_enabled")
            print("        Aliases accepted: company, industry, title, audience, language, cadence, etc.")
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
    print(f"Next: multi-agent-brief start --workspace {target}")
    print(f"  or: multi-agent-brief hermes prompt --config {target}/config.yaml")

    # Print web-search setup guidance if enabled
    if getattr(profile, "web_search_enabled", False) or getattr(profile, "tavily_enabled", False):
        _print_search_backend_guidance(profile)

    # Auto-recommend capabilities based on profile
    _print_capability_recommendations(target, profile)

    return 0


def _print_capability_recommendations(target: Path, profile) -> None:
    """Recommend capabilities based on workspace profile and print suggestions."""
    from multi_agent_brief.capabilities.recommend import recommend_from_text, recommend_from_input_dir

    # Build text from profile
    text_parts = []
    if getattr(profile, "company", None):
        text_parts.append(profile.company)
    if getattr(profile, "industry_text", None):
        text_parts.append(profile.industry_text)
    if getattr(profile, "brief_title", None):
        text_parts.append(profile.brief_title)
    if getattr(profile, "task_objective", None):
        text_parts.append(profile.task_objective)
    focus = getattr(profile, "focus_areas", None)
    if focus:
        text_parts.extend(focus if isinstance(focus, list) else [focus])

    combined_text = " ".join(text_parts)
    if not combined_text:
        return

    # Get enabled providers from the workspace we just created
    enabled_providers = set()
    sources_path = target / "sources.yaml"
    if sources_path.exists():
        try:
            from multi_agent_brief.sources.registry import load_sources_config
            sc = load_sources_config(sources_path)
            enabled_providers = set(sc.enabled_providers)
        except Exception:
            pass

    # Run text + input recommendations
    recs = recommend_from_text(combined_text, enabled_providers)
    recs += recommend_from_input_dir(target / "input", enabled_providers)

    if not recs:
        return

    print()
    print(f"Recommended capabilities for your workspace:")
    for rec in recs:
        print(f"  → {rec.capability_id}: {rec.reason}")
    print()
    print(f"To enable recommended features:")
    print(f"  multi-agent-brief setup {target}")
    print()


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
        added_local = result.get('added_local', 0)
        local_part = f" + {added_local} local signal tasks" if added_local else ""
        print(f"[sources] Merged {result['added_manual']} manual + {result['added_rss']} RSS{local_part} into sources.yaml")
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
            print("[error] --search requires a configured search backend.")
            print("        Supported backends: tavily, exa, brave, firecrawl, serper.")
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

    # Generate collector_tasks.json if local signal tasks exist
    from multi_agent_brief.sources.local_signal_planner import write_collector_tasks_json
    collector_path = workspace / "output" / "intermediate" / "collector_tasks.json"
    collector_tasks = write_collector_tasks_json(discovery, collector_path)
    if collector_tasks:
        print(f"[sources] Generated collector_tasks.json at {collector_path}")
        print(f"[sources] {len(collector_tasks['tasks'])} local signal collection tasks ready")

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


def run_analysis_blocks_from_args(args: argparse.Namespace) -> int:
    """Build analysis blocks from a claim ledger and export JSON."""
    from multi_agent_brief.analysis_blocks.builder import build_analysis_blocks
    from multi_agent_brief.analysis_blocks.renderer import render_analysis_blocks

    ledger_path = Path(args.ledger)
    if not ledger_path.exists():
        print(f"[error] Claim ledger not found: {ledger_path}")
        return 1

    ledger = ClaimLedger.import_json(ledger_path)
    blocks = build_analysis_blocks(ledger)

    # Default output: same directory as ledger
    output_path = Path(args.output) if args.output else ledger_path.parent / "analysis_blocks.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    data = [b.to_dict() for b in blocks]
    output_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[analysis-blocks] Wrote {len(blocks)} blocks to {output_path}")

    if args.markdown:
        md = render_analysis_blocks(blocks, ledger, audience=args.audience, language=args.language)
        print()
        print(md)

    return 0


def run_limitation_hygiene_from_args(args: argparse.Namespace) -> int:
    """Audit limitation hygiene from a claim ledger."""
    from multi_agent_brief.analysis_blocks.builder import build_analysis_blocks
    from multi_agent_brief.audit.limitation_hygiene import (
        audit_limitation_hygiene,
        format_limitation_hygiene_report,
    )

    ledger_path = Path(args.ledger)
    if not ledger_path.exists():
        print(f"[error] Claim ledger not found: {ledger_path}")
        return 1

    ledger = ClaimLedger.import_json(ledger_path)
    blocks = build_analysis_blocks(ledger)
    report = audit_limitation_hygiene(blocks, ledger)

    # Default output: same directory as ledger
    output_path = Path(args.output) if args.output else ledger_path.parent / "limitation_hygiene_report.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(format_limitation_hygiene_report(report))
    print(f"\n[limitation-hygiene] Wrote report to {output_path}")

    warnings = sum(1 for f in report.findings if f.severity == "warning")
    fails = sum(1 for f in report.findings if f.severity == "fail")
    return 1 if fails else 0


def _build_hermes_plan_from_args(args: argparse.Namespace):
    config_path = Path(args.config).resolve()
    if not config_path.exists():
        print(f"[error] config.yaml not found: {config_path}")
        return None
    config = load_config(config_path)
    cadences = None
    if getattr(args, "cadence", None):
        cadences = [c.strip() for c in args.cadence.split(",") if c.strip()]
    repo_workdir = Path(args.repo_workdir).resolve() if getattr(args, "repo_workdir", None) else Path.cwd().resolve()
    return build_hermes_cron_plan(
        config=config,
        workspace=config_path.parent,
        repo_workdir=repo_workdir,
        cadences=cadences,
        deliver=getattr(args, "deliver", "local"),
        profile=getattr(args, "profile", ""),
    )


def run_hermes_from_args(args: argparse.Namespace) -> int:
    if args.hermes_action == "skill":
        output = Path(args.output) if args.output else Path(".agents/hermes-skills/multi-agent-brief-hermes/SKILL.md")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(render_hermes_skill(), encoding="utf-8")
        print(f"[hermes] Wrote Hermes skill: {output}")
        print("[hint] Run 'multi-agent-brief hermes install-skill' to install to a Hermes-discoverable directory.")
        return 0

    if args.hermes_action == "cron-plan":
        plan = _build_hermes_plan_from_args(args)
        if plan is None:
            return 1
        workspace = Path(plan.workspace)
        output = Path(args.output) if args.output else workspace / "output" / "intermediate" / "hermes_cron_plan.json"
        write_json(output, plan.to_dict())
        print(f"[hermes] Wrote cron plan: {output}")
        if args.markdown:
            md_path = Path(args.markdown)
            md_path.parent.mkdir(parents=True, exist_ok=True)
            md_path.write_text(render_hermes_cron_markdown(plan), encoding="utf-8")
            print(f"[hermes] Wrote cron plan Markdown: {md_path}")
        print("[hint] Run 'multi-agent-brief hermes cron-commands --config <workspace>/config.yaml' to print install commands.")
        return 0

    if args.hermes_action == "cron-commands":
        plan = _build_hermes_plan_from_args(args)
        if plan is None:
            return 1
        print(render_hermes_cron_commands(plan), end="")
        return 0

    if args.hermes_action == "sync-sources":
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
        action = "would update" if args.dry_run and result["changed"] else "updated" if result["changed"] else "already configured"
        print(f"[hermes] sources.yaml {action}: {sources_path}")
        print(f"[hermes] cached_package path: {result['cache_dir']}")
        print(f"[hermes] enabled providers: {', '.join(result['enabled_providers'])}")
        return 0

    if args.hermes_action == "install-skill":
        result = install_hermes_skill(target_dir=args.target)
        print(f"[hermes] Installed skill: {result['skill_path']}")
        if result["auto_detected"]:
            print(f"[hermes] Auto-detected Hermes skill directory: {result['skill_dir']}")
        if result["hint"]:
            print(f"[hint] {result['hint']}")
        print()
        print("[hermes] Next: generate a run prompt for your workspace:")
        print("  multi-agent-brief hermes prompt --config <workspace>/config.yaml")
        print("[hermes] Then paste the prompt into Hermes to start the delegated brief workflow.")
        return 0

    if args.hermes_action == "prompt":
        config_path = Path(args.config).resolve()
        workspace = config_path.parent
        repo_workdir = Path(args.repo_workdir).resolve() if getattr(args, "repo_workdir", None) else Path.cwd().resolve()
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

    return 1


def run_start_from_args(args: argparse.Namespace) -> int:
    """start — unified launcher. Never generates a brief."""
    repo_workdir = Path(args.repo_workdir).resolve() if getattr(args, "repo_workdir", None) else Path.cwd().resolve()

    workspace = getattr(args, "workspace", None)
    if not workspace:
        cwd = Path.cwd()
        if (cwd / "config.yaml").exists() and (cwd / "user.md").exists():
            workspace = str(cwd)
        else:
            print("[start] No workspace found.")
            print("[start] Create one with: multi-agent-brief init <path>")
            print("[start] Or pass --workspace <path> to use an existing workspace.")
            return 1

    workspace_path = Path(workspace).resolve()
    if not (workspace_path / "config.yaml").exists():
        print(f"[start] Not a workspace (no config.yaml): {workspace_path}")
        print("[start] Create one with: multi-agent-brief init <path>")
        return 1

    handoff = build_handoff(
        workspace=workspace_path,
        repo_workdir=repo_workdir,
        runtime=args.runtime,
        venv=getattr(args, "venv", None),
        run_doctor=not getattr(args, "skip_doctor", False),
    )

    md_path, json_path = write_handoff_artifacts(handoff, workspace_path)
    print(render_handoff_cli(handoff))
    print(f"[start] Handoff written: {md_path}")
    print(f"[start] Handoff JSON:  {json_path}")
    return 0


def run_handoff_from_args(args: argparse.Namespace) -> int:
    """handoff — generate runtime handoff from workspace config."""
    config_path = Path(args.config).resolve()
    if not config_path.exists():
        print(f"[error] config.yaml not found: {config_path}")
        return 1
    workspace = config_path.parent
    repo_workdir = Path(args.repo_workdir).resolve() if getattr(args, "repo_workdir", None) else Path.cwd().resolve()

    handoff = build_handoff(
        workspace=workspace,
        repo_workdir=repo_workdir,
        runtime=args.runtime,
        venv=getattr(args, "venv", None),
        run_doctor=not getattr(args, "skip_doctor", False),
    )

    md_path, json_path = write_handoff_artifacts(handoff, workspace)
    print(render_handoff_cli(handoff))
    print(f"[handoff] Written: {md_path}")
    print(f"[handoff] JSON:   {json_path}")
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
    if args.command == "finalize":
        return run_finalize_from_args(args)
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
    if args.command in ("features", "capability"):
        return run_features_from_args(args)
    if args.command == "recommend":
        return run_recommend_from_args(args)
    if args.command == "setup":
        return run_setup_from_args(args)
    if args.command == "analysis-blocks":
        return run_analysis_blocks_from_args(args)
    if args.command == "limitation-hygiene":
        return run_limitation_hygiene_from_args(args)
    if args.command == "hermes":
        return run_hermes_from_args(args)
    if args.command == "start":
        return run_start_from_args(args)
    if args.command == "handoff":
        return run_handoff_from_args(args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
