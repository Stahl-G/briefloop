"""CI smoke test for the subagent-first reference workspace.

This script intentionally does not generate a brief. MABW no longer has a
Python brief-generation pipeline; `/generate-brief <workspace>` is the runtime.
The smoke checks that the example workspace is present, doctor passes, and the
removed `prepare` command cannot silently run.
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "src"
if _SRC.exists() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from multi_agent_brief.cli.main import main


def smoke_reference_workspace(workspace: Path) -> None:
    required = ["config.yaml", "sources.yaml", "user.md", "input"]
    missing = [name for name in required if not (workspace / name).exists()]
    if missing:
        raise SystemExit(f"reference workspace missing: {', '.join(missing)}")

    doctor_code = main(["doctor", "--config", str(workspace / "config.yaml")])
    if doctor_code != 0:
        raise SystemExit("doctor failed for reference workspace")

    prepare_code = main(["prepare", "--config", str(workspace / "config.yaml")])
    if prepare_code == 0:
        raise SystemExit("prepare unexpectedly succeeded; Python brief pipeline must stay removed")

    print("SMOKE PASSED: reference workspace is valid and prepare is deprecated")


if __name__ == "__main__":
    ws = Path(sys.argv[1]) if len(sys.argv) > 1 else _ROOT / "examples" / "reference_workflow_demo"
    smoke_reference_workspace(ws)
