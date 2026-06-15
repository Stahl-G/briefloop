"""Structural guardrails for fail-closed runtime safety surfaces."""

from __future__ import annotations

import ast
import importlib
import inspect
from pathlib import Path
from types import ModuleType

from multi_agent_brief.orchestrator.runtime_state.safety_surfaces import SAFETY_SURFACES


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src" / "multi_agent_brief"
RUNTIME_STATE_FACADE = "multi_agent_brief.orchestrator.runtime_state"


def test_safety_surface_registry_is_complete():
    assert set(SAFETY_SURFACES) == {
        "run_integrity",
        "quality_gate_binding",
        "finalize_audit_binding",
        "frozen_artifact_integrity",
        "stage_completion",
    }
    assert all(surface.surface_id == surface_id for surface_id, surface in SAFETY_SURFACES.items())


def test_each_safety_surface_has_exactly_one_public_interpreter_and_adapters():
    for surface in SAFETY_SURFACES.values():
        module = importlib.import_module(surface.module)

        verdict_class = getattr(module, surface.verdict_class)
        interpreter = getattr(module, surface.interpreter)
        read_adapter = getattr(module, surface.read_adapter)
        write_adapter = getattr(module, surface.write_adapter)

        assert inspect.isclass(verdict_class), surface.surface_id
        assert callable(interpreter), surface.surface_id
        assert callable(read_adapter), surface.surface_id
        assert callable(write_adapter), surface.surface_id

        public_interpreters = _public_functions_with_prefix(module, "interpret_")
        assert public_interpreters == [surface.interpreter], surface.surface_id
        public_read_adapters = _public_read_adapter_functions(module)
        assert public_read_adapters == [surface.read_adapter], surface.surface_id
        public_write_adapters = _public_functions_with_prefix(module, "require_")
        assert public_write_adapters == [surface.write_adapter], surface.surface_id


def test_safety_surface_registry_stays_out_of_runtime_state_facade():
    runtime_state = importlib.import_module(RUNTIME_STATE_FACADE)

    assert "safety_surfaces" not in runtime_state.__all__
    assert "SAFETY_SURFACES" not in runtime_state.__all__
    assert not hasattr(runtime_state, "SAFETY_SURFACES")


def test_optimistic_constructors_are_not_used_outside_owner_module():
    owners = {
        constructor: _module_file(surface.module)
        for surface in SAFETY_SURFACES.values()
        for constructor in surface.optimistic_constructors
    }
    assert owners

    violations: list[str] = []
    for path in _source_python_files():
        if path.name == "safety_surfaces.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names = _referenced_safety_names(node, set(owners))
            for name, lineno in names:
                if path.resolve() != owners[name]:
                    rel_path = path.relative_to(REPO_ROOT).as_posix()
                    violations.append(f"{rel_path}:{lineno}:{name}")

    assert violations == []


def test_removed_pre_b4_safety_helpers_do_not_reappear():
    forbidden_names = {
        "_frozen_artifact_integrity_reasons",
        "normalize_run_integrity",
        "classify_run_integrity",
    }
    violations: list[str] = []
    for path in _source_python_files():
        if path.name == "safety_surfaces.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names = _referenced_safety_names(node, forbidden_names)
            for name, lineno in names:
                rel_path = path.relative_to(REPO_ROOT).as_posix()
                violations.append(f"{rel_path}:{lineno}:{name}")

    assert violations == []


def test_runtime_modules_reference_registered_write_paths():
    for surface in SAFETY_SURFACES.values():
        for module_name, required_ref in surface.required_runtime_refs:
            module = importlib.import_module(module_name)
            source = Path(module.__file__).read_text(encoding="utf-8")
            assert required_ref in source, f"{surface.surface_id}: {module_name} missing {required_ref}"


def test_binding_gates_do_not_reimplement_status_checks_in_completion_gates():
    module = importlib.import_module("multi_agent_brief.orchestrator.runtime_state.completion_gates")
    source = Path(module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(module.__file__))

    assert "interpret_quality_gate_binding" in source
    assert "require_quality_gate_binding_pass" in source
    assert "interpret_finalize_audit_binding" in source
    assert "require_finalize_audit_binding_pass" in source

    forbidden_status_checks: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        if _compare_mentions(node, "audit_binding", "pass"):
            forbidden_status_checks.append(f"audit_binding pass check at line {node.lineno}")
        if _compare_mentions(node, "gate_results", "fail"):
            forbidden_status_checks.append(f"gate_results fail check at line {node.lineno}")

    assert forbidden_status_checks == []


def test_stage_status_read_helpers_use_stage_completion_projection():
    module = importlib.import_module("multi_agent_brief.orchestrator.runtime_state.workflow")
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"), filename=str(module.__file__))
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in {"_stage_status", "_stage_is_complete_or_skipped"}
    }

    assert set(functions) == {"_stage_status", "_stage_is_complete_or_skipped"}
    for function in functions.values():
        calls = {
            node.func.id
            for node in ast.walk(function)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert "interpret_stage_completion" in calls
        assert "project_stage_completion_for_read" in calls


def _public_functions_with_prefix(module: ModuleType, prefix: str) -> list[str]:
    names = [
        name
        for name, value in vars(module).items()
        if name.startswith(prefix) and inspect.isfunction(value) and value.__module__ == module.__name__
    ]
    return sorted(names)


def _source_python_files() -> list[Path]:
    return sorted(SRC_ROOT.rglob("*.py"))


def _module_file(module_name: str) -> Path:
    module = importlib.import_module(module_name)
    return Path(module.__file__).resolve()


def _referenced_safety_names(node: ast.AST, names: set[str]) -> list[tuple[str, int]]:
    references: list[tuple[str, int]] = []
    if isinstance(node, ast.Name) and node.id in names:
        references.append((node.id, node.lineno))
    elif isinstance(node, ast.Attribute) and node.attr in names:
        references.append((node.attr, node.lineno))
    elif isinstance(node, ast.ImportFrom):
        for alias in node.names:
            if alias.name in names:
                references.append((alias.name, node.lineno))
    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name in names:
        references.append((node.name, node.lineno))
    return references


def _compare_mentions(node: ast.Compare, name: str, literal: str) -> bool:
    text_tokens = {
        item.id
        for item in ast.walk(node)
        if isinstance(item, ast.Name)
    }
    text_tokens.update({
        item.value
        for item in ast.walk(node)
        if isinstance(item, ast.Constant) and isinstance(item.value, str)
    })
    return name in text_tokens and literal in text_tokens


def _public_read_adapter_functions(module: ModuleType) -> list[str]:
    names = [
        name
        for name, value in vars(module).items()
        if (name.startswith("project_") or name == "project_for_read")
        and inspect.isfunction(value)
        and value.__module__ == module.__name__
    ]
    return sorted(names)
