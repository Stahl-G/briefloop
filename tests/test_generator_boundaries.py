from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GENERATOR = ROOT / "scripts" / "generate_agent_configs.py"


def test_generator_rejects_prompt_contract_targets():
    for target in ["skills", "agents_md"]:
        result = subprocess.run(
            [sys.executable, str(GENERATOR), "--target", target, "--check"],
            cwd=str(ROOT), capture_output=True, text=True,
        )
        assert result.returncode != 0
        assert "invalid choice" in result.stderr


def test_generator_help_states_operating_contract_boundary():
    result = subprocess.run(
        [sys.executable, str(GENERATOR), "--help"],
        cwd=str(ROOT), capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "hand-maintained operating contracts" in result.stdout
    assert "agents_md" not in result.stdout
    assert "{codex,docs}" in result.stdout
