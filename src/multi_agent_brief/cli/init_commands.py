"""init — workspace creation command."""

from __future__ import annotations

import argparse
from pathlib import Path


def register(subparsers: argparse._SubParsersAction) -> None:
    """Register the init subparser."""
    init_parser = subparsers.add_parser(
        "init",
        help="Create a brief workspace from onboarding.json."
        " Run 'briefloop onboard' first for conversational setup.",
    )
    init_parser.add_argument(
        "target",
        nargs="?",
        default="brief-workspace",
        help="Target workspace directory.",
    )
    init_parser.add_argument(
        "--demo",
        action="store_true",
        help="Create the existing synthetic demo workspace.",
    )
    init_parser.add_argument(
        "--force", action="store_true", help="Overwrite existing init files."
    )
    init_parser.add_argument(
        "--language",
        choices=["en-US", "zh-CN", "bilingual"],
        help="Wizard/interface language.",
    )
    init_parser.add_argument(
        "--output-language",
        choices=["en-US", "zh-CN", "bilingual"],
        help="Generated brief language.",
    )
    init_parser.add_argument(
        "--company", help="Company or organization name."
    )
    init_parser.add_argument(
        "--role", help="User role, e.g. strategy_office."
    )
    init_parser.add_argument(
        "--industry", help="Industry slug, e.g. manufacturing."
    )
    init_parser.add_argument("--title", help="Brief title.")
    init_parser.add_argument("--audience", help="Target reader group.")
    init_parser.add_argument(
        "--focus-areas", help="Comma-separated focus areas."
    )
    init_parser.add_argument(
        "--cadence",
        choices=["weekly", "biweekly", "monthly", "ad_hoc"],
        help="Reporting cadence.",
    )
    init_parser.add_argument(
        "--selector-max-items",
        type=int,
        help="Maximum selected items per brief.",
    )
    init_parser.add_argument(
        "--rag",
        choices=["on", "off"],
        help="Enable or disable retrieval settings.",
    )
    init_parser.add_argument(
        "--retrieval-provider",
        choices=["ollama", "gemini"],
        help="Retrieval provider.",
    )
    init_parser.add_argument(
        "--output-formats", help="Comma-separated output formats."
    )
    init_parser.add_argument(
        "--source-profile",
        choices=[
            "conservative",
            "research",
            "aggressive_signal",
            "custom",
            "llm_decide",
        ],
        help="Source collection profile.",
    )
    init_parser.add_argument(
        "--tavily",
        action="store_true",
        help="Legacy alias: enable Tavily live web search backend.",
    )
    init_parser.add_argument(
        "--web-search-mode",
        choices=["disabled", "runtime_tool", "external_api", "configure_later"],
        help="How web search is provided.",
    )
    init_parser.add_argument(
        "--search-backend",
        choices=["tavily", "exa", "brave", "firecrawl", "serper"],
        help="Search backend for --web-search-mode external_api.",
    )
    init_parser.add_argument(
        "--initial-news-backfill",
        action="store_true",
        help=(
            "Configure first-run seven-day news discovery"
            " (20 relevant news items per day)."
        ),
    )
    init_parser.add_argument(
        "--initial-news-backfill-days",
        type=int,
        help="Number of past days for initial news discovery. Default: 7.",
    )
    init_parser.add_argument(
        "--initial-news-backfill-daily-max-results",
        type=int,
        help="Maximum news results per day for initial discovery. Default: 20.",
    )
    init_parser.add_argument(
        "--preferred-news-domains",
        help=(
            "Comma-separated preferred news domains for source discovery"
            " (for example: reuters.com,bloomberg.com)."
        ),
    )
    init_parser.add_argument(
        "--excluded-news-domains",
        help=(
            "Comma-separated news domains to exclude from discovered candidates."
        ),
    )
    init_parser.add_argument(
        "--from-onboarding",
        help="Path to onboarding.json for conversational init.",
    )


def handle(args: argparse.Namespace) -> int:
    """init — create a brief workspace."""
    return _init_workspace(args)


def print_tavily_guidance() -> None:
    """Print setup guidance for Tavily live search."""
    print()
    print(
        "Tavily live search is enabled. To use it, set the TAVILY_API_KEY"
    )
    print("environment variable before running the pipeline.")
    print()
    print(
        "  Do not paste API keys into chat, config files, README, or GitHub."
    )
    print("  Keys should be stored in environment variables only.")
    print()
    print(
        "  Check configuration: multi-agent-brief doctor"
        " --config <workspace>/config.yaml"
    )


