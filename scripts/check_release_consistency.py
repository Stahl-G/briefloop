#!/usr/bin/env python3
"""Release Consistency Gate — verifies version and config sync across the repo.

Checks:
  1. pyproject.toml version == __init__.py __version__
  2. README.md current version line matches
  3. README_en.md current version line matches
  4. CHANGELOG.md has a section for the current version
  5. Latest git tag matches current version (skipped if no tags or --no-tag)
  6. Generated agent configs are up to date (delegates to generate_agent_configs.py --check)

Usage:
  python scripts/check_release_consistency.py [--strict] [--no-tag]

Exit codes:
  0 = all checks pass
  1 = one or more checks failed
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ERRORS: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    if ok:
        print(f"  [OK] {label}")
    else:
        msg = f"  [FAIL] {label}"
        if detail:
            msg += f" — {detail}"
        print(msg)
        ERRORS.append(msg)


def extract_pyproject_version() -> str:
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    return m.group(1) if m else ""


def extract_init_version() -> str:
    """Get __version__ from the installed package. Fall back to parsing __init__.py."""
    try:
        import importlib
        return importlib.import_module("multi_agent_brief").__version__
    except Exception:
        pass
    # Fallback: parse from file
    text = (REPO_ROOT / "src" / "multi_agent_brief" / "__init__.py").read_text(encoding="utf-8")
    m = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    if m:
        return m.group(1)
    # Try importlib.metadata pattern
    if "importlib.metadata" in text:
        # Package is dynamic — try to get it from installed metadata
        try:
            import subprocess, sys
            result = subprocess.run(
                [sys.executable, "-c", "from multi_agent_brief import __version__; print(__version__)"],
                capture_output=True, text=True,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception:
            pass
    return ""


def extract_readme_version(lang: str = "zh") -> str:
    filename = "README.md" if lang == "zh" else "README_en.md"
    text = (REPO_ROOT / filename).read_text(encoding="utf-8")
    # Match patterns like: 当前版本：**v0.4.0** or Current version: **v0.4.0**
    m = re.search(r'(?:当前版本|Current version)[：:]\s*\*\*v?([^*]+)\*\*', text)
    return m.group(1).strip() if m else ""


def extract_changelog_latest() -> str:
    text = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    # Find first ## [x.y.z] section (skip [Unreleased])
    m = re.search(r'^## \[(\d+\.\d+\.\d+)\]', text, re.MULTILINE)
    return m.group(1) if m else ""


def extract_latest_git_tag() -> str:
    """Return the latest semver git tag (without 'v' prefix), or '' if none."""
    try:
        result = subprocess.run(
            ["git", "tag", "--sort=-v:refname"],
            capture_output=True, text=True, cwd=str(REPO_ROOT),
        )
        if result.returncode != 0:
            return ""
        for line in result.stdout.splitlines():
            tag = line.strip()
            if re.match(r'^v?\d+\.\d+\.\d+$', tag):
                return tag.lstrip("v")
    except FileNotFoundError:
        pass
    return ""


def check_agent_configs() -> bool:
    """Run generate_agent_configs.py --check and return True if it passes."""
    import subprocess
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "generate_agent_configs.py"), "--check"],
        capture_output=True, text=True, cwd=str(REPO_ROOT),
    )
    if result.returncode != 0:
        print(f"  [FAIL] Agent configs out of sync:")
        print(f"         {result.stdout.strip()}")
        return False
    return True


def main(strict: bool = False, check_tag: bool = True) -> int:
    print("Release Consistency Check")
    print("=" * 40)

    pyproject_ver = extract_pyproject_version()
    init_ver = extract_init_version()
    readme_zh_ver = extract_readme_version("zh")
    readme_en_ver = extract_readme_version("en")
    changelog_ver = extract_changelog_latest()

    check("pyproject.toml version present", bool(pyproject_ver),
          "missing version" if not pyproject_ver else "")
    check("__init__.py __version__ present", bool(init_ver),
          "missing __version__" if not init_ver else "")

    if pyproject_ver and init_ver:
        check("pyproject.toml == __init__.py", pyproject_ver == init_ver,
              f"pyproject={pyproject_ver}, init={init_ver}")

    if pyproject_ver and readme_zh_ver:
        check("README.md current version", readme_zh_ver == pyproject_ver,
              f"README={readme_zh_ver}, expected={pyproject_ver}")
    elif strict:
        check("README.md current version", False, "could not extract version")

    if pyproject_ver and readme_en_ver:
        check("README_en.md current version", readme_en_ver == pyproject_ver,
              f"README_en={readme_en_ver}, expected={pyproject_ver}")
    elif strict:
        check("README_en.md current version", False, "could not extract version")

    if pyproject_ver and changelog_ver:
        check("CHANGELOG.md has version section", changelog_ver == pyproject_ver,
              f"latest={changelog_ver}, expected={pyproject_ver}")
    elif strict:
        check("CHANGELOG.md has version section", False, "could not extract version")

    # Git tag check (skip when no tags exist — no tag ≠ mismatch)
    if check_tag and pyproject_ver:
        tag_ver = extract_latest_git_tag()
        if tag_ver:
            check("Latest git tag matches version", tag_ver == pyproject_ver,
                  f"tag=v{tag_ver}, expected={pyproject_ver}")
        else:
            print("  [SKIP] Latest git tag matches version — no semver tags found")

    # Agent configs check
    try:
        configs_ok = check_agent_configs()
        check("Generated agent configs in sync", configs_ok)
    except Exception as exc:
        check("Generated agent configs in sync", False, str(exc))

    print()
    if ERRORS:
        print(f"FAILED: {len(ERRORS)} check(s) failed.")
        return 1
    else:
        print("ALL CHECKS PASSED.")
        return 0


if __name__ == "__main__":
    strict = "--strict" in sys.argv
    no_tag = "--no-tag" in sys.argv
    sys.exit(main(strict=strict, check_tag=not no_tag))
