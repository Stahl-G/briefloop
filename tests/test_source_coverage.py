"""Tests for Source Coverage Report and Research Gaps."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from multi_agent_brief.sources.base import SourceItem
from multi_agent_brief.sources.coverage import (
    COVERAGE_DIMENSIONS,
    SourceCoverageReport,
    calculate_coverage,
    render_research_gaps,
    _classify_recency,
    _extract_dimension_value,
)


def _make_source(
    source_id: str = "SRC_001",
    source_type: str = "manual",
    language: str = "en",
    metadata: dict | None = None,
    published_at: str = "",
) -> SourceItem:
    """Helper to create a SourceItem for testing."""
    return SourceItem(
        source_id=source_id,
        source_name=f"Source {source_id}",
        source_type=source_type,
        title=f"Test title {source_id}",
        content=f"Test content for {source_id}",
        url=f"https://example.com/{source_id}",
        published_at=published_at,
        language=language,
        metadata=metadata or {},
    )


# --- Unit Tests ---

class TestCoverageDimensions:
    """Test coverage dimension constants."""

    def test_all_expected_dimensions_exist(self):
        assert len(COVERAGE_DIMENSIONS) == 8
        assert "source_kind" in COVERAGE_DIMENSIONS
        assert "source_tier" in COVERAGE_DIMENSIONS
        assert "geography" in COVERAGE_DIMENSIONS
        assert "language" in COVERAGE_DIMENSIONS
        assert "platform" in COVERAGE_DIMENSIONS
        assert "publisher_type" in COVERAGE_DIMENSIONS
        assert "official_status" in COVERAGE_DIMENSIONS
        assert "recency_bucket" in COVERAGE_DIMENSIONS


class TestRecencyClassification:
    """Test recency bucket classification."""

    def test_recent_source(self):
        from datetime import datetime, timedelta, timezone
        recent = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        assert _classify_recency(recent) == "within_7d"

    def test_medium_source(self):
        from datetime import datetime, timedelta, timezone
        medium = (datetime.now(timezone.utc) - timedelta(days=15)).isoformat()
        assert _classify_recency(medium) == "within_30d"

    def test_old_source(self):
        from datetime import datetime, timedelta, timezone
        old = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
        assert _classify_recency(old) == "older"

    def test_empty_published_at(self):
        assert _classify_recency("") == "unknown"

    def test_invalid_date(self):
        assert _classify_recency("not-a-date") == "unknown"


class TestDimensionExtraction:
    """Test dimension value extraction from SourceItem."""

    def test_source_kind(self):
        source = _make_source(source_type="web_search")
        assert _extract_dimension_value(source, "source_kind") == "web_search"

    def test_language(self):
        source = _make_source(language="zh-CN")
        assert _extract_dimension_value(source, "language") == "zh-CN"

    def test_unknown_language(self):
        source = _make_source(language="")
        assert _extract_dimension_value(source, "language") == "unknown"

    def test_geography_from_metadata(self):
        source = _make_source(metadata={"geography": "US"})
        assert _extract_dimension_value(source, "geography") == "US"

    def test_unknown_dimension_returns_unknown(self):
        source = _make_source()
        assert _extract_dimension_value(source, "nonexistent_dim") == "unknown"


class TestCalculateCoverage:
    """Test coverage calculation logic."""

    def test_empty_sources(self):
        report = calculate_coverage([], {})
        assert report.total_sources == 0
        assert len(report.dimensions) > 0
        assert len(report.required_gaps) == 0

    def test_single_source(self):
        sources = [_make_source(source_type="manual")]
        report = calculate_coverage(sources, {})
        assert report.total_sources == 1

    def test_multiple_sources_different_types(self):
        sources = [
            _make_source(source_id="S1", source_type="manual"),
            _make_source(source_id="S2", source_type="web_search"),
            _make_source(source_id="S3", source_type="rss"),
        ]
        report = calculate_coverage(sources, {})
        assert report.total_sources == 3

    def test_required_gap_detected(self):
        sources = [_make_source(source_type="manual")]
        config = {
            "dimensions": {
                "source_kind": {
                    "required": True,
                    "expected": {"manual": 1, "web_search": 1},
                },
            },
        }
        report = calculate_coverage(sources, config)
        assert len(report.required_gaps) == 1
        assert report.required_gaps[0]["missing"] == ["web_search"]

    def test_preferred_gap_detected(self):
        sources = [_make_source(source_type="manual")]
        config = {
            "dimensions": {
                "source_kind": {
                    "required": False,
                    "expected": {"web_search": 1},
                },
            },
        }
        report = calculate_coverage(sources, config)
        assert len(report.preferred_gaps) == 1
        assert len(report.required_gaps) == 0

    def test_no_gap_when_expected_met(self):
        sources = [
            _make_source(source_id="S1", source_type="manual"),
            _make_source(source_id="S2", source_type="web_search"),
        ]
        config = {
            "dimensions": {
                "source_kind": {
                    "required": True,
                    "expected": {"manual": 1, "web_search": 1},
                },
            },
        }
        report = calculate_coverage(sources, config)
        assert len(report.required_gaps) == 0

    def test_unknown_dimension_skipped(self):
        sources = [_make_source()]
        config = {
            "dimensions": {
                "nonexistent_dimension": {"required": True},
            },
        }
        report = calculate_coverage(sources, config)
        # Should not crash, just skip unknown dimension
        assert len(report.dimensions) == 0


class TestSourceCoverageReport:
    """Test SourceCoverageReport dataclass."""

    def test_roundtrip_serialization(self):
        sources = [
            _make_source(source_id="S1", source_type="manual"),
            _make_source(source_id="S2", source_type="rss"),
        ]
        report = calculate_coverage(sources, {})

        # to_dict and from_dict
        data = report.to_dict()
        restored = SourceCoverageReport.from_dict(data)
        assert restored.total_sources == report.total_sources
        assert len(restored.dimensions) == len(report.dimensions)

    def test_export_json(self, tmp_path):
        sources = [_make_source()]
        report = calculate_coverage(sources, {})

        json_path = tmp_path / "coverage.json"
        report.export_json(json_path)

        assert json_path.exists()
        data = json.loads(json_path.read_text(encoding="utf-8"))
        assert "total_sources" in data
        assert "dimensions" in data


class TestRenderResearchGaps:
    """Test research_gaps.md rendering."""

    def test_render_empty_report(self):
        report = calculate_coverage([], {})
        md = render_research_gaps(report)

        assert "# Research Gaps" in md
        assert "No coverage gaps detected" in md
        assert "Dimension Summary" in md

    def test_render_with_required_gaps(self):
        sources = [_make_source(source_type="manual")]
        config = {
            "dimensions": {
                "source_kind": {
                    "required": True,
                    "expected": {"web_search": 1},
                },
            },
        }
        report = calculate_coverage(sources, config)
        md = render_research_gaps(report)

        assert "Required Coverage Gaps" in md
        assert "web_search" in md

    def test_render_with_preferred_gaps(self):
        sources = [_make_source(source_type="manual")]
        config = {
            "dimensions": {
                "source_kind": {
                    "required": False,
                    "expected": {"rss": 1},
                },
            },
        }
        report = calculate_coverage(sources, config)
        md = render_research_gaps(report)

        assert "Preferred Coverage Gaps" in md
        assert "rss" in md

    def test_render_dimension_summary_table(self):
        sources = [
            _make_source(source_id="S1", source_type="manual"),
            _make_source(source_id="S2", source_type="web_search"),
        ]
        report = calculate_coverage(sources, {})
        md = render_research_gaps(report)

        assert "Dimension Summary" in md
        assert "| source_kind |" in md


class TestIntegration:
    """Integration tests with pipeline and formatter."""

    def test_pipeline_generates_coverage(self, tmp_path):
        from multi_agent_brief.core.pipeline import BriefPipeline
        from multi_agent_brief.core.schemas import PipelineContext
        from multi_agent_brief.sources.base import SourceConfig

        input_dir = tmp_path / "input"
        output_dir = tmp_path / "output"
        input_dir.mkdir()

        (input_dir / "news.md").write_text(
            "- Solar manufacturing capacity expanded 15% in Q1 2026.\n",
            encoding="utf-8",
        )

        source_config = SourceConfig(
            profile="research",
            industry="manufacturing",
            enabled_providers=["manual"],
            manual={"enabled": True, "sources": [{"name": "Test", "path": str(input_dir), "enabled": True}]},
        )

        context = PipelineContext(
            project_name="Coverage Test Brief",
            input_dir=str(input_dir),
            output_dir=str(output_dir),
            report_date="2026-06-02",
            max_source_age_days=14,
        )
        context.metadata["source_config"] = source_config

        pipeline = BriefPipeline()
        outputs = pipeline.run(context)

        # Verify coverage report was calculated
        assert "source_coverage" in context.metadata
        coverage = context.metadata["source_coverage"]
        assert isinstance(coverage, SourceCoverageReport)
        assert coverage.total_sources > 0

    def test_formatter_writes_coverage_files(self, tmp_path):
        from multi_agent_brief.core.pipeline import BriefPipeline
        from multi_agent_brief.core.schemas import PipelineContext
        from multi_agent_brief.sources.base import SourceConfig

        input_dir = tmp_path / "input"
        output_dir = tmp_path / "output"
        input_dir.mkdir()

        (input_dir / "news.md").write_text(
            "- Manufacturing demand grew 10% in Q1.\n",
            encoding="utf-8",
        )

        source_config = SourceConfig(
            profile="research",
            enabled_providers=["manual"],
            manual={"enabled": True, "sources": [{"name": "Test", "path": str(input_dir), "enabled": True}]},
        )

        context = PipelineContext(
            project_name="Formatter Test",
            input_dir=str(input_dir),
            output_dir=str(output_dir),
            report_date="2026-06-02",
        )
        context.metadata["source_config"] = source_config

        pipeline = BriefPipeline()
        pipeline.run(context)

        # Verify coverage files exist
        coverage_path = output_dir / "intermediate" / "source_coverage_report.json"
        assert coverage_path.exists(), "source_coverage_report.json should be created"

        # Verify it's valid JSON
        data = json.loads(coverage_path.read_text(encoding="utf-8"))
        assert "total_sources" in data
        assert "dimensions" in data
