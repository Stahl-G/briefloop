from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from xml.sax.saxutils import escape, quoteattr
import zipfile

import pytest

from multi_agent_brief.contracts.v2 import (
    MarketDataCorporateActionV2,
    MarketDataFieldValueV2,
    MarketDataFxRateV2,
    MarketDataSecurityV2,
    MarketDataSeriesPointV2,
    MarketDataSnapshotV2,
)
from multi_agent_brief.control_store.serialization import canonical_fingerprint
from multi_agent_brief.core_run_v2.gates import _append_solar_market_data_findings
from multi_agent_brief.product.market_data_charts import render_market_chart_assets
from multi_agent_brief.product.market_data_service import MarketDataRecordInputV2
from multi_agent_brief.sources.market_data import MarketDataError
from multi_agent_brief.sources.market_data_v2 import (
    MarketDataProviderOutcomeV2,
    YahooMarketDataV2Adapter,
    merge_manual_workbook_with_yahoo,
)
from multi_agent_brief.sources.market_data_xlsx import parse_toyo_weekly_xlsx
from multi_agent_brief.sources.solar_stock_plan import (
    SOLAR_STOCK_OVERSEAS_SECURITIES,
    SOLAR_STOCK_PRIMARY_SECURITIES,
)


_WINDOW = "Report window 2026-08-03 ~ 2026-08-12"
_TREND_LABELS = (
    ("TOYO", "TOYO Solar"),
    ("TE", "T1 Energy"),
    ("FSLR", "First Solar"),
    ("CSIQ", "阿特斯"),
    ("JKS", "晶科能源"),
    ("NXT", "Nextracker"),
    ("DQ", "大全能源"),
    ("009830.KS", "韩华解决方案"),
    ("WAAREEENER.NS", "Waaree Energies"),
    ("VIKRAMSOLR.NS", "Vikram Solar"),
)
_CURRENCIES = {
    "009830.KS": "KRW",
    "WAAREEENER.NS": "INR",
    "VIKRAMSOLR.NS": "INR",
}


@dataclass(frozen=True)
class _FormulaCell:
    formula: str
    cached_value: float


def _cell_reference(row: int, column: int) -> str:
    number = column + 1
    letters = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        letters = chr(65 + remainder) + letters
    return f"{letters}{row + 1}"


def _worksheet(rows: list[list[object]]) -> bytes:
    rendered_rows: list[str] = []
    for row_index, values in enumerate(rows):
        cells: list[str] = []
        for column, value in enumerate(values):
            if value is None:
                continue
            reference = _cell_reference(row_index, column)
            if isinstance(value, _FormulaCell):
                cells.append(
                    f'<c r="{reference}"><f>{escape(value.formula)}</f>'
                    f"<v>{value.cached_value}</v></c>"
                )
            elif isinstance(value, str):
                cells.append(
                    f'<c r="{reference}" t="inlineStr"><is><t>'
                    f"{escape(value)}</t></is></c>"
                )
            else:
                cells.append(f'<c r="{reference}"><v>{value}</v></c>')
        rendered_rows.append(f'<row r="{row_index + 1}">' + "".join(cells) + "</row>")
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        "<sheetData>" + "".join(rendered_rows) + "</sheetData></worksheet>"
    ).encode("utf-8")


def _comparison_rows(tickers: tuple[str, ...]) -> list[list[object]]:
    rows: list[list[object]] = [
        [_WINDOW],
        [
            "代码",
            "区间涨跌幅%",
            "区间日均成交量(股)",
            "量比(vs基准日前30日)",
            "区间成交额(百万美元)",
        ],
    ]
    rows.extend([[ticker, 10.0, 100_000, 1.2, 5.0] for ticker in tickers])
    return rows


