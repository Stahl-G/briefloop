"""Guard the single-citation-parser invariant.

The scanner itself lives in `scripts/check_citation_parser_home.py`. It was
extracted from the retired v1.0 RC readiness gate in the LD2-3 follow-up
(ruling §20.1): the gate drove the deleted legacy runtime-state stack, this
guard never did, and it had no coverage outside that gate.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_citation_parser_home.py"


@pytest.fixture(scope="module")
def guard():
    name = "check_citation_parser_home"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # `@dataclass` resolves `sys.modules[cls.__module__]`, so the module has to
    # be registered before it is executed.
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        del sys.modules[name]
        raise
    yield module
    del sys.modules[name]


def test_internal_citation_parsing_lives_only_in_the_canonical_home(guard) -> None:
    violations = guard.find_citation_parser_violations(
        ROOT / "src", guard.CANONICAL_CITATION_MODULES, rel_to=ROOT
    )

    assert violations == [], [
        f"{item.path}:{item.lineno} {item.name} [{item.signal}]" for item in violations
    ]


def test_scanner_still_detects_marker_parsing(guard) -> None:
    """Without the allowlist the canonical home must trip its own scanner.

    A guard that reports zero violations because it scanned nothing looks
    identical to a guard that passes. This pins the scanner as live.
    """

    violations = guard.find_citation_parser_violations(ROOT / "src", set(), rel_to=ROOT)

    assert violations, "scanner found nothing even with an empty allowlist"
    assert {item.path for item in violations} == set(guard.CANONICAL_CITATION_MODULES)


def test_guard_script_runs_clean_in_strict_mode() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--require-satisfied"],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "[SATISFIED]" in result.stdout


def test_guard_carries_no_legacy_stack_dependency() -> None:
    text = SCRIPT.read_text(encoding="utf-8")

    # The scanner was extracted precisely because it never depended on the
    # stack LD2-3 deleted; a reintroduced import would make it non-runnable
    # exactly the way its former host became non-runnable.
    assert "run_v1_rc_safety_smoke" not in text
    assert "orchestrator.runtime_state" not in text
