"""onboard — interactive and non-interactive onboarding commands.

Two paths:
  Human terminal:  briefloop onboard
  Agent runtime:   collect answers in chat → write onboarding.json →
                   briefloop init <workspace> --from-onboarding onboarding.json
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, fields
from pathlib import Path

from multi_agent_brief.onboarding.io import load_onboarding_result, save_onboarding_result
from multi_agent_brief.onboarding.schema import OnboardingResult


def register(subparsers: argparse._SubParsersAction) -> None:
    """Register the onboard subparser."""
    onboard_parser = subparsers.add_parser(
        "onboard",
        help="Start conversational onboarding: answer questions and generate onboarding.json. "
        "Human terminal: run 'briefloop onboard'. "
        "Agent runtime: create onboarding.json from chat answers, "
        "then run 'briefloop init <workspace> --from-onboarding onboarding.json'.",
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
    onboard_parser.add_argument(
        "--template",
        action="store_true",
        help="Write a template onboarding.json with all fields and exit (non-interactive).",
    )
    onboard_parser.add_argument(
        "--validate",
        metavar="PATH",
        help="Validate an existing onboarding.json and report results (non-interactive).",
    )


def handle(args: argparse.Namespace) -> int:
    """Dispatch onboard subcommand actions."""
    if args.template:
        return _onboard_template(args)
    if args.validate:
        return _onboard_validate(args)
    return _onboard_interactive(args)


def _onboard_template(args: argparse.Namespace) -> int:
    """Write a template onboarding.json with default values."""
    template = OnboardingResult()
    output_path = Path(args.output)
    save_onboarding_result(template, output_path)
    print(f"[onboard] Template written to: {output_path}")
    print()
    print("Fill in the fields, then validate:")
    print(f"  briefloop onboard --validate {output_path}")
    print()
    print("Then create the workspace:")
    print(f"  briefloop init <workspace> --from-onboarding {output_path}")
    print(f"  briefloop run --workspace <workspace>")
    return 0


def _onboard_validate(args: argparse.Namespace) -> int:
    """Validate an onboarding.json file.

    Reports:
      - Whether the file parses as valid JSON
      - Which required fields are present or missing
      - Unknown or unexpected keys
    """
    path = Path(args.validate)
    if not path.exists():
        print(f"[error] File not found: {path}")
        return 1

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"[error] Invalid JSON in {path}: {exc}")
        return 1

    if not isinstance(raw, dict):
        print(f"[error] onboarding.json must be a JSON object, got {type(raw).__name__}.")
        return 1

    # Load through the normal loader to get aliases and type coercion
    try:
        result = load_onboarding_result(path)
    except Exception as exc:
        print(f"[error] Failed to load onboarding.json: {exc}")
        return 1

    # Report
    _REQUIRED_FIELDS = {"company_or_org", "industry_or_theme", "task_objective"}
    dataclass_fields = {f.name for f in fields(OnboardingResult)}
    present = {k for k, v in asdict(result).items()
               if v not in (None, "", [], {}) and k != "missing"}
    missing = _REQUIRED_FIELDS - present
    unknown = set(raw.keys()) - dataclass_fields

    printed_ok = False  # track if we printed anything good
    if missing:
        print(f"[onboard] Missing required fields: {', '.join(sorted(missing))}")
    else:
        print("[onboard] Required fields: OK")
        printed_ok = True

    if unknown:
        print(f"[onboard] Unknown keys (ignored): {', '.join(sorted(unknown))}")

    # Print a summary of what was found
    field_names = sorted(dataclass_fields)
    print()
    print("Field summary:")
    for name in field_names:
        val = getattr(result, name)
        display = repr(val) if val not in (None, "", [], {}) else "(not set)"
        required = " [required]" if name in _REQUIRED_FIELDS else ""
        print(f"  {name}: {display}{required}")

    return 1 if missing else 0


def _onboard_interactive(args: argparse.Namespace) -> int:
    """Run interactive conversational onboarding (human terminal path)."""
    from multi_agent_brief.cli.init_wizard import prompt_for_profile
    from multi_agent_brief.onboarding.schema import OnboardingResult

    if not sys.stdin.isatty():
        print("[error] onboard requires an interactive terminal.")
        print("        Agent runtime path: create onboarding.json from chat answers.")
        print("        Then run: briefloop init <workspace> --from-onboarding onboarding.json")
        print("        Non-interactive helpers:")
        print("          briefloop onboard --template")
        print("          briefloop onboard --validate onboarding.json")
        return 1

    print()
    print("=== BriefLoop Conversational Onboarding ===")
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
        f"Next: briefloop init <workspace> --from-onboarding"
        f" {output_path}"
    )
    print(f"Then: briefloop run --workspace <workspace>")
    return 0
