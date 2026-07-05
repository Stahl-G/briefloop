#!/usr/bin/env python3
"""Guard BriefLoop public first-path naming.

The project still carries compatibility names such as MABW, /mabw, and
multi-agent-brief in historical, schema, migration, and command-compatibility
surfaces. This guard is narrower: it blocks those names from the public
first-user product path where BriefLoop should be the only primary name.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

TARGET_FILES = [
    "README.md",
    "README.zh-CN.md",
    "docs/getting-started.md",
    "docs/weekly-loop.md",
    "docs/troubleshooting.md",
    "docs/workbuddy.md",
    "docs/workbuddy.zh-CN.md",
    "docs/workbuddy-smoke-checklist.md",
    "docs/windows-powershell.md",
    "docs/windows-powershell.zh-CN.md",
    "CLAUDE.md",
    "scripts/setup.sh",
    "scripts/setup.ps1",
    ".agents/skills/briefloop-workbuddy/SKILL.md",
    "integrations/workbuddy/briefloop/SKILL.md",
    ".claude/commands/briefloop.md",
    ".opencode/commands/briefloop.md",
    "examples/reference-workspaces/industry-weekly-demo/README.md",
    "examples/reference-workspaces/industry-weekly-demo/artifacts/final_brief.md",
    "examples/reference-workspaces/industry-weekly-demo/artifacts/quality_summary.md",
    "examples/reference-workspaces/industry-weekly-demo/artifacts/source_appendix.md",
]

CLI_HELP_COMMANDS = [
    (),
    ("new",),
    ("run",),
    ("status",),
    ("feedback",),
    ("deliver",),
    ("onboard",),
    ("init",),
    ("claude",),
    ("claude", "install"),
]

FORBIDDEN_PATTERNS = [
    ("slash_mabw", re.compile(r"(?<![\w.-])/mabw\b", re.IGNORECASE)),
    ("multi_agent_brief_cli", re.compile(r"(?<![\w.-])multi-agent-brief(?![\w-])")),
    ("mabw_name", re.compile(r"(?<![\w./-])mabw(?![\w-])", re.IGNORECASE)),
]

FORBIDDEN_SETUP_OUTPUT_PATTERNS = [
    ("package_name_setup_output", re.compile(r"multi-agent-brief-workflow", re.IGNORECASE)),
]

SUGGESTION = (
    "Use BriefLoop public naming here: prefer `briefloop` for shell commands "
    "and `/briefloop` for Claude Code. Move compatibility or history wording "
    "to docs/MIGRATION.md or another explicit compatibility/history surface."
)


@dataclass(frozen=True)
class Finding:
    path: Path
    line: int
    kind: str
    sample: str

    def format(self, root: Path) -> str:
        try:
            rel = self.path.relative_to(root)
        except ValueError:
            rel = self.path
        return f"{rel}:{self.line}: {self.kind}: {self.sample}\n  suggestion: {SUGGESTION}"


def _line_findings(path: Path, line_no: int, line: str) -> list[Finding]:
    findings: list[Finding] = []
    for kind, pattern in FORBIDDEN_PATTERNS:
        for match in pattern.finditer(line):
            sample = line.strip()
            findings.append(Finding(path=path, line=line_no, kind=kind, sample=sample))
            # One finding per kind per line is enough and keeps diagnostics readable.
            break
    if _is_setup_user_visible_output(path, line):
        for kind, pattern in FORBIDDEN_SETUP_OUTPUT_PATTERNS:
            if pattern.search(line):
                findings.append(Finding(path=path, line=line_no, kind=kind, sample=line.strip()))
                break
    return findings


def _is_setup_user_visible_output(path: Path, line: str) -> bool:
    if path.name not in {"setup.sh", "setup.ps1"}:
        return False
    stripped = line.lstrip()
    return stripped.startswith("echo ") or stripped.startswith("Write-Host ")


def scan_file(path: Path) -> list[Finding]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return [
            Finding(
                path=path,
                line=1,
                kind="missing_target",
                sample="configured public rename target file is missing",
            )
        ]
    findings: list[Finding] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        findings.extend(_line_findings(path, line_no, line))
    return findings


def _briefloop_help_text(args: tuple[str, ...]) -> str:
    src_path = ROOT / "src"
    if str(src_path) not in sys.path:
        sys.path.insert(0, str(src_path))
    from multi_agent_brief.cli.main import build_parser

    parser = build_parser(prog="briefloop")
    output = io.StringIO()
    argv = [*args, "--help"]
    with contextlib.redirect_stdout(output):
        try:
            parser.parse_args(argv)
        except SystemExit:
            pass
    return output.getvalue()


def _briefloop_cli_help_findings() -> list[Finding]:
    findings: list[Finding] = []
    for args in CLI_HELP_COMMANDS:
        label = " ".join(("briefloop", *args, "--help"))
        path = Path(f"<{label}>")
        help_lines = _briefloop_help_text(args).splitlines()
        if not help_lines:
            findings.append(
                Finding(
                    path=path,
                    line=1,
                    kind="missing_cli_help",
                    sample="configured CLI help target produced no stdout",
                )
            )
            continue
        for line_no, line in enumerate(help_lines, start=1):
            findings.extend(_line_findings(path, line_no, line))
    return findings


def scan(paths: list[Path] | None = None, *, root: Path = ROOT) -> list[Finding]:
    target_paths = [root / rel_path for rel_path in TARGET_FILES] if paths is None else paths
    findings: list[Finding] = []
    for path in target_paths:
        findings.extend(scan_file(path))
    if paths is None:
        findings.extend(_briefloop_cli_help_findings())
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--path",
        action="append",
        default=None,
        help="Replacement path to scan instead of the default first-user surfaces. May be repeated.",
    )
    args = parser.parse_args(argv)

    paths = [Path(item).expanduser().resolve() for item in args.path] if args.path else None
    findings = scan(paths=paths)
    if findings:
        print("Public product rename guard failed:")
        for finding in findings:
            print(finding.format(ROOT))
        return 1
    print("[OK] Public product rename guard passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
