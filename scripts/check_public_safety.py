#!/usr/bin/env python3
"""Scan release/public files for private paths, tokens, and local banned terms."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parent.parent

LARK_TOKEN_CANDIDATE_RE = re.compile(
    r"\b(?:"
    r"(?:oc|ou|on|om)_[A-Za-z0-9][A-Za-z0-9_-]{5,}"
    r"|(?:oc|ou|on|om)[A-Za-z0-9][A-Za-z0-9_-]{7,}"
    r"|cli_[A-Za-z0-9][A-Za-z0-9_-]{5,}"
    r"|cli[A-Za-z0-9][A-Za-z0-9_-]{7,}"
    r"|fld[A-Za-z0-9-]{8,}"
    r"|f[A-Za-z0-9]{15,}"
    r")\b"
)
MESSAGE_ID_RE = re.compile(r"\bmessage_id\b", re.IGNORECASE)
FILE_URL_RE = re.compile(r"file://[^\s`'\"),\]]+")
USER_PATH_RE = re.compile(
    r"/Users/(?!example(?:/|$)|user(?:/|$)|you(?:/|$)|name(?:/|$)|<[^/]+>)[^/\s:`]+(?:/|$)"
)
API_SECRET_RE = re.compile(
    r"(?i)\b(?:api[_-]?key|secret[_-]?key|access[_-]?token|refresh[_-]?token)"
    r"\s*[:=]\s*['\"]?[A-Za-z0-9_./+=-]{16,}"
)
COMMON_SECRET_RE = re.compile(r"\b(?:sk-[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9-]{20,})\b")

TEXT_SUFFIXES = {
    ".cfg",
    ".csv",
    ".ini",
    ".json",
    ".jsonl",
    ".md",
    ".py",
    ".rst",
    ".sh",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}

EXCLUDED_ARCHIVE_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
}


@dataclass(frozen=True)
class Finding:
    path: Path
    line: int
    kind: str
    sample: str


def _repo_tracked_files() -> list[Path]:
    try:
        proc = subprocess.run(
            ["git", "ls-files"],
            cwd=ROOT,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return list(_iter_archive_files(ROOT))
    return [ROOT / line for line in proc.stdout.splitlines() if line.strip()]


def _has_excluded_dir(path: Path, root: Path) -> bool:
    try:
        parts = path.resolve().relative_to(root.resolve()).parts
    except ValueError:
        parts = path.parts
    directory_parts = parts[:-1] if path.is_file() else parts
    return any(part in EXCLUDED_ARCHIVE_DIRS for part in directory_parts)


def _iter_archive_files(root: Path) -> Iterable[Path]:
    for child in sorted(root.rglob("*")):
        if child.is_file() and not _has_excluded_dir(child, root):
            yield child


def _iter_scan_files(paths: list[Path] | None) -> Iterable[Path]:
    if not paths:
        yield from _repo_tracked_files()
        return

    for root in paths:
        path = root.expanduser().resolve()
        if path.is_file():
            yield path
            continue
        if not path.is_dir():
            continue
        yield from _iter_archive_files(path)


def _banned_terms_from_env() -> list[str]:
    raw = os.environ.get("MABW_PUBLIC_SAFETY_BANNED_TERMS", "")
    return [term.strip() for term in raw.split(",") if term.strip()]


def _is_probably_text(path: Path) -> bool:
    if path.name in {".env", ".env.local", ".env.production"}:
        return True
    if path.suffix.lower() in TEXT_SUFFIXES:
        return True
    return path.suffix == ""


def _read_text(path: Path) -> str | None:
    if not _is_probably_text(path):
        return None
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None
    except OSError:
        return None


def _relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def _allowed_fixture(path: Path, line: str, kind: str) -> bool:
    rel = _relative(path)
    if rel == "scripts/check_public_safety.py":
        return True
    if rel in {
        "src/multi_agent_brief/improvement/contract.py",
        "src/multi_agent_brief/outputs/reader_final_gate.py",
    } and kind in {"user_path", "file_url"}:
        return True
    if rel in _PUBLIC_TEST_FIXTURE_FINDINGS and kind in _PUBLIC_TEST_FIXTURE_FINDINGS[rel]:
        return True
    if _path_has_part(path, "tests") and "PUBLIC_SAFETY_TEST_FIXTURE" in line and kind != "banned_term":
        return True
    if kind == "file_url" and line.strip() in {
        "- `file://`",
        "- `file://` URLs",
    }:
        return True
    if kind in {"file_url", "user_path"} and any(
        marker in line
        for marker in (
            "must not expose",
            "不得暴露",
            "forbidden",
            "FORBIDDEN",
            "omitted",
            "Reader-facing output",
            "reader-facing output",
            "must not contain",
            "_LOCAL_PATH_RE",
            "_FORBIDDEN_PATH_FRAGMENTS",
        )
    ):
        return True
    if kind == "lark_token" and re.search(r"\b(?:oc|ou|on|om)_x+\b", line):
        return True
    return False


def _path_has_part(path: Path, part: str) -> bool:
    return part in path.parts or part in Path(_relative(path)).parts


def _sample(line: str, needle: str) -> str:
    text = line.strip().replace("\t", " ")
    if len(text) <= 160:
        return text
    index = max(text.find(needle), 0)
    start = max(0, index - 50)
    end = min(len(text), index + len(needle) + 70)
    return text[start:end]


_LARK_TOKEN_CONTEXT_RE = re.compile(
    r"(?:\brecipient\b|\bchat\b|\bfolder\b|\bfile\b|\btoken\b|open[_ -]?id|folder[_ -]?token|chat[_ -]?id|\bmessage\b)\W*$",
    re.IGNORECASE,
)

_PUBLIC_TEST_FIXTURE_FINDINGS: dict[str, set[str]] = {
    "tests/test_improvement_contract.py": {"file_url", "user_path", "common_secret"},
    "tests/test_provenance_projection.py": {"file_url", "user_path"},
    "tests/test_reader_final_gate.py": {"file_url", "user_path"},
    "tests/test_source_appendix.py": {"file_url", "user_path"},
}


def _looks_like_lark_token(value: str, line: str, start: int) -> bool:
    if any(char.isdigit() for char in value):
        return True
    if value.startswith("cli") and not value.startswith("cli_"):
        return True
    if value.startswith("fld") and re.fullmatch(r"fld[a-z-]{8,}", value):
        return True
    context = line[max(0, start - 48):start]
    return bool(_LARK_TOKEN_CONTEXT_RE.search(context))


def scan(paths: list[Path] | None = None, *, banned_terms: list[str] | None = None) -> list[Finding]:
    findings: list[Finding] = []
    banned = banned_terms if banned_terms is not None else _banned_terms_from_env()

    for path in _iter_scan_files(paths):
        if path.name in {".env", ".env.local", ".env.production"}:
            findings.append(Finding(path=path, line=0, kind="env_file", sample=path.name))
            continue

        text = _read_text(path)
        if text is None:
            continue

        for lineno, line in enumerate(text.splitlines(), start=1):
            checks: list[tuple[str, str]] = []
            for term in banned:
                if term in line:
                    checks.append(("banned_term", term))
            for match in LARK_TOKEN_CANDIDATE_RE.finditer(line):
                token = match.group(0)
                if _looks_like_lark_token(token, line, match.start()):
                    checks.append(("lark_token", token))
            for kind, regex in (
                ("user_path", USER_PATH_RE),
                ("file_url", FILE_URL_RE),
                ("message_id", MESSAGE_ID_RE),
                ("api_secret", API_SECRET_RE),
                ("common_secret", COMMON_SECRET_RE),
            ):
                for match in regex.finditer(line):
                    checks.append((kind, match.group(0)))

            for kind, needle in checks:
                if _allowed_fixture(path, line, kind):
                    continue
                findings.append(
                    Finding(
                        path=path,
                        line=lineno,
                        kind=kind,
                        sample=_sample(line, needle),
                    )
                )

    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Scan tracked release files or explicit paths for private data leaks."
    )
    parser.add_argument(
        "--path",
        action="append",
        default=[],
        help="Additional file or directory to scan. Defaults to git-tracked files.",
    )
    args = parser.parse_args(argv)

    paths = [Path(value) for value in args.path] if args.path else None
    findings = scan(paths)
    if findings:
        print("Public safety scan failed:")
        for finding in findings:
            location = _relative(finding.path)
            suffix = f":{finding.line}" if finding.line else ""
            print(f"  - {location}{suffix} [{finding.kind}] {finding.sample}")
        return 1

    print("[OK] Public safety scan passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