def _valuation_rows(
    formula_cache_overrides: dict[tuple[str, str], float] | None = None,
) -> list[list[object]]:
    headers = [
        "代码",
        "币种",
        "现价(本币)",
        "现价(美元)",
        "市值(百万本币)",
        "市值(百万美元)",
        "P/E(TTM)",
        "P/E(Fwd)",
        "P/S(TTM)",
        "EV/EBITDA",
        "Beta",
        "空头占流通%",
        "分析师数",
        "目标价均值(本币)",
        "评级",
    ]
    rows: list[list[object]] = [headers]
    for index, (ticker, _label) in enumerate(_TREND_LABELS):
        close = round((10 + index) * 1.1, 6)
        currency = _CURRENCIES.get(ticker, "USD")
        rate = {"USD": 1.0, "KRW": 1380.0, "INR": 87.0}[currency]
        local_market_cap = 1_000 + index * 10
        values: list[object] = [
            ticker,
            currency,
            close,
            round(close / rate, 2),
            local_market_cap,
            round(local_market_cap / rate, 0),
            15.0,
            14.0,
            2.0,
            8.0,
            1.1,
            2.0,
            8,
            close * 1.2,
            "Hold",
        ]
        for label, cached_value in (formula_cache_overrides or {}).items():
            if label[0] == ticker:
                column = headers.index(label[1])
                values[column] = _FormulaCell(
                    formula=f"PRODUCT_OWNED_RECOMPUTE({column + 1})",
                    cached_value=cached_value,
                )
        rows.append(values)
    rows.extend(
        [
            ["汇率假设：KRW→USD", 1380.0],
            ["汇率假设：INR→USD", 87.0],
            ["汇率假设：CNY→USD", 7.1],
        ]
    )
    return rows


def _make_public_safe_workbook(
    path: Path,
    *,
    formula_cache_overrides: dict[tuple[str, str], float] | None = None,
    event_source_url: str | None = None,
) -> None:
    event_headers = [
        "事件",
        "发布日期",
        "当日涨跌%",
        "TAN当日%",
        "超额收益%",
        "次日涨跌%",
        "事件日成交量",
        "量比(vs事件日前30日)",
    ]
    event_row = [
        "Public-safe product update",
        "2026-08-05",
        2.0,
        0.5,
        1.5,
        -0.2,
        120_000,
        1.4,
    ]
    if event_source_url is not None:
        event_headers.append("官方来源URL")
        event_row.append(event_source_url)
    trend_headers = ["日期", *[label for _ticker, label in _TREND_LABELS], "TAN(基准)"]
    trend_start = ["2026-08-03", *[10 + index for index in range(10)], 50.0]
    trend_end = [
        "2026-08-12",
        *[round((10 + index) * 1.1, 6) for index in range(10)],
        51.0,
    ]
    sheets = {
        "PR事件复盘": [event_headers, event_row],
        "Sources": [["source"], ["public-safe fixture"]],
        "TOYO周明细": [
            ["日期", "收盘", "成交量(股)"],
            ["2026-08-03", 10.0, 100_000],
            ["2026-08-12", 11.0, 120_000],
        ],
        "估值与多空": _valuation_rows(formula_cache_overrides),
        "海外对标": _comparison_rows(
            SOLAR_STOCK_OVERSEAS_SECURITIES[:2] + SOLAR_STOCK_OVERSEAS_SECURITIES[3:]
        ),
        "美股对标": _comparison_rows(SOLAR_STOCK_PRIMARY_SECURITIES),
        "走势数据": [trend_headers, trend_start, trend_end],
    }
    workbook_sheets: list[str] = []
    relationships: list[str] = []
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for index, (name, rows) in enumerate(sheets.items(), start=1):
            workbook_sheets.append(
                f'<sheet name={quoteattr(name)} sheetId="{index}" r:id="rId{index}"/>'
            )
            relationships.append(
                "<Relationship "
                f'Id="rId{index}" '
                'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
                f'Target="worksheets/sheet{index}.xml"/>'
            )
            archive.writestr(f"xl/worksheets/sheet{index}.xml", _worksheet(rows))
        archive.writestr(
            "xl/workbook.xml",
            (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                "<sheets>" + "".join(workbook_sheets) + "</sheets></workbook>"
            ).encode("utf-8"),
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                + "".join(relationships)
                + "</Relationships>"
            ).encode("utf-8"),
        )


def _field_value(
    security: MarketDataSecurityV2, field_id: str
) -> MarketDataFieldValueV2:
    return next(item for item in security.fields if item.field_id == field_id)


