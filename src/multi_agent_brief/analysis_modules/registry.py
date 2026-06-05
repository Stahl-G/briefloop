"""Analysis Module Registry — discovers and loads pluggable analysis modules."""
from __future__ import annotations

from typing import Any

from multi_agent_brief.analysis_modules.base import AnalysisModule

# Global registry: maps module name → module class.
MODULE_REGISTRY: dict[str, type[AnalysisModule]] = {}


def register_module(name: str, cls: type[AnalysisModule]) -> None:
    """Register an AnalysisModule class."""
    MODULE_REGISTRY[name] = cls


def load_enabled_modules(config: dict[str, Any] | None) -> list[AnalysisModule]:
    """Instantiate analysis modules enabled in config.yaml's ``modules`` section.

    Args:
        config: Full parsed config.yaml (with optional ``modules`` key).

    Returns:
        A list of instantiated AnalysisModules, ordered by registration.
        Returns an empty list when ``modules`` is missing or empty.
    """
    if config is None:
        return []

    modules_config = config.get("modules")
    if not modules_config:
        return []

    if not isinstance(modules_config, dict):
        return []

    enabled_modules: list[AnalysisModule] = []
    for module_name, cls in MODULE_REGISTRY.items():
        module_cfg = modules_config.get(module_name, {})
        if not isinstance(module_cfg, dict):
            continue
        if not module_cfg.get("enabled", False):
            continue

        instance = cls()
        instance.name = getattr(cls, "name", "") or cls.__name__
        enabled_modules.append(instance)

    return enabled_modules


# ── Auto-registration ───────────────────────────────────────────────────────

def _auto_register() -> None:
    """Register built-in analysis modules."""
    try:
        from multi_agent_brief.analysis_modules.market_competitor import (
            MarketCompetitorModule,
        )
        register_module("market_competitor", MarketCompetitorModule)
    except ImportError:
        pass


_auto_register()
