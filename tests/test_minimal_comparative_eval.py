"""Tests for the v0.11.4 minimal comparative evaluation packet guard."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "check_minimal_comparative_eval.py"


def test_minimal_comparative_eval_check_runs_clean() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Minimal Comparative Evaluation Check" in result.stdout
    assert "ALL CHECKS PASSED" in result.stdout
