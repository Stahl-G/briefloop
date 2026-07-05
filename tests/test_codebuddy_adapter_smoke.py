from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "check_codebuddy_adapter_smoke.py"


def test_codebuddy_adapter_smoke_json_passes() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--json"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["runtime_effect"] == "readiness_check_only"
    assert "delegated_runtime_proof" in payload["non_goals"]
    check_ids = {item["id"] for item in payload["checks"]}
    assert {
        "codebuddy.skill.contract",
        "codebuddy.role_agents.contract",
        "codebuddy.handoff.contract",
    } <= check_ids


def test_codebuddy_adapter_smoke_human_output_passes() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "CodeBuddy Adapter Smoke" in result.stdout
    assert "ALL CHECKS PASSED" in result.stdout
