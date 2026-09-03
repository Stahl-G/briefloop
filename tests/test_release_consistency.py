"""Tests for Release Consistency Gate."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_release_consistency.py"


class TestCheckReleaseConsistency:
    @pytest.mark.explicit_e2e
    def test_script_runs_clean(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--no-tag"],
            capture_output=True, text=True,
            cwd=str(SCRIPT.parent.parent),
        )
        assert result.returncode == 0, f"Script failed:\n{result.stdout}\n{result.stderr}"
        assert "Product baseline readiness passes" in result.stdout
        assert "BriefLoop skill freshness passes" in result.stdout
        assert "Minimal comparative evaluation packet passes" in result.stdout
        assert "Launch demo smoke passes" in result.stdout
        assert "Public product rename guard passes" in result.stdout
        assert "v1.0 pilot evidence shape passes" in result.stdout
        assert "ALL CHECKS PASSED" in result.stdout

    def test_version_consistency(self):
        """pyproject.toml and VERSION must agree without importing installed packages."""
        import re
        repo = SCRIPT.parent.parent
        pyproject = (repo / "pyproject.toml").read_text()
        v1 = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE).group(1)
        v2 = (repo / "VERSION").read_text(encoding="utf-8").strip()
        assert v1 == v2, f"pyproject={v1}, VERSION={v2}"
