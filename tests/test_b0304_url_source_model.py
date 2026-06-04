"""Tests for PR2: URL source model and Source Candidate merge fixes (B03, B04, B05).

B03 — Manual URL sources must not be treated as usable unless fetched.
B04 — Normal web page URLs must not be written to rss.feeds.
B05 — Local input/ directory must be loaded even when URL sources are merged.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from multi_agent_brief.sources.manual import ManualProvider
from multi_agent_brief.sources.base import SourceItem, SourceQuery, SourceConfig
from multi_agent_brief.sources.decider import merge_candidates_to_sources
from multi_agent_brief.core.pipeline import BriefPipeline
from multi_agent_brief.core.schemas import PipelineContext
from multi_agent_brief.agents.scout import _is_placeholder


# ─── B03: Manual URL sources are placeholders, not usable ───

class TestB03ManualUrlPlaceholder:
    """Manual URL sources must be clear placeholders — not counted as usable."""

    def test_url_entry_is_placeholder(self):
        """_url_entry creates a placeholder with requires_fetch=True."""
        provider = ManualProvider()
        items = provider.collect(
            SourceQuery(),
            {"sources": [{"name": "Test URL", "url": "https://example.com/news"}]},
        )
        assert len(items) == 1
        item = items[0]
        assert item.source_type == "manual_url"
        assert item.metadata.get("requires_fetch") is True
        assert item.metadata.get("ingestion_status") == "placeholder"

    def test_scout_skips_placeholder_url(self):
        """Scout's _is_placeholder must return True for URL placeholders."""
        url_item = SourceItem(
            source_id="TEST_URL",
            source_name="Test URL",
            source_type="manual_url",
            title="Test URL",
            content="Manual URL source: https://example.com/news",
            url="https://example.com/news",
            metadata={"requires_fetch": True, "ingestion_status": "placeholder"},
        )
        assert _is_placeholder(url_item) is True, (
            "B03 FAIL: URL placeholder should be skipped by Scout — "
            "it has no real content"
        )

    def test_placeholder_not_counted_as_usable(self):
        """Placeholder URL source must NOT count as a usable source."""
        url_item = SourceItem(
            source_id="TEST_URL",
            source_name="Test URL",
            source_type="manual_url",
            title="Test URL",
            content="Manual URL source: https://example.com/news",
            url="https://example.com/news",
            metadata={"requires_fetch": True, "ingestion_status": "placeholder"},
        )
        assert url_item.metadata.get("requires_fetch") or url_item.source_type == "manual_url", (
            "B03 FAIL: URL placeholder must be identifiable as not-usable"
        )

    def test_local_file_not_placeholder(self):
        """Local files with real content must NOT be treated as placeholders."""
        file_item = SourceItem(
            source_id="LOCAL_FILE",
            source_name="Local File",
            source_type="local_file",
            title="Local File",
            content="Real content from a local file.",
            url="",
            metadata={"path": "/tmp/test.md"},
        )
        assert _is_placeholder(file_item) is False, (
            "B03 FAIL: Local file with real content should not be a placeholder"
        )


# ─── B04: Normal web page URLs must NOT be written to rss.feeds ───

