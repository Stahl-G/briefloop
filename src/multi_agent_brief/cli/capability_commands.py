"""features / capability / recommend / setup commands."""

from __future__ import annotations

import argparse
from pathlib import Path


def register_features(subparsers: argparse._SubParsersAction) -> None:
    """Register the features subparser."""
    features_parser = subparsers.add_parser(
        "features",
        help="Show all available features and their status.",
    )
    features_parser.add_argument(
        "workspace",
        nargs="?",
        help="Optional workspace path to check provider status.",
    )
    features_parser.add_argument(
        "--info",
        metavar="ID",
        help="Show details for a specific capability.",
    )
    features_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output as JSON.",
    )


def register_capability(subparsers: argparse._SubParsersAction) -> None:
    """Register the capability subparser (alias for features)."""
    capability_parser = subparsers.add_parser(
        "capability",
        help="Alias for 'features': show capabilities and setup status.",
    )
    capability_parser.add_argument(
        "workspace",
        nargs="?",
        help="Optional workspace path to check provider status.",
    )
    capability_parser.add_argument(
        "--info",
        metavar="ID",
        help="Show details for a specific capability.",
    )
    capability_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output as JSON.",
    )


def register_recommend(subparsers: argparse._SubParsersAction) -> None:
    """Register the recommend subparser."""
    rec_parser = subparsers.add_parser(
        "recommend",
        help="Recommend capabilities based on task description.",
    )
    rec_parser.add_argument(
        "workspace", nargs="?", help="Workspace path to check."
    )
    rec_parser.add_argument(
        "--text",
        help="Task description text to scan for keywords.",
    )
    rec_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output as JSON.",
    )


def register_setup(subparsers: argparse._SubParsersAction) -> None:
    """Register the setup subparser."""
    setup_parser = subparsers.add_parser(
        "setup",
        help="Apply recommended capabilities to a workspace.",
    )
    setup_parser.add_argument(
        "workspace", help="Workspace path to configure."
    )
    setup_parser.add_argument(
        "--from-plan",
        help="Path to setup-plan.json to apply.",
    )
    setup_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would change without writing.",
    )


