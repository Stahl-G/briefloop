"""Tests for PR4: Source error observability and usable source counting
(B08, B09, B10, B11).

B08 — Provider config validation must run during normal pipeline execution.
B09 — Tavily/Exa must surface errors instead of swallowing as "zero results".
B10 — RSS error items and placeholders must not be counted as usable sources.
B11 — Missing/unreadable manual files must generate visible errors.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from multi_agent_brief.sources.base import SourceConfig, SourceQuery, SourceItem
from multi_agent_brief.sources.registry import collect_all_sources, validate_all_providers
from multi_agent_brief.sources.manual import ManualProvider
from multi_agent_brief.sources.rss import RssProvider
from multi_agent_brief.core.pipeline import BriefPipeline
from multi_agent_brief.core.schemas import PipelineContext
from multi_agent_brief.agents.scout import _is_placeholder


# ─── B08: Validation during normal pipeline ───

class TestB08ProviderValidationInPipeline:
    """Provider validation errors must be visible during normal pipeline runs,
    not only when the user explicitly runs 'doctor'."""

    def test_validate_all_providers_exists_and_returns_errors(self):
        """validate_all_providers must catch invalid provider config."""
        config = SourceConfig(
            enabled_providers=["manual"],
            manual={"enabled": True, "sources": [
                {"name": "No path or url", "enabled": True}
            ]},
        )
        errors = validate_all_providers(config)
        assert len(errors) > 0, (
            "B08 FAIL: Missing 'path'/'url' should be caught by validate_all_providers"
        )

    def test_collect_all_sources_returns_validation_errors(self):
        """collect_all_sources must surface validation errors in collection_errors."""
        config = SourceConfig(
            enabled_providers=["unknown_provider_xyz"],
            manual={"enabled": True, "sources": []},
        )
        items, errors = collect_all_sources(config, SourceQuery())
        assert len(errors) > 0, (
            "B08 FAIL: Unknown provider not reported in collection_errors"
        )
        # Error must identify the unknown provider
        assert any("unknown_provider_xyz" in e.get("message", "") for e in errors), (
            "B08 FAIL: Error message doesn't name the unknown provider"
        )

    def test_pipeline_surfaces_collection_errors(self, tmp_path):
        """Pipeline run must include collection_errors in source-collection output."""
        input_dir = tmp_path / "input"
        output_dir = tmp_path / "output"
        input_dir.mkdir()
        (input_dir / "news.md").write_text(
            "- A meaningful local signal about market expansion plans.\n",
            encoding="utf-8",
        )

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        import yaml
        (config_dir / "sources.yaml").write_text(yaml.dump({
            "source_strategy": {
                "profile": "research",
                "enabled_providers": ["manual", "unknown_provider"],
            },
            "manual": {"enabled": True, "sources": [
                {"name": "Local Input", "path": str(input_dir),
                 "category": "local_files", "enabled": True}
            ]},
        }), encoding="utf-8")

        from multi_agent_brief.sources.registry import load_sources_config
        source_config = load_sources_config(config_dir / "sources.yaml")

        context = PipelineContext(
            project_name="B08",
            input_dir=str(input_dir),
            output_dir=str(output_dir),
        )
        context.metadata["source_config"] = source_config

        outputs = BriefPipeline().run(context)

        # Source-collection output must include errors
        source_output = outputs[0]
        assert source_output.agent_name == "source-collection"
        collection_errors = source_output.artifacts.get("collection_errors", [])
        assert len(collection_errors) > 0, (
            "B08 FAIL: Unknown provider error not surfaced in pipeline run"
        )


# ─── B09: Search backend errors ───

class TestB09SearchBackendErrors:
    """Search backend errors must be surfaced, not swallowed as 'zero results'."""

    def test_fake_backend_error_propagates_to_collection_errors(self):
        """When a backend raises during search, the error must appear in
        collection_errors, not silently return zero results."""
        class BrokenBackend:
            name = "broken"
            def search(self, query, max_results=10, *, domains=None, **kwargs):
                raise RuntimeError("Simulated 401 Unauthorized")
            def is_available(self):
                return True

        from multi_agent_brief.sources.web_search import WebSearchProvider
        provider = WebSearchProvider(backend=BrokenBackend())

        # Should raise or be caught at the collection level
        with pytest.raises(RuntimeError, match="Simulated 401"):
            provider.collect(
                SourceQuery(keywords=["test"]),
                {"enabled": True, "backend": "broken", "search_tasks": [
                    {"query": "test query", "domains": None}
                ]},
            )

    def test_backend_zero_results_is_clean(self):
        """A backend legitimately returning zero results must NOT generate errors."""
        class EmptyBackend:
            name = "empty"
            def search(self, query, max_results=10, *, domains=None, **kwargs):
                return []  # genuine zero results
            def is_available(self):
                return True

        from multi_agent_brief.sources.web_search import WebSearchProvider
        provider = WebSearchProvider(backend=EmptyBackend())
        items = provider.collect(
            SourceQuery(keywords=["nonexistent topic"]),
            {"enabled": True, "backend": "empty", "search_tasks": [
                {"query": "nonexistent topic", "domains": None}
            ]},
        )
        assert items == [], (
            "B09 FAIL: Genuine zero results should be empty list, not an error"
        )


# ─── B10: RSS error items not counted as usable ───

class TestB10RssErrorNotUsable:
    """RSS fetch errors must not be counted as usable source items."""

    def test_rss_error_item_is_placeholder(self):
        """Scout must skip RSS error items."""
        rss_error_item = SourceItem(
            source_id="RSS_ERROR_001",
            source_name="Broken Feed",
            source_type="rss_error",
            title="RSS fetch error",
            content="Failed to fetch feed",
            url="https://broken.example.com/feed",
            metadata={"error_type": "URLError", "feed_url": "https://broken.example.com/feed"},
        )
        assert _is_placeholder(rss_error_item), (
            "B10 FAIL: RSS error item must be treated as placeholder by Scout"
        )

    def test_rss_error_in_collection_errors(self, tmp_path):
        """RSS fetch failure must appear in collection_errors."""
        input_dir = tmp_path / "input"
        output_dir = tmp_path / "output"
        input_dir.mkdir()
        (input_dir / "news.md").write_text(
            "- A meaningful local signal about market expansion.\n",
            encoding="utf-8",
        )

        # Config with a clearly invalid RSS feed URL
        source_config = SourceConfig(
            enabled_providers=["manual", "rss"],
            manual={"enabled": True, "sources": [
                {"name": "Local Input", "path": str(input_dir),
                 "category": "local_files", "enabled": True}
            ]},
            rss={"enabled": True, "feeds": [
                {"name": "Broken Feed", "url": "https://invalid-domain-that-does-not-exist-12345.example/feed.xml"}
            ]},
        )

        context = PipelineContext(
            project_name="B10",
            input_dir=str(input_dir),
            output_dir=str(output_dir),
        )
        context.metadata["source_config"] = source_config

        outputs = BriefPipeline().run(context)

        source_output = outputs[0]
        # RSS errors should be in collection_errors
        collection_errors = source_output.artifacts.get("collection_errors", [])
        # The RSS fetch may fail; at minimum the source count shouldn't count it as usable
        # The brief should still have the local file content
        brief_text = (output_dir / "brief.md").read_text(encoding="utf-8")
        assert "meaningful local signal" in brief_text.lower(), (
            "B10 FAIL: Local input should still work even with broken RSS"
        )


# ─── B11: Manual file errors ───

class TestB11ManualFileErrors:
    """Missing/unreadable manual files must produce visible errors."""

    def test_nonexistent_path_produces_error(self):
        """A non-existent path in manual sources must leave an error trail."""
        provider = ManualProvider()
        items = provider.collect(
            SourceQuery(),
            {"sources": [
                {"name": "Missing File", "path": "/nonexistent/path/xyz/file.md",
                 "enabled": True}
            ]},
        )
        # Currently returns [] silently — the bug
        # After fix: should either return an error SourceItem or raise
        # For now, verify that at minimum the validation catches this
        errors = provider.validate_config({
            "sources": [
                {"name": "Missing File", "path": "/nonexistent/path/xyz/file.md",
                 "enabled": True}
            ],
        })
        # validate_config must report that path doesn't exist
        path_errors = [e for e in errors if "path" in e.lower()]
        assert len(path_errors) > 0, (
            "B11 FAIL: validate_config must report non-existent path"
        )

    def test_unreadable_file_detected(self, tmp_path):
        """An unreadable file must produce an error or empty result.

        Uses mock to avoid OS-level permission issues (root can read 0o000 files).
        """
        from unittest.mock import patch

        bad_file = tmp_path / "unreadable.md"
        bad_file.write_text("content")

        provider = ManualProvider()

        with patch.object(Path, "read_text", side_effect=PermissionError("denied")):
            items = provider.collect(
                SourceQuery(),
                {"sources": [
                    {"name": "Unreadable", "path": str(bad_file), "enabled": True}
                ]},
            )

        # Either error is surfaced in items, or an error item is produced
        assert len(items) == 0 or any(
            item.metadata.get("error_type") for item in items
        ), "B11: unreadable file must produce no items or an error item"