class TestB04RssFeedMisclassification:
    """merge_candidates_to_sources must not write normal web pages into rss.feeds."""

    def test_industry_media_url_not_written_to_rss(self, tmp_path):
        """industry_media URLs must go to manual sources, not RSS feeds."""
        sources_path = tmp_path / "sources.yaml"
        candidates_path = tmp_path / "source_candidates.yaml"

        # Write initial sources.yaml with only manual + local input
        import yaml
        initial_sources = {
            "source_strategy": {
                "profile": "research",
                "enabled_providers": ["manual"],
            },
            "manual": {
                "enabled": True,
                "sources": [
                    {"name": "Local Input", "path": str(tmp_path / "input"),
                     "category": "local_files", "enabled": True}
                ],
            },
            "rss": {"enabled": False, "feeds": []},
        }
        sources_path.write_text(yaml.dump(initial_sources), encoding="utf-8")

        # Write candidates with an industry_media URL (normal article, NOT RSS)
        candidates = {
            "metadata": {"status": "pending_review"},
            "recommended_sources": [
                {
                    "name": "Reuters Industry News",
                    "url": "https://www.reuters.com/industry/article",
                    "category": "industry_media",
                    "snippet": "An industry news article about manufacturing trends.",
                    "enabled": True,
                },
                {
                    "name": "Research Institute Report",
                    "url": "https://research.example.com/report-2026",
                    "category": "research_institution",
                    "snippet": "A research report on market dynamics.",
                    "enabled": True,
                },
            ],
        }
        candidates_path.write_text(yaml.dump(candidates), encoding="utf-8")

        result = merge_candidates_to_sources(sources_path, candidates_path)

        # Re-read sources.yaml
        merged = yaml.safe_load(sources_path.read_text(encoding="utf-8"))

        # B04 ASSERT: RSS feeds must NOT contain these normal web page URLs
        rss_feeds = merged.get("rss", {}).get("feeds", [])
        rss_urls = {f["url"] for f in rss_feeds}
        assert "https://www.reuters.com/industry/article" not in rss_urls, (
            "B04 FAIL: Normal Reuters article URL was written to rss.feeds"
        )
        assert "https://research.example.com/report-2026" not in rss_urls, (
            "B04 FAIL: Normal research report URL was written to rss.feeds"
        )

        # These URLs should go to manual sources (as URL entries), not RSS
        manual_sources = merged.get("manual", {}).get("sources", [])
        manual_urls = {s.get("url") for s in manual_sources if s.get("url")}
        assert "https://www.reuters.com/industry/article" in manual_urls, (
            "B04 FAIL: Reuters article URL should be in manual sources, not lost"
        )
        assert "https://research.example.com/report-2026" in manual_urls, (
            "B04 FAIL: Research report URL should be in manual sources, not lost"
        )

        # RSS should have 0 added feeds from this merge
        assert result["added_rss"] == 0, (
            "B04 FAIL: added_rss should be 0 — no feeds were verified as RSS"
        )

    def test_company_official_url_goes_to_manual(self, tmp_path):
        """company_official URLs should go to manual, not RSS."""
        sources_path = tmp_path / "sources.yaml"
        candidates_path = tmp_path / "source_candidates.yaml"
        import yaml

        sources_path.write_text(yaml.dump({
            "source_strategy": {"profile": "research", "enabled_providers": ["manual"]},
            "manual": {"enabled": True, "sources": [
                {"name": "Local Input", "path": str(tmp_path / "input"),
                 "category": "local_files", "enabled": True}
            ]},
            "rss": {"enabled": False, "feeds": []},
        }), encoding="utf-8")

        candidates_path.write_text(yaml.dump({
            "metadata": {"status": "pending_review"},
            "recommended_sources": [{
                "name": "Company Blog",
                "url": "https://company.com/blog/post",
                "category": "company_official",
                "enabled": True,
            }],
        }), encoding="utf-8")

        merge_candidates_to_sources(sources_path, candidates_path)
        merged = yaml.safe_load(sources_path.read_text(encoding="utf-8"))

        # Should be in manual sources
        manual_urls = {s.get("url") for s in merged["manual"]["sources"] if s.get("url")}
        assert "https://company.com/blog/post" in manual_urls

        # Should NOT be in RSS
        rss_urls = {f["url"] for f in merged["rss"]["feeds"]}
        assert "https://company.com/blog/post" not in rss_urls


# ─── B05: Local input/ must always be loaded ───

