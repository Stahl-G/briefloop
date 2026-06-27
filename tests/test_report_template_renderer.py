"""Tests for the experimental ReportTemplate renderer."""

from __future__ import annotations

from pathlib import Path

import yaml

from multi_agent_brief.product.template_renderer import render_reader_markdown_with_template


def _write_market_report_spec(workspace: Path) -> None:
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "report_spec.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": "briefloop.report_spec.v1",
                "report_pack": "market_weekly",
                "policy_profile": "manufacturing_default",
                "report_type": "market_weekly",
                "title": "Market Weekly Brief",
                "cadence": "weekly",
                "audience": {"label": "business reader", "language": "en-US"},
                "source_policy": {"mode": "local_first", "hidden_autonomous_crawling": False},
                "control_spine": {
                    "claim_ledger": True,
                    "artifact_registry": True,
                    "quality_gates": True,
                    "event_log": True,
                    "archive": True,
                    "source_appendix": True,
                    "support_records": True,
                    "human_delivery_approval": True,
                    "frozen_artifact_integrity": True,
                },
                "outputs": ["markdown"],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def _complete_market_markdown() -> str:
    return "\n".join([
        "# Market Weekly Brief",
        "Opening note.",
        "## Executive Summary",
        "Summary.",
        "## Market Signals",
        "Signals.",
        "## Demand and Supply",
        "Demand.",
        "## Competitor Moves",
        "Competitors.",
        "## Policy and Regulatory",
        "Policy.",
        "## Risks and Watchlist",
        "Risks.",
        "## Source Appendix",
        "Sources.",
    ]) + "\n"


def test_template_renderer_is_noop_without_report_spec(tmp_path: Path) -> None:
    markdown = _complete_market_markdown()

    result = render_reader_markdown_with_template(workspace=tmp_path / "ws", markdown=markdown)

    assert result.status == "not_available"
    assert result.markdown == markdown


def test_template_renderer_does_not_drop_extra_top_level_sections(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    _write_market_report_spec(ws)
    markdown = _complete_market_markdown() + "\n# Unplanned Commentary\nDo not drop me.\n"

    result = render_reader_markdown_with_template(workspace=ws, markdown=markdown)

    assert result.status == "skipped_unresolved_sections"
    assert result.extra_headings == ["Unplanned Commentary"]
    assert result.markdown == markdown