def _provider_security(ticker: str = "PREMIERENE.NS") -> MarketDataSecurityV2:
    points = [
        MarketDataSeriesPointV2.model_validate(
            {
                "date": date_value,
                "close": close,
                "adjusted_close": close,
                "volume": 1_000,
                "data_origin": "yahoo_chart_api",
                "source_locator": f"yahoo:{ticker}:{date_value}",
                "source_sha256": "a" * 64,
            },
            strict=True,
        )
        for date_value, close in (("2026-08-03", 100.0), ("2026-08-12", 105.0))
    ]
    fields = [
        MarketDataFieldValueV2.model_validate(
            {
                "field_id": field_id,
                "status": "available",
                "value_number": value,
                "value_text": None,
                "unit": unit,
                "as_of": "2026-08-12",
                "currency": currency,
                "data_origin": "yahoo_quote_summary"
                if "market_cap" in field_id
                else "derived",
                "derivation": "provider_fill"
                if "market_cap" in field_id
                else "recomputed",
                "source_locator": f"yahoo:{ticker}",
                "source_sha256": "a" * 64,
                "reason_code": None,
            },
            strict=True,
        )
        for field_id, value, unit, currency in (
            ("latest_close_local", 105.0, "price", "INR"),
            ("market_cap_local_millions", 12_000.0, "millions", "INR"),
            ("return_1w_pct", 5.0, "percent", None),
        )
    ]
    return MarketDataSecurityV2.model_validate(
        {
            "ticker": ticker,
            "display_name": "Premier Energies",
            "universe": "overseas",
            "exchange": "NSE",
            "currency": "INR",
            "return_basis": "adjusted_close",
            "price_series": [item.model_dump(mode="json") for item in points],
            "corporate_actions": [],
            "fields": [
                item.model_dump(mode="json")
                for item in sorted(fields, key=lambda value: value.field_id)
            ],
        },
        strict=True,
    )


def test_profile_bound_xlsx_degrades_missing_watchlist_security_to_warning(
    tmp_path: Path,
) -> None:
    path = tmp_path / "public-safe-weekly.xlsx"
    _make_public_safe_workbook(path)

    parsed = parse_toyo_weekly_xlsx(path)
    command = MarketDataRecordInputV2.model_validate(
        parsed.record_payload(), strict=True
    )

    assert (command.report_window_start, command.report_window_end) == (
        "2026-08-03",
        "2026-08-12",
    )
    assert len(command.securities) == 10
    assert command.workbook is not None
    assert command.workbook.content_sha256
    assert command.provider_ids == ["manual_xlsx"]
    assert len(command.events) == 1
    assert command.events[0].evidence_status == "display_only_source_url_missing"
    toyo = next(item for item in command.securities if item.ticker == "TOYO")
    assert [item.volume for item in toyo.price_series] == [100_000, 120_000]
    assert _field_value(toyo, "return_1w_pct").value_number == pytest.approx(10.0)
    premier_gap = next(
        item
        for item in command.gaps
        if item.ticker == "PREMIERENE.NS"
        and item.category == "missing_security_series"
    )
    assert premier_gap.severity == "warning"
    assert premier_gap.reason_code == "watchlist_security_price_series_missing"
    assert not any(
        item.severity == "blocking" for item in command.gaps
    )


def test_profile_bound_xlsx_preserves_official_event_urls(tmp_path: Path) -> None:
    path = tmp_path / "public-safe-weekly.xlsx"
    _make_public_safe_workbook(
        path,
        event_source_url="https://www.prnewswire.com/news-releases/example",
    )

    parsed = parse_toyo_weekly_xlsx(path)
    command = MarketDataRecordInputV2.model_validate(
        parsed.record_payload(), strict=True
    )

    assert len(command.events) == 1
    event = command.events[0]
    assert str(event.original_url) == "https://www.prnewswire.com/news-releases/example"
    assert event.evidence_status == "claim_eligible"
    assert not any(
        item.category == "event_source_url_missing" for item in command.gaps
    )