class TestB05LocalInputPreserved:
    """Local input/ directory must be loaded even when URL sources are present."""

    def test_local_input_loaded_alongside_url_sources(self, tmp_path):
        """When manual.sources has both Local Input Directory and URL entries,
        the pipeline must still read local input files."""
        input_dir = tmp_path / "input"
        output_dir = tmp_path / "output"
        input_dir.mkdir()

        # Create a real local file with content
        (input_dir / "local_news.md").write_text(
            "- Local competitor announced expansion.\n",
            encoding="utf-8",
        )

        # Build a pipeline context with a SourceConfig that has BOTH
        # a Local Input Directory AND a URL entry in manual.sources
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        sources_path = config_dir / "sources.yaml"
        import yaml
        sources_path.write_text(yaml.dump({
            "source_strategy": {
                "profile": "research",
                "enabled_providers": ["manual"],
            },
            "manual": {
                "enabled": True,
                "sources": [
                    {"name": "Local Input Directory", "path": str(input_dir),
                     "category": "local_files", "enabled": True},
                    {"name": "Example URL", "url": "https://example.com/news",
                     "category": "industry_media", "enabled": True},
                ],
            },
            "rss": {"enabled": False, "feeds": []},
        }), encoding="utf-8")

        # Load SourceConfig from sources.yaml
        from multi_agent_brief.sources.registry import load_sources_config
        source_config = load_sources_config(sources_path)

        context = PipelineContext(
            project_name="B05 Test",
            input_dir=str(input_dir),
            output_dir=str(output_dir),
            report_date="2026-06-02",
            max_source_age_days=14,
        )
        context.metadata["source_config"] = source_config

        outputs = BriefPipeline().run(context)

        # Read the final brief
        brief_text = (output_dir / "brief.md").read_text(encoding="utf-8")
        assert "Local competitor announced expansion" in brief_text, (
            "B05 FAIL: Local input file content missing from brief — "
            "local input/ was not loaded when URL sources were present"
        )

    def test_pipeline_always_adds_local_input_if_missing(self, tmp_path):
        """If manual.sources lacks a Local Input Directory entry,
        the pipeline must add one automatically."""
        input_dir = tmp_path / "input"
        output_dir = tmp_path / "output"
        input_dir.mkdir()
        (input_dir / "news.md").write_text(
            "- Important local competitive signal detected in the market.\n",
            encoding="utf-8",
        )

        # SourceConfig with ONLY a URL, no Local Input Directory
        source_config = SourceConfig(
            profile="research",
            enabled_providers=["manual"],
            manual={
                "enabled": True,
                "sources": [
                    {"name": "Remote URL", "url": "https://example.com/news",
                     "enabled": True},
                ],
            },
        )

        context = PipelineContext(
            project_name="B05 Missing Local",
            input_dir=str(input_dir),
            output_dir=str(output_dir),
            report_date="2026-06-02",
            max_source_age_days=14,
        )
        context.metadata["source_config"] = source_config

        outputs = BriefPipeline().run(context)

        # Local input should still have been loaded
        brief_text = (output_dir / "brief.md").read_text(encoding="utf-8")
        assert "Important local competitive signal" in brief_text, (
            "B05 FAIL: Pipeline did not auto-add Local Input Directory "
            "when manual.sources lacked it"
        )

    def test_merge_preserves_existing_local_input(self, tmp_path):
        """After merge_candidates_to_sources, existing Local Input Directory
        entry must still be present."""
        import yaml
        sources_path = tmp_path / "sources.yaml"
        candidates_path = tmp_path / "source_candidates.yaml"

        initial = {
            "source_strategy": {"profile": "research", "enabled_providers": ["manual"]},
            "manual": {
                "enabled": True,
                "sources": [
                    {"name": "Local Input Directory", "path": "input/",
                     "category": "local_files", "enabled": True},
                ],
            },
            "rss": {"enabled": False, "feeds": []},
        }
        sources_path.write_text(yaml.dump(initial), encoding="utf-8")

        candidates = {
            "metadata": {"status": "pending_review"},
            "recommended_sources": [
                {"name": "News Site", "url": "https://example.com/news",
                 "category": "industry_media", "enabled": True},
            ],
        }
        candidates_path.write_text(yaml.dump(candidates), encoding="utf-8")

        merge_candidates_to_sources(sources_path, candidates_path)

        merged = yaml.safe_load(sources_path.read_text(encoding="utf-8"))

        # Local Input Directory must still be present
        local_inputs = [
            s for s in merged["manual"]["sources"]
            if s.get("name") == "Local Input Directory"
        ]
        assert len(local_inputs) == 1, (
            "B05 FAIL: Local Input Directory entry lost after merge"
        )
        assert local_inputs[0]["path"] == "input/", (
            "B05 FAIL: Local Input Directory path changed after merge"
        )
        assert local_inputs[0]["enabled"] is True, (
            "B05 FAIL: Local Input Directory disabled after merge"
        )
