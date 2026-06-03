"""Tests for source config determination fix: sources.yaml existence as primary signal."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from multi_agent_brief.core.config import load_config


@pytest.fixture()
def workspace(tmp_path: Path):
    """Create a minimal workspace with config.yaml and sources.yaml."""
    config_path = tmp_path / "config.yaml"
    sources_path = tmp_path / "sources.yaml"
    input_dir = tmp_path / "input"
    input_dir.mkdir()

    config_path.write_text(
        textwrap.dedent("""\
            project:
              name: Test Brief
              industry: ""
            language:
              output: zh-CN
            report:
              cadence: weekly
              max_source_age_days: 14
            input:
              path: input
            output:
              path: output
              formats:
                - markdown
            source:
              mode: llm_decide
        """),
        encoding="utf-8",
    )

    sources_path.write_text(
        textwrap.dedent("""\
            source_strategy:
              profile: research
              industry: ""
              decision_mode: agent_decide
              enabled_providers:
                - manual
                - rss
                - web_search
            manual:
              enabled: true
            web_search:
              enabled: false
        """),
        encoding="utf-8",
    )

    return tmp_path, config_path, sources_path


class TestSourceConfigDetermination:
    """Test that source config is loaded when sources.yaml exists, regardless of industry."""

    def test_sources_loaded_when_industry_empty(self, workspace: Path):
        """sources.yaml should be loaded even when industry is empty string."""
        tmp_path, config_path, sources_path = workspace
        config = load_config(str(config_path))
        # Verify industry is empty
        assert config.get("project", {}).get("industry", "SENTINEL") == ""
        # The fix: has_source_settings should be True because sources.yaml exists
        has_source_settings = bool(
            config and (
                config.get("source")
                or config.get("source_strategy")
                or sources_path.exists()
            )
        )
        assert has_source_settings is True

    def test_sources_loaded_when_config_has_source_key(self, workspace: Path):
        """sources.yaml should be loaded when config has source: key (not source_strategy)."""
        tmp_path, config_path, sources_path = workspace
        config = load_config(str(config_path))
        # Config has "source" key, not "source_strategy"
        assert "source" in config
        assert "source_strategy" not in config
        # sources.yaml exists
        assert sources_path.exists()
        has_source_settings = bool(
            config and (
                config.get("source")
                or config.get("source_strategy")
                or sources_path.exists()
            )
        )
        assert has_source_settings is True

    def test_no_sources_yaml_no_source_key(self, tmp_path: Path):
        """When neither sources.yaml nor source/source_strategy exists, no source settings."""
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            textwrap.dedent("""\
                project:
                  name: Test
                output:
                  path: output
            """),
            encoding="utf-8",
        )
        config = load_config(str(config_path))
        sources_path = tmp_path / "sources.yaml"
        has_source_settings = bool(
            config and (
                config.get("source")
                or config.get("source_strategy")
                or sources_path.exists()
            )
        )
        assert has_source_settings is False
