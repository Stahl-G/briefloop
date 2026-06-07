"""onboard — interactive conversational onboarding command."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from multi_agent_brief.onboarding.io import save_onboarding_result


def register(subparsers: argparse._SubParsersAction) -> None:
    """Register the onboard subparser."""
    onboard_parser = subparsers.add_parser(
        "onboard",
        help="Start conversational onboarding: answer questions and generate onboarding.json.",
    )
    onboard_parser.add_argument(
        "--output",
        default="onboarding.json",
        help="Output path for onboarding.json (default: onboarding.json).",
    )
    onboard_parser.add_argument(
        "--language",
        choices=["en-US", "zh-CN", "bilingual"],
        help="Wizard/interface language.",
    )


def handle(args: argparse.Namespace) -> int:
    """onboard — interactive conversational onboarding, outputs onboarding.json."""
    from multi_agent_brief.cli.init_wizard import prompt_for_profile
    from multi_agent_brief.onboarding.schema import OnboardingResult

    if not sys.stdin.isatty():
        print("[error] onboard requires an interactive terminal.")
        print(
            "        For non-interactive onboarding, create onboarding.json"
            " directly:"
        )
        print(
            "        https://github.com/Stahl-G/multi-agent-brief-workflow#readme"
        )
        return 1

    print()
    print("=== MABW Conversational Onboarding ===")
    print("Answer a few questions to define your brief workspace.")
    print("Press Ctrl-C to cancel at any time.")
    print()

    try:
        profile = prompt_for_profile()
    except (KeyboardInterrupt, EOFError):
        print()
        print("[onboard] Onboarding cancelled.")
        return 1

    result = OnboardingResult(
        company_or_org=profile.company,
        industry_or_theme=profile.industry_text or profile.industry,
        brief_title=profile.brief_title,
        task_objective=profile.task_objective,
        forbidden_sources=profile.forbidden_sources,
        audience_plain=profile.audience,
        source_style_plain=profile.source_profile,
        output_style_plain=", ".join(profile.output_formats),
        language_plain=profile.output_language,
        cadence_plain=profile.cadence,
        must_watch=profile.focus_areas,
        search_backend_plain=profile.search_backend,
        max_items_per_brief=profile.selector_max_items,
        source_age_days=profile.max_source_age_days,
        tavily_enabled=profile.tavily_enabled,
    )

    output_path = Path(args.output)
    save_onboarding_result(result, output_path)

    print()
    print(f"[onboard] Onboarding complete. Saved to: {output_path}")
    print(
        f"Next: multi-agent-brief init <workspace> --from-onboarding"
        f" {output_path}"
    )
    print(f"Then: multi-agent-brief run --workspace <workspace>")
    return 0
