#!/usr/bin/env python3
"""Sync canonical repo skills into the Hermes plugin projection."""

from __future__ import annotations

import argparse
import filecmp
import shutil
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
PROJECTIONS = {
    ROOT / ".agents" / "skills" / "briefloop": (
        ROOT / "integrations" / "hermes-plugin" / "mabw" / "skills" / "briefloop"
    ),
}


def _relative_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(path.relative_to(root) for path in root.rglob("*") if path.is_file())


def _sync_projection(source: Path, target: Path) -> None:
    if not source.exists():
        raise FileNotFoundError(f"source skill is missing: {source}")
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)
    print(f"[sync-hermes-plugin-skills] synced {source.relative_to(ROOT)} -> {target.relative_to(ROOT)}")


def _check_projection(source: Path, target: Path) -> list[str]:
    errors: list[str] = []
    if not source.exists():
        return [f"source skill is missing: {source.relative_to(ROOT)}"]
    if not target.exists():
        return [f"Hermes plugin projection is missing: {target.relative_to(ROOT)}"]

    source_files = set(_relative_files(source))
    target_files = set(_relative_files(target))
    for rel_path in sorted(source_files - target_files):
        errors.append(f"missing projected file: {target.relative_to(ROOT) / rel_path}")
    for rel_path in sorted(target_files - source_files):
        errors.append(f"extra projected file: {target.relative_to(ROOT) / rel_path}")
    for rel_path in sorted(source_files & target_files):
        source_file = source / rel_path
        target_file = target / rel_path
        if not filecmp.cmp(source_file, target_file, shallow=False):
            errors.append(f"projected file differs: {target.relative_to(ROOT) / rel_path}")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Sync canonical BriefLoop skills into the Hermes plugin projection.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify the plugin projection is byte-identical without writing files",
    )
    args = parser.parse_args(argv)

    if args.check:
        errors: list[str] = []
        for source, target in PROJECTIONS.items():
            errors.extend(_check_projection(source, target))
        if errors:
            for error in errors:
                print(f"[sync-hermes-plugin-skills] {error}", file=sys.stderr)
            return 1
        print("[sync-hermes-plugin-skills] ok")
        return 0

    for source, target in PROJECTIONS.items():
        _sync_projection(source, target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
