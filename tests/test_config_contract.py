"""Tests for config contract alignment between init wizard and runtime."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from multi_agent_brief.core.config import build_run_settings

EXPECTED_PIPELINE_STEPS = [
    "source_collection",
    "scout",
    "screener",
    "analyst",
    "auditor",
    "editor",
    "formatter",
]


class TestSelectorMaxItems:
    """selector.max_items must be read by build_run_settings."""

    def test_selector_max_items_from_config(self, tmp_path):
        """Config with selector.max_items should set max_claims."""
        config = {
            "selector": {"max_items": 3},
            "input": {"path": str(tmp_path / "input")},
            "output": {"path": str(tmp_path / "output")},
        }
        settings = build_run_settings(
            config=config,
            input_dir=None,
            output_dir=None,
            name=None,
            language=None,
            audience=None,
        )
        assert settings["max_claims"] == 3

    def test_selector_max_items_fallback_to_selection(self, tmp_path):
        """Backward compat: selection.max_claims still works."""
        config = {
            "selection": {"max_claims": 10},
            "input": {"path": str(tmp_path / "input")},
            "output": {"path": str(tmp_path / "output")},
        }
        settings = build_run_settings(
            config=config,
            input_dir=None,
            output_dir=None,
            name=None,
            language=None,
            audience=None,
        )
        assert settings["max_claims"] == 10

    def test_selector_max_items_default_160(self, tmp_path):
        """No selector or selection config → default 160."""
        config = {
            "input": {"path": str(tmp_path / "input")},
            "output": {"path": str(tmp_path / "output")},
        }
        settings = build_run_settings(
            config=config,
            input_dir=None,
            output_dir=None,
            name=None,
            language=None,
            audience=None,
        )
        assert settings["max_claims"] == 160

    def test_selector_takes_precedence_over_selection(self, tmp_path):
        """selector.max_items wins over selection.max_claims."""
        config = {
            "selector": {"max_items": 5},
            "selection": {"max_claims": 10},
            "input": {"path": str(tmp_path / "input")},
            "output": {"path": str(tmp_path / "output")},
        }
        settings = build_run_settings(
            config=config,
            input_dir=None,
            output_dir=None,
            name=None,
            language=None,
            audience=None,
        )
        assert settings["max_claims"] == 5


class TestPipelineSteps:
    """Init-generated pipeline steps must match the real runtime pipeline."""

    def test_init_config_pipeline_steps(self, tmp_path):
        """Generated config pipeline steps should match expected list."""
        sys.path.insert(0, str(ROOT / "src"))
        from multi_agent_brief.cli.init_wizard import build_config, InitProfile

        profile = InitProfile(
            company="TestCo",
            industry="solar",
            brief_title="Test Brief",
            audience="management",
            source_profile="conservative",
            interface_language="zh-CN",
        )
        config = build_config(profile)
        steps = config.get("pipeline", {}).get("steps", [])
        assert steps == EXPECTED_PIPELINE_STEPS, (
            f"Pipeline steps mismatch.\n"
            f"  Expected: {EXPECTED_PIPELINE_STEPS}\n"
            f"  Got:      {steps}"
        )
