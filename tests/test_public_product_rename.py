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


def test_active_runtime_primary_cli_surface_is_ratchet_locked() -> None:
    module = _load_module()

    expected = {
        ".agents/skills/brief-onboarding/SKILL.md",
    }

    assert expected <= set(module.TARGET_FILES)
