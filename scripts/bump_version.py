"""Sync version from VERSION file to all other locations."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE = ROOT / "VERSION"

def main() -> int:
    if not VERSION_FILE.exists():
        print("ERROR: VERSION file not found", file=sys.stderr)
        return 1

    version = VERSION_FILE.read_text().strip()
    tag = f"v{version}"
    changed = 0

    # 1. pyproject.toml
    changed += _replace_in_file(
        ROOT / "pyproject.toml",
        r'(?m)^version\s*=\s*"[^"]*"',
        f'version = "{version}"',
        "pyproject.toml",
    )

    # 2. __init__.py — uses importlib.metadata, no hardcoded version to update
    print("  __init__.py (dynamic — no update needed)")

    # 3. README.md current version
    readme = ROOT / "README.md"
    readme_text = readme.read_text(encoding="utf-8")
    if readme_text.count(f"Current version: **{tag}**") != 1:
        import re
        new_text = re.sub(
            r"Current version: \*\*v?\d+\.\d+\.\d+\*\*[^\n]*",
            f"Current version: **{tag}**",
            readme_text,
        )
        readme.write_text(new_text, encoding="utf-8")
        changed += 1
        print("  Updated README.md current version")

    # 4. README_en.md is a compatibility pointer to README.md; no version update needed.
    print("  README_en.md compatibility pointer (no update needed)")

    # 5. README.zh-CN.md current version
    readme_zh_cn = ROOT / "README.zh-CN.md"
    readme_zh_cn_text = readme_zh_cn.read_text(encoding="utf-8")
    if readme_zh_cn_text.count(f"当前版本：**{tag}**") != 1:
        import re
        new_text = re.sub(
            r"当前版本：\*\*v?\d+\.\d+\.\d+\*\*[^\n]*",
            f"当前版本：**{tag}**",
            readme_zh_cn_text,
        )
        readme_zh_cn.write_text(new_text, encoding="utf-8")
        changed += 1
        print("  Updated README.zh-CN.md current version")

    # 6. Hermes adapter
    changed += _replace_in_file(
        ROOT / "src" / "multi_agent_brief" / "hermes" / "adapter.py",
        r'version="v\d+\.\d+\.\d+"',
        f'version="{tag}"',
        "hermes/adapter.py",
    )
    changed += _replace_in_file(
        ROOT / "src" / "multi_agent_brief" / "hermes" / "adapter.py",
        r'version: \d+\.\d+\.\d+',
        f'version: {version}',
        "hermes/adapter.py (metadata)",
    )
    changed += _replace_in_file(
        ROOT / "src" / "multi_agent_brief" / "hermes" / "adapter.py",
        r'version: str = "v\d+\.\d+\.\d+"',
        f'version: str = "{tag}"',
        "hermes/adapter.py (default)",
    )

    # 7. Hermes skill metadata
    changed += _replace_in_file(
        ROOT / ".agents" / "hermes-skills" / "multi-agent-brief-hermes" / "SKILL.md",
        r'version: \d+\.\d+\.\d+',
        f'version: {version}',
        "Hermes skill SKILL.md",
    )

    # 8. Homebrew Formula stays on the last published archive until the new
    # GitHub tag exists and the release archive can be hashed.
    print("  Homebrew formula (no update before tag/archive checksum exists)")

    print(f"[bump] Synced {version} across {changed} location(s)")
    return 0


def _replace_in_file(
    path: Path,
    pattern: str,
    replacement: str,
    label: str,
    *,
    count: int = 0,
) -> int:
    import re

    text = path.read_text(encoding="utf-8")
    new_text, n = re.subn(pattern, replacement, text, count=count)
    if n == 0:
        print(f"  [{label}] no match for version pattern — skip", file=sys.stderr)
        return 0
    if n > 1:
        print(f"  [{label}] replaced {n} occurrence(s)", file=sys.stderr)
    else:
        print(f"  [{label}] updated")
    path.write_text(new_text, encoding="utf-8")
    return n


if __name__ == "__main__":
    raise SystemExit(main())
