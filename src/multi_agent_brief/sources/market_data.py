"""Weekly market data acquisition for the Solar Stock Periodic product.

Two acquisition channels feed one exact contract shape
(``MarketDataSecurityV1`` / ``MarketDataSnapshotV1``):

- ``YahooMarketDataAdapter`` pulls weekly bars from the Yahoo chart API
  (``query1.finance.yahoo.com/v8/finance/chart/<symbol>?range=3mo&interval=1wk``)
  using stdlib urllib only.  Each symbol is isolated: a transport, HTTP, or
  payload failure records one value-free gap and never blocks or fabricates
  the remaining securities.  The last weekly bar with a non-null close wins;
  it may be the in-progress week.  Bar dates are the UTC calendar date of the
  bar timestamp.  Valuation fields (``market_cap``, ``trailing_pe``) are
  copied from the provider ``meta`` object only when present and positive;
  otherwise they stay explicit nulls.
- Manual input files under ``input/market_data/`` take precedence over API
  quotes for the same ticker.  Files are consumed in sorted path order and
  the first occurrence of a ticker wins.

Manual JSON format (``*.json``)::

    {
      "as_of_date": "2026-08-07",          // optional per-row default
      "securities": [
        {
          "ticker": "DEMO",
          "exchange": "NasdaqCM",
          "currency": "USD",
          "as_of": "2026-08-07",            // optional when as_of_date is set
          "week_open": 10.4,                // optional, null when omitted
          "week_high": 10.9,
          "week_low": 10.1,
          "week_close": 10.62,              // required
          "week_volume": 1523400,           // optional non-negative integer
          "weekly_change_pct": 2.31,        // optional signed percentage
          "market_cap": 812000000.0,        // optional, null when unknown
          "trailing_pe": null               // optional, null when unknown
        }
      ]
    }

Manual CSV format (``*.csv``) uses exactly this header; empty cells become
explicit nulls and ``week_close`` is required::

    ticker,exchange,currency,as_of,week_open,week_high,week_low,week_close,week_volume,weekly_change_pct,market_cap,trailing_pe

A manual row that fails the contract (bad date, negative price, unknown
column value) becomes one ``manual_record_invalid`` gap; it never poisons
the remaining rows.  A file that cannot be parsed at all raises
``MarketDataError`` and records nothing.
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
import urllib.error
import urllib.request
from urllib.parse import quote, urlencode

from pydantic import ValidationError

from multi_agent_brief.contracts.v2 import (
    MarketDataSecurityGapV1,
    MarketDataSecurityV1,
)

YAHOO_CHART_API_BASE = "https://query1.finance.yahoo.com/v8/finance/chart"
YAHOO_CHART_RANGE = "3mo"
YAHOO_CHART_INTERVAL = "1wk"
YAHOO_RESPONSE_BYTE_CAP = 2 * 1024 * 1024
YAHOO_TIMEOUT_SECONDS = 30
MARKET_DATA_INPUT_DIR = "input/market_data"
_ORIGINAL_URLOPEN = urllib.request.urlopen

_CSV_COLUMNS = (
    "ticker",
    "exchange",
    "currency",
    "as_of",
    "week_open",
    "week_high",
    "week_low",
    "week_close",
    "week_volume",
    "weekly_change_pct",
    "market_cap",
    "trailing_pe",
)


class MarketDataError(RuntimeError):
    """Stable, value-free market data failure."""


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Retain a 3xx response as the one bounded exchange; never follow it."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_NO_REDIRECT_OPENER = urllib.request.build_opener(_NoRedirectHandler())


def _open_no_redirect(request: urllib.request.Request, *, timeout: int):
    """Use the no-redirect product transport while retaining test injection."""

    if urllib.request.urlopen is not _ORIGINAL_URLOPEN:
        return urllib.request.urlopen(request, timeout=timeout)
    return _NO_REDIRECT_OPENER.open(request, timeout=timeout)


@dataclass(frozen=True)
class MarketDataFetchOutcome:
    """DTO-validated quotes and value-free gaps for one acquisition pass."""

    securities: tuple[MarketDataSecurityV1, ...]
    gaps: tuple[MarketDataSecurityGapV1, ...]


@dataclass(frozen=True)
class ManualMarketDataFile:
    """Validated rows of one manual input file."""

    path: str
    securities: tuple[MarketDataSecurityV1, ...]
    gaps: tuple[MarketDataSecurityGapV1, ...]


def _gap(ticker: str, failure_class: str) -> MarketDataSecurityGapV1:
    return MarketDataSecurityGapV1.model_validate(
        {"ticker": ticker, "failure_class": failure_class},
        strict=True,
    )


def _finite_number(value: object) -> float | None:
    if type(value) is bool or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _non_negative_int(value: object) -> int | None:
    number = _finite_number(value)
    if number is None or number < 0 or not float(number).is_integer():
        return None
    return int(number)


def _positive_number(value: object) -> float | None:
    number = _finite_number(value)
    if number is None or number <= 0:
        return None
    return number


def _security_payload(
    *,
    ticker: str,
    exchange: str,
    currency: str,
    as_of: str,
    data_origin: str,
    week_open: float | None,
    week_high: float | None,
    week_low: float | None,
    week_close: float,
    week_volume: int | None,
    weekly_change_pct: float | None,
    market_cap: float | None,
    trailing_pe: float | None,
) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "exchange": exchange,
        "currency": currency,
        "as_of": as_of,
        "data_origin": data_origin,
        "week_open": week_open,
        "week_high": week_high,
        "week_low": week_low,
        "week_close": week_close,
        "week_volume": week_volume,
        "weekly_change_pct": weekly_change_pct,
        "market_cap": market_cap,
        "trailing_pe": trailing_pe,
    }


class YahooMarketDataAdapter:
    """Bounded, no-redirect reader of the Yahoo weekly chart endpoint."""

    provider_id = "yahoo_finance_chart"

    def fetch_weekly(self, symbols: Iterable[str]) -> MarketDataFetchOutcome:
        securities: list[MarketDataSecurityV1] = []
        gaps: list[MarketDataSecurityGapV1] = []
        for symbol in symbols:
            try:
                security = self._fetch_one(symbol)
            except MarketDataError as exc:
                gaps.append(_gap(symbol, str(exc)))
                continue
            if security is None:
                gaps.append(_gap(symbol, "symbol_data_missing"))
                continue
            securities.append(security)
        return MarketDataFetchOutcome(
            securities=tuple(securities),
            gaps=tuple(gaps),
        )

    def _fetch_one(self, symbol: str) -> MarketDataSecurityV1 | None:
        body = self._request_chart(symbol)
        try:
            document = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
            raise MarketDataError("response_invalid") from None
        security = self._parse_chart(symbol, document)
        return security

    @staticmethod
    def _request_chart(symbol: str) -> bytes:
        query = urlencode(
            {"range": YAHOO_CHART_RANGE, "interval": YAHOO_CHART_INTERVAL}
        )
        url = f"{YAHOO_CHART_API_BASE}/{quote(symbol, safe='')}?{query}"
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "briefloop-market-data/1.0",
            },
            method="GET",
        )
        try:
            with _open_no_redirect(request, timeout=YAHOO_TIMEOUT_SECONDS) as response:
                status_code = int(getattr(response, "status", 200))
                body = response.read(YAHOO_RESPONSE_BYTE_CAP + 1)
        except urllib.error.HTTPError as exc:
            status_code = int(exc.code)
            body = exc.read(YAHOO_RESPONSE_BYTE_CAP + 1)
        except Exception:
            raise MarketDataError("transport_unavailable") from None
        if status_code != 200:
            raise MarketDataError("http_error")
        if len(body) > YAHOO_RESPONSE_BYTE_CAP:
            raise MarketDataError("response_invalid")
        return body

    @staticmethod
    def _parse_chart(symbol: str, document: object) -> MarketDataSecurityV1 | None:
        if not isinstance(document, dict):
            raise MarketDataError("response_invalid")
        chart = document.get("chart")
        if not isinstance(chart, dict) or chart.get("error") is not None:
            raise MarketDataError("response_invalid")
        results = chart.get("result")
        if (
            not isinstance(results, list)
            or len(results) != 1
            or not isinstance(results[0], dict)
        ):
            raise MarketDataError("response_invalid")
        result = results[0]
        meta = result.get("meta")
        timestamps = result.get("timestamp")
        indicators = result.get("indicators")
        if not isinstance(meta, dict) or not isinstance(timestamps, list):
            raise MarketDataError("response_invalid")
        if not isinstance(indicators, dict):
            raise MarketDataError("response_invalid")
        quotes = indicators.get("quote")
        if (
            not isinstance(quotes, list)
            or len(quotes) != 1
            or not isinstance(quotes[0], dict)
        ):
            raise MarketDataError("response_invalid")
        quote_block = quotes[0]
        closes = quote_block.get("close")
        if not isinstance(closes, list) or len(closes) != len(timestamps):
            raise MarketDataError("response_invalid")

        def series(name: str) -> list[object]:
            values = quote_block.get(name)
            if not isinstance(values, list) or len(values) != len(timestamps):
                return [None] * len(timestamps)
            return values

        opens = series("open")
        highs = series("high")
        lows = series("low")
        volumes = series("volume")
        complete = [
            index
            for index, close in enumerate(closes)
            if _positive_number(close) is not None
            and isinstance(timestamps[index], (int, float))
        ]
        if not complete:
            return None
        latest = complete[-1]
        previous = complete[-2] if len(complete) >= 2 else None
        week_close = _positive_number(closes[latest])
        assert week_close is not None
        weekly_change_pct: float | None = None
        if previous is not None:
            previous_close = _positive_number(closes[previous])
            if previous_close is not None:
                weekly_change_pct = round(
                    (week_close / previous_close - 1) * 100,
                    4,
                )
        as_of = (
            datetime.fromtimestamp(float(timestamps[latest]), tz=timezone.utc)
            .date()
            .isoformat()
        )
        exchange = meta.get("fullExchangeName") or meta.get("exchangeName")
        currency = meta.get("currency")
        if not isinstance(exchange, str) or not isinstance(currency, str):
            raise MarketDataError("response_invalid")
        payload = _security_payload(
            ticker=symbol,
            exchange=exchange,
            currency=currency,
            as_of=as_of,
            data_origin="yahoo_chart_api",
            week_open=_positive_number(opens[latest]),
            week_high=_positive_number(highs[latest]),
            week_low=_positive_number(lows[latest]),
            week_close=week_close,
            week_volume=_non_negative_int(volumes[latest]),
            weekly_change_pct=weekly_change_pct,
            market_cap=_positive_number(meta.get("marketCap")),
            trailing_pe=_positive_number(meta.get("trailingPE")),
        )
        try:
            return MarketDataSecurityV1.model_validate(payload, strict=True)
        except ValidationError:
            raise MarketDataError("response_invalid") from None


def _manual_row_payload(
    row: dict[str, Any],
    *,
    default_as_of: str | None,
) -> dict[str, Any]:
    as_of = row.get("as_of") or default_as_of
    payload = _security_payload(
        ticker=row.get("ticker"),
        exchange=row.get("exchange"),
        currency=row.get("currency"),
        as_of=as_of,
        data_origin="manual_input",
        week_open=_positive_number(row.get("week_open")),
        week_high=_positive_number(row.get("week_high")),
        week_low=_positive_number(row.get("week_low")),
        week_close=row.get("week_close"),
        week_volume=_non_negative_int(row.get("week_volume")),
        weekly_change_pct=_finite_number(row.get("weekly_change_pct")),
        market_cap=_positive_number(row.get("market_cap")),
        trailing_pe=_positive_number(row.get("trailing_pe")),
    )
    return payload


def _validate_manual_rows(
    rows: Iterable[dict[str, Any]],
    *,
    default_as_of: str | None,
) -> tuple[list[MarketDataSecurityV1], list[MarketDataSecurityGapV1]]:
    securities: list[MarketDataSecurityV1] = []
    gaps: list[MarketDataSecurityGapV1] = []
    for position, row in enumerate(rows, start=1):
        ticker = row.get("ticker")
        ticker_text = (
            ticker if isinstance(ticker, str) and ticker else f"ROW-{position:02d}"
        )
        payload = _manual_row_payload(row, default_as_of=default_as_of)
        try:
            security = MarketDataSecurityV1.model_validate(payload, strict=True)
        except (ValidationError, TypeError):
            gaps.append(_gap(ticker_text, "manual_record_invalid"))
            continue
        securities.append(security)
    return securities, gaps


def _load_manual_json(text: str) -> tuple[list[dict[str, Any]], str | None]:
    try:
        document = json.loads(text)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        raise MarketDataError("market_data_manual_file_invalid") from None
    if not isinstance(document, dict):
        raise MarketDataError("market_data_manual_file_invalid")
    rows = document.get("securities")
    default_as_of = document.get("as_of_date")
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise MarketDataError("market_data_manual_file_invalid")
    if default_as_of is not None and not isinstance(default_as_of, str):
        raise MarketDataError("market_data_manual_file_invalid")
    return rows, default_as_of


def _load_manual_csv(text: str) -> tuple[list[dict[str, Any]], None]:
    try:
        reader = csv.DictReader(io.StringIO(text))
        if tuple(reader.fieldnames or ()) != _CSV_COLUMNS:
            raise MarketDataError("market_data_manual_file_invalid")
        rows: list[dict[str, Any]] = []
        for record in reader:
            row: dict[str, Any] = {}
            for column in _CSV_COLUMNS:
                raw = record.get(column)
                if raw is None or raw.strip() == "":
                    row[column] = None
                    continue
                value: Any = raw.strip()
                if column in {
                    "week_open",
                    "week_high",
                    "week_low",
                    "week_close",
                    "weekly_change_pct",
                    "market_cap",
                    "trailing_pe",
                }:
                    try:
                        value = float(value)
                    except ValueError:
                        raise MarketDataError(
                            "market_data_manual_file_invalid"
                        ) from None
                elif column == "week_volume":
                    try:
                        value = int(value)
                    except ValueError:
                        raise MarketDataError(
                            "market_data_manual_file_invalid"
                        ) from None
                row[column] = value
            rows.append(row)
    except csv.Error:
        raise MarketDataError("market_data_manual_file_invalid") from None
    return rows, None


def load_manual_market_data_file(path: str | Path) -> ManualMarketDataFile:
    """Load one manual JSON or CSV input file through the same contract."""

    file_path = Path(path)
    try:
        text = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        raise MarketDataError("market_data_manual_file_invalid") from None
    suffix = file_path.suffix.lower()
    if suffix == ".json":
        rows, default_as_of = _load_manual_json(text)
    elif suffix == ".csv":
        rows, default_as_of = _load_manual_csv(text)
    else:
        raise MarketDataError("market_data_manual_file_invalid")
    securities, gaps = _validate_manual_rows(rows, default_as_of=default_as_of)
    return ManualMarketDataFile(
        path=file_path.name,
        securities=tuple(securities),
        gaps=tuple(gaps),
    )


def merge_manual_first(
    manual_files: Iterable[ManualMarketDataFile],
    fetched: MarketDataFetchOutcome,
) -> MarketDataFetchOutcome:
    """Apply declared precedence: manual rows win over API quotes per ticker.

    Manual files are consumed in the given order and the first occurrence of
    a ticker wins.  A manual quote resolves the matching API gap; a surviving
    gap never carries a quote.
    """

    merged: dict[str, MarketDataSecurityV1] = {}
    manuals = list(manual_files)
    for manual in manuals:
        for security in manual.securities:
            merged.setdefault(security.ticker, security)
    for security in fetched.securities:
        merged.setdefault(security.ticker, security)
    gaps: dict[str, MarketDataSecurityGapV1] = {}
    for source_gaps in [manual.gaps for manual in manuals] + [fetched.gaps]:
        for gap in source_gaps:
            if gap.ticker not in merged:
                gaps.setdefault(gap.ticker, gap)
    return MarketDataFetchOutcome(
        securities=tuple(merged[ticker] for ticker in sorted(merged)),
        gaps=tuple(gaps[ticker] for ticker in sorted(gaps)),
    )


__all__ = [
    "MARKET_DATA_INPUT_DIR",
    "MarketDataError",
    "MarketDataFetchOutcome",
    "ManualMarketDataFile",
    "YAHOO_CHART_API_BASE",
    "YahooMarketDataAdapter",
    "load_manual_market_data_file",
    "merge_manual_first",
]