def test_xlsx_parser_rejects_external_link_packages(tmp_path: Path) -> None:
    path = tmp_path / "unsafe.xlsx"
    _make_public_safe_workbook(path)
    with zipfile.ZipFile(path, "a") as archive:
        archive.writestr("xl/externalLinks/externalLink1.xml", b"<externalLink/>")

    with pytest.raises(MarketDataError) as excinfo:
        parse_toyo_weekly_xlsx(path)
    assert str(excinfo.value) == "market_data_xlsx_unsafe"


def test_formula_cache_never_overrides_product_recomputation(tmp_path: Path) -> None:
    path = tmp_path / "stale-formula-cache.xlsx"
    _make_public_safe_workbook(
        path,
        formula_cache_overrides={("TOYO", "现价(美元)"): 999.0},
    )

    parsed = parse_toyo_weekly_xlsx(path)
    toyo = next(item for item in parsed.securities if item.ticker == "TOYO")
    latest_usd = _field_value(toyo, "latest_close_usd")

    assert latest_usd.value_number == pytest.approx(11.0)
    assert latest_usd.data_origin == "derived"
    assert latest_usd.derivation == "converted"
    assert any(
        item.ticker == "TOYO"
        and item.field_id == "latest_close_usd"
        and item.category == "formula_recompute_mismatch"
        and item.severity == "blocking"
        for item in parsed.conflicts
    )


def test_manual_workbook_wins_and_yahoo_fills_missing_security(tmp_path: Path) -> None:
    path = tmp_path / "public-safe-weekly.xlsx"
    _make_public_safe_workbook(path)
    parsed = parse_toyo_weekly_xlsx(path)
    provider = MarketDataProviderOutcomeV2(
        securities=(_provider_security(),),
        benchmark=None,
        fx_rates=(
            MarketDataFxRateV2.model_validate(
                {
                    "base_currency": "INR",
                    "quote_currency": "USD",
                    "units_per_usd": 88.0,
                    "as_of": "2026-08-12",
                    "data_origin": "yahoo_chart_api",
                    "source_locator": "yahoo:INR=X",
                    "source_sha256": "b" * 64,
                },
                strict=True,
            ),
        ),
        gaps=(),
    )

    merged = MarketDataRecordInputV2.model_validate(
        merge_manual_workbook_with_yahoo(parsed.record_payload(), provider),
        strict=True,
    )

    assert len(merged.securities) == 11
    assert merged.provider_ids == ["manual_xlsx", "yahoo_finance_chart_v2"]
    assert not any(
        item.category == "missing_security_series" and item.ticker == "PREMIERENE.NS"
        for item in merged.gaps
    )
    premier = next(item for item in merged.securities if item.ticker == "PREMIERENE.NS")
    assert _field_value(
        premier, "market_cap_usd_millions"
    ).value_number == pytest.approx(12_000 / 87.0)
    toyo = next(item for item in merged.securities if item.ticker == "TOYO")
    assert toyo.price_series[-1].data_origin == "manual_xlsx"


def test_yahoo_daily_parser_uses_adjusted_close_and_freezes_actions() -> None:
    timestamps = [
        int(datetime(2025, 12, 31, tzinfo=timezone.utc).timestamp()),
        int(datetime(2026, 1, 2, tzinfo=timezone.utc).timestamp()),
        int(datetime(2026, 7, 10, tzinfo=timezone.utc).timestamp()),
        int(datetime(2026, 8, 3, tzinfo=timezone.utc).timestamp()),
        int(datetime(2026, 8, 12, tzinfo=timezone.utc).timestamp()),
    ]
    document = {
        "chart": {
            "error": None,
            "result": [
                {
                    "meta": {"currency": "USD", "fullExchangeName": "NasdaqGS"},
                    "timestamp": timestamps,
                    "indicators": {
                        "quote": [
                            {
                                "close": [8.0, 8.2, 9.0, 10.0, 11.0],
                                "volume": [1, 2, 3, 4, 5],
                            }
                        ],
                        "adjclose": [{"adjclose": [4.0, 4.1, 4.5, 10.0, 11.0]}],
                    },
                    "events": {
                        "splits": {
                            str(timestamps[2]): {
                                "date": timestamps[2],
                                "numerator": 2,
                                "denominator": 1,
                            }
                        }
                    },
                }
            ],
        }
    }
    quote_row = (
        {
            "symbol": "TOYO",
            "marketCap": 900_000_000,
            "trailingPE": -4.0,
            "enterpriseToRevenue": 2.5,
            "enterpriseToEbitda": 0.0,
        },
        "c" * 64,
        "yahoo:quote:TOYO",
    )

    security = YahooMarketDataV2Adapter()._parse_security(
        "TOYO",
        document,
        response_sha256="d" * 64,
        source_locator="yahoo:chart:TOYO",
        as_of_date="2026-08-12",
        quote_row=quote_row,
    )

    assert security.return_basis == "adjusted_close"
    assert len(security.corporate_actions) == 1
    assert security.corporate_actions[0].action_type == "split"
    assert _field_value(security, "return_1w_pct").value_number == pytest.approx(10.0)
    assert _field_value(security, "return_ytd_pct").value_number == pytest.approx(175.0)
    assert _field_value(security, "pe_ttm").status == "not_meaningful"
    assert _field_value(security, "ev_ebitda_ttm").status == "not_meaningful"
    assert _field_value(security, "ev_sales_ttm").value_number == 2.5


