"""Effort Budgets — deterministic runtime limits for pipeline execution.

This module provides budget levels that control pipeline resource usage
without implementing model routing or paid-provider behavior.

Budget levels:
- low: Minimal resources for quick checks
- medium: Standard resources for regular reports
- high: Extended resources for comprehensive analysis
- xhigh: Maximum resources for deep research

Each budget level expands to deterministic limits:
- max_sources: Maximum number of sources to collect
- max_search_tasks: Maximum number of search tasks
- max_claims: Maximum number of claims in ledger
- max_candidates: Maximum number of screener candidates
- max_analysis_modules: Maximum number of analysis modules to run
- source_recency_days: How far back to look for sources
- semantic_audit_mode: Semantic audit strictness
- timeout_seconds: Pipeline timeout hint
- retry_limits: Max retries for transient failures
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class EffortBudget:
    """Resolved effort budget with deterministic runtime limits."""

    level: str = "medium"

    # Source collection limits
    max_sources: int = 50
    max_search_tasks: int = 10

    # Processing limits
    max_claims: int = 100
    max_candidates: int = 50

    # Analysis limits
    max_analysis_modules: int = 3

    # Timing limits
    source_recency_days: int = 14
    timeout_seconds: int = 300
    retry_limits: int = 3

    # Audit mode
    semantic_audit_mode: str = "standard"  # standard, strict, lenient

    # Metadata
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "max_sources": self.max_sources,
            "max_search_tasks": self.max_search_tasks,
            "max_claims": self.max_claims,
            "max_candidates": self.max_candidates,
            "max_analysis_modules": self.max_analysis_modules,
            "source_recency_days": self.source_recency_days,
            "timeout_seconds": self.timeout_seconds,
            "retry_limits": self.retry_limits,
            "semantic_audit_mode": self.semantic_audit_mode,
            "metadata": self.metadata,
        }


# Budget level definitions
BUDGET_LEVELS: dict[str, dict[str, Any]] = {
    "low": {
        "max_sources": 10,
        "max_search_tasks": 3,
        "max_claims": 30,
        "max_candidates": 15,
        "max_analysis_modules": 1,
        "source_recency_days": 7,
        "timeout_seconds": 120,
        "retry_limits": 1,
        "semantic_audit_mode": "lenient",
    },
    "medium": {
        "max_sources": 50,
        "max_search_tasks": 10,
        "max_claims": 100,
        "max_candidates": 50,
        "max_analysis_modules": 3,
        "source_recency_days": 14,
        "timeout_seconds": 300,
        "retry_limits": 3,
        "semantic_audit_mode": "standard",
    },
    "high": {
        "max_sources": 100,
        "max_search_tasks": 20,
        "max_claims": 200,
        "max_candidates": 100,
        "max_analysis_modules": 5,
        "source_recency_days": 30,
        "timeout_seconds": 600,
        "retry_limits": 5,
        "semantic_audit_mode": "strict",
    },
    "xhigh": {
        "max_sources": 200,
        "max_search_tasks": 40,
        "max_claims": 500,
        "max_candidates": 200,
        "max_analysis_modules": 10,
        "source_recency_days": 60,
        "timeout_seconds": 1200,
        "retry_limits": 10,
        "semantic_audit_mode": "strict",
    },
}


def validate_budget_level(level: str) -> bool:
    """Validate that a budget level name is recognized.

    Args:
        level: Budget level name to validate.

    Returns:
        True if level is valid, False otherwise.
    """
    return level in BUDGET_LEVELS


def validate_budget_values(budget: EffortBudget) -> list[str]:
    """Validate budget values are within acceptable ranges.

    Args:
        budget: EffortBudget to validate.

    Returns:
        List of validation error messages. Empty if valid.
    """
    errors: list[str] = []

    # Check for negative values
    if budget.max_sources < 0:
        errors.append(f"max_sources must be non-negative, got {budget.max_sources}")
    if budget.max_search_tasks < 0:
        errors.append(f"max_search_tasks must be non-negative, got {budget.max_search_tasks}")
    if budget.max_claims < 0:
        errors.append(f"max_claims must be non-negative, got {budget.max_claims}")
    if budget.max_candidates < 0:
        errors.append(f"max_candidates must be non-negative, got {budget.max_candidates}")
    if budget.max_analysis_modules < 0:
        errors.append(f"max_analysis_modules must be non-negative, got {budget.max_analysis_modules}")
    if budget.source_recency_days < 0:
        errors.append(f"source_recency_days must be non-negative, got {budget.source_recency_days}")
    if budget.timeout_seconds < 0:
        errors.append(f"timeout_seconds must be non-negative, got {budget.timeout_seconds}")
    if budget.retry_limits < 0:
        errors.append(f"retry_limits must be non-negative, got {budget.retry_limits}")

    # Check semantic audit mode
    valid_modes = {"standard", "strict", "lenient"}
    if budget.semantic_audit_mode not in valid_modes:
        errors.append(f"semantic_audit_mode must be one of {valid_modes}, got {budget.semantic_audit_mode}")

    return errors


def resolve_budget(
    level: str = "medium",
    overrides: dict[str, Any] | None = None,
) -> EffortBudget:
    """Resolve an effort budget from level name and optional overrides.

    Args:
        level: Budget level name (low, medium, high, xhigh).
        overrides: Optional dictionary of override values.

    Returns:
        Resolved EffortBudget with validated values.

    Raises:
        ValueError: If level is invalid or values fail validation.
    """
    if not validate_budget_level(level):
        raise ValueError(
            f"Invalid budget level: '{level}'. "
            f"Must be one of: {', '.join(BUDGET_LEVELS.keys())}"
        )

    # Start with level defaults
    level_config = BUDGET_LEVELS[level]

    # Apply overrides
    config = dict(level_config)
    if overrides:
        config.update(overrides)

    # Create budget
    budget = EffortBudget(
        level=level,
        max_sources=config.get("max_sources", level_config["max_sources"]),
        max_search_tasks=config.get("max_search_tasks", level_config["max_search_tasks"]),
        max_claims=config.get("max_claims", level_config["max_claims"]),
        max_candidates=config.get("max_candidates", level_config["max_candidates"]),
        max_analysis_modules=config.get("max_analysis_modules", level_config["max_analysis_modules"]),
        source_recency_days=config.get("source_recency_days", level_config["source_recency_days"]),
        timeout_seconds=config.get("timeout_seconds", level_config["timeout_seconds"]),
        retry_limits=config.get("retry_limits", level_config["retry_limits"]),
        semantic_audit_mode=config.get("semantic_audit_mode", level_config["semantic_audit_mode"]),
    )

    # Validate
    errors = validate_budget_values(budget)
    if errors:
        raise ValueError(f"Invalid budget values: {'; '.join(errors)}")

    return budget


def get_budget_from_config(config: dict[str, Any]) -> EffortBudget:
    """Extract and resolve effort budget from pipeline config.

    Args:
        config: Pipeline configuration dictionary.

    Returns:
        Resolved EffortBudget.
    """
    effort_config = config.get("effort", {})
    if not isinstance(effort_config, dict):
        effort_config = {}

    level = effort_config.get("level", "medium")
    if not isinstance(level, str):
        level = "medium"

    # Extract overrides
    overrides = {}
    for key in [
        "max_sources", "max_search_tasks", "max_claims", "max_candidates",
        "max_analysis_modules", "source_recency_days", "timeout_seconds",
        "retry_limits", "semantic_audit_mode",
    ]:
        if key in effort_config:
            overrides[key] = effort_config[key]

    try:
        return resolve_budget(level, overrides if overrides else None)
    except ValueError:
        # Fall back to medium budget if config is invalid
        return resolve_budget("medium")
