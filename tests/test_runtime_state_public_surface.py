"""Runtime state facade public-surface guardrails."""

from __future__ import annotations

import ast
import importlib
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_STATE_MODULE = "multi_agent_brief.orchestrator.runtime_state"
PINNED_EXTRA_EXPORTS = {"new_run_id"}


def test_runtime_state_all_matches_in_repo_from_imports():
    runtime_state = importlib.import_module(RUNTIME_STATE_MODULE)
    expected = _runtime_state_from_imports() | PINNED_EXTRA_EXPORTS

    assert set(runtime_state.__all__) == expected
    assert runtime_state.__all__ == sorted(runtime_state.__all__)
    assert "__getattr__" not in vars(runtime_state)


def test_runtime_state_facade_does_not_proxy_impl_internals():
    runtime_state = importlib.import_module(RUNTIME_STATE_MODULE)

    assert not hasattr(runtime_state, "_impl")
    assert hasattr(runtime_state, "operations")
    assert "operations" not in runtime_state.__all__
    assert not hasattr(runtime_state, "_append_jsonl")
    assert not hasattr(runtime_state, "_sha256_file")
    assert not hasattr(runtime_state, "_allowed_decisions_for_stage")
    assert hasattr(runtime_state, "new_run_id")
    assert not hasattr(runtime_state, "EVENT_TYPES")
    assert not hasattr(runtime_state, "E_TRANSACTION_INTEGRITY")


def test_core_manifest_has_no_live_consumers():
    consumers: list[str] = []
    for path in _python_files():
        if path.name == "test_runtime_state_public_surface.py":
            continue
        text = path.read_text(encoding="utf-8")
        if "multi_agent_brief.core.manifest" in text:
            consumers.append(str(path.relative_to(REPO_ROOT)))

    assert consumers == []


def _runtime_state_from_imports() -> set[str]:
    names: set[str] = set()
    for path in _python_files():
        if path.name == "test_runtime_state_public_surface.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == RUNTIME_STATE_MODULE:
                names.update(alias.name for alias in node.names)
    return names


def _python_files() -> list[Path]:
    return sorted([*REPO_ROOT.joinpath("src").rglob("*.py"), *REPO_ROOT.joinpath("tests").rglob("*.py")])
