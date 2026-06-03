#!/usr/bin/env python3
"""Public-safe content scanner.

Scans public-facing files for real personal identity, employer, client,
credential, and sensitive internal-context leaks.

Usage:
    python scripts/public_safe_scan.py          # scan and exit with code 1 if hits
    python scripts/public_safe_scan.py --json    # JSON output for CI
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Sensitive patterns — single source of truth for tests and CLI.
# Each tuple: (regex_pattern, human_description)
# Patterns are matched case-insensitively.
# ---------------------------------------------------------------------------

SENSITIVE_PATTERNS: list[tuple[str, str]] = [
    # -- Personal identity --
    (r"\bYihong\b", "real personal name"),
    (r"\bgyh0256\b", "real GitHub handle"),
    (r"\bStahl\b", "real GitHub username fragment"),
    # -- Real company / employer / client --
    (r"\bTOYO\b", "real company name"),
    (r"\b小米\b", "real company name"),
    (r"\b重庆啤酒\b", "real company name"),
    (r"\b特变电工\b", "real company name"),
    # -- Real locations in sensitive context --
    (r"\b胡志明\b", "real city in sensitive context"),
    (r"\b埃塞\b", "real country reference"),
    # -- Internal fund abbreviation --
    (r"\bVCPE\b", "internal fund abbreviation"),
    # -- Real email (test@example.com is allowed) --
    (
        r"[a-zA-Z0-9._%+-]+@(?!example\.com)[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
        "real email address",
    ),
    # -- Hardcoded credentials --
    (
        r"(?:api[_-]?key|token|secret|password)\s*[:=]\s*['\"][^'\"]{8,}",
        "hardcoded credential",
    ),
]

# Public-facing files and directories to scan.
PUBLIC_TARGETS: list[Path] = [
    REPO / "README.md",
    REPO / "README_en.md",
    REPO / "LICENSE",
    REPO / "AGENTS.md",
    REPO / "CLAUDE.md",
    REPO / "pyproject.toml",
]

PUBLIC_DIRS: list[Path] = [
    REPO / "docs",
    REPO / "examples",
    REPO / "tests",
    REPO / "configs",
    REPO / ".claude",
    REPO / ".agents",
    REPO / "scripts",
    REPO / "src",
]

SCAN_EXTENSIONS: set[str] = {".md", ".toml", ".yaml", ".yml", ".txt", ".py"}

SKIP_DIRS: set[str] = {".git", ".venv", "__pycache__", "dist", "build", ".pytest_cache"}


@dataclass
class ScanHit:
    file: str
    line: int
    pattern_desc: str
    match: str


@dataclass
class ScanResult:
    hits: list[ScanHit] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return len(self.hits) == 0


def collect_files() -> list[Path]:
    """Collect all public-facing files for scanning."""
    files: list[Path] = []

    for p in PUBLIC_TARGETS:
        if p.exists():
            files.append(p)

    for d in PUBLIC_DIRS:
        if not d.exists():
            continue
        for f in d.rglob("*"):
            if not f.is_file():
                continue
            if f.suffix not in SCAN_EXTENSIONS:
                continue
            # Skip excluded directories
            if any(skip in f.parts for skip in SKIP_DIRS):
                continue
            files.append(f)

    return files


def scan_file(path: Path) -> list[ScanHit]:
    """Scan a single file for sensitive patterns."""
    hits: list[ScanHit] = []
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return hits

    rel = str(path.relative_to(REPO))
    for line_no, line in enumerate(text.splitlines(), 1):
        for pattern, desc in SENSITIVE_PATTERNS:
            for m in re.finditer(pattern, line, re.IGNORECASE):
                hits.append(ScanHit(file=rel, line=line_no, pattern_desc=desc, match=m.group()))
    return hits


def scan_all() -> ScanResult:
    """Scan all public-facing files and return aggregated results."""
    result = ScanResult()
    for f in collect_files():
        result.hits.extend(scan_file(f))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Public-safe content scanner")
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")
    args = parser.parse_args()

    result = scan_all()

    if args.json_output:
        output = {
            "ok": result.ok,
            "hit_count": len(result.hits),
            "hits": [
                {"file": h.file, "line": h.line, "pattern": h.pattern_desc, "match": h.match}
                for h in result.hits
            ],
        }
        print(json.dumps(output, indent=2, ensure_ascii=False))
    else:
        if result.ok:
            print("✅ No sensitive content found in public-facing files.")
        else:
            print(f"❌ Found {len(result.hits)} sensitive hit(s):\n")
            for h in result.hits:
                print(f"  {h.file}:{h.line}  [{h.pattern_desc}]  '{h.match}'")

    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
