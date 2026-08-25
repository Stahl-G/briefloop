"""Unit tests for the v2 finalize reader projection (labels + appendix + footer)."""

from __future__ import annotations

import pytest

from multi_agent_brief.contracts.v2 import RunDirection
from multi_agent_brief.core.claim_ledger import ClaimLedger
from multi_agent_brief.core.schemas import Claim
from multi_agent_brief.outputs.reader_final_gate import detect_reader_residue
from multi_agent_brief.runtime_host_v2.reader_render import (
    normalize_reader_image_paths,
    render_reader_markdown,
)


def _direction() -> RunDirection:
    return RunDirection.model_validate(
        {
            "schema_version": "briefloop.run_direction.v2",
            "subject_name": "ExampleCo",
            "industry_or_theme": "solar",
            "brief_title": "ExampleCo Market Report",
            "task_objective": "objective",
            "audience": "investors",
            "audience_profile": "management",
            "output_language": "zh-CN",
            "source_handling": "preserve_original",
            "cadence": "weekly",
            "focus_areas": ["ExampleCo"],
            "excluded_topics": [],
            "forbidden_sources": ["credentials"],
            "source_profile": "conservative",
            "web_search_mode": "external_api",
            "search_backend": "tavily",
            "output_formats": ["markdown"],
            "report_date": "2026-08-25",
            "report_window_start": "2026-08-03",
            "report_window_end": "2026-08-25",
            "max_source_age_days": 22,
            "selector_max_items": 20,
            "target_terms": ["ExampleCo"],
        },
        strict=True,
    )


def _ledger() -> ClaimLedger:
    claim = Claim.from_dict(
        {
            "claim_id": "CL-0001",
            "statement": "ExampleCo revenue doubled.",
            "evidence_text": "Revenue rose to $2.6 million.",
            "source_id": "SRC-1",
            "claim_type": "fact",
            "confidence": "medium",
            "requires_audit": False,
            "created_by": "claim-ledger",
            "used_in_sections": [],
            "source_url": "https://example.com/report",
            "source_type": "news",
            "metadata": {
                "source_title": "ExampleCo results coverage",
                "publisher": "Example Wire",
                "published_at": "2026-08-20",
                "retrieved_at": "2026-08-25T01:00:00Z",
                "source_category": "news_media",
            },
        }
    )
    return ClaimLedger([claim])


_AUDITED = (
    "# ExampleCo Market Report\n"
    "\n"
    "Revenue doubled this half [src:CL-0001].\n"
    "\n"
    "![Trend](../charts/market_data/trend.png)\n"
)


def test_render_replaces_citations_with_labels_and_appends_appendix() -> None:
    reader = render_reader_markdown(
        audited_markdown=_AUDITED,
        ledger=_ledger(),
        run_direction=_direction(),
        run_id="RUN-TEST-0001",
        store_revision=7,
    )
    assert "[src:CL-0001]" not in reader
    assert "[S1]" in reader
    assert "## Sources" in reader
    assert "ExampleCo results coverage" in reader
    assert "https://example.com/report" in reader


def test_render_normalizes_chart_paths_to_output_relative() -> None:
    reader = render_reader_markdown(
        audited_markdown=_AUDITED,
        ledger=_ledger(),
        run_direction=_direction(),
        run_id="RUN-TEST-0001",
        store_revision=7,
    )
    assert "(charts/market_data/trend.png)" in reader
    assert "../charts" not in reader


def test_render_stamps_deterministic_compliance_footer() -> None:
    kwargs = {
        "audited_markdown": _AUDITED,
        "ledger": _ledger(),
        "run_direction": _direction(),
        "run_id": "RUN-TEST-0001",
        "store_revision": 7,
    }
    first = render_reader_markdown(**kwargs)
    assert "RUN-TEST-0001" in first
    assert "r7" in first
    assert "2026-08-03~2026-08-25" in first
    assert "不构成投资建议" in first
    assert first == render_reader_markdown(**kwargs)


def test_render_without_ledger_strips_markers_and_keeps_footer() -> None:
    reader = render_reader_markdown(
        audited_markdown=_AUDITED,
        ledger=None,
        run_direction=_direction(),
        run_id="RUN-TEST-0001",
        store_revision=7,
    )
    assert "[src:CL-0001]" not in reader
    assert "## Sources" not in reader
    assert "不构成投资建议" in reader


def test_render_output_passes_reader_residue_gate() -> None:
    reader = render_reader_markdown(
        audited_markdown=_AUDITED,
        ledger=_ledger(),
        run_direction=_direction(),
        run_id="RUN-TEST-0001",
        store_revision=7,
    )
    result = detect_reader_residue(
        reader,
        "reader_brief",
        allow_compliance_footer=True,
    )
    assert result.status == "pass", [f.message for f in result.findings]


def test_normalize_reader_image_paths_only_touches_image_refs() -> None:
    markdown = (
        "![A](../charts/market_data/a.png)\n"
        "[link](../charts/not-image)\n"
    )
    assert normalize_reader_image_paths(markdown) == (
        "![A](charts/market_data/a.png)\n[link](../charts/not-image)\n"
    )


if __name__ == "__main__":
    pytest.main([__file__])
