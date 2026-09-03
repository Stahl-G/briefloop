from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_v1_pilot_evidence.py"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        cwd=str(SCRIPT.parent.parent),
    )


def test_repo_v1_pilot_evidence_doc_passes_as_advisory() -> None:
    result = _run("--json")
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["status"] == "not_satisfied"
    assert payload["satisfied"] is False
    assert any(check["status"] == "warn" for check in payload["checks"])


def test_require_satisfied_fails_until_evidence_is_recorded() -> None:
    result = _run("--require-satisfied", "--json")
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["status"] == "not_satisfied"
    assert any(check["id"] == "v1_pilot_evidence.require_satisfied" for check in payload["checks"])
