"""Tests for Release Consistency Gate."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_release_consistency.py"


class TestCheckReleaseConsistency:
    def test_script_runs_clean(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--no-tag"],
            capture_output=True, text=True,
            cwd=str(SCRIPT.parent.parent),
        )
        assert result.returncode == 0, f"Script failed:\n{result.stdout}\n{result.stderr}"
        assert "ALL CHECKS PASSED" in result.stdout

    def test_strict_mode_runs(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--strict", "--no-tag"],
            capture_output=True, text=True,
            cwd=str(SCRIPT.parent.parent),
        )
        assert result.returncode == 0, f"Strict mode failed:\n{result.stdout}\n{result.stderr}"

    def test_version_consistency(self):
        """pyproject.toml and __init__.py must agree."""
        import re
        repo = SCRIPT.parent.parent
        pyproject = (repo / "pyproject.toml").read_text()
        init = (repo / "src" / "multi_agent_brief" / "__init__.py").read_text()
        v1 = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE).group(1)
        v2 = re.search(r'__version__\s*=\s*"([^"]+)"', init).group(1)
        assert v1 == v2, f"pyproject={v1}, init={v2}"