def print_search_backend_guidance(profile) -> None:
    """Print safe, backend-agnostic web-search setup guidance."""
    backend = getattr(profile, "search_backend", "") or (
        "tavily" if getattr(profile, "tavily_enabled", False) else ""
    )
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
        print(
            "Runtime web search is enabled. Make sure your execution runtime"
            " provides a web-search tool."
        )
        print(
            "  Check configuration: multi-agent-brief doctor"
            " --config <workspace>/config.yaml"
        )
        return
    if mode == "configure_later":
        print()
        print("Web search is marked for later configuration.")
        print(
            "  Supported backends: tavily, exa, brave, firecrawl, serper"
        )
        print(
            "  Set one API key in .env or export it in your shell, then set"
            " web_search.backend in sources.yaml."
        )
        print(
            "  Do not paste API keys into chat, config files, README, or"
            " GitHub."
        )
        return
    if mode == "external_api" and backend:
        env_var = backend_env.get(backend, "the backend API key env var")
        print()
        print(
            f"Web search backend '{backend}' is enabled. Set {env_var} in"
            " .env or your shell before running the pipeline."
        )
        print(
            "  Supported backends: tavily, exa, brave, firecrawl, serper"
        )
        print(
            "  Do not paste API keys into chat, config files, README, or"
            " GitHub."
        )
        print(
            "  Check configuration: multi-agent-brief doctor"
            " --config <workspace>/config.yaml"
        )


def print_context_reference_guidance(target: Path, language: str = "en-US") -> None:
    """Tell users where prior briefs and example reports belong."""
    context_dir = (target / "input" / "context").as_posix()
    if language == "zh-CN":
        print()
        print(
            "提示：请在"
            f" {context_dir} 里加入你的简报示例 Markdown 文件"
            "（例如往期周报）。"
        )
        print("      它只作为结构、口吻和版式参考，不进入 Claim Ledger。")
        return
    if language == "bilingual":
        print_context_reference_guidance(target, "zh-CN")
        print_context_reference_guidance(target, "en-US")
        return
    print()
    print(
        "Tip: add example brief Markdown files, such as prior weekly reports,"
        f" to {context_dir}."
    )
    print("     They are style/context references only and do not enter the Claim Ledger.")


def _apply_cli_overrides(profile, args: argparse.Namespace) -> None:
    """Apply explicit CLI args onto a profile built from onboarding.

    Only overrides fields where the CLI arg is non-None / truthy.
    """
    from multi_agent_brief.cli.init_wizard import (
        normalize_language,
        parse_list_arg,
        parse_int,
        apply_rag_args,
    )

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
    if getattr(args, "selector_max_items", None) is not None:
        profile.selector_max_items = args.selector_max_items
    apply_rag_args(
        profile,
        getattr(args, "rag", None),
        getattr(args, "retrieval_provider", None),
    )
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
    if getattr(args, "initial_news_backfill", False):
        profile.initial_news_backfill_enabled = True
    if getattr(args, "initial_news_backfill_days", None):
        profile.initial_news_backfill_days = parse_int(
            str(args.initial_news_backfill_days),
            profile.initial_news_backfill_days,
        )
    if getattr(args, "initial_news_backfill_daily_max_results", None):
        profile.initial_news_backfill_daily_max_results = parse_int(
            str(args.initial_news_backfill_daily_max_results),
            profile.initial_news_backfill_daily_max_results,
        )
    preferred_domains = parse_list_arg(getattr(args, "preferred_news_domains", None))
    if preferred_domains:
        profile.preferred_news_domains = preferred_domains
    excluded_domains = parse_list_arg(getattr(args, "excluded_news_domains", None))
    if excluded_domains:
        profile.excluded_news_domains = excluded_domains


def _profile_option_errors(profile) -> list[str]:
    """Return errors for option combinations that cannot produce runnable config."""
    errors: list[str] = []
    selector_max_items = getattr(profile, "selector_max_items", None)
    if not isinstance(selector_max_items, int) or selector_max_items < 20:
        errors.append(
            "--selector-max-items must be at least 20 because generated workspaces "
            "set brief_quality.min_items to 20."
        )
    if (
        getattr(profile, "initial_news_backfill_enabled", False)
        and getattr(profile, "source_profile", "") != "llm_decide"
    ):
        errors.append(
            "--initial-news-backfill requires --source-profile llm_decide "
            "because it runs through sources decide and source_discovery."
        )
    return errors


