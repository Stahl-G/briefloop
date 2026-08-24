"""Deterministic OOXML ingestion for the TOYO weekly market workbook.

The parser is deliberately profile-bound.  It reads values and formulas from
the workbook package, recomputes the product metrics from their input cells,
and emits strict MarketDataSnapshotV2 child records.  Excel formula caches are
never authoritative and embedded chart pixels never become claim evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from typing import Any, Iterable, Mapping
from xml.etree import ElementTree
import zipfile

from pydantic import ValidationError

from multi_agent_brief.contracts.v2 import (
    MarketDataBenchmarkV2,
    MarketDataConflictV2,
    MarketDataEventReactionV2,
    MarketDataFieldValueV2,
    MarketDataFxRateV2,
    MarketDataGapV2,
    MarketDataSecurityV2,
    MarketDataSeriesPointV2,
    MarketDataWorkbookIdentityV2,
)
from multi_agent_brief.core.fingerprint import canonical_fingerprint
from multi_agent_brief.sources.market_data import MarketDataError
from multi_agent_brief.sources.equity_universe import (
    DEFAULT_SOLAR_EQUITY_UNIVERSE,
    EquityPeriodicUniverse,
    SOLAR_STOCK_PRIMARY_SECURITIES,
)

TOYO_WEEKLY_XLSX_PROFILE_ID = "toyo-weekly-v1"
MARKET_DATA_DERIVATION_VERSION = "solar-market-data-v2"
_XLSX_BYTE_CAP = 32 * 1024 * 1024
_XLSX_ENTRY_CAP = 5_000
_XLSX_UNCOMPRESSED_CAP = 128 * 1024 * 1024
_XLSX_MEMBER_CAP = 32 * 1024 * 1024
_XML_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_DOC_REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_WINDOW_RE = re.compile(
    r"(?P<start>\d{4}-\d{2}-\d{2})\s*~\s*(?:(?P<year>\d{4})-)?(?P<end>\d{2}-\d{2})"
)

_REQUIRED_SHEETS = (
    "PR事件复盘",
    "Sources",
    "TOYO周明细",
    "估值与多空",
    "海外对标",
    "美股对标",
    "走势数据",
)
_TREND_TICKERS = {
    "TOYO Solar": "TOYO",
    "T1 Energy": "TE",
    "First Solar": "FSLR",
    "阿特斯": "CSIQ",
    "晶科能源": "JKS",
    "Nextracker": "NXT",
    "大全能源": "DQ",
    "韩华解决方案": "009830.KS",
    "Waaree Energies": "WAAREEENER.NS",
    "Vikram Solar": "VIKRAMSOLR.NS",
}
_SECURITY_META = {
    "TOYO": ("TOYO Solar", "NASDAQ", "USD", "primary"),
    "TE": ("T1 Energy", "NYSE", "USD", "primary"),
    "FSLR": ("First Solar", "NASDAQ", "USD", "primary"),
    "CSIQ": ("Canadian Solar", "NASDAQ", "USD", "primary"),
    "JKS": ("JinkoSolar", "NYSE", "USD", "primary"),
    "NXT": ("Nextracker", "NASDAQ", "USD", "primary"),
    "DQ": ("Daqo New Energy", "NYSE", "USD", "primary"),
    "009830.KS": ("Hanwha Solutions", "KRX", "KRW", "overseas"),
    "WAAREEENER.NS": ("Waaree Energies", "NSE", "INR", "overseas"),
    "PREMIERENE.NS": ("Premier Energies", "NSE", "INR", "overseas"),
    "VIKRAMSOLR.NS": ("Vikram Solar", "NSE", "INR", "overseas"),
}


def _ticker_meta(
    ticker: str, universe: EquityPeriodicUniverse
) -> tuple[str, str, str, str]:
    if ticker in _SECURITY_META:
        return _SECURITY_META[ticker]
    if ticker.endswith(".KS"):
        return (ticker, "KRX", "KRW", universe.group_for(ticker))
    if ticker.endswith(".NS"):
        return (ticker, "NSE", "INR", universe.group_for(ticker))
    if ticker.endswith(".HK"):
        return (ticker, "HKEX", "HKD", universe.group_for(ticker))
    return (ticker, "UNKNOWN", "USD", universe.group_for(ticker))


_VALUATION_FIELDS = {
    "现价(本币)": ("latest_close_local", "price"),
    "现价(美元)": ("latest_close_usd", "price"),
    "市值(百万本币)": ("market_cap_local_millions", "millions"),
    "市值(百万美元)": ("market_cap_usd_millions", "millions_usd"),
    "P/E(TTM)": ("pe_ttm", "multiple"),
    "P/E(Fwd)": ("pe_forward", "multiple"),
    "P/S(TTM)": ("ps_ttm", "multiple"),
    "EV/EBITDA": ("ev_ebitda_ttm", "multiple"),
    "Beta": ("beta", "ratio"),
    "空头占流通%": ("short_float_pct", "percent"),
    "分析师数": ("analyst_count", "count"),
    "目标价均值(本币)": ("target_price_local", "price"),
    "评级": ("rating", "text"),
}


def _column_index(reference: str) -> int:
    letters = "".join(char for char in reference if char.isalpha()).upper()
    value = 0
    for char in letters:
        value = value * 26 + ord(char) - 64
    return value - 1


def _cell_reference(row: int, column: int) -> str:
    number = column + 1
    letters = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        letters = chr(65 + remainder) + letters
    return f"{letters}{row + 1}"


def _stable_id(prefix: str, payload: Mapping[str, object]) -> str:
    return f"{prefix}-{canonical_fingerprint(payload)[:24]}"


def _number(value: object) -> float | None:
    if type(value) in {int, float}:
        return float(value)
    if isinstance(value, str):
        cleaned = value.strip().replace(",", "")
        if cleaned in {"", "-", "N/A", "#NAME?", "#VALUE!", "#REF!"}:
            return None
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def _integer(value: object) -> int | None:
    number = _number(value)
    if number is None or number < 0 or not number.is_integer():
        return None
    return int(number)


def _iso_date(value: object) -> str | None:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        text = value.strip()
        if _DATE_RE.fullmatch(text):
            try:
                return date.fromisoformat(text).isoformat()
            except ValueError:
                return None
    if type(value) in {int, float} and 1 <= float(value) <= 100_000:
        return (date(1899, 12, 30) + timedelta(days=int(float(value)))).isoformat()
    return None


@dataclass(frozen=True)
class XlsxCell:
    value: object
    formula: str | None
    reference: str


class XlsxSheet:
    def __init__(self, name: str, cells: dict[tuple[int, int], XlsxCell]) -> None:
        self.name = name
        self._cells = cells

    def cell(self, row: int, column: int) -> XlsxCell:
        return self._cells.get(
            (row, column),
            XlsxCell(value=None, formula=None, reference=_cell_reference(row, column)),
        )

    def value(self, row: int, column: int) -> object:
        return self.cell(row, column).value

    def locator(self, row: int, column: int) -> str:
        return f"{self.name}!{self.cell(row, column).reference}"

    def rows(self) -> Iterable[tuple[int, list[object]]]:
        max_row = max((row for row, _column in self._cells), default=-1)
        max_column = max((column for _row, column in self._cells), default=-1)
        for row in range(max_row + 1):
            yield row, [self.value(row, column) for column in range(max_column + 1)]

    def find_row(self, exact_values: Iterable[str]) -> tuple[int, dict[str, int]]:
        required = tuple(exact_values)
        for row_index, values in self.rows():
            mapping = {
                str(value).strip(): column
                for column, value in enumerate(values)
                if isinstance(value, str) and str(value).strip()
            }
            if all(value in mapping for value in required):
                return row_index, mapping
        raise MarketDataError("market_data_xlsx_profile_invalid")


class XlsxWorkbook:
    def __init__(self, sheets: dict[str, XlsxSheet]) -> None:
        self.sheets = sheets

    def sheet(self, name: str) -> XlsxSheet:
        try:
            return self.sheets[name]
        except KeyError as exc:
            raise MarketDataError("market_data_xlsx_profile_invalid") from exc


@dataclass(frozen=True)
class ParsedMarketDataWorkbook:
    report_window_start: str
    report_window_end: str
    as_of_date: str
    universe_tickers: tuple[str, ...]
    provider_ids: tuple[str, ...]
    workbook: MarketDataWorkbookIdentityV2
    securities: tuple[MarketDataSecurityV2, ...]
    benchmark: MarketDataBenchmarkV2 | None
    fx_rates: tuple[MarketDataFxRateV2, ...]
    events: tuple[MarketDataEventReactionV2, ...]
    gaps: tuple[MarketDataGapV2, ...]
    conflicts: tuple[MarketDataConflictV2, ...]
    derivation_version: str = MARKET_DATA_DERIVATION_VERSION

    def record_payload(self) -> dict[str, object]:
        return {
            "schema_version": "briefloop.market_data_record_input.v2",
            "report_window_start": self.report_window_start,
            "report_window_end": self.report_window_end,
            "as_of_date": self.as_of_date,
            "universe_tickers": list(self.universe_tickers),
            "provider_ids": list(self.provider_ids),
            "workbook": self.workbook.model_dump(mode="json", exclude_unset=False),
            "securities": [
                item.model_dump(mode="json", exclude_unset=False)
                for item in self.securities
            ],
            "benchmark": (
                None
                if self.benchmark is None
                else self.benchmark.model_dump(mode="json", exclude_unset=False)
            ),
            "fx_rates": [
                item.model_dump(mode="json", exclude_unset=False)
                for item in self.fx_rates
            ],
            "events": [
                item.model_dump(mode="json", exclude_unset=False)
                for item in self.events
            ],
            "gaps": [
                item.model_dump(mode="json", exclude_unset=False) for item in self.gaps
            ],
            "conflicts": [
                item.model_dump(mode="json", exclude_unset=False)
                for item in self.conflicts
            ],
            "derivation_version": self.derivation_version,
        }


def _safe_zip_members(archive: zipfile.ZipFile) -> None:
    infos = archive.infolist()
    if len(infos) > _XLSX_ENTRY_CAP:
        raise MarketDataError("market_data_xlsx_unsafe")
    total = 0
    for info in infos:
        member = PurePosixPath(info.filename)
        if (
            member.is_absolute()
            or ".." in member.parts
            or info.file_size > _XLSX_MEMBER_CAP
        ):
            raise MarketDataError("market_data_xlsx_unsafe")
        total += info.file_size
        if total > _XLSX_UNCOMPRESSED_CAP:
            raise MarketDataError("market_data_xlsx_unsafe")
        lowered = info.filename.casefold()
        if lowered.endswith("vbaproject.bin") or lowered.startswith(
            "xl/externallinks/"
        ):
            raise MarketDataError("market_data_xlsx_unsafe")


def _relationships(xml_bytes: bytes) -> dict[str, tuple[str, bool]]:
    root = ElementTree.fromstring(xml_bytes)
    result: dict[str, tuple[str, bool]] = {}
    for item in root.findall(f"{_REL_NS}Relationship"):
        identifier = item.attrib.get("Id", "")
        target = item.attrib.get("Target", "")
        external = item.attrib.get("TargetMode") == "External"
        if identifier and target:
            result[identifier] = (target, external)
    return result


def _read_ooxml(path: Path) -> tuple[XlsxWorkbook, bytes]:
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise MarketDataError("market_data_xlsx_unavailable") from exc
    if len(payload) == 0 or len(payload) > _XLSX_BYTE_CAP:
        raise MarketDataError("market_data_xlsx_unsafe")
    try:
        with zipfile.ZipFile(path) as archive:
            _safe_zip_members(archive)
            names = set(archive.namelist())
            required = {"xl/workbook.xml", "xl/_rels/workbook.xml.rels"}
            if not required <= names:
                raise MarketDataError("market_data_xlsx_profile_invalid")
            workbook_rels = _relationships(archive.read("xl/_rels/workbook.xml.rels"))
            if any(external for _target, external in workbook_rels.values()):
                raise MarketDataError("market_data_xlsx_unsafe")
            shared: list[str] = []
            if "xl/sharedStrings.xml" in names:
                shared_root = ElementTree.fromstring(
                    archive.read("xl/sharedStrings.xml")
                )
                for item in shared_root.findall(f"{_XML_NS}si"):
                    shared.append(
                        "".join(node.text or "" for node in item.iter(f"{_XML_NS}t"))
                    )
            workbook_root = ElementTree.fromstring(archive.read("xl/workbook.xml"))
            sheets: dict[str, XlsxSheet] = {}
            for sheet in workbook_root.findall(f".//{_XML_NS}sheet"):
                name = sheet.attrib.get("name", "")
                relation_id = sheet.attrib.get(f"{_DOC_REL_NS}id", "")
                relation = workbook_rels.get(relation_id)
                if not name or relation is None or relation[1]:
                    raise MarketDataError("market_data_xlsx_profile_invalid")
                target = relation[0].lstrip("/")
                member = str(PurePosixPath("xl") / target)
                member = str(PurePosixPath(member))
                while "/../" in member:
                    member = str(PurePosixPath(member.replace("/../", "/")))
                if member not in names:
                    candidate = str(PurePosixPath(target))
                    if candidate not in names:
                        raise MarketDataError("market_data_xlsx_profile_invalid")
                    member = candidate
                root = ElementTree.fromstring(archive.read(member))
                cells: dict[tuple[int, int], XlsxCell] = {}
                for node in root.findall(f".//{_XML_NS}c"):
                    reference = node.attrib.get("r", "")
                    row_text = "".join(char for char in reference if char.isdigit())
                    if not reference or not row_text:
                        continue
                    row = int(row_text) - 1
                    column = _column_index(reference)
                    cell_type = node.attrib.get("t", "n")
                    formula_node = node.find(f"{_XML_NS}f")
                    formula = None if formula_node is None else formula_node.text or ""
                    value_node = node.find(f"{_XML_NS}v")
                    inline = node.find(f"{_XML_NS}is")
                    raw = None if value_node is None else value_node.text
                    value: object = None
                    if cell_type == "s" and raw is not None:
                        index = int(raw)
                        value = shared[index] if 0 <= index < len(shared) else None
                    elif cell_type == "inlineStr" and inline is not None:
                        value = "".join(
                            item.text or "" for item in inline.iter(f"{_XML_NS}t")
                        )
                    elif cell_type == "b" and raw is not None:
                        value = raw == "1"
                    elif cell_type in {"str", "e"}:
                        value = raw
                    elif raw is not None:
                        try:
                            numeric = float(raw)
                            value = int(numeric) if numeric.is_integer() else numeric
                        except ValueError:
                            value = raw
                    cells[(row, column)] = XlsxCell(value, formula, reference)
                sheets[name] = XlsxSheet(name, cells)
    except MarketDataError:
        raise
    except (
        OSError,
        ValueError,
        KeyError,
        zipfile.BadZipFile,
        ElementTree.ParseError,
    ) as exc:
        raise MarketDataError("market_data_xlsx_profile_invalid") from exc
    return XlsxWorkbook(sheets), payload


def _report_window(sheet: XlsxSheet) -> tuple[str, str]:
    match = None
    for _row, values in sheet.rows():
        for value in values:
            if isinstance(value, str):
                match = _WINDOW_RE.search(value)
                if match is not None:
                    break
        if match is not None:
            break
    if match is None:
        raise MarketDataError("market_data_xlsx_profile_invalid")
    start = date.fromisoformat(match.group("start"))
    end = date.fromisoformat(
        f"{match.group('year') or start.year}-{match.group('end')}"
    )
    if end < start:
        raise MarketDataError("market_data_xlsx_profile_invalid")
    return start.isoformat(), end.isoformat()


def _available_field(
    *,
    field_id: str,
    value: float | str,
    unit: str,
    as_of: str,
    currency: str | None,
    origin: str,
    derivation: str,
    locator: str,
    source_sha256: str,
) -> MarketDataFieldValueV2:
    return MarketDataFieldValueV2.model_validate(
        {
            "field_id": field_id,
            "status": "available",
            "value_number": value if isinstance(value, (float, int)) else None,
            "value_text": value if isinstance(value, str) else None,
            "unit": unit,
            "as_of": as_of,
            "currency": currency,
            "data_origin": origin,
            "derivation": derivation,
            "source_locator": locator,
            "source_sha256": source_sha256,
            "reason_code": None,
        },
        strict=True,
    )


def _unavailable_field(
    *,
    field_id: str,
    unit: str,
    as_of: str,
    currency: str | None,
    locator: str,
    source_sha256: str,
    reason: str,
) -> MarketDataFieldValueV2:
    return MarketDataFieldValueV2.model_validate(
        {
            "field_id": field_id,
            "status": "unavailable",
            "value_number": None,
            "value_text": None,
            "unit": unit,
            "as_of": as_of,
            "currency": currency,
            "data_origin": "manual_xlsx",
            "derivation": "direct",
            "source_locator": locator,
            "source_sha256": source_sha256,
            "reason_code": reason,
        },
        strict=True,
    )


def _parse_trend_series(
    workbook: XlsxWorkbook,
    source_sha256: str,
) -> tuple[dict[str, list[MarketDataSeriesPointV2]], MarketDataBenchmarkV2 | None]:
    sheet = workbook.sheet("走势数据")
    header_row, header = sheet.find_row(("日期", "TOYO Solar", "TAN(基准)"))
    series: dict[str, list[MarketDataSeriesPointV2]] = {
        ticker: [] for ticker in _TREND_TICKERS.values()
    }
    benchmark_points: list[MarketDataSeriesPointV2] = []
    row = header_row + 1
    while True:
        current_date = _iso_date(sheet.value(row, header["日期"]))
        if current_date is None:
            break
        for label, ticker in _TREND_TICKERS.items():
            column = header.get(label)
            close = None if column is None else _number(sheet.value(row, column))
            if close is None:
                continue
            series[ticker].append(
                MarketDataSeriesPointV2.model_validate(
                    {
                        "date": current_date,
                        "close": close,
                        "adjusted_close": None,
                        "volume": None,
                        "data_origin": "manual_xlsx",
                        "source_locator": sheet.locator(row, column),
                        "source_sha256": source_sha256,
                    },
                    strict=True,
                )
            )
        tan_column = header["TAN(基准)"]
        tan_close = _number(sheet.value(row, tan_column))
        if tan_close is not None:
            benchmark_points.append(
                MarketDataSeriesPointV2.model_validate(
                    {
                        "date": current_date,
                        "close": tan_close,
                        "adjusted_close": None,
                        "volume": None,
                        "data_origin": "manual_xlsx",
                        "source_locator": sheet.locator(row, tan_column),
                        "source_sha256": source_sha256,
                    },
                    strict=True,
                )
            )
        row += 1
    benchmark = None
    if len(benchmark_points) >= 2:
        benchmark = MarketDataBenchmarkV2.model_validate(
            {
                "ticker": "TAN",
                "display_name": "Invesco Solar ETF",
                "currency": "USD",
                "return_basis": "close",
                "price_series": [
                    item.model_dump(mode="json", exclude_unset=False)
                    for item in benchmark_points
                ],
            },
            strict=True,
        )
    return series, benchmark


def _parse_toyo_volume(
    workbook: XlsxWorkbook,
    points: list[MarketDataSeriesPointV2],
    source_sha256: str,
) -> list[MarketDataSeriesPointV2]:
    sheet = workbook.sheet("TOYO周明细")
    header_row, header = sheet.find_row(("日期", "收盘", "成交量(股)"))
    volumes: dict[str, int] = {}
    row = header_row + 1
    while True:
        current_date = _iso_date(sheet.value(row, header["日期"]))
        if current_date is None:
            break
        volume = _integer(sheet.value(row, header["成交量(股)"]))
        if volume is not None:
            volumes[current_date] = volume
        row += 1
    result: list[MarketDataSeriesPointV2] = []
    for point in points:
        locator = point.source_locator
        volume = volumes.get(point.date)
        if volume is not None:
            locator = f"{locator};TOYO周明细:{point.date}"
        result.append(
            MarketDataSeriesPointV2.model_validate(
                {
                    **point.model_dump(mode="json", exclude_unset=False),
                    "volume": volume,
                    "source_locator": locator,
                    "source_sha256": source_sha256,
                },
                strict=True,
            )
        )
    return result


def _parse_comparison_fields(
    workbook: XlsxWorkbook,
    ticker: str,
    source_sha256: str,
    as_of: str,
    formula_caches: dict[str, tuple[float, str]],
) -> list[MarketDataFieldValueV2]:
    sheet_name = "美股对标" if ticker in SOLAR_STOCK_PRIMARY_SECURITIES else "海外对标"
    sheet = workbook.sheet(sheet_name)
    header_row, header = sheet.find_row(("代码", "区间涨跌幅%", "区间日均成交量(股)"))
    for row, _values in sheet.rows():
        if (
            row <= header_row
            or str(sheet.value(row, header["代码"]) or "").strip() != ticker
        ):
            continue
        result: list[MarketDataFieldValueV2] = []
        for label, field_id, unit in (
            ("区间涨跌幅%", "return_1w_pct", "percent"),
            ("区间日均成交量(股)", "average_volume", "shares"),
            ("量比(vs基准日前30日)", "volume_ratio", "ratio"),
            ("区间成交额(百万美元)", "period_turnover_usd_millions", "millions_usd"),
        ):
            column = header.get(label)
            cell = None if column is None else sheet.cell(row, column)
            value = None if cell is None else _number(cell.value)
            if cell is not None and cell.formula is not None:
                if value is not None:
                    formula_caches[field_id] = (value, sheet.locator(row, column))
                # Weekly return is recomputed from the frozen daily series.  Any
                # other formula-backed comparison field has no product-owned
                # derivation and therefore cannot enter the authoritative row.
                continue
            if value is not None:
                result.append(
                    _available_field(
                        field_id=field_id,
                        value=value,
                        unit=unit,
                        as_of=as_of,
                        currency="USD" if unit == "millions_usd" else None,
                        origin="manual_xlsx",
                        derivation="direct",
                        locator=sheet.locator(row, column),
                        source_sha256=source_sha256,
                    )
                )
        return result
    return []


def _parse_valuation_fields(
    workbook: XlsxWorkbook,
    ticker: str,
    source_sha256: str,
    as_of: str,
    formula_caches: dict[str, tuple[float, str]],
    gaps: list[MarketDataGapV2],
) -> list[MarketDataFieldValueV2]:
    sheet = workbook.sheet("估值与多空")
    header_row, header = sheet.find_row(
        ("代码", "现价(本币)", "市值(百万美元)", "评级")
    )
    for row, _values in sheet.rows():
        if (
            row <= header_row
            or str(sheet.value(row, header["代码"]) or "").strip() != ticker
        ):
            continue
        currency = str(
            sheet.value(row, header["币种"])
            or (_SECURITY_META[ticker][2] if ticker in _SECURITY_META else "USD")
        ).strip()
        result: list[MarketDataFieldValueV2] = []
        for label, (field_id, unit) in _VALUATION_FIELDS.items():
            column = header[label]
            cell = sheet.cell(row, column)
            raw = cell.value
            value: float | str | None
            if field_id == "rating":
                value = (
                    str(raw).strip() if isinstance(raw, str) and raw.strip() else None
                )
            else:
                value = _number(raw)
            field_currency = None
            if field_id in {
                "latest_close_local",
                "market_cap_local_millions",
                "target_price_local",
            }:
                field_currency = currency
            elif field_id in {"latest_close_usd", "market_cap_usd_millions"}:
                field_currency = "USD"
            if cell.formula is not None:
                formula_value = _number(raw)
                if formula_value is not None:
                    formula_caches[field_id] = (
                        formula_value,
                        sheet.locator(row, column),
                    )
                if field_id in {
                    "latest_close_local",
                    "latest_close_usd",
                    "market_cap_usd_millions",
                }:
                    # These cells are deterministically reconstructed below from
                    # frozen daily observations, direct local market cap, and a
                    # frozen FX rate.  The cached Excel value is comparison-only.
                    continue
                gaps.append(
                    MarketDataGapV2.model_validate(
                        {
                            "gap_id": _stable_id(
                                "market-gap",
                                {
                                    "ticker": ticker,
                                    "field_id": field_id,
                                    "locator": sheet.locator(row, column),
                                },
                            ),
                            "severity": "warning",
                            "category": "workbook_formula_unresolved",
                            "ticker": ticker,
                            "field_id": field_id,
                            "source_locator": sheet.locator(row, column),
                            "reason_code": "workbook_formula_not_product_derived",
                        },
                        strict=True,
                    )
                )
                continue
            if value is None:
                result.append(
                    _unavailable_field(
                        field_id=field_id,
                        unit=unit,
                        as_of=as_of,
                        currency=field_currency,
                        locator=sheet.locator(row, column),
                        source_sha256=source_sha256,
                        reason="workbook_field_empty",
                    )
                )
            else:
                result.append(
                    _available_field(
                        field_id=field_id,
                        value=value,
                        unit=unit,
                        as_of=as_of,
                        currency=field_currency,
                        origin="manual_xlsx",
                        derivation="direct",
                        locator=sheet.locator(row, column),
                        source_sha256=source_sha256,
                    )
                )
        return result
    return []


def _recomputed_fields(
    ticker: str,
    series: list[MarketDataSeriesPointV2],
    valuation_fields: list[MarketDataFieldValueV2],
    fx_rates: Mapping[str, MarketDataFxRateV2],
    source_sha256: str,
    as_of: str,
    universe: EquityPeriodicUniverse,
) -> list[MarketDataFieldValueV2]:
    if not series:
        return []
    first, last = series[0], series[-1]
    locator = f"{first.source_locator}:{last.source_locator}"
    period_return = round((last.close / first.close - 1.0) * 100.0, 6)
    currency = _ticker_meta(ticker, universe)[2]
    fields = [
        _available_field(
            field_id="latest_close_local",
            value=last.close,
            unit="price",
            as_of=last.date,
            currency=currency,
            origin="derived",
            derivation="recomputed",
            locator=last.source_locator,
            source_sha256=source_sha256,
        ),
        _available_field(
            field_id="return_1w_pct",
            value=period_return,
            unit="percent",
            as_of=as_of,
            currency=None,
            origin="derived",
            derivation="recomputed",
            locator=locator,
            source_sha256=source_sha256,
        ),
    ]
    rate = 1.0 if currency == "USD" else None
    rate_locator = "identity:USD"
    if currency != "USD":
        fx_rate = fx_rates.get(currency)
        if fx_rate is not None:
            rate = fx_rate.units_per_usd
            rate_locator = fx_rate.source_locator
    if rate is None:
        fields.append(
            _unavailable_field(
                field_id="latest_close_usd",
                unit="price",
                as_of=last.date,
                currency="USD",
                locator=last.source_locator,
                source_sha256=source_sha256,
                reason="workbook_fx_rate_missing",
            )
        )
    else:
        fields.append(
            _available_field(
                field_id="latest_close_usd",
                # ``toyo-weekly-v1`` presents converted prices at two
                # decimals.  Reproduce that product rule rather than an
                # arbitrary binary-float precision or Excel cache.
                value=round(last.close / rate, 2),
                unit="price",
                as_of=last.date,
                currency="USD",
                origin="derived",
                derivation="converted",
                locator=f"{last.source_locator};{rate_locator}",
                source_sha256=source_sha256,
            )
        )
    local_market_cap = next(
        (
            field
            for field in valuation_fields
            if field.field_id == "market_cap_local_millions"
            and field.status == "available"
            and field.value_number is not None
        ),
        None,
    )
    if local_market_cap is not None and rate is not None:
        fields.append(
            _available_field(
                field_id="market_cap_usd_millions",
                # The workbook profile defines market-cap display in whole
                # USD millions; preserve that exact published precision.
                value=round(local_market_cap.value_number / rate, 0),
                unit="millions_usd",
                as_of=as_of,
                currency="USD",
                origin="derived",
                derivation="converted",
                locator=f"{local_market_cap.source_locator};{rate_locator}",
                source_sha256=source_sha256,
            )
        )
    else:
        fields.append(
            _unavailable_field(
                field_id="market_cap_usd_millions",
                unit="millions_usd",
                as_of=as_of,
                currency="USD",
                locator=(
                    last.source_locator
                    if local_market_cap is None
                    else local_market_cap.source_locator
                ),
                source_sha256=source_sha256,
                reason=(
                    "workbook_local_market_cap_missing"
                    if local_market_cap is None
                    else "workbook_fx_rate_missing"
                ),
            )
        )
    for field_id in ("return_1m_pct", "return_ytd_pct"):
        fields.append(
            _unavailable_field(
                field_id=field_id,
                unit="percent",
                as_of=as_of,
                currency=None,
                locator=locator,
                source_sha256=source_sha256,
                reason="insufficient_price_history",
            )
        )
    return fields


def _record_formula_cache_conflicts(
    ticker: str,
    fields: Iterable[MarketDataFieldValueV2],
    formula_caches: Mapping[str, tuple[float, str]],
    conflicts: list[MarketDataConflictV2],
) -> None:
    """Compare cached formula values without ever selecting them as authority."""

    by_id = {field.field_id: field for field in fields}
    for field_id, (cached, cache_locator) in sorted(formula_caches.items()):
        field = by_id.get(field_id)
        if field is None or field.status != "available" or field.value_number is None:
            continue
        tolerance = (
            0.051
            if field.unit == "percent"
            else max(1e-6, abs(field.value_number) * 1e-4)
        )
        if abs(cached - field.value_number) <= tolerance:
            continue
        payload = {
            "ticker": ticker,
            "field_id": field_id,
            "formula_cache": cached,
            "recomputed": field.value_number,
        }
        conflicts.append(
            MarketDataConflictV2.model_validate(
                {
                    "conflict_id": _stable_id("market-conflict", payload),
                    "severity": "blocking",
                    "category": "formula_recompute_mismatch",
                    "ticker": ticker,
                    "field_id": field_id,
                    "manual_value": cached,
                    "provider_value": field.value_number,
                    "resolution": "unresolved",
                    "source_locator": f"{cache_locator};{field.source_locator}",
                },
                strict=True,
            )
        )


def _merge_fields(
    ticker: str,
    groups: Iterable[Iterable[MarketDataFieldValueV2]],
    conflicts: list[MarketDataConflictV2],
) -> list[MarketDataFieldValueV2]:
    chosen: dict[str, MarketDataFieldValueV2] = {}
    for group in groups:
        for field in group:
            existing = chosen.get(field.field_id)
            if existing is None:
                chosen[field.field_id] = field
                continue
            if existing.status != "available" and field.status == "available":
                chosen[field.field_id] = field
                continue
            if (
                existing.status == "available"
                and field.status == "available"
                and existing.value_number is not None
                and field.value_number is not None
                and abs(existing.value_number - field.value_number)
                > (
                    0.051
                    if field.unit == "percent"
                    else max(1e-6, abs(existing.value_number) * 1e-4)
                )
            ):
                payload = {
                    "ticker": ticker,
                    "field_id": field.field_id,
                    "manual": existing.value_number,
                    "derived": field.value_number,
                }
                conflicts.append(
                    MarketDataConflictV2.model_validate(
                        {
                            "conflict_id": _stable_id("market-conflict", payload),
                            "severity": "blocking",
                            "category": "formula_recompute_mismatch",
                            "ticker": ticker,
                            "field_id": field.field_id,
                            "manual_value": existing.value_number,
                            "provider_value": field.value_number,
                            "resolution": "unresolved",
                            "source_locator": (
                                f"{existing.source_locator};{field.source_locator}"
                            ),
                        },
                        strict=True,
                    )
                )
            if field.derivation == "recomputed":
                chosen[field.field_id] = field
    if "ev_sales_ttm" not in chosen:
        reference = next(iter(chosen.values()))
        chosen["ev_sales_ttm"] = _unavailable_field(
            field_id="ev_sales_ttm",
            unit="multiple",
            as_of=reference.as_of or "1970-01-01",
            currency=None,
            locator=reference.source_locator,
            source_sha256=reference.source_sha256,
            reason="workbook_field_absent",
        )
    return [chosen[key] for key in sorted(chosen)]


def _parse_fx_rates(
    workbook: XlsxWorkbook,
    source_sha256: str,
    as_of: str,
) -> list[MarketDataFxRateV2]:
    sheet = workbook.sheet("估值与多空")
    result: list[MarketDataFxRateV2] = []
    for row, values in sheet.rows():
        for column, value in enumerate(values):
            if not isinstance(value, str) or "→USD" not in value:
                continue
            base = value.split("→", 1)[0].replace("汇率假设：", "").strip()
            rate_column = next(
                (
                    candidate
                    for candidate in (column + 1, column + 2)
                    if _number(sheet.value(row, candidate)) is not None
                ),
                None,
            )
            rate = (
                None if rate_column is None else _number(sheet.value(row, rate_column))
            )
            if rate is None or not re.fullmatch(r"[A-Z]{3}", base):
                continue
            result.append(
                MarketDataFxRateV2.model_validate(
                    {
                        "base_currency": base,
                        "quote_currency": "USD",
                        "units_per_usd": rate,
                        "as_of": as_of,
                        "data_origin": "manual_xlsx",
                        "source_locator": sheet.locator(row, rate_column),
                        "source_sha256": source_sha256,
                    },
                    strict=True,
                )
            )
    return sorted(result, key=lambda item: (item.base_currency, item.quote_currency))


def _parse_events(
    workbook: XlsxWorkbook,
    source_sha256: str,
    gaps: list[MarketDataGapV2],
) -> list[MarketDataEventReactionV2]:
    sheet = workbook.sheet("PR事件复盘")
    header_row, header = sheet.find_row(("事件", "发布日期", "超额收益%"))
    result: list[MarketDataEventReactionV2] = []
    row = header_row + 1
    while True:
        title = sheet.value(row, header["事件"])
        published_at = _iso_date(sheet.value(row, header["发布日期"]))
        if not isinstance(title, str) or not title.strip() or published_at is None:
            break
        payload = {
            "ticker": "TOYO",
            "title": title.strip(),
            "published_at": published_at,
        }
        event_id = _stable_id("market-event", payload)
        locator = sheet.locator(row, header["事件"])
        raw_url = sheet.value(row, header.get("官方来源URL", -1))
        original_url = (
            raw_url.strip()
            if isinstance(raw_url, str)
            and raw_url.strip().startswith(("http://", "https://"))
            else None
        )
        result.append(
            MarketDataEventReactionV2.model_validate(
                {
                    "event_id": event_id,
                    "ticker": "TOYO",
                    "title": title.strip(),
                    "published_at": published_at,
                    "publication_timing": "pre_market"
                    if "盘前" in title
                    else "unknown",
                    "original_url": original_url,
                    "evidence_status": (
                        "claim_eligible"
                        if original_url is not None
                        else "display_only_source_url_missing"
                    ),
                    "event_day_return_pct": _number(
                        sheet.value(row, header.get("当日涨跌%", -1))
                    ),
                    "benchmark_event_day_return_pct": _number(
                        sheet.value(row, header.get("TAN当日%", -1))
                    ),
                    "event_day_excess_return_pct": _number(
                        sheet.value(row, header["超额收益%"])
                    ),
                    "t1_return_pct": _number(
                        sheet.value(row, header.get("次日涨跌%", -1))
                    ),
                    "t5_return_pct": None,
                    "event_day_volume": _integer(
                        sheet.value(row, header.get("事件日成交量", -1))
                    ),
                    "volume_ratio": _number(
                        sheet.value(row, header.get("量比(vs事件日前30日)", -1))
                    ),
                    "data_origin": "manual_xlsx",
                    "source_locator": locator,
                    "source_sha256": source_sha256,
                },
                strict=True,
            )
        )
        if original_url is None:
            gaps.append(
                MarketDataGapV2.model_validate(
                    {
                        "gap_id": _stable_id("market-gap", {**payload, "kind": "url"}),
                        "severity": "warning",
                        "category": "event_source_url_missing",
                        "ticker": "TOYO",
                        "field_id": None,
                        "source_locator": locator,
                        "reason_code": "event_original_url_missing",
                    },
                    strict=True,
                )
            )
        row += 1
    return sorted(result, key=lambda item: item.event_id)


def parse_toyo_weekly_xlsx(
    path: str | Path,
    *,
    universe: EquityPeriodicUniverse | None = None,
) -> ParsedMarketDataWorkbook:
    """Parse one workbook into validated snapshot children without Store writes."""

    watchlist = universe or DEFAULT_SOLAR_EQUITY_UNIVERSE
    source_path = Path(path).expanduser().resolve()
    workbook, payload = _read_ooxml(source_path)
    if any(name not in workbook.sheets for name in _REQUIRED_SHEETS):
        raise MarketDataError("market_data_xlsx_profile_invalid")
    source_sha256 = hashlib.sha256(payload).hexdigest()
    report_start, report_end = _report_window(workbook.sheet("美股对标"))
    if _report_window(workbook.sheet("海外对标")) != (report_start, report_end):
        raise MarketDataError("market_data_xlsx_profile_invalid")
    tickers = watchlist.watchlist
    workbook_identity = MarketDataWorkbookIdentityV2.model_validate(
        {
            "source_name": source_path.name,
            "content_sha256": source_sha256,
            "content_size_bytes": len(payload),
            "profile_id": TOYO_WEEKLY_XLSX_PROFILE_ID,
            "parsed_sheet_names": sorted(_REQUIRED_SHEETS),
            "contains_macros": False,
            "contains_external_links": False,
        },
        strict=True,
    )
    series_by_ticker, benchmark = _parse_trend_series(workbook, source_sha256)
    series_by_ticker["TOYO"] = _parse_toyo_volume(
        workbook,
        series_by_ticker.get("TOYO", []),
        source_sha256,
    )
    fx_rates = _parse_fx_rates(workbook, source_sha256, report_end)
    fx_by_currency = {item.base_currency: item for item in fx_rates}
    conflicts: list[MarketDataConflictV2] = []
    gaps: list[MarketDataGapV2] = []
    securities: list[MarketDataSecurityV2] = []
    for ticker in tickers:
        series = series_by_ticker.get(ticker, [])
        if not series:
            # The universe is a default watchlist, not a delivery quota:
            # only a core-subject miss blocks; every other miss becomes a
            # visible warning for coverage disclosure.
            gaps.append(
                MarketDataGapV2.model_validate(
                    {
                        "gap_id": _stable_id(
                            "market-gap", {"ticker": ticker, "kind": "series"}
                        ),
                        "severity": (
                            "blocking" if watchlist.is_core(ticker) else "warning"
                        ),
                        "category": "missing_security_series",
                        "ticker": ticker,
                        "field_id": "price_series",
                        "source_locator": "走势数据",
                        "reason_code": (
                            "core_security_price_series_missing"
                            if watchlist.is_core(ticker)
                            else "watchlist_security_price_series_missing"
                        ),
                    },
                    strict=True,
                )
            )
            continue
        formula_caches: dict[str, tuple[float, str]] = {}
        valuation_fields = _parse_valuation_fields(
            workbook,
            ticker,
            source_sha256,
            report_end,
            formula_caches,
            gaps,
        )
        fields = _merge_fields(
            ticker,
            (
                valuation_fields,
                _parse_comparison_fields(
                    workbook,
                    ticker,
                    source_sha256,
                    report_end,
                    formula_caches,
                ),
                _recomputed_fields(
                    ticker,
                    series,
                    valuation_fields,
                    fx_by_currency,
                    source_sha256,
                    report_end,
                    watchlist,
                ),
            ),
            conflicts,
        )
        _record_formula_cache_conflicts(ticker, fields, formula_caches, conflicts)
        display_name, exchange, currency, universe_kind = _ticker_meta(
            ticker, watchlist
        )
        securities.append(
            MarketDataSecurityV2.model_validate(
                {
                    "ticker": ticker,
                    "display_name": display_name,
                    "universe": universe_kind,
                    "exchange": exchange,
                    "currency": currency,
                    "return_basis": "close",
                    "price_series": [
                        item.model_dump(mode="json", exclude_unset=False)
                        for item in series
                    ],
                    "corporate_actions": [],
                    "fields": [
                        item.model_dump(mode="json", exclude_unset=False)
                        for item in fields
                    ],
                },
                strict=True,
            )
        )
    events = _parse_events(workbook, source_sha256, gaps)
    gaps.append(
        MarketDataGapV2.model_validate(
            {
                "gap_id": _stable_id("market-gap", {"kind": "legacy-image-3"}),
                "severity": "warning",
                "category": "display_only_chart_underlying_series_missing",
                "ticker": "TOYO",
                "field_id": "one_month_price_volume_chart",
                "source_locator": "embedded:image3.png",
                "reason_code": "display_only_underlying_series_missing",
            },
            strict=True,
        )
    )
    try:
        return ParsedMarketDataWorkbook(
            report_window_start=report_start,
            report_window_end=report_end,
            as_of_date=report_end,
            universe_tickers=tickers,
            provider_ids=("manual_xlsx",),
            workbook=workbook_identity,
            securities=tuple(sorted(securities, key=lambda item: item.ticker)),
            benchmark=benchmark,
            fx_rates=tuple(fx_rates),
            events=tuple(events),
            gaps=tuple(sorted(gaps, key=lambda item: item.gap_id)),
            conflicts=tuple(sorted(conflicts, key=lambda item: item.conflict_id)),
        )
    except (TypeError, ValueError, ValidationError) as exc:
        raise MarketDataError("market_data_xlsx_profile_invalid") from exc


def parsed_workbook_debug_summary(parsed: ParsedMarketDataWorkbook) -> str:
    """Value-safe debug summary; never includes workbook cell prose."""

    payload = {
        "profile_id": parsed.workbook.profile_id,
        "workbook_sha256": parsed.workbook.content_sha256,
        "report_window_start": parsed.report_window_start,
        "report_window_end": parsed.report_window_end,
        "security_count": len(parsed.securities),
        "event_count": len(parsed.events),
        "gap_count": len(parsed.gaps),
        "conflict_count": len(parsed.conflicts),
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


__all__ = [
    "MARKET_DATA_DERIVATION_VERSION",
    "TOYO_WEEKLY_XLSX_PROFILE_ID",
    "ParsedMarketDataWorkbook",
    "parse_toyo_weekly_xlsx",
    "parsed_workbook_debug_summary",
]
