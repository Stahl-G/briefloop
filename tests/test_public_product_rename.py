"""Tests for the public product rename guard."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path


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


def test_public_product_rename_scan_is_limited_to_requested_paths(tmp_path) -> None:
    module = _load_module()
    compatibility_doc = tmp_path / "docs" / "MIGRATION.md"
    compatibility_doc.parent.mkdir()
    compatibility_doc.write_text("MABW and /mabw remain compatibility names.\n", encoding="utf-8")

    assert module.scan(paths=[]) == []
    findings = module.scan(paths=[compatibility_doc])
    assert len(findings) == 2