def _print_profile_option_errors(errors: list[str]) -> None:
    print("[error] Incompatible init options.")
    for error in errors:
        print(f"        {error}")


def _init_workspace(args: argparse.Namespace) -> int:
    """Create a brief workspace from onboarding or CLI args."""
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

    # Priority: explicit CLI target > onboarding.target > default "brief-workspace"
    if args.demo:
        target = Path(args.target)
        create_demo_workspace(target, force=args.force)
        print(f"Created demo workspace: {target}")
        print_context_reference_guidance(target, "en-US")
        print(
            "Demo only — sample data for feature exploration, not a real"
            " brief workspace."
        )
        print("For a real brief workspace:")
        print("  briefloop onboard")
        print(
            "  briefloop init <workspace> --from-onboarding"
            " onboarding.json"
        )
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
            print(
                "[error] Onboarding data is incomplete. Required fields are"
                " missing or empty:"
            )
            for f in missing_onboarding:
                print(f"  - {f}")
            print(
                "        Supported field names: company_or_org,"
                " industry_or_theme, task_objective,"
            )
            print(
                "        audience_plain, language_plain, cadence_plain,"
                " source_style_plain,"
            )
            print(
                "        output_style_plain, must_watch, forbidden_sources,"
                " tavily_enabled"
            )
            print(
                "        Aliases accepted: company, industry, title,"
                " audience, language, cadence, etc."
            )
            print("        Run conversational onboarding:")
            print("          briefloop onboard")
            print("        Then create the workspace:")
            print(
                "          briefloop init <workspace>"
                " --from-onboarding onboarding.json"
            )
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
            print("        Run conversational onboarding:")
            print("          briefloop onboard")
            print("        Then create the workspace:")
            print(
                "          briefloop init <workspace>"
                " --from-onboarding onboarding.json"
            )
            print(
                "        Developer-only direct init must provide all"
                " business fields:"
            )
            print(f"        missing: {', '.join(missing)}")
            return 1
        if not _is_interactive() and missing:
            print(
                "[error] Non-interactive init cannot create a workspace from"
                " defaults."
            )
            print("        Run conversational onboarding:")
            print("          briefloop onboard")
            print("        Then create the workspace:")
            print(
                "          briefloop init <workspace>"
                " --from-onboarding onboarding.json"
            )
            print(
                "        Developer-only direct init must provide all"
                " business fields:"
            )
            print(f"        missing: {', '.join(missing)}")
            return 1
        target = Path(args.target)
        try:
            profile = build_profile_from_args(args)
        except InitOnboardingRequired as exc:
            print(f"[error] {exc}")
            print("        Run conversational onboarding:")
            print("          briefloop onboard")
            print(
                "        Then: briefloop init <workspace>"
                " --from-onboarding onboarding.json"
            )
            return 1

    option_errors = _profile_option_errors(profile)
    if option_errors:
        _print_profile_option_errors(option_errors)
        return 1

    create_workspace(target, profile, force=args.force)
    print(f"Created brief workspace: {target}")
    print_context_reference_guidance(target, profile.interface_language)
    print(f"Next: briefloop run --workspace {target}")
    print(
        "Hermes prompt: briefloop hermes prompt"
        f" --config {target}/config.yaml"
    )

    # Print web-search setup guidance if enabled
    if getattr(profile, "web_search_enabled", False) or getattr(
        profile, "tavily_enabled", False
    ):
        print_search_backend_guidance(profile)

    # Auto-recommend capabilities based on profile
    _print_capability_recommendations(target, profile)

    return 0


def _print_capability_recommendations(target: Path, profile) -> None:
    """Recommend capabilities based on workspace profile and print suggestions."""
    from multi_agent_brief.capabilities.recommend import (
        recommend_from_text,
        recommend_from_input_dir,
    )

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
    print("Recommended capabilities for your workspace:")
    for rec in recs:
        print(f"  → {rec.capability_id}: {rec.reason}")
    print()
    print("To enable recommended features:")
    print(f"  briefloop setup {target}")
    print()
