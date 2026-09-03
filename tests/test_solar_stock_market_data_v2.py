
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from xml.sax.saxutils import escape, quoteattr
import zipfile

import pytest

from multi_agent_brief.contracts.v2 import MarketDataFieldValueV2, MarketDataSecurityV2, MarketDataSeriesPointV2, MarketDataSnapshotV2
from multi_agent_brief.control_store.serialization import canonical_fingerprint
from multi_agent_brief.core_run_v2.gates import _append_solar_market_data_findings
from multi_agent_brief.sources.market_data import MarketDataError
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
