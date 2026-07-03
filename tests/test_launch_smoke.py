"""Tests for the launch/demo smoke guard."""

from __future__ import annotations

import json
import importlib.util
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
        "cli_version_matches_repo",
        "demo_init",
        "demo_doctor",
        "demo_runtime_handoff",
        "deterministic_demo_script",
        "handoff_artifacts",
        "deterministic_demo_artifacts",
    }
    by_id = {step["id"]: step for step in payload["steps"]}
    assert by_id["cli_version_matches_repo"]["expected"] == (
        SCRIPT.parent.parent / "VERSION"
    ).read_text(encoding="utf-8").strip()
    assert (
        by_id["cli_version_matches_repo"]["actual"]
        == by_id["cli_version_matches_repo"]["expected"]
    )
    assert not by_id["handoff_artifacts"]["missing"]
    assert not by_id["deterministic_demo_artifacts"]["missing"]


def test_launch_smoke_rejects_cli_version_drift():
    spec = importlib.util.spec_from_file_location("launch_smoke_test", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    result = module._check_cli_version({"stdout_tail": "0.8.5\n"})

    assert result["ok"] is False
    assert result["expected"] == (
        SCRIPT.parent.parent / "VERSION"
    ).read_text(encoding="utf-8").strip()
    assert result["actual"] == "0.8.5"


def test_launch_smoke_timeout_output_is_json_serializable(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("launch_smoke_test", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def fake_run(*args, **kwargs):
        exc = subprocess.TimeoutExpired(cmd=["demo"], timeout=1)
        exc.stdout = b"partial stdout"
        exc.stderr = b"partial stderr"
        raise exc

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = module._run_step(
        step_id="timeout_demo",
        command=["demo"],
        cwd=tmp_path,
        env={},
        timeout=1,
    )

    assert result["ok"] is False
    assert result["stdout_tail"] == "partial stdout"
    assert result["stderr_tail"] == "partial stderr"
    json.dumps(result)
