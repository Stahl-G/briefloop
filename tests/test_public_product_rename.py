"""Tests for the public product rename guard."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

from multi_agent_brief.cli.main import main


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "check_public_product_rename.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("check_public_product_rename_test", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_public_product_rename_guard_runs_clean() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Public product rename guard passed" in result.stdout


def test_public_product_rename_guard_reports_line_and_suggestion(tmp_path) -> None:
    target = tmp_path / "README.md"
    target.write_text(
        "Use multi-agent-brief for the first run.\n"
        "The old /mabw command is also shown here.\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--path", str(target)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert f"{target}:1" in result.stdout
    assert f"{target}:2" in result.stdout
    assert "suggestion:" in result.stdout
    assert "prefer `briefloop`" in result.stdout


def test_public_product_rename_guard_rejects_legacy_names_before_sentence_punctuation(tmp_path) -> None:
    target = tmp_path / "getting-started.md"
    target.write_text(
        "Use multi-agent-brief.\n"
        "Formerly MABW.\n"
        "Still not /mabw.\n"
        "Do not flag multi-agent-brief-workflow or MABW-080 compatibility ids here.\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--path", str(target)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert f"{target}:1" in result.stdout
    assert f"{target}:2" in result.stdout
    assert f"{target}:3" in result.stdout
    assert f"{target}:4" not in result.stdout


def test_public_product_rename_guard_rejects_old_setup_banner(tmp_path) -> None:
    target = tmp_path / "scripts" / "setup.sh"
    target.parent.mkdir()
    target.write_text(
        "# package implementation name remains allowed in comments: multi-agent-brief-workflow\n"
        "echo \"=== multi-agent-brief-workflow setup ===\"\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--path", str(target)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "package_name_setup_output" in result.stdout
    assert f"{target}:2" in result.stdout


def test_public_product_rename_scan_is_limited_to_requested_paths(tmp_path) -> None:
    module = _load_module()
    compatibility_doc = tmp_path / "docs" / "MIGRATION.md"
    compatibility_doc.parent.mkdir()
    compatibility_doc.write_text("MABW and /mabw remain compatibility names.\n", encoding="utf-8")

    assert module.scan(paths=[]) == []
    findings = module.scan(paths=[compatibility_doc])
    assert len(findings) == 2


def test_installed_briefloop_command_passes_public_rename_guard(tmp_path, capsys) -> None:
    module = _load_module()
    target = tmp_path / "claude"

    rc = main(["claude", "install", "--repo-workdir", str(ROOT), "--target", str(target)])

    assert rc == 0
    capsys.readouterr()
    installed_briefloop = target / "commands" / "briefloop.md"
    installed_mabw = target / "commands" / "mabw.md"
    assert installed_briefloop.exists()
    assert installed_mabw.exists()
    findings = module.scan(paths=[installed_briefloop])
    assert findings == [], "\n".join(finding.format(ROOT) for finding in findings)
    first_screen = installed_briefloop.read_text(encoding="utf-8").split("## Routing", maxsplit=1)[0]
    assert "/mabw" not in first_screen


def test_public_product_rename_guard_scans_briefloop_cli_help() -> None:
    module = _load_module()

    findings = [
        finding
        for finding in module.scan()
        if str(finding.path).startswith("<briefloop")
    ]

    assert findings == [], "\n".join(finding.format(ROOT) for finding in findings)


def test_compatibility_quarantine_classifies_remaining_legacy_names() -> None:
    naming = (ROOT / "docs" / "briefloop-naming.md").read_text(encoding="utf-8")
    normalized = " ".join(naming.lower().split())
    assert "## Compatibility quarantine" in naming
    assert "not the public product identity" in naming
    assert "do not use them as first-user instructions" in normalized

    expected_rows = [
        "| `/mabw` | Deprecated Claude compatibility alias |",
        "| `multi-agent-brief` | Compatibility CLI and script entrypoint |",
        "| `multi_agent_brief` | Python module compatibility surface |",
        "| `multi-agent-brief-workflow` | Distribution/package compatibility surface |",
        "| `MABW-080` | Historical experiment namespace |",
        "| `mabw.*` schema ids | Old-workspace compatibility ids |",
        "| Old release notes and tech reports | Historical archive |",
    ]
    for row in expected_rows:
        assert row in naming

    forbidden_promotional_claims = [
        "truth proof",
        "delivery approval",
        "autonomous agent runtime",
        "output-quality improvement proof",
    ]
    for phrase in forbidden_promotional_claims:
        assert phrase in normalized


def test_compatibility_surfaces_remain_available_but_not_first_user() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'briefloop = "multi_agent_brief.cli.main:main"' in pyproject
    assert 'multi-agent-brief = "multi_agent_brief.cli.main:main"' in pyproject

    mabw_command = (ROOT / ".claude" / "commands" / "mabw.md").read_text(encoding="utf-8")
    assert "The command name `/mabw` is retained for compatibility" in mabw_command
    assert "BRIEFLOOP_CLI=multi-agent-brief" in mabw_command

    briefloop_command = (ROOT / ".claude" / "commands" / "briefloop.md").read_text(encoding="utf-8")
    first_screen = briefloop_command.split("## Routing", maxsplit=1)[0]
    assert "/briefloop new" in first_screen
    assert "/mabw" not in first_screen
    assert "multi-agent-brief" not in first_screen
