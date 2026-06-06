"""Tests for Effort Budgets."""

from __future__ import annotations

import pytest

from multi_agent_brief.core.effort_budgets import (
    BUDGET_LEVELS,
    EffortBudget,
    get_budget_from_config,
    resolve_budget,
    validate_budget_level,
    validate_budget_values,
)


class TestBudgetLevels:
    """Test budget level definitions."""

    def test_all_levels_defined(self):
        """All budget levels are defined."""
        assert "low" in BUDGET_LEVELS
        assert "medium" in BUDGET_LEVELS
        assert "high" in BUDGET_LEVELS
        assert "xhigh" in BUDGET_LEVELS

    def test_level_values_are_positive(self):
        """All level values are positive."""
        for level_name, level_config in BUDGET_LEVELS.items():
            for key, value in level_config.items():
                if key != "semantic_audit_mode":
                    assert value >= 0, f"{level_name}.{key} must be non-negative"

    def test_levels_increase_monotonically(self):
        """Higher levels have higher or equal limits."""
        numeric_keys = [
            "max_sources", "max_search_tasks", "max_claims", "max_candidates",
            "max_analysis_modules", "source_recency_days", "timeout_seconds", "retry_limits",
        ]
        for key in numeric_keys:
            low = BUDGET_LEVELS["low"][key]
            medium = BUDGET_LEVELS["medium"][key]
            high = BUDGET_LEVELS["high"][key]
            xhigh = BUDGET_LEVELS["xhigh"][key]
            assert low <= medium <= high <= xhigh, f"{key} not monotonically increasing"


class TestValidateBudgetLevel:
    """Test validate_budget_level function."""

    def test_valid_levels(self):
        """Valid levels pass validation."""
        assert validate_budget_level("low") is True
        assert validate_budget_level("medium") is True
        assert validate_budget_level("high") is True
        assert validate_budget_level("xhigh") is True

    def test_invalid_level(self):
        """Invalid levels fail validation."""
        assert validate_budget_level("invalid") is False
        assert validate_budget_level("") is False
        assert validate_budget_level("LOW") is False


class TestValidateBudgetValues:
    """Test validate_budget_values function."""

    def test_valid_budget(self):
        """Valid budget passes validation."""
        budget = EffortBudget()
        errors = validate_budget_values(budget)
        assert errors == []

    def test_negative_max_sources(self):
        """Negative max_sources fails validation."""
        budget = EffortBudget(max_sources=-1)
        errors = validate_budget_values(budget)
        assert len(errors) == 1
        assert "max_sources" in errors[0]

    def test_negative_max_claims(self):
        """Negative max_claims fails validation."""
        budget = EffortBudget(max_claims=-1)
        errors = validate_budget_values(budget)
        assert len(errors) == 1
        assert "max_claims" in errors[0]

    def test_invalid_semantic_audit_mode(self):
        """Invalid semantic_audit_mode fails validation."""
        budget = EffortBudget(semantic_audit_mode="invalid")
        errors = validate_budget_values(budget)
        assert len(errors) == 1
        assert "semantic_audit_mode" in errors[0]

    def test_multiple_errors(self):
        """Multiple validation errors are collected."""
        budget = EffortBudget(max_sources=-1, max_claims=-1)
        errors = validate_budget_values(budget)
        assert len(errors) == 2


class TestResolveBudget:
    """Test resolve_budget function."""

    def test_resolve_low(self):
        """Resolve low budget."""
        budget = resolve_budget("low")
        assert budget.level == "low"
        assert budget.max_sources == BUDGET_LEVELS["low"]["max_sources"]

    def test_resolve_medium(self):
        """Resolve medium budget."""
        budget = resolve_budget("medium")
        assert budget.level == "medium"
        assert budget.max_sources == BUDGET_LEVELS["medium"]["max_sources"]

    def test_resolve_high(self):
        """Resolve high budget."""
        budget = resolve_budget("high")
        assert budget.level == "high"
        assert budget.max_sources == BUDGET_LEVELS["high"]["max_sources"]

    def test_resolve_xhigh(self):
        """Resolve xhigh budget."""
        budget = resolve_budget("xhigh")
        assert budget.level == "xhigh"
        assert budget.max_sources == BUDGET_LEVELS["xhigh"]["max_sources"]

    def test_resolve_with_overrides(self):
        """Resolve budget with overrides."""
        budget = resolve_budget("medium", {"max_sources": 75})
        assert budget.level == "medium"
        assert budget.max_sources == 75

    def test_resolve_invalid_level(self):
        """Invalid level raises ValueError."""
        with pytest.raises(ValueError, match="Invalid budget level"):
            resolve_budget("invalid")

    def test_resolve_invalid_values(self):
        """Invalid values raise ValueError."""
        with pytest.raises(ValueError, match="Invalid budget values"):
            resolve_budget("medium", {"max_sources": -1})


class TestGetBudgetFromConfig:
    """Test get_budget_from_config function."""

    def test_default_config(self):
        """Default config returns medium budget."""
        budget = get_budget_from_config({})
        assert budget.level == "medium"

    def test_explicit_level(self):
        """Explicit level is used."""
        budget = get_budget_from_config({"effort": {"level": "high"}})
        assert budget.level == "high"

    def test_with_overrides(self):
        """Overrides are applied."""
        budget = get_budget_from_config({
            "effort": {
                "level": "medium",
                "max_sources": 75,
            }
        })
        assert budget.level == "medium"
        assert budget.max_sources == 75

    def test_invalid_level_falls_back(self):
        """Invalid level falls back to medium."""
        budget = get_budget_from_config({"effort": {"level": "invalid"}})
        assert budget.level == "medium"

    def test_invalid_values_fall_back(self):
        """Invalid values fall back to medium."""
        budget = get_budget_from_config({
            "effort": {
                "level": "medium",
                "max_sources": -1,
            }
        })
        assert budget.level == "medium"


class TestEffortBudget:
    """Test EffortBudget dataclass."""

    def test_to_dict(self):
        """to_dict returns correct structure."""
        budget = EffortBudget(level="high", max_sources=100)
        d = budget.to_dict()
        assert d["level"] == "high"
        assert d["max_sources"] == 100
        assert "max_search_tasks" in d
        assert "semantic_audit_mode" in d

    def test_default_values(self):
        """Default values are set."""
        budget = EffortBudget()
        assert budget.level == "medium"
        assert budget.max_sources == 50
        assert budget.semantic_audit_mode == "standard"
