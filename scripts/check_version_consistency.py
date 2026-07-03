"""Check version consistency across the repo.  Intended for CI.

Single source of truth: VERSION file at repo root.
Every other file must agree with it.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE = ROOT / "VERSION"
PENDING_FORMULA_SHA256 = "0" * 64
README_EN_POINTER = """# BriefLoop English README

The English README has moved to [README.md](README.md).

This file is kept as a compatibility pointer so existing external links to
`README_en.md` do not break.

For the Simplified Chinese README, see [README.zh-CN.md](README.zh-CN.md).
"""

# ── helpers ──────────────────────────────────────────────────────────

if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomllib  # type: ignore[no-redef, unused-ignore]
    except ImportError:
        import tomli as tomllib  # type: ignore[no-redef]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _error(msg: str) -> None:
    print(f"  [FAIL] {msg}", flush=True)


def _ok(msg: str) -> None:
    print(f"  [OK] {msg}", flush=True)


# ── main ─────────────────────────────────────────────────────────────

def main() -> int:
    if not VERSION_FILE.exists():
        print("ERROR: VERSION file not found", file=sys.stderr)
        return 1

    version = VERSION_FILE.read_text().strip()
    tag = f"v{version}"
    errors: list[str] = []

    print(f"Version Consistency Check — expected {tag}")
    print("=" * 48)

    # 1. pyproject.toml
    try:
        pyproject = tomllib.loads(_read(ROOT / "pyproject.toml"))
        pv = pyproject.get("project", {}).get("version")
    except Exception:
        pv = None
    if pv and pv == version:
        _ok("pyproject.toml")
    else:
        _error(f"pyproject.toml version={pv}, expected {version}")
        errors.append("pyproject.toml")

    # 2. __init__.py — must use importlib.metadata.version(), not a hardcoded string
    init = _read(ROOT / "src" / "multi_agent_brief" / "__init__.py")
    if "importlib.metadata" in init and 'version("multi-agent-brief-workflow")' in init:
        _ok("__init__.py (dynamic via importlib.metadata)")
    else:
        _error("__init__.py does not use importlib.metadata.version()")
        errors.append("__init__.py")

    # 3. README.md current version
    readme = _read(ROOT / "README.md")
    if f"Current version: **{tag}**" in readme:
        _ok("README.md current version")
    else:
        _error(f"README.md current version does not match {tag}")
        errors.append("README.md")

    # 4. README_en.md compatibility pointer
    readme_en_path = ROOT / "README_en.md"
    if readme_en_path.exists():
        readme_en = _read(readme_en_path)
        if readme_en.strip() == README_EN_POINTER.strip():
            _ok("README_en.md compatibility pointer")
        else:
            _error("README_en.md is not the compatibility pointer to README.md")
            errors.append("README_en.md")
    else:
        _error("README_en.md compatibility pointer missing")
        errors.append("README_en.md")

    # 5. README.zh-CN.md
    readme_zh_cn_path = ROOT / "README.zh-CN.md"
    if readme_zh_cn_path.exists():
        readme_zh_cn = _read(readme_zh_cn_path)
        if f"当前版本：**{tag}**" in readme_zh_cn:
            _ok("README.zh-CN.md current version")
        else:
            _error(f"README.zh-CN.md does not mention {tag}")
            errors.append("README.zh-CN.md")
    else:
        _error("README.zh-CN.md missing")
        errors.append("README.zh-CN.md")

    # 6. CHANGELOG top-level entry (matches both "[0.5.8]" and "0.5.8")
    changelog = _read(ROOT / "CHANGELOG.md")
    version_no_v = version.lstrip("v")
    if re.search(rf"^##\s+\[?{re.escape(version_no_v)}\]?", changelog, re.MULTILINE):
        _ok("CHANGELOG.md version section")
    else:
        _error(f"CHANGELOG.md missing entry for {version}")
        errors.append("CHANGELOG.md")

    # 7. Stale old versions in "current version" context
    current_version_patterns = [
        (r"当前版本[:：]\s*\*\*v?([0-9]+\.[0-9]+\.[0-9]+)\*\*", ["README.zh-CN.md"]),
        (r"Current version[:：]\s*\*\*v?([0-9]+\.[0-9]+\.[0-9]+)\*\*", ["README.md", "docs/roadmap.md"]),
    ]
    for pat, files in current_version_patterns:
        for file in files:
            path = ROOT / file
            if not path.exists():
                continue
            text = _read(path)
            for m in re.finditer(pat, text):
                found = m.group(1)
                if found != version:
                    _error(f"{file} says current version {found}, expected {version}")
                    errors.append(f"{file}:{found}")

    # 8. Hermes adapter
    adapter = _read(ROOT / "src" / "multi_agent_brief" / "hermes" / "adapter.py")
    hermes_ok = True
    if f'version="{tag}"' not in adapter:
        _error(f"hermes/adapter.py missing version={tag}")
        hermes_ok = False
    if f"version: {version}" not in adapter:
        _error(f"hermes/adapter.py skill metadata missing version {version}")
        hermes_ok = False
    if hermes_ok:
        _ok("hermes/adapter.py version")
    else:
        errors.append("hermes/adapter.py")

    # 9. Hermes skill SKILL.md
    skill_path = ROOT / ".agents" / "hermes-skills" / "multi-agent-brief-hermes" / "SKILL.md"
    if skill_path.exists():
        skill_text = _read(skill_path)
        if f"version: {version}" in skill_text:
            _ok("Hermes skill SKILL.md")
        else:
            _error(f"Hermes skill SKILL.md version mismatch")
            errors.append("SKILL.md")

    # 10. Homebrew Formula source archive tag
    formula_path = ROOT / "Formula" / "multi-agent-brief.rb"
    if formula_path.exists():
        formula = _read(formula_path)
        match = re.search(r"refs/tags/(v\d+\.\d+\.\d+)\.tar\.gz", formula)
        formula_tag = match.group(1) if match else ""
        sha_match = re.search(r'sha256\s+"([0-9a-fA-F]{64})"', formula)
        formula_sha = sha_match.group(1).lower() if sha_match else ""
        if not formula_tag:
            _error("Homebrew formula URL tag missing")
            errors.append("Formula/multi-agent-brief.rb")
        elif formula_tag != tag:
            _error(f"Homebrew formula URL points at {formula_tag}, expected {tag}")
            errors.append("Formula/multi-agent-brief.rb")
        elif not formula_sha:
            _error("Homebrew formula sha256 missing")
            errors.append("Formula/multi-agent-brief.rb")
        elif _git_tag_exists(tag):
            if formula_sha == PENDING_FORMULA_SHA256:
                _error(
                    "Homebrew formula checksum is still pending after the git tag exists; "
                    "recompute the release archive sha256."
                )
                errors.append("Formula/multi-agent-brief.rb")
            else:
                _ok("Homebrew formula tag and checksum")
        else:
            if formula_sha == PENDING_FORMULA_SHA256:
                _ok("Homebrew formula staged for current tag; checksum pending until tag exists")
            else:
                _error(
                    "Homebrew formula points at current VERSION before the git tag exists "
                    "with a non-pending checksum; use the pending checksum marker until "
                    "the release archive can be hashed."
                )
                errors.append("Formula/multi-agent-brief.rb")

    # ── report ──
    print()
    if errors:
        print(f"FAILED: {len(errors)} location(s) are out of sync with VERSION ({version}).", flush=True)
        print("Run: python scripts/bump_version.py", flush=True)
        return 1

    print("ALL CHECKS PASSED.", flush=True)
    return 0


def _git_tag_exists(tag: str) -> bool:
    return _local_git_tag_exists(tag) or _remote_git_tag_exists(tag)


def _local_git_tag_exists(tag: str) -> bool:
    try:
        result = subprocess.run(
            ["git", "tag", "--list", tag],
            cwd=ROOT,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except (FileNotFoundError, OSError):
        return False
    return tag in {line.strip() for line in result.stdout.splitlines()}


def _remote_git_tag_exists(tag: str) -> bool:
    try:
        result = subprocess.run(
            ["git", "ls-remote", "--exit-code", "--tags", "origin", f"refs/tags/{tag}"],
            cwd=ROOT,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except (FileNotFoundError, OSError):
        return False
    if result.returncode != 0:
        return False
    expected_ref = f"refs/tags/{tag}"
    return any(line.strip().endswith(expected_ref) for line in result.stdout.splitlines())


if __name__ == "__main__":
    raise SystemExit(main())
