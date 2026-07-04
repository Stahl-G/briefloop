"""WorkBuddy integration commands."""

from __future__ import annotations

import argparse
import json

from multi_agent_brief.workbuddy.skill_pack import (
    WorkBuddySkillPackError,
    package_workbuddy_skill,
)


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "workbuddy",
        help="Package source-clone WorkBuddy Skill assets.",
    )
    actions = parser.add_subparsers(dest="workbuddy_action", required=True)
    pack = actions.add_parser(
        "pack-skill",
        help="Generate a local WorkBuddy Skill zip from checked-in source files.",
    )
    pack.add_argument(
        "--output",
        required=True,
        help="Output directory for the generated zip and manifest.",
    )
    pack.add_argument(
        "--repo-workdir",
        help=(
            "BriefLoop source repository root. Required when the installed "
            "package cannot discover source-clone WorkBuddy Skill assets."
        ),
    )
    pack.add_argument("--json", action="store_true", help="Emit JSON output.")


def handle(args: argparse.Namespace) -> int:
    if args.workbuddy_action == "pack-skill":
        try:
            result = package_workbuddy_skill(
                output_dir=args.output,
                repo_workdir=getattr(args, "repo_workdir", None),
            )
        except WorkBuddySkillPackError as exc:
            if getattr(args, "json", False):
                print(
                    json.dumps(
                        {
                            "ok": False,
                            "error": str(exc),
                            "runtime_effect": "packaging_only",
                        },
                        indent=2,
                        sort_keys=True,
                    )
                )
            else:
                print(f"[workbuddy pack-skill] {exc}")
            return 1
        payload = {
            "ok": True,
            "runtime_effect": "packaging_only",
            "zip_path": str(result.zip_path),
            "manifest_path": str(result.manifest_path),
            "zip_sha256": result.zip_sha256,
            "included_file_count": len(result.included_files),
        }
        if getattr(args, "json", False):
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print(f"[workbuddy pack-skill] wrote {result.zip_path}")
            print(f"[workbuddy pack-skill] wrote {result.manifest_path}")
            print(f"[workbuddy pack-skill] zip_sha256={result.zip_sha256}")
            print(
                "[workbuddy pack-skill] local Skill zip only; "
                "not marketplace-ready and not Python package data."
            )
        return 0
    return 1
