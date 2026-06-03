"""Tests for terminology consistency and documented CLI commands."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _load_terminology() -> dict:
    path = ROOT / "configs" / "terminology.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_terminology_script_passes():
    """check_terms.py must exit 0 on current repo state."""
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check_terms.py")],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"check_terms.py failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_forbidden_terms_not_in_public_docs():
    """Forbidden terms must not appear in public docs."""
    config = _load_terminology()
    forbidden = config.get("forbidden_terms", [])
    doc_files = [
        ROOT / "README.md",
        ROOT / "README_en.md",
        ROOT / "AGENTS.md",
        ROOT / "CLAUDE.md",
    ]
    for fpath in doc_files:
        if not fpath.exists():
            continue
        text = fpath.read_text(encoding="utf-8").lower()
        for term in forbidden:
            assert term.lower() not in text, (
                f"Forbidden term '{term}' found in {fpath.name}"
            )


def test_documented_cli_commands_have_help():
    """Documented CLI commands should be parseable."""
    import os
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    result = subprocess.run(
        [sys.executable, "-c", "from multi_agent_brief.cli.main import main; main(['sources', 'decide', '-h'])"],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        env=env,
    )
    # --help exits 0
    assert result.returncode == 0 or "usage" in result.stdout.lower()


def test_readme_mentions_documented_cli_commands():
    """README files should mention documented CLI commands."""
    config = _load_terminology()
    commands = config.get("cli_commands", [])
    readme_files = [ROOT / "README.md", ROOT / "README_en.md"]
    for cmd in commands:
        for required in cmd.get("docs_should_contain", []):
            found = False
            for readme in readme_files:
                if not readme.exists():
                    continue
                if required in readme.read_text(encoding="utf-8"):
                    found = True
                    break
            assert found, (
                f"CLI command doc '{required}' not found in README files"
            )


def test_required_readme_command_snippets_present():
    """Required command snippets should appear in at least one README."""
    config = _load_terminology()
    snippets = config.get("readme_command_snippets", [])
    readme_files = [ROOT / "README.md", ROOT / "README_en.md"]
    for snippet in snippets:
        found = False
        for readme in readme_files:
            if not readme.exists():
                continue
            if snippet in readme.read_text(encoding="utf-8"):
                found = True
                break
        assert found, (
            f"Required README snippet '{snippet}' not found in any README"
        )
