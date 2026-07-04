#!/usr/bin/env python3
"""Release Consistency Gate — verifies version and config sync across the repo.

Checks:
  1. pyproject.toml version == __init__.py __version__
  2. README.md current version line matches
  3. README_en.md remains a compatibility pointer to README.md
  4. README.zh-CN.md current version line matches
  5. CHANGELOG.md has a section for the current version
  6. Latest git tag matches current version (skipped if no tags or --no-tag)
  7. Generated agent configs are up to date (delegates to generate_agent_configs.py --check)
  8. Public safety scan passes for tracked release files
  9. Product baseline readiness guard passes
  10. BriefLoop operator skill freshness guard passes
  11. Minimal comparative evaluation packet guard passes
  12. Launch/demo smoke guard passes
  13. WorkBuddy Skill pack guard passes

Usage:
  python scripts/check_release_consistency.py [--strict] [--no-tag]

Exit codes:
  0 = all checks pass
  1 = one or more checks failed
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ERRORS: list[str] = []
README_EN_POINTER = """# BriefLoop English README

The English README has moved to [README.md](README.md).

This file is kept as a compatibility pointer so existing external links to
`README_en.md` do not break.

For the Simplified Chinese README, see [README.zh-CN.md](README.zh-CN.md).
"""


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
    """Get the source package version contract without importing ambient installs."""
    text = (REPO_ROOT / "src" / "multi_agent_brief" / "__init__.py").read_text(encoding="utf-8")
    if "importlib.metadata" in text and 'version("multi-agent-brief-workflow")' in text:
        return extract_pyproject_version()
    if "from importlib.metadata import" in text and 'version("multi-agent-brief-workflow")' in text:
        return extract_pyproject_version()
    m = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    if m:
        return m.group(1)
    return ""


def extract_readme_version(filename: str) -> str:
    text = (REPO_ROOT / filename).read_text(encoding="utf-8")
    if filename == "README.zh-CN.md":
        pattern = r'当前版本[：:]\s*\*\*v?([^*]+)\*\*'
    else:
        pattern = r'Current version[：:]\s*\*\*v?([^*]+)\*\*'
    m = re.search(pattern, text)
    return m.group(1).strip() if m else ""


def readme_en_is_pointer() -> bool:
    path = REPO_ROOT / "README_en.md"
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8")
    return text.strip() == README_EN_POINTER.strip()


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
        stderr = result.stderr.strip()
        stdout = result.stdout.strip()
        label = "Agent config generation failed"
        if "ModuleNotFoundError" in stderr or "No module named" in stderr:
            label += " (missing dependency)"
        print(f"  [FAIL] {label}:")
        if stdout:
            print("         stdout:")
            for line in stdout.splitlines():
                print(f"           {line}")
        if stderr:
            print("         stderr:")
            for line in stderr.splitlines():
                print(f"           {line}")
        if not stdout and not stderr:
            print("         no stdout/stderr captured")
        return False
    return True


def check_public_safety() -> bool:
    """Run tracked-file public safety scan and return True if it passes."""
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "check_public_safety.py")],
        capture_output=True, text=True, cwd=str(REPO_ROOT),
    )
    if result.returncode != 0:
        print("  [FAIL] Public safety scan failed:")
        stdout = result.stdout.strip()
        stderr = result.stderr.strip()
        if stdout:
            print("         stdout:")
            for line in stdout.splitlines():
                print(f"           {line}")
        if stderr:
            print("         stderr:")
            for line in stderr.splitlines():
                print(f"           {line}")
        if not stdout and not stderr:
            print("         no stdout/stderr captured")
        return False
    return True


def check_product_baseline() -> bool:
    """Run v0.11 product-baseline readiness guard and return True if it passes."""
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "check_product_baseline.py"), "--json"],
        capture_output=True, text=True, cwd=str(REPO_ROOT),
    )
    stdout = result.stdout.strip()
    stderr = result.stderr.strip()
    payload_ok = False
    if result.returncode == 0 and stdout:
        try:
            payload = json.loads(stdout)
            payload_ok = payload.get("ok") is True
        except json.JSONDecodeError:
            payload_ok = False
    if result.returncode != 0 or not payload_ok:
        print("  [FAIL] Product baseline readiness failed:")
        if stdout:
            print("         stdout:")
            for line in stdout.splitlines():
                print(f"           {line}")
        if stderr:
            print("         stderr:")
            for line in stderr.splitlines():
                print(f"           {line}")
        if not stdout and not stderr:
            print("         no stdout/stderr captured")
        return False
    return True


def check_briefloop_skill_freshness() -> bool:
    """Run BriefLoop operator skill freshness guard and return True if it passes."""
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "check_briefloop_skill_freshness.py"), "--json"],
        capture_output=True, text=True, cwd=str(REPO_ROOT),
    )
    stdout = result.stdout.strip()
    stderr = result.stderr.strip()
    payload_ok = False
    if result.returncode == 0 and stdout:
        try:
            payload = json.loads(stdout)
            payload_ok = payload.get("ok") is True
        except json.JSONDecodeError:
            payload_ok = False
    if result.returncode != 0 or not payload_ok:
        print("  [FAIL] BriefLoop skill freshness failed:")
        if stdout:
            print("         stdout:")
            for line in stdout.splitlines():
                print(f"           {line}")
        if stderr:
            print("         stderr:")
            for line in stderr.splitlines():
                print(f"           {line}")
        if not stdout and not stderr:
            print("         no stdout/stderr captured")
        return False
    return True


def check_minimal_comparative_eval() -> bool:
    """Run v0.11.4 minimal comparative evaluation packet guard."""
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "check_minimal_comparative_eval.py"), "--json"],
        capture_output=True, text=True, cwd=str(REPO_ROOT),
    )
    stdout = result.stdout.strip()
    stderr = result.stderr.strip()
    payload_ok = False
    if result.returncode == 0 and stdout:
        try:
            payload = json.loads(stdout)
            payload_ok = payload.get("ok") is True
        except json.JSONDecodeError:
            payload_ok = False
    if result.returncode != 0 or not payload_ok:
        print("  [FAIL] Minimal comparative evaluation check failed:")
        if stdout:
            print("         stdout:")
            for line in stdout.splitlines():
                print(f"           {line}")
        if stderr:
            print("         stderr:")
            for line in stderr.splitlines():
                print(f"           {line}")
        if not stdout and not stderr:
            print("         no stdout/stderr captured")
        return False
    return True


def check_launch_smoke() -> bool:
    """Run quick launch/demo smoke and return True if it passes."""
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "check_launch_smoke.py"), "--json"],
        capture_output=True, text=True, cwd=str(REPO_ROOT),
    )
    stdout = result.stdout.strip()
    stderr = result.stderr.strip()
    payload_ok = False
    if result.returncode == 0 and stdout:
        try:
            payload = json.loads(stdout)
            payload_ok = payload.get("ok") is True
        except json.JSONDecodeError:
            payload_ok = False
    if result.returncode != 0 or not payload_ok:
        print("  [FAIL] Launch demo smoke failed:")
        if stdout:
            print("         stdout:")
            for line in stdout.splitlines():
                print(f"           {line}")
        if stderr:
            print("         stderr:")
            for line in stderr.splitlines():
                print(f"           {line}")
        if not stdout and not stderr:
            print("         no stdout/stderr captured")
        return False
    return True


def check_workbuddy_skill_pack() -> bool:
    """Run WorkBuddy Skill pack shape guard and return True if it passes."""
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "check_workbuddy_skill_pack.py"), "--json"],
        capture_output=True, text=True, cwd=str(REPO_ROOT),
    )
    stdout = result.stdout.strip()
    stderr = result.stderr.strip()
    payload_ok = False
    if result.returncode == 0 and stdout:
        try:
            payload = json.loads(stdout)
            payload_ok = payload.get("ok") is True
        except json.JSONDecodeError:
            payload_ok = False
    if result.returncode != 0 or not payload_ok:
        print("  [FAIL] WorkBuddy Skill pack check failed:")
        if stdout:
            print("         stdout:")
            for line in stdout.splitlines():
                print(f"           {line}")
        if stderr:
            print("         stderr:")
            for line in stderr.splitlines():
                print(f"           {line}")
        if not stdout and not stderr:
            print("         no stdout/stderr captured")
        return False
    return True


def main(strict: bool = False, check_tag: bool = True) -> int:
    print("Release Consistency Check")
    print("=" * 40)

    pyproject_ver = extract_pyproject_version()
    init_ver = extract_init_version()
    readme_ver = extract_readme_version("README.md")
    readme_zh_cn_ver = extract_readme_version("README.zh-CN.md")
    changelog_ver = extract_changelog_latest()

    check("pyproject.toml version present", bool(pyproject_ver),
          "missing version" if not pyproject_ver else "")
    check("__init__.py __version__ present", bool(init_ver),
          "missing __version__" if not init_ver else "")

    if pyproject_ver and init_ver:
        check("pyproject.toml == __init__.py", pyproject_ver == init_ver,
              f"pyproject={pyproject_ver}, init={init_ver}")

    if pyproject_ver and readme_ver:
        check("README.md current version", readme_ver == pyproject_ver,
              f"README={readme_ver}, expected={pyproject_ver}")
    elif strict:
        check("README.md current version", False, "could not extract version")

    check("README_en.md compatibility pointer", readme_en_is_pointer())

    if pyproject_ver and readme_zh_cn_ver:
        check("README.zh-CN.md current version", readme_zh_cn_ver == pyproject_ver,
              f"README.zh-CN={readme_zh_cn_ver}, expected={pyproject_ver}")
    elif strict:
        check("README.zh-CN.md current version", False, "could not extract version")

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

    try:
        public_safety_ok = check_public_safety()
        check("Public safety scan passes", public_safety_ok)
    except Exception as exc:
        check("Public safety scan passes", False, str(exc))

    try:
        product_baseline_ok = check_product_baseline()
        check("Product baseline readiness passes", product_baseline_ok)
    except Exception as exc:
        check("Product baseline readiness passes", False, str(exc))

    try:
        skill_freshness_ok = check_briefloop_skill_freshness()
        check("BriefLoop skill freshness passes", skill_freshness_ok)
    except Exception as exc:
        check("BriefLoop skill freshness passes", False, str(exc))

    try:
        comparative_eval_ok = check_minimal_comparative_eval()
        check("Minimal comparative evaluation packet passes", comparative_eval_ok)
    except Exception as exc:
        check("Minimal comparative evaluation packet passes", False, str(exc))

    try:
        launch_smoke_ok = check_launch_smoke()
        check("Launch demo smoke passes", launch_smoke_ok)
    except Exception as exc:
        check("Launch demo smoke passes", False, str(exc))

    try:
        workbuddy_pack_ok = check_workbuddy_skill_pack()
        check("WorkBuddy Skill pack passes", workbuddy_pack_ok)
    except Exception as exc:
        check("WorkBuddy Skill pack passes", False, str(exc))

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