def handle_features_capability(args: argparse.Namespace) -> int:
    """Show all available features with status indicators."""
    import json as json_mod

    from multi_agent_brief.capabilities.catalog import (
        CAPABILITIES,
        get_capability,
    )
    from multi_agent_brief.capabilities.detect import assess_capability

    # --info mode: single capability detail
    if args.info:
        cap = get_capability(args.info)
        if cap is None:
            print(f"[error] Unknown capability: {args.info}")
            print(
                f"        Available IDs:"
                f" {', '.join(c.id for c in CAPABILITIES)}"
            )
            return 1

        enabled_providers = None
        if args.workspace:
            from multi_agent_brief.sources.registry import load_sources_config

            sources_path = Path(args.workspace) / "sources.yaml"
            if sources_path.exists():
                sc = load_sources_config(sources_path)
                enabled_providers = set(sc.enabled_providers)

        status = assess_capability(
            cap.id, args.workspace, enabled_providers
        )

        if args.json_output:
            print(
                json_mod.dumps(
                    {
                        "id": cap.id,
                        "name": cap.name,
                        "summary": cap.summary,
                        "category": cap.category,
                        "visibility": cap.visibility,
                        "maturity": cap.maturity,
                        "state": status.state,
                        "notes": status.notes,
                        "options": [
                            {
                                "id": o.id,
                                "name": o.name,
                                "description": o.description,
                            }
                            for o in cap.options
                        ],
                        "requirements": cap.requirements,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            print(
                f"\n  {cap.name.get('en', cap.id)} ({cap.id})"
            )
            print(f"  {cap.summary.get('en', '')}")
            print(
                f"  Category: {cap.category}  |  Visibility:"
                f" {cap.visibility}  |  Maturity: {cap.maturity}"
            )
            print(f"  Status: {status.state}")
            if status.notes:
                print(f"  Notes: {status.notes}")
            if cap.options:
                print("\n  Options:")
                for o in cap.options:
                    print(
                        f"    {o.name:16s}  {o.description}"
                    )
            if cap.requirements:
                print("\n  Requirements:")
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
            status = assess_capability(
                cap.id, args.workspace, enabled_providers
            )
            items.append(
                {
                    "id": cap.id,
                    "name": cap.name.get("en", cap.id),
                    "category": cap.category,
                    "visibility": cap.visibility,
                    "state": status.state,
                    "notes": status.notes,
                }
            )
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
            status = assess_capability(
                cap.id, args.workspace, enabled_providers
            )
            sym = symbols.get(status.state, "?")
            name = cap.name.get("en", cap.id)
            note = f"  — {status.notes}" if status.notes else ""
            print(f"  {sym} {name:40s}{note}")
        print()

    print(
        "Run 'briefloop features --info <id>' for details on any"
        " feature."
    )
    print(
        "Run 'briefloop features <workspace>' to check status"
        " against a workspace."
    )
    return 0


def handle_recommend(args: argparse.Namespace) -> int:
    """Recommend capabilities based on task description or workspace config."""
    import json as json_mod

    from multi_agent_brief.capabilities.recommend import (
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
    text_recs = (
        recommend_from_text(combined_text, enabled_providers)
        if combined_text
        else []
    )

    all_recs = text_recs + file_recs

    if not all_recs:
        print(
            "[recommend] No capability recommendations for this task."
        )
        print(
            "[hint] Use --text to provide a task description, or specify a"
            " workspace."
        )
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


def handle_setup(args: argparse.Namespace) -> int:
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
        recs = (
            recommend_from_text(combined_text, enabled_providers)
            if combined_text
            else []
        )
        recs += recommend_from_input_dir(
            workspace / "input", enabled_providers
        )
        plan = generate_setup_plan(recs, workspace)

    capabilities = plan.get("capabilities", [])
    if not capabilities:
        print("[setup] No capabilities to enable.")
        return 0

    # Show what would change
    if args.dry_run:
        print("[dry-run] Changes that would be applied:")
        for cap in capabilities:
            print(
                f"  → {cap['name']}:"
                f" {cap.get('config_hint', 'enable in sources.yaml')}"
            )
        print()
        print("[dry-run] No files modified.")
        return 0

    # Apply changes
    try:
        import yaml
    except ModuleNotFoundError:
        print(
            "[error] PyYAML required for setup. Install: pip install pyyaml"
        )
        return 1

    sources_data = (
        yaml.safe_load(sources_path.read_text(encoding="utf-8")) or {}
    )
    changes_made = 0

    for cap in capabilities:
        cap_id = cap["id"]
        if cap_id == "web_search":
            ws = sources_data.setdefault("web_search", {})
            if not ws.get("enabled"):
                ws["enabled"] = True
                ws.setdefault("mode", "runtime_tool")
                ws.setdefault("required_capability", "web_search")
                changes_made += 1
                print(
                    "  ✓ Enabled web_search in sources.yaml"
                    " (mode: runtime_tool; optional external backends:"
                    " tavily, exa, brave, firecrawl, serper)"
                )
        elif cap_id == "mineru":
            mu = sources_data.setdefault("mineru", {})
            if not mu.get("enabled"):
                mu["enabled"] = True
                changes_made += 1
                print("  ✓ Enabled mineru in sources.yaml")
        elif cap_id == "filing_resolver":
            fr = sources_data.setdefault("filing_resolver", {})
            if not fr.get("enabled"):
                fr["enabled"] = True
                changes_made += 1
                print("  ✓ Enabled filing_resolver in sources.yaml")
        elif cap_id == "feishu":
            fe = sources_data.setdefault("feishu", {})
            if not fe.get("enabled"):
                fe["enabled"] = True
                changes_made += 1
                print("  ✓ Enabled feishu in sources.yaml")
        elif cap_id == "rss":
            rss = sources_data.setdefault("rss", {})
            if not rss.get("enabled"):
                rss["enabled"] = True
                changes_made += 1
                print("  ✓ Enabled rss in sources.yaml")
        elif cap_id == "api_news":
            api = sources_data.setdefault("api", {})
            if not api.get("enabled"):
                api["enabled"] = True
                changes_made += 1
                print("  ✓ Enabled api in sources.yaml")
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
                            yaml.safe_dump(
                                config_data,
                                sort_keys=False,
                                default_flow_style=False,
                            ),
                            encoding="utf-8",
                        )
                        changes_made += 1
                        print(
                            "  ✓ Enabled market_competitor in config.yaml"
                        )
        elif cap_id == "docx_output":
            if config_path.exists():
                config_text = config_path.read_text(encoding="utf-8")
                if "docx" not in config_text:
                    config_data = yaml.safe_load(config_text) or {}
                    output = config_data.setdefault("output", {})
                    formats = output.setdefault(
                        "formats", ["markdown"]
                    )
                    if "docx" not in formats:
                        formats.append("docx")
                        config_path.write_text(
                            yaml.safe_dump(
                                config_data,
                                sort_keys=False,
                                default_flow_style=False,
                            ),
                            encoding="utf-8",
                        )
                        changes_made += 1
                        print(
                            "  ✓ Added docx to output.formats in"
                            " config.yaml"
                        )

    if changes_made:
        # Write updated sources.yaml
        sources_path.write_text(
            yaml.safe_dump(
                sources_data,
                sort_keys=False,
                default_flow_style=False,
            ),
            encoding="utf-8",
        )
        print(
            f"\n[setup] {changes_made} change(s) applied to {workspace}"
        )
        print(
            "[hint] Run 'briefloop doctor"
            " --config <workspace>/config.yaml' to verify."
        )
    else:
        print(
            "[setup] All recommended capabilities are already enabled."
        )

    return 0