def test_chart_projection_is_deterministic_and_png() -> None:
    payload = deepcopy(MarketDataSnapshotV2.minimal_example)
    payload["snapshot_fingerprint"] = canonical_fingerprint(
        {key: value for key, value in payload.items() if key != "snapshot_fingerprint"}
    )
    snapshot = MarketDataSnapshotV2.model_validate(payload, strict=True)

    first = render_market_chart_assets(snapshot)
    second = render_market_chart_assets(snapshot)

    # Charts without any plottable data are omitted instead of rendered as
    # empty frames; the minimal example only backs these three.
    assert [item.chart_id for item in first] == [
        "primary-indexed-trend",
        "toyo-price-volume",
        "one-week-return",
    ]
    assert [item.sha256 for item in first] == [item.sha256 for item in second]
    assert all(item.png_bytes.startswith(b"\x89PNG\r\n\x1a\n") for item in first)
    assert len({item.relative_path for item in first}) == len(first)


def test_corporate_action_contract_rejects_incomplete_split() -> None:
    with pytest.raises(ValueError):
        MarketDataCorporateActionV2.model_validate(
            {
                "action_id": "market-action-invalid",
                "date": "2026-08-10",
                "action_type": "split",
                "value": 2.0,
                "currency": None,
                "split_numerator": 2.0,
                "split_denominator": None,
                "data_origin": "manual_json",
                "source_locator": "fixture:split",
                "source_sha256": "e" * 64,
            },
            strict=True,
        )


def test_snapshot_contract_rejects_security_outside_frozen_universe() -> None:
    payload = deepcopy(MarketDataSnapshotV2.minimal_example)
    payload["universe_tickers"] = ["FSLR"]
    payload["snapshot_fingerprint"] = canonical_fingerprint(
        {key: value for key, value in payload.items() if key != "snapshot_fingerprint"}
    )

    with pytest.raises(ValueError, match="outside the frozen universe"):
        MarketDataSnapshotV2.model_validate(payload, strict=True)


def _gate_bound_snapshot(
    *,
    warning: bool = False,
    exclude_tickers: tuple[str, ...] = (),
) -> MarketDataSnapshotV2:
    payload = deepcopy(MarketDataSnapshotV2.minimal_example)
    universe = SOLAR_STOCK_PRIMARY_SECURITIES + SOLAR_STOCK_OVERSEAS_SECURITIES
    base_security = payload["securities"][0]
    securities = []
    for ticker in sorted(universe):
        if ticker in exclude_tickers:
            continue
        security = deepcopy(base_security)
        security["ticker"] = ticker
        security["display_name"] = ticker
        security["universe"] = (
            "primary" if ticker in SOLAR_STOCK_PRIMARY_SECURITIES else "overseas"
        )
        securities.append(security)
    payload.update(
        {
            "report_window_start": "2026-08-03",
            "report_window_end": "2026-08-12",
            "as_of_date": "2026-08-12",
            "universe_tickers": list(universe),
            "security_count": len(securities),
            "securities": securities,
            "gaps": (
                [
                    {
                        "gap_id": "market-gap-display-only-event",
                        "severity": "warning",
                        "category": "event_source_url_missing",
                        "ticker": "TOYO",
                        "field_id": None,
                        "source_locator": "PR事件复盘!A2",
                        "reason_code": "event_source_url_missing",
                    }
                ]
                if warning
                else []
            ),
        }
    )
    payload["snapshot_fingerprint"] = canonical_fingerprint(
        {key: value for key, value in payload.items() if key != "snapshot_fingerprint"}
    )
    return MarketDataSnapshotV2.model_validate(payload, strict=True)


