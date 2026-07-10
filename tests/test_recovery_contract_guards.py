from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "multi_agent_brief"


def test_contaminated_repaired_is_legacy_read_only() -> None:
    token = "RUN_INTEGRITY_CONTAMINATED_REPAIRED"
    allowed = {
        "orchestrator/run_integrity.py",
        "orchestrator/recovery_state.py",
    }
    users = {
        path.relative_to(SRC).as_posix()
        for path in SRC.rglob("*.py")
        if token in path.read_text(encoding="utf-8")
    }

    assert users == allowed
    run_integrity = (SRC / "orchestrator" / "run_integrity.py").read_text(
        encoding="utf-8"
    )
    assert "def finalize_run_integrity" not in run_integrity
    assert '"status": RUN_INTEGRITY_CONTAMINATED_REPAIRED' not in run_integrity


def test_recovery_consumers_do_not_replay_event_log_state() -> None:
    consumers = (
        SRC / "orchestrator" / "runtime_state" / "completion_projection.py",
        SRC / "cli" / "deliver_commands.py",
        SRC / "status.py",
        SRC / "workbuddy" / "diagnose.py",
    )

    for path in consumers:
        text = path.read_text(encoding="utf-8")
        assert "workflow_with_sticky_contamination_events" not in text, path
        assert "interpret_recovery_state" not in text, path


def test_recovery_state_does_not_import_gate_or_route_authority() -> None:
    path = SRC / "orchestrator" / "recovery_state.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported_modules = {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    imported_modules.update(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )

    assert not any("quality_gates" in module for module in imported_modules)
    assert not any(module.endswith("repair.router") for module in imported_modules)
    assert "route_repair" not in path.read_text(encoding="utf-8")
