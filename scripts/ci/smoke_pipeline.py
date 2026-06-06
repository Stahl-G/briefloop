"""CI smoke test: assert the removed Python pipeline stays removed."""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "src"
if _SRC.exists() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from multi_agent_brief.cli.main import main


def main_smoke() -> None:
    config_path = sys.argv[1] if len(sys.argv) > 1 else "examples/reference_workflow_demo/config.yaml"
    exit_code = main(["prepare", "--config", config_path])
    if exit_code == 0:
        raise SystemExit("prepare unexpectedly succeeded; Python brief pipeline must stay removed")
    print("SMOKE PASSED: prepare is deprecated and does not run the workflow")


if __name__ == "__main__":
    main_smoke()
