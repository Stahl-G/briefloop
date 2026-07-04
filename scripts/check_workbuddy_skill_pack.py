#!/usr/bin/env python3
"""Build and validate the source-clone WorkBuddy Skill package."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from multi_agent_brief.workbuddy.skill_pack import (  # noqa: E402
    WorkBuddySkillPackError,
    package_workbuddy_skill,
    validate_workbuddy_skill_pack,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        help="Optional output directory. Defaults to a temporary directory.",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON output.")
    args = parser.parse_args()

    temp_dir: tempfile.TemporaryDirectory[str] | None = None
    if args.output:
        output_dir = _explicit_output(Path(args.output).expanduser().resolve())
        temporary_output = False
    else:
        temp_dir = tempfile.TemporaryDirectory(prefix="briefloop-workbuddy-pack-")
        output_dir = Path(temp_dir.name)
        temporary_output = True
    try:
        result = package_workbuddy_skill(output_dir=output_dir, repo_workdir=ROOT)
        errors = validate_workbuddy_skill_pack(
            zip_path=result.zip_path,
            manifest_path=result.manifest_path,
        )
    except WorkBuddySkillPackError as exc:
        payload = {
            "ok": False,
            "runtime_effect": "readiness_check_only",
            "error": str(exc),
        }
        _print(payload, json_mode=args.json)
        return 1
    finally:
        if temp_dir is not None:
            temp_dir.cleanup()

    payload = {
        "ok": not errors,
        "runtime_effect": "readiness_check_only",
        "temporary_output": temporary_output,
        "zip_path": "" if temporary_output else str(result.zip_path),
        "manifest_path": "" if temporary_output else str(result.manifest_path),
        "zip_sha256": result.zip_sha256,
        "included_file_count": len(result.included_files),
        "errors": errors,
    }
    _print(payload, json_mode=args.json)
    return 0 if not errors else 1


def _explicit_output(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def _print(payload: dict, *, json_mode: bool) -> None:
    if json_mode:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    if payload["ok"]:
        print("[OK] WorkBuddy Skill package validates.")
        if payload.get("temporary_output"):
            print("  temporary package artifacts were validated and removed")
        else:
            print(f"  zip: {payload['zip_path']}")
            print(f"  manifest: {payload['manifest_path']}")
        print(f"  zip_sha256: {payload['zip_sha256']}")
    else:
        print("[FAIL] WorkBuddy Skill package check failed.")
        if payload.get("error"):
            print(f"  - {payload['error']}")
        for error in payload.get("errors", []):
            print(f"  - {error}")


if __name__ == "__main__":
    raise SystemExit(main())
