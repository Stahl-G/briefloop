from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_briefloop_skill_contract_and_runtime_asset_parity_run_clean() -> None:
    for relative in (
        "scripts/check_skill_contract.py",
        "scripts/check_runtime_asset_parity.py",
    ):
        result = subprocess.run(
            [sys.executable, str(ROOT / relative)],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr
