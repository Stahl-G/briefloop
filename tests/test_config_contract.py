"""Tests for config contract alignment between init wizard and runtime."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from multi_agent_brief.core.config import build_run_settings


class TestSelectorMaxItems:
    """selector.max_items must be read by build_run_settings."""

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

    def test_init_config_includes_output_filename_template(self, tmp_path):
        """Generated configs should enable human-readable named output files."""
        sys.path.insert(0, str(ROOT / "src"))
        from multi_agent_brief.cli.init_wizard import build_config, InitProfile

        profile = InitProfile(
            company="ExampleCo",
            brief_title="ExampleCo 光储周报",
        )
        config = build_config(profile)

        assert config["output"]["filename_template"] == "{project_name}_{report_date}"
        assert config["output"]["named_outputs"] is True
