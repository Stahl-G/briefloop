"""Tests for the v0.11.4 minimal comparative evaluation packet guard."""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "check_minimal_comparative_eval.py"
EVAL_ROOT = ROOT / "docs" / "evaluation-results" / "v0.11.4-minimal-comparative-evaluation"


def _load_module():
    spec = importlib.util.spec_from_file_location("check_minimal_comparative_eval_test", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


def test_minimal_comparative_eval_json_contract() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)

    assert payload["ok"] is True
    assert payload["evaluation_id"] == "v0.11.4-minimal-comparative-evaluation"
    assert payload["task_count"] == 3
    assert payload["arm_count"] == 2
    assert payload["observation_count"] >= 8
    assert payload["runtime_effect"] == "readiness_check_only"
    assert "output_quality_proof" in payload["non_goals"]
    assert "delivery_or_release_approval" in payload["non_goals"]


def test_minimal_comparative_eval_rejects_hash_drift(tmp_path) -> None:
    module = _load_module()
    copied = tmp_path / "eval"
    shutil.copytree(EVAL_ROOT, copied)
    raw_output = copied / "raw_outputs" / "task1_C0_direct_prompt.md"
    raw_output.write_text(raw_output.read_text(encoding="utf-8") + "\nHand edit.\n", encoding="utf-8")

    payload = module.check_minimal_comparative_eval(copied)

    assert payload["ok"] is False
    assert any("sha256 mismatch" in error for error in payload["errors"])


def test_minimal_comparative_eval_rejects_authority_keys(tmp_path) -> None:
    module = _load_module()
    copied = tmp_path / "eval"
    shutil.copytree(EVAL_ROOT, copied)
    observations_path = copied / "raw_observations.json"
    payload = json.loads(observations_path.read_text(encoding="utf-8"))
    payload["winner"] = "C1_briefloop_workflow"
    observations_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    result = module.check_minimal_comparative_eval(copied)

    assert result["ok"] is False
    assert any("authority-looking keys" in error for error in result["errors"])


def test_minimal_comparative_eval_requires_second_reviewer_subset(tmp_path) -> None:
    module = _load_module()
    copied = tmp_path / "eval"
    shutil.copytree(EVAL_ROOT, copied)
    observations_path = copied / "raw_observations.json"
    payload = json.loads(observations_path.read_text(encoding="utf-8"))
    payload["observations"] = [
        observation
        for observation in payload["observations"]
        if observation.get("reviewer_id") != "R2"
    ]
    observations_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    result = module.check_minimal_comparative_eval(copied)

    assert result["ok"] is False
    assert any("distinct reviewers" in error for error in result["errors"])
