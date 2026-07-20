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
    "HERMES.md",
    "scripts/setup.sh",
    "scripts/setup.ps1",
    ".agents/skills/brief-onboarding/SKILL.md",
    ".agents/skills/claim-ledger/SKILL.md",
    ".agents/skills/orchestrator/SKILL.md",
    ".agents/skills/briefloop-workbuddy/SKILL.md",
    "integrations/hermes-plugin/mabw/schemas.py",
    "integrations/hermes-plugin/mabw/skills/mabw-workflow/SKILL.md",
    "integrations/hermes-plugin/mabw/skills/mabw-workflow/references/artifact-contract.md",
    "integrations/hermes-plugin/mabw/skills/mabw-workflow/references/delegated-workflow.md",
    "integrations/workbuddy/briefloop/SKILL.md",
    ".claude/commands/briefloop.md",
    ".opencode/commands/briefloop.md",
    "examples/reference-workspaces/industry-weekly-demo/README.md",
    "examples/reference-workspaces/industry-weekly-demo/artifacts/final_brief.md",
    "examples/reference-workspaces/industry-weekly-demo/artifacts/quality_summary.md",
    "examples/reference-workspaces/industry-weekly-demo/artifacts/source_appendix.md",
]

# The supported Hermes plugin retains `mabw_*` tool and `/mabw` compatibility
# identifiers. These paths still must not teach the compatibility shell CLI as
# their primary deterministic command.
PRIMARY_CLI_FILES = [
    "integrations/hermes-plugin/README.md",
    "integrations/hermes-plugin/mabw/__init__.py",
]

FORBIDDEN_PRIMARY_CLI_PATTERN = re.compile(
    r"(?<![\w.-])multi-agent-brief(?![\w-])"
)

NAMING_AUTHORITY_FILES = [
    "docs/briefloop-naming.md",
    "docs/architecture-status.md",
    "docs/support-matrix.md",
    "docs/README.md",
]