def test_solar_market_data_gate_blocks_missing_snapshot() -> None:
    raw: dict[str, list[dict[str, object]]] = {"material_fact": []}
    binding = SimpleNamespace(
        run_direction=SimpleNamespace(
            report_type="solar_stock_periodic",
            report_window_start="2026-08-03",
            report_window_end="2026-08-12",
        )
    )

    _append_solar_market_data_findings(
        raw,
        snapshot=SimpleNamespace(market_data_snapshots=()),
        binding=binding,
    )

    assert [item["finding_type"] for item in raw["material_fact"]] == [
        "market_data_snapshot_missing"
    ]
    assert raw["material_fact"][0]["blocking_level"] == "blocking"


def test_solar_market_data_gate_accepts_full_universe_and_keeps_warning_visible() -> (
    None
):
    raw: dict[str, list[dict[str, object]]] = {"material_fact": []}
    binding = SimpleNamespace(
        run_direction=SimpleNamespace(
            report_type="solar_stock_periodic",
            report_window_start="2026-08-03",
            report_window_end="2026-08-12",
        )
    )

    _append_solar_market_data_findings(
        raw,
        snapshot=SimpleNamespace(
            market_data_snapshots=(_gate_bound_snapshot(warning=True),)
        ),
        binding=binding,
    )

    assert [item["finding_type"] for item in raw["material_fact"]] == [
        "market_data_snapshot_disclosures_required"
    ]
    assert raw["material_fact"][0]["blocking_level"] == "warning"


def test_solar_market_data_gate_degrades_missing_watchlist_security_to_disclosure() -> (
    None
):
    raw: dict[str, list[dict[str, object]]] = {"material_fact": []}
    binding = SimpleNamespace(
        run_direction=SimpleNamespace(
            report_type="solar_stock_periodic",
            report_window_start="2026-08-03",
            report_window_end="2026-08-12",
        )
    )

    _append_solar_market_data_findings(
        raw,
        snapshot=SimpleNamespace(
            market_data_snapshots=(
                _gate_bound_snapshot(exclude_tickers=("PREMIERENE.NS",)),
            )
        ),
        binding=binding,
    )

    assert [item["finding_type"] for item in raw["material_fact"]] == [
        "market_data_snapshot_disclosures_required"
    ]
    finding = raw["material_fact"][0]
    assert finding["blocking_level"] == "warning"
    assert finding["metadata"]["watchlist_missing_tickers"] == ["PREMIERENE.NS"]
    assert finding["metadata"]["watchlist_expected_total"] == 11


def test_solar_market_data_gate_blocks_missing_core_subject() -> None:
    raw: dict[str, list[dict[str, object]]] = {"material_fact": []}
    binding = SimpleNamespace(
        run_direction=SimpleNamespace(
            report_type="solar_stock_periodic",
            report_window_start="2026-08-03",
            report_window_end="2026-08-12",
        )
    )

    _append_solar_market_data_findings(
        raw,
        snapshot=SimpleNamespace(
            market_data_snapshots=(_gate_bound_snapshot(exclude_tickers=("TOYO",)),)
        ),
        binding=binding,
    )

    assert [item["finding_type"] for item in raw["material_fact"]] == [
        "market_data_snapshot_incomplete",
        "market_data_snapshot_disclosures_required",
    ]
    blocking = raw["material_fact"][0]
    assert blocking["blocking_level"] == "blocking"
    assert blocking["metadata"]["core_missing_tickers"] == ["TOYO"]
    assert blocking["metadata"]["watchlist_missing_tickers"] == ["TOYO"]
