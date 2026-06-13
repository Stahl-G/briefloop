#!/usr/bin/env python3
"""Check terminology consistency across docs, source, and tests.

Usage:
    python scripts/check_terms.py

Exit code 0 if all checks pass; non-zero otherwise.
"""
from __future__ import annotations

import sys
from pathlib import Path

try:
    import yaml
except ModuleNotFoundError:
    yaml = None  # type: ignore[assignment]

ROOT = Path(__file__).resolve().parent.parent
TERMINOLOGY_PATH = ROOT / "configs" / "terminology.yaml"

# Directories to scan (relative to ROOT)
SCAN_DIRS = [
    "src",
    "tests",
    "docs",
    ".agents",
    ".claude",
]

# Individual files to scan
SCAN_FILES = [
    "README.md",
    "README_en.md",
    "README.zh-CN.md",
    "AGENTS.md",
    "CLAUDE.md",
]

# Paths to exclude
EXCLUDE = {".git", ".venv", "__pycache__", "dist", "build", ".pytest_cache", "worktrees"}


def load_terminology() -> dict:
    if yaml is None:
        raise RuntimeError("PyYAML is required. Install with: pip install pyyaml")
    text = TERMINOLOGY_PATH.read_text(encoding="utf-8")
    return yaml.safe_load(text)


def collect_scan_files() -> list[Path]:
    """Collect all text files to scan."""
    result: list[Path] = []
    for name in SCAN_FILES:
        p = ROOT / name
        if p.exists():
            result.append(p)
    for dirname in SCAN_DIRS:
        d = ROOT / dirname
        if not d.exists():
            continue
        for p in d.rglob("*"):
            rel_parts = p.relative_to(ROOT).parts
            if any(part in EXCLUDE for part in rel_parts):
                continue
            if p.is_file() and p.suffix in (".md", ".py", ".yaml", ".yml", ".toml", ".txt"):
                result.append(p)
    return result


def check_forbidden_terms(files: list[Path], forbidden: list[str]) -> list[str]:
    """Check that forbidden terms do not appear in scanned files."""
    errors: list[str] = []
    for fpath in files:
        try:
            lines = fpath.read_text(encoding="utf-8").splitlines()
        except Exception:
            continue
        for i, line in enumerate(lines, 1):
            lower = line.lower()
            for term in forbidden:
                if term.lower() in lower:
                    errors.append(
                        f"FORBIDDEN: '{term}' found in {fpath.relative_to(ROOT)}:{i}"
                    )
    return errors


def check_readme_command_snippets(files: list[Path], snippets: list[str]) -> list[str]:
    """Check that required command snippets appear in README files."""
    errors: list[str] = []
    readme_files = [
        f for f in files
        if f.name in ("README.md", "README_en.md", "README.zh-CN.md")
    ]
    for snippet in snippets:
        found = False
        for readme in readme_files:
            try:
                text = readme.read_text(encoding="utf-8")
            except Exception:
                continue
            if snippet in text:
                found = True
                break
        if not found:
            errors.append(
                f"MISSING: README command snippet '{snippet}' not found in any README"
            )
    return errors


def check_cli_commands_in_docs(files: list[Path], commands: list[dict]) -> list[str]:
    """Check that documented CLI commands appear in docs."""
    errors: list[str] = []
    doc_files = [
        f for f in files
        if f.suffix in (".md", ".txt")
        and ".agents" not in str(f.relative_to(ROOT))
        and ".claude" not in str(f.relative_to(ROOT))
    ]
    for cmd in commands:
        for required in cmd.get("docs_should_contain", []):
            found = False
            for fpath in doc_files:
                try:
                    text = fpath.read_text(encoding="utf-8")
                except Exception:
                    continue
                if required in text:
                    found = True
                    break
            if not found:
                errors.append(
                    f"MISSING: CLI command doc '{required}' not found in docs"
                )
    return errors


def main() -> int:
    print(f"[check_terms] Loading {TERMINOLOGY_PATH.relative_to(ROOT)}...")
    config = load_terminology()

    files = collect_scan_files()
    print(f"[check_terms] Scanning {len(files)} files...")

    errors: list[str] = []

    forbidden = config.get("forbidden_terms", [])
    if forbidden:
        errors.extend(check_forbidden_terms(files, forbidden))

    snippets = config.get("readme_command_snippets", [])
    if snippets:
        errors.extend(check_readme_command_snippets(files, snippets))

    commands = config.get("cli_commands", [])
    if commands:
        errors.extend(check_cli_commands_in_docs(files, commands))

    if errors:
        print(f"\n[check_terms] FAILED with {len(errors)} issue(s):")
        for e in errors:
            print(f"  {e}")
        return 1

    print("[check_terms] All terminology checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
