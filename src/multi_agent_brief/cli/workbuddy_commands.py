"""WorkBuddy integration commands."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from multi_agent_brief.workbuddy.skill_pack import (
    WorkBuddySkillPackError,
    package_workbuddy_skill,
)
from multi_agent_brief.workbuddy.diagnose import (
    build_workbuddy_diagnosis,
    format_workbuddy_diagnosis,
)
from multi_agent_brief.workbuddy.support_bundle import (
    WorkBuddySupportBundleError,
    package_workbuddy_support_bundle,
    validate_workbuddy_support_bundle,
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

    diagnose = actions.add_parser(
        "diagnose",
        help="Print a read-only WorkBuddy Run Card for a workspace.",
    )
    diagnose.add_argument(
        "--workspace",
        required=True,
        help="BriefLoop workspace directory.",
    )
    diagnose.add_argument("--json", action="store_true", help="Emit JSON output.")

    support = actions.add_parser(
        "support-bundle",
        help="Create a secret-safe WorkBuddy support bundle for debugging.",
    )
    support.add_argument(
        "--workspace",
        required=True,
        help="BriefLoop workspace directory.",
    )
    support.add_argument(
        "--output",
        required=True,
        help="Output directory outside the workspace for the generated bundle.",
    )
    support.add_argument("--json", action="store_true", help="Emit JSON output.")


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
    if args.workbuddy_action == "diagnose":
        payload = build_workbuddy_diagnosis(workspace=args.workspace)
        if getattr(args, "json", False):
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print(format_workbuddy_diagnosis(payload))
        return 0
    if args.workbuddy_action == "support-bundle":
        try:
            result = package_workbuddy_support_bundle(
                workspace=args.workspace,
                output_dir=args.output,
            )
            validation_errors = validate_workbuddy_support_bundle(
                zip_path=result.zip_path,
                manifest_path=result.manifest_path,
            )
            if validation_errors:
                _remove_rejected_support_bundle(result.zip_path, result.manifest_path)
                raise WorkBuddySupportBundleError(
                    "generated support bundle failed validation: "
                    + "; ".join(validation_errors)
                )
        except WorkBuddySupportBundleError as exc:
            if getattr(args, "json", False):
                print(
                    json.dumps(
                        {
                            "ok": False,
                            "error": str(exc),
                            "runtime_effect": "packaging_only_read_only",
                            "share_workspace_zip_allowed": False,
                        },
                        indent=2,
                        sort_keys=True,
                    )
                )
            else:
                print(f"[workbuddy support-bundle] {exc}")
            return 1
        payload = {
            "ok": True,
            "runtime_effect": "packaging_only_read_only",
            "zip_path": str(result.zip_path),
            "manifest_path": str(result.manifest_path),
            "zip_sha256": result.zip_sha256,
            "included_file_count": len(result.included_files),
            "excluded_file_count": len(result.excluded_files),
            "redacted_files": list(result.redacted_files),
            "share_workspace_zip_allowed": False,
            "boundary": "secret_safe_support_bundle_not_delivery_gate_release_authority",
        }
        if getattr(args, "json", False):
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print(f"[workbuddy support-bundle] wrote {result.zip_path}")
            print(f"[workbuddy support-bundle] wrote {result.manifest_path}")
            print(f"[workbuddy support-bundle] zip_sha256={result.zip_sha256}")
            print(
                "[workbuddy support-bundle] secret-safe support package only; "
                "not delivery, gate, release, or semantic-proof authority."
            )
        return 0
    return 1


def _remove_rejected_support_bundle(*paths: object) -> None:
    for path in paths:
        try:
            if path:
                Path(path).unlink(missing_ok=True)
        except OSError:
            continue
