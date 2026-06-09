"""Tests for config contract alignment between init wizard and runtime."""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from multi_agent_brief.core.config import build_run_settings

EXPECTED_PIPELINE_STEPS = [
    "source_collection",
    "scout",
    "screener",
    "analyst",
    "editor",
    "auditor",
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


class TestOutputConfig:
    """Output config should use canonical output key while preserving legacy alias."""

    def test_default_config_uses_canonical_output_key(self):
        config = yaml.safe_load((ROOT / "configs" / "default.yaml").read_text(encoding="utf-8"))

        assert "output" in config
        assert "outputs" not in config

        settings = build_run_settings(
            config={
                **config,
                "input": {"path": str(ROOT / "input")},
                "output": {
                    **config["output"],
                    "path": str(ROOT / "output"),
                },
            },
            input_dir=None,
            output_dir=None,
            name=None,
            language=None,
            audience=None,
        )

        assert "source_appendix" in settings["output_formats"]

    def test_legacy_outputs_alias_is_still_supported(self, tmp_path):
        config = {
            "input": {"path": str(tmp_path / "input")},
            "outputs": {
                "path": str(tmp_path / "output"),
                "formats": ["markdown", "source_appendix"],
                "named_outputs": False,
            },
        }

        settings = build_run_settings(
            config=config,
            input_dir=None,
            output_dir=None,
            name=None,
            language=None,
            audience=None,
        )

        assert settings["output_dir"] == str(tmp_path / "output")
        assert settings["output_formats"] == ["markdown", "source_appendix"]
        assert settings["output_named_outputs"] is False


class TestPipelineSteps:
    """Init-generated pipeline steps must match the real runtime pipeline."""

    def test_init_config_pipeline_steps(self, tmp_path):
        """Generated config pipeline steps should match expected list."""
        sys.path.insert(0, str(ROOT / "src"))
        from multi_agent_brief.cli.init_wizard import build_config, InitProfile

        profile = InitProfile(
            company="TestCo",
            industry="manufacturing",
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


class TestReportDateAuto:
    """report.date == 'auto' must resolve to today's date."""

    def test_auto_date_resolves_to_today(self, tmp_path):
        config = {
            "report": {"date": "auto"},
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
        assert settings["report_date"] == date.today().isoformat()

    def test_explicit_date_preserved(self, tmp_path):
        config = {
            "report": {"date": "2026-01-15"},
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
        assert settings["report_date"] == "2026-01-15"

    def test_empty_date_stays_empty(self, tmp_path):
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
        assert settings["report_date"] == ""

    def test_init_generates_auto_date(self, tmp_path):
        """Init-generated config should have report.date = 'auto' which resolves."""
        from multi_agent_brief.cli.main import main

        ws = tmp_path / "ws"
        main([
            "init",
            str(ws),
            "--language",
            "zh-CN",
            "--company",
            "Test Company",
            "--industry",
            "manufacturing",
            "--title",
            "Weekly Brief",
            "--audience",
            "management",
            "--cadence",
            "weekly",
            "--source-profile",
            "research",
        ])
        config_text = (ws / "config.yaml").read_text(encoding="utf-8")
        assert "auto" in config_text

        import yaml
        config = yaml.safe_load(config_text)
        config["_config_dir"] = str(ws)
        settings = build_run_settings(
            config=config,
            input_dir=None,
            output_dir=None,
            name=None,
            language=None,
            audience=None,
        )
        assert settings["report_date"] == date.today().isoformat()
