"""Unit tests for chart placement and renderer-v2 event markers."""

from __future__ import annotations

import json
from pathlib import Path

from multi_agent_brief.quality_gates.chart_placement import (
    chart_placement_findings,
    manifest_chart_ids,
)

_INTENTS = ["market_reaction_divergence", "earnings_valuation"]
_MANIFEST = [
    "primary-indexed-trend",
    "overseas-indexed-trend",
    "toyo-price-volume",
    "one-week-return",
    "market-cap-usd",
    "ps-ttm",
]


def _brief(
    *,
    reaction_charts: bool,
    valuation_charts: bool,
    others: bool = True,
) -> str:
    parts = ["# Brief", "", "## 市场反应与分歧", "", "TOYO 本周下跌 14.95%（行情快照）；原因无证据入账。"]
    if reaction_charts:
        parts += [
            "",
            "![TOYO Close and Volume](charts/market_data/toyo-price-volume.png)",
            "![One-week Return](charts/market_data/one-week-return.png)",
        ]
    parts += ["", "## 估值", "", "TOYO 的 P/S 显著低于同业。"]
    if valuation_charts:
        parts += [
            "",
            "![P/S](charts/market_data/ps-ttm.png)",
            "![Market Cap](charts/market_data/market-cap-usd.png)",
        ]
    if others:
        parts += [
            "",
            "## 图表",
            "",
            "![Primary](charts/market_data/primary-indexed-trend.png)",
            "![Overseas](charts/market_data/overseas-indexed-trend.png)",
        ]
    return "\n".join(parts) + "\n"


def test_fully_placed_brief_has_no_findings() -> None:
    assert (
        chart_placement_findings(
            _brief(reaction_charts=True, valuation_charts=True),
            manifest_ids=_MANIFEST,
            required_intents=_INTENTS,
        )
        == []
    )


def test_chart_outside_its_bound_section_is_blocking() -> None:
    findings = chart_placement_findings(
        _brief(reaction_charts=False, valuation_charts=True),
        manifest_ids=_MANIFEST,
        required_intents=_INTENTS,
    )
    missing = [
        f["metadata"]["chart_id"]
        for f in findings
        if f["finding_type"] == "chart_placement_missing"
    ]
    assert set(missing) == {"toyo-price-volume", "one-week-return"}
    assert all(f["blocking_level"] == "blocking" for f in findings)


def test_silently_omitted_unbound_chart_is_blocking_but_disclosure_escapes() -> None:
    brief = _brief(reaction_charts=True, valuation_charts=True, others=False)
    findings = chart_placement_findings(
        brief,
        manifest_ids=_MANIFEST,
        required_intents=_INTENTS,
    )
    assert [f["finding_type"] for f in findings] == [
        "chart_omitted_silently",
        "chart_omitted_silently",
    ]
    disclosed = brief + (
        "\n## 覆盖缺口\n\n- primary-indexed-trend、"
        "overseas-indexed-trend 因篇幅未收录。\n"
    )
    assert (
        chart_placement_findings(
            disclosed,
            manifest_ids=_MANIFEST,
            required_intents=_INTENTS,
        )
        == []
    )


def test_manifest_chart_ids_reads_real_shape(tmp_path: Path) -> None:
    manifest = tmp_path / "market_data_chart_manifest.json"
    manifest.write_text(
        json.dumps({"charts": [{"chart_id": "ps-ttm"}]}),
        encoding="utf-8",
    )
    assert manifest_chart_ids(manifest) == ["ps-ttm"]
    assert manifest_chart_ids(tmp_path / "missing.json") == []


def test_subject_chart_renders_event_marker_bytes() -> None:
    from multi_agent_brief.contracts.v2 import (
        MarketDataSecurityV2,
        MarketDataSeriesPointV2,
    )
    from multi_agent_brief.product.market_data_charts import _toyo_asset

    points = [
        MarketDataSeriesPointV2.model_validate(
            {
                "date": day,
                "close": 10.0 + index * 0.1,
                "adjusted_close": 10.0 + index * 0.1,
                "volume": 1000 + index,
                "data_origin": "manual_xlsx",
                "source_locator": f"走势数据!A{index + 2}",
                "source_sha256": "b" * 64,
            },
            strict=True,
        )
        for index, day in enumerate(
            ["2026-08-18", "2026-08-19", "2026-08-20", "2026-08-21"]
        )
    ]
    from multi_agent_brief.contracts.v2 import MarketDataFieldValueV2

    field = MarketDataFieldValueV2.model_validate(
        {
            "field_id": "latest_close_local",
            "status": "available",
            "value_number": 10.3,
            "value_text": None,
            "unit": "price",
            "as_of": "2026-08-21",
            "currency": "USD",
            "data_origin": "derived",
            "derivation": "recomputed",
            "source_locator": "走势数据!B5",
            "source_sha256": "b" * 64,
            "reason_code": None,
        },
        strict=True,
    )
    security = MarketDataSecurityV2.model_validate(
        {
            "ticker": "TOYO",
            "display_name": "TOYO Co., Ltd.",
            "universe": "primary",
            "exchange": "NASDAQ",
            "currency": "USD",
            "return_basis": "adjusted_close",
            "price_series": [item.model_dump(mode="json") for item in points],
            "corporate_actions": [],
            "fields": [field.model_dump(mode="json")],
        },
        strict=True,
    )

    class _Event:
        event_id = "EVT-1"
        ticker = "TOYO"
        title = "earnings"
        published_at = "2026-08-19"

    plain = _toyo_asset(security, [])
    marked = _toyo_asset(security, [_Event()])
    assert plain is not None and marked is not None
    assert plain.png_bytes != marked.png_bytes
    assert marked.chart_id == "toyo-price-volume"


def test_bound_chart_omission_with_explicit_disclosure_passes() -> None:
    brief = _brief(reaction_charts=False, valuation_charts=True)
    disclosed = brief + (
        "\n## 覆盖缺口\n\n- toyo-price-volume、one-week-return "
        "因篇幅原因未收录。\n"
    )
    assert (
        chart_placement_findings(
            disclosed,
            manifest_ids=_MANIFEST,
            required_intents=_INTENTS,
        )
        == []
    )
