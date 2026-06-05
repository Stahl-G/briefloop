"""Tests for AnalysisModule registry."""
from __future__ import annotations

import pytest

from multi_agent_brief.analysis_modules.base import AnalysisModule, ModuleOutput
from multi_agent_brief.analysis_modules.registry import (
    MODULE_REGISTRY,
    load_enabled_modules,
    register_module,
)


# ── Minimal mock module for tests ───────────────────────────────────────────

class _MockModule(AnalysisModule):
    name = "mock_module"

    def validate_config(self, config: dict) -> list[str]:
        return []

    def analyze(self, context, ledger) -> ModuleOutput:
        return ModuleOutput(module_name=self.name)


class _MockModuleWithValidation(AnalysisModule):
    name = "mock_validator"

    def validate_config(self, config: dict) -> list[str]:
        errors = []
        if config.get("required_field") is None:
            errors.append("missing required_field")
        return errors

    def analyze(self, context, ledger) -> ModuleOutput:
        return ModuleOutput(module_name=self.name)


# Ensure registry is clean before each test and register test modules
@pytest.fixture(autouse=True)
def _clear_registry():
    MODULE_REGISTRY.clear()
    register_module("mock_module", _MockModule)
    register_module("mock_validator", _MockModuleWithValidation)
    yield
    MODULE_REGISTRY.clear()


# ── Tests ───────────────────────────────────────────────────────────────────

def test_no_config_returns_empty():
    """Missing config → empty list."""
    modules = load_enabled_modules(None)
    assert modules == []


def test_no_modules_section_returns_empty():
    """Config with no 'modules' key → empty list."""
    modules = load_enabled_modules({"project": {"name": "test"}})
    assert modules == []


def test_modules_not_dict_returns_empty():
    """Malformed modules section → empty list."""
    modules = load_enabled_modules({"modules": "not_a_dict"})
    assert modules == []


def test_disabled_module_not_loaded():
    """Disabled module → not instantiated."""
    modules = load_enabled_modules({"modules": {"mock_module": {"enabled": False}}})
    assert modules == []


def test_enabled_module_loaded():
    """Enabled module → instantiated."""
    modules = load_enabled_modules({"modules": {"mock_module": {"enabled": True}}})
    assert len(modules) == 1
    assert modules[0].name == "mock_module"


def test_unknown_module_in_config_ignored():
    """Config for unregistered module → silently skipped."""
    modules = load_enabled_modules({"modules": {"nonexistent": {"enabled": True}}})
    assert modules == []