NAMING_CONSUMER_FILES = [
    ".agents/skills/briefloop/references/naming-and-compatibility.md",
    ".agents/skills/briefloop/references/version-matrix.md",
    "integrations/hermes-plugin/mabw/skills/briefloop/references/naming-and-compatibility.md",
    "integrations/hermes-plugin/mabw/skills/briefloop/references/version-matrix.md",
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

# The Hermes runtime owns one plugin-only compatibility command. It creates a
# chat-to-onboarding session and is not a public shell CLI alias or product name.
CLASSIFIED_HERMES_PLUGIN_COMMAND = (
    "4. For a new Hermes brief, run the Hermes plugin command `/mabw new`."
)

FORBIDDEN_NAMING_AUTHORITY_PATTERNS = [
    (
        "implementation_lineage_alias",
        re.compile(
            r"MABW\s+remains\s+(?:the\s+)?implementation(?:-|\s+)lineage",
            re.IGNORECASE,
        ),
    ),
    (
        "historical_lineage_alias",
        re.compile(
            r"Historical\s+implementation(?:-|\s+)lineage:\s*MABW",
            re.IGNORECASE,
        ),
    ),
    (
        "formerly_dual_brand",
        re.compile(r"BriefLoop,\s*formerly\s+MABW", re.IGNORECASE),
    ),
    (
        "slash_dual_brand",
        re.compile(r"BriefLoop\s*/\s*MABW", re.IGNORECASE),
    ),
]

CLASSIFIED_RETIRED_NAME_LITERAL_PATTERNS = [
    re.compile(r"(?<![\w-])MABW-080(?![\w-])", re.IGNORECASE),
    re.compile(r"(?<![\w./-])/mabw(?![\w./-])", re.IGNORECASE),
    re.compile(r"(?<![\w])mabw\.[\w.*-]+", re.IGNORECASE),
    re.compile(r"(?<![\w])\.mabw[\w.-]*", re.IGNORECASE),
    re.compile(r"\bMABW_[A-Z0-9_]+\b"),
    re.compile(
        r"(?<![\w.~+-])(?:[\w.~+-]+/)+mabw(?:/[\w.~+-]+)*(?![\w.~+/-])",
        re.IGNORECASE,
    ),
    re.compile(r"(?<![\w-])mabw-workflow(?![\w-])"),
]

FORBIDDEN_NAMING_CONSUMER_TOKEN_PATTERNS = [
    (
        "retired_project_name_alias",
        re.compile(r"(?<![A-Za-z0-9.])mabw(?![A-Za-z0-9.])", re.IGNORECASE),
    ),
    (
        "retired_long_project_name_alias",
        re.compile(
            r"\bMulti(?:-|\s+)Agent\s+Brief\s+Workflow\b",
            re.IGNORECASE,
        ),
    ),
]

REQUIRED_NAMING_AUTHORITY_STATEMENTS = [
    (
        "missing_current_project_name_rule",
        "briefloop is the only current project",
        "naming authority must state that BriefLoop is the only current project",
    ),
    (
        "missing_retired_name_rule",
        "former project acronym is retired",
        "naming authority must state that the former project acronym is retired",
    ),
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
        if (
            kind == "slash_mabw"
            and path.name == "HERMES.md"
            and line.strip() == CLASSIFIED_HERMES_PLUGIN_COMMAND
        ):
            continue
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


def scan_primary_cli_file(path: Path) -> list[Finding]:
    """Reject compatibility CLI instructions without rejecting plugin API names."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return [
            Finding(
                path=path,
                line=1,
                kind="missing_primary_cli_target",
                sample="configured primary CLI target file is missing",
            )
        ]

    findings: list[Finding] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        if FORBIDDEN_PRIMARY_CLI_PATTERN.search(line):
            findings.append(
                Finding(
                    path=path,
                    line=line_no,
                    kind="compatibility_cli_in_primary_path",
                    sample=line.strip(),
                )
            )
    return findings


def scan_naming_authority_file(path: Path) -> list[Finding]:
    """Validate current naming truth without rejecting classified legacy ids."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return [
            Finding(
                path=path,
                line=1,
                kind="missing_naming_authority",
                sample="configured naming authority file is missing",
            )
        ]

    findings: list[Finding] = []
    for kind, pattern in FORBIDDEN_NAMING_AUTHORITY_PATTERNS:
        for match in pattern.finditer(text):
            line_no = text.count("\n", 0, match.start()) + 1
            findings.append(
                Finding(
                    path=path,
                    line=line_no,
                    kind=kind,
                    sample=" ".join(match.group(0).split()),
                )
            )

    normalized = " ".join(text.lower().split())
    for kind, required_text, sample in REQUIRED_NAMING_AUTHORITY_STATEMENTS:
        if required_text not in normalized:
            findings.append(Finding(path=path, line=1, kind=kind, sample=sample))
    return findings


def _mask_classified_retired_name_literals(line: str) -> str:
    """Remove explicitly classified compatibility identifiers before scanning."""

    masked = line
    for pattern in CLASSIFIED_RETIRED_NAME_LITERAL_PATTERNS:
        masked = pattern.sub(lambda match: " " * len(match.group(0)), masked)
    return masked


def scan_naming_consumer_file(path: Path) -> list[Finding]:
    """Reject retired-name aliases while allowing classified literal identifiers."""

    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return [
            Finding(
                path=path,
                line=1,
                kind="missing_naming_consumer",
                sample="configured Operator naming consumer file is missing",
            )
        ]

    findings: list[Finding] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        classified = _mask_classified_retired_name_literals(line)
        for kind, pattern in FORBIDDEN_NAMING_CONSUMER_TOKEN_PATTERNS:
            if pattern.search(classified):
                findings.append(
                    Finding(
                        path=path,
                        line=line_no,
                        kind=kind,
                        sample=line.strip(),
                    )
                )

    normalized = " ".join(text.lower().split())
    for kind, required_text, sample in REQUIRED_NAMING_AUTHORITY_STATEMENTS:
        if required_text not in normalized:
            findings.append(Finding(path=path, line=1, kind=kind, sample=sample))
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
        for rel_path in PRIMARY_CLI_FILES:
            findings.extend(scan_primary_cli_file(root / rel_path))
        for rel_path in NAMING_AUTHORITY_FILES:
            findings.extend(scan_naming_authority_file(root / rel_path))
        for rel_path in NAMING_CONSUMER_FILES:
            findings.extend(scan_naming_consumer_file(root / rel_path))
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
