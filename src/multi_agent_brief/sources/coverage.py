"""Source Coverage Report — tracks source diversity and research gaps."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from multi_agent_brief.sources.base import SourceItem


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# Supported coverage dimensions
COVERAGE_DIMENSIONS: list[str] = [
    "source_kind",       # manual, rss, web_search, api, filings, etc.
    "source_tier",       # official, industry, media, social, etc.
    "geography",         # country/region from metadata
    "language",          # source language
    "platform",          # platform/website category
    "publisher_type",    # government, corporate, media, analyst
    "official_status",   # official, unofficial, unknown
    "recency_bucket",    # within_7d, within_30d, older
]


def _extract_dimension_value(source: SourceItem, dimension: str) -> str:
    """Extract the value of a coverage dimension from a SourceItem."""
    if dimension == "source_kind":
        return source.source_type or "unknown"
    elif dimension == "language":
        return source.language or "unknown"
    elif dimension == "official_status":
        # Check metadata for official status, default to unknown
        return source.metadata.get("official_status", "unknown")
    elif dimension == "recency_bucket":
        return _classify_recency(source.published_at)
    elif dimension == "geography":
        return source.metadata.get("geography", "unknown")
    elif dimension == "platform":
        return source.metadata.get("platform", "unknown")
    elif dimension == "publisher_type":
        return source.metadata.get("publisher_type", "unknown")
    elif dimension == "source_tier":
        return source.metadata.get("source_tier", _infer_tier(source))
    else:
        return source.metadata.get(dimension, "unknown")


def _classify_recency(published_at: str) -> str:
    """Classify a source into recency buckets based on published_at."""
    if not published_at:
        return "unknown"

    try:
        # Try ISO format parsing
        from datetime import timedelta
        now = datetime.now(timezone.utc)
        pub_date = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        if pub_date.tzinfo is None:
            pub_date = pub_date.replace(tzinfo=timezone.utc)

        delta = now - pub_date
        if delta <= timedelta(days=7):
            return "within_7d"
        elif delta <= timedelta(days=30):
            return "within_30d"
        else:
            return "older"
    except (ValueError, TypeError):
        return "unknown"


def _infer_tier(source: SourceItem) -> str:
    """Infer source tier from source type and metadata."""
    source_type = source.source_type.lower() if source.source_type else ""

    if source_type in ("manual", "local_file", "filing_resolver"):
        return "official"
    elif source_type in ("rss", "web_search"):
        return "media"
    elif source_type in ("api", "filings"):
        return "official"
    elif source_type in ("mcp", "cli"):
        return "industry"
    else:
        return "other"


@dataclass
class CoverageDimension:
    """Coverage statistics for a single dimension."""
    dimension: str
    expected: dict[str, int] = field(default_factory=dict)  # required counts
    actual: dict[str, int] = field(default_factory=dict)    # actual counts
    coverage_pct: float = 0.0
    gaps: list[str] = field(default_factory=list)           # missing categories

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension,
            "expected": self.expected,
            "actual": self.actual,
            "coverage_pct": self.coverage_pct,
            "gaps": self.gaps,
        }


@dataclass
class SourceCoverageReport:
    """Source coverage report tracking diversity and gaps."""
    total_sources: int = 0
    dimensions: list[CoverageDimension] = field(default_factory=list)
    required_gaps: list[dict[str, Any]] = field(default_factory=list)
    preferred_gaps: list[dict[str, Any]] = field(default_factory=list)
    generated_at: str = field(default_factory=_utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_sources": self.total_sources,
            "dimensions": [d.to_dict() for d in self.dimensions],
            "required_gaps": self.required_gaps,
            "preferred_gaps": self.preferred_gaps,
            "generated_at": self.generated_at,
        }

    def export_json(self, path: str | Path) -> None:
        import json
        Path(path).write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SourceCoverageReport:
        known = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in data.items() if k in known})


def _count_by_dimension(
    sources: list[SourceItem],
    dimension: str,
) -> dict[str, int]:
    """Count sources by dimension value."""
    counts: dict[str, int] = {}
    for source in sources:
        value = _extract_dimension_value(source, dimension)
        counts[value] = counts.get(value, 0) + 1
    return counts


def _calculate_coverage_pct(
    actual: dict[str, int],
    expected: dict[str, int],
) -> float:
    """Calculate coverage percentage based on expected vs actual."""
    if not expected:
        return 100.0  # No expectations = full coverage

    covered = 0
    for category, min_count in expected.items():
        if actual.get(category, 0) >= min_count:
            covered += 1

    return (covered / len(expected)) * 100 if expected else 100.0


def calculate_coverage(
    sources: list[SourceItem],
    config: dict[str, Any] | None = None,
) -> SourceCoverageReport:
    """Calculate source coverage across configured dimensions.

    Args:
        sources: List of collected SourceItems.
        config: Coverage configuration with dimensions and requirements.
            Example:
            {
                "enabled": True,
                "dimensions": {
                    "source_kind": {"required": True, "expected": {"manual": 1}},
                    "language": {"required": False},
                }
            }

    Returns:
        SourceCoverageReport with coverage statistics and gaps.
    """
    if config is None:
        config = {}

    dimensions_config = config.get("dimensions", {})
    if not dimensions_config:
        # Use default dimensions if none configured
        dimensions_config = {dim: {"required": False} for dim in COVERAGE_DIMENSIONS}

    dimensions = []
    all_gaps: list[dict[str, Any]] = []

    for dim_name, dim_opts in dimensions_config.items():
        if dim_name not in COVERAGE_DIMENSIONS:
            # Skip unknown dimensions
            continue

        actual = _count_by_dimension(sources, dim_name)
        expected = dim_opts.get("expected", {})
        required = dim_opts.get("required", False)

        gaps: list[str] = []
        for category, min_count in expected.items():
            if actual.get(category, 0) < min_count:
                gaps.append(category)

        coverage_pct = _calculate_coverage_pct(actual, expected)

        dim = CoverageDimension(
            dimension=dim_name,
            expected=expected,
            actual=actual,
            coverage_pct=coverage_pct,
            gaps=gaps,
        )
        dimensions.append(dim)

        if gaps:
            gap_entry = {
                "dimension": dim_name,
                "missing": gaps,
                "severity": "required" if required else "preferred",
                "coverage_pct": coverage_pct,
            }
            all_gaps.append(gap_entry)

    # Split gaps into required and preferred
    required_gaps = [g for g in all_gaps if g["severity"] == "required"]
    preferred_gaps = [g for g in all_gaps if g["severity"] != "required"]

    return SourceCoverageReport(
        total_sources=len(sources),
        dimensions=dimensions,
        required_gaps=required_gaps,
        preferred_gaps=preferred_gaps,
        generated_at=_utc_now_iso(),
    )


def render_research_gaps(report: SourceCoverageReport) -> str:
    """Render a research_gaps.md document from coverage report.

    This document is for internal use and should NOT be included
    in the reader-facing brief.md.
    """
    lines = [
        "# Research Gaps",
        "",
        f"Generated: {report.generated_at}",
        f"Total sources analyzed: {report.total_sources}",
        "",
    ]

    if report.required_gaps:
        lines.append("## Required Coverage Gaps")
        lines.append("")
        lines.append("The following gaps may impact brief quality:")
        lines.append("")
        for gap in report.required_gaps:
            lines.append(f"### {gap['dimension']}")
            lines.append(f"- Missing categories: {', '.join(gap['missing'])}")
            lines.append(f"- Coverage: {gap['coverage_pct']:.1f}%")
            lines.append("")

    if report.preferred_gaps:
        lines.append("## Preferred Coverage Gaps")
        lines.append("")
        lines.append("The following are informational and do not block delivery:")
        lines.append("")
        for gap in report.preferred_gaps:
            lines.append(f"- **{gap['dimension']}**: missing {', '.join(gap['missing'])} ({gap['coverage_pct']:.1f}% coverage)")
        lines.append("")

    if not report.required_gaps and not report.preferred_gaps:
        lines.append("No coverage gaps detected.")
        lines.append("")

    # Add dimension summary
    lines.append("## Dimension Summary")
    lines.append("")
    lines.append("| Dimension | Sources | Coverage |")
    lines.append("|-----------|---------|----------|")
    for dim in report.dimensions:
        total = sum(dim.actual.values())
        lines.append(f"| {dim.dimension} | {total} | {dim.coverage_pct:.1f}% |")
    lines.append("")

    return "\n".join(lines)
