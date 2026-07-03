"""Tests for the launch/demo smoke guard."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_launch_smoke.py"


def test_launch_smoke_json_runs_demo_handoff_path():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--json"],
        capture_output=True,
        text=True,
        cwd=str(SCRIPT.parent.parent),
    )
    assert result.returncode == 0, f"launch smoke failed:\n{result.stdout}\n{result.stderr}"
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert "not semantic truth proof" in payload["boundary"]
    assert "output-quality improvement proof" in payload["boundary"]
    assert {step["id"] for step in payload["steps"]} >= {
        "repo_layout",
        "source_import",
        "cli_version",
        "demo_init",
        "demo_doctor",
        "demo_runtime_handoff",
        "handoff_artifacts",
    }
