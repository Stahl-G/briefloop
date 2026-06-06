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
from multi_agent_brief.core.manifest import build_manifest, save_manifest
from multi_agent_brief.core.schemas import PipelineContext
from multi_agent_brief.sources.registry import load_sources_config


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

    # features subcommand
    features_parser = subparsers.add_parser("features", help="Show all available features and their status.")
    features_parser.add_argument("workspace", nargs="?", help="Optional workspace path to check provider status.")
    features_parser.add_argument("--info", metavar="ID", help="Show details for a specific capability.")
    features_parser.add_argument("--json", action="store_true", dest="json_output", help="Output as JSON.")

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
                ws.setdefault("backend", "tavily")
                ws.setdefault("api_key_env", "TAVILY_API_KEY")
                changes_made += 1
                print(f"  ✓ Enabled web_search in sources.yaml")
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
    print("[notice] 'multi-agent-brief run' has been replaced by 'multi-agent-brief prepare'.")
    print("         'prepare' runs the same deterministic pipeline (source collection → Scout →")
    print("         Screener → Claim Ledger → draft artifacts) and is used by /generate-brief.")
    print("")
    print("  Instead of: multi-agent-brief run --config workspace/config.yaml")
    print("  Use:        multi-agent-brief prepare --config workspace/config.yaml")
    return 1


def run_prepare_from_args(args: argparse.Namespace) -> int:
    """Run the deterministic pipeline (source collection → Scout → Screener →
    Claim Ledger → draft artifacts).  Used by /generate-brief as step 3.

    Loads config.yaml for project settings AND sources.yaml for source
    providers, search_tasks, and source_discovery policy — both are required
    for a working prepare run.
    """
    config_path = Path(args.config).resolve()
    workspace = config_path.parent
    config = load_config(str(config_path))

    # Load sources.yaml — required for provider config, search tasks, filing resolver, etc.
    sources_path = workspace / "sources.yaml"
    source_config = None
    source_discovery = None
    coverage_config = None
    if sources_path.exists():
        source_config = load_sources_config(sources_path)
        try:
            from multi_agent_brief.sources.decider import load_source_discovery
            source_discovery = load_source_discovery(sources_path)
        except Exception:
            source_discovery = None
        # Load coverage config from sources.yaml
        try:
            import yaml as _yaml
            with open(sources_path, encoding="utf-8") as f:
                sources_raw = _yaml.safe_load(f) or {}
            coverage_config = sources_raw.get("coverage", None)
        except Exception:
            coverage_config = None

    settings = build_run_settings(
        config=config,
        input_dir=args.input,
        output_dir=args.output,
        name=None,
        language=None,
        audience=None,
    )
    context = PipelineContext(**settings)

    # Inject sources.yaml data into metadata so _collect_sources can use it
    if source_config is not None:
        context.metadata["source_config"] = source_config
    if source_discovery is not None:
        context.metadata["source_discovery"] = source_discovery
    if coverage_config is not None:
        context.metadata["coverage_config"] = coverage_config
    context.metadata["_config_dir"] = str(workspace)

    # Wire brief_quality config → final_quality metadata for FinalQualityAuditAgent
    brief_quality = config.get("brief_quality", {})
    if brief_quality:
        context.metadata["final_quality"] = brief_quality

    outputs = BriefPipeline().run(context)

    # Generate run_manifest.json
    try:
        formatter_output = next((o for o in outputs if o.agent_name == "formatter"), None)
        artifact_paths = formatter_output.artifacts if formatter_output else {}
        stage_dicts = [o.to_dict() for o in outputs]

        audit_report = context.report_state.audit_report
        # Build source coverage summary for manifest
        coverage_report = context.metadata.get("source_coverage")
        source_coverage_summary = coverage_report.to_dict() if hasattr(coverage_report, "to_dict") else {}

        manifest = build_manifest(
            config_path=str(config_path),
            workspace=str(workspace),
            enabled_providers=source_config.enabled_providers if source_config else [],
            output_formats=context.output_formats,
            language=context.language,
            report_date=context.report_date,
            source_count=len(context.sources),
            claim_count=len(context.metadata.get("_ledger", [])),
            candidate_count=len(context.candidates),
            audit_status=audit_report.audit_status if audit_report else "not_run",
            audit_score=audit_report.audit_score if audit_report else None,
            audit_finding_count=len(audit_report.findings) if audit_report else 0,
            semantic_status=(audit_report.metadata.get("semantic_status", "") if audit_report else ""),
            artifact_paths=artifact_paths,
            stage_outputs=stage_dicts,
            source_coverage=source_coverage_summary,
        )
        manifest_path = save_manifest(manifest, context.output_dir)
        print(f"[prepare] Run manifest: {manifest_path}")
    except Exception as exc:
        print(f"[prepare] Warning: could not generate run manifest: {exc}")

    # Determine exit code based on pipeline results
    exit_code = _determine_pipeline_exit_code(outputs, context)

    if exit_code == 0:
        print(f"[prepare] Pipeline complete — {len(outputs)} stages run.")
    else:
        print(f"[prepare] Pipeline failed — {len(outputs)} stages run. Exit code: {exit_code}")

    return exit_code


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
    print(f"Run: /generate-brief {target} in Claude Code to generate a real brief.")

    # Print Tavily setup guidance if enabled
    if profile.tavily_enabled:
        _print_tavily_guidance()

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
    if args.command == "features":
        return run_features_from_args(args)
    if args.command == "recommend":
        return run_recommend_from_args(args)
    if args.command == "setup":
        return run_setup_from_args(args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
