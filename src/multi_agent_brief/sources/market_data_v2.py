"""Daily Yahoo observations and deterministic manual-first V2 merging.

This module is intentionally an acquisition/derivation layer only.  It never
writes the ControlStore.  The caller performs a read-only recording preflight,
acquires all provider responses, merges them with a frozen manual workbook, and
then submits exactly one strict V2 record to ``MarketDataService``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from typing import Any, Iterable, Mapping
import urllib.error
import urllib.request
from urllib.parse import quote, urlencode

from pydantic import ValidationError

from multi_agent_brief.contracts.v2 import (
    MarketDataBenchmarkV2,
    MarketDataConflictV2,
    MarketDataCorporateActionV2,
    MarketDataFieldValueV2,
    MarketDataFxRateV2,
    MarketDataGapV2,
    MarketDataSecurityV2,
    MarketDataSeriesPointV2,
)
from multi_agent_brief.core.fingerprint import canonical_fingerprint
from multi_agent_brief.sources.market_data import MarketDataError, _open_no_redirect
from multi_agent_brief.sources.equity_universe import (
    DEFAULT_SOLAR_EQUITY_UNIVERSE,
    PACKAGED_SOLAR_BENCHMARK_TICKER,
    infer_listing_group,
)

YAHOO_DAILY_CHART_BASE = "https://query1.finance.yahoo.com/v8/finance/chart"
YAHOO_QUOTE_BASE = "https://query1.finance.yahoo.com/v7/finance/quote"
YAHOO_V2_TIMEOUT_SECONDS = 30
YAHOO_V2_RESPONSE_BYTE_CAP = 4 * 1024 * 1024
YAHOO_V2_RANGE = "1y"
YAHOO_V2_INTERVAL = "1d"
YAHOO_V2_PROVIDER_ID = "yahoo_finance_chart_v2"

_UNIVERSE = DEFAULT_SOLAR_EQUITY_UNIVERSE.watchlist
_META = {
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
_FX_SYMBOLS = {"KRW": "KRW=X", "INR": "INR=X"}


@dataclass(frozen=True)
class MarketDataProviderOutcomeV2:
    securities: tuple[MarketDataSecurityV2, ...]
    benchmark: MarketDataBenchmarkV2 | None
    fx_rates: tuple[MarketDataFxRateV2, ...]
    gaps: tuple[MarketDataGapV2, ...]
    provider_ids: tuple[str, ...] = (YAHOO_V2_PROVIDER_ID,)


def _stable_id(prefix: str, payload: Mapping[str, object]) -> str:
    return f"{prefix}-{canonical_fingerprint(payload)[:24]}"


def _number(value: object) -> float | None:
    if type(value) not in {int, float}:
        return None
    number = float(value)
    if number != number or number in {float("inf"), float("-inf")}:
        return None
    return number


def _positive(value: object) -> float | None:
    number = _number(value)
    return number if number is not None and number > 0 else None


def _non_negative_int(value: object) -> int | None:
    number = _number(value)
    if number is None or number < 0 or not number.is_integer():
        return None
    return int(number)


def _field(
    *,
    field_id: str,
    value: float | str | None,
    unit: str,
    as_of: str,
    currency: str | None,
    origin: str,
    derivation: str,
    locator: str,
    source_sha256: str,
    reason_code: str | None = None,
    not_meaningful: bool = False,
) -> MarketDataFieldValueV2:
    if value is None:
        status = "not_meaningful" if not_meaningful else "unavailable"
        payload_value_number = None
        payload_value_text = None
        reason = reason_code or (
            "provider_value_not_meaningful"
            if not_meaningful
            else "provider_field_unavailable"
        )
    else:
        status = "available"
        payload_value_number = float(value) if type(value) in {int, float} else None
        payload_value_text = value if isinstance(value, str) else None
        reason = None
    return MarketDataFieldValueV2.model_validate(
        {
            "field_id": field_id,
            "status": status,
            "value_number": payload_value_number,
            "value_text": payload_value_text,
            "unit": unit,
            "as_of": as_of,
            "currency": currency,
            "data_origin": origin,
            "derivation": derivation,
            "source_locator": locator,
            "source_sha256": source_sha256,
            "reason_code": reason,
        },
        strict=True,
    )


def _gap(
    *,
    category: str,
    reason_code: str,
    severity: str,
    ticker: str | None = None,
    field_id: str | None = None,
    source_locator: str | None = None,
) -> MarketDataGapV2:
    identity = {
        "category": category,
        "reason_code": reason_code,
        "ticker": ticker,
        "field_id": field_id,
        "source_locator": source_locator,
    }
    return MarketDataGapV2.model_validate(
        {
            "gap_id": _stable_id("market-gap", identity),
            "severity": severity,
            "category": category,
            "ticker": ticker,
            "field_id": field_id,
            "source_locator": source_locator,
            "reason_code": reason_code,
        },
        strict=True,
    )


class YahooMarketDataV2Adapter:
    """Bounded no-redirect Yahoo daily/quote reader with value-free failures."""

    provider_id = YAHOO_V2_PROVIDER_ID

    def fetch(
        self,
        symbols: Iterable[str],
        *,
        as_of_date: str,
        core_tickers: Iterable[str] | None = None,
        benchmark_ticker: str | None = PACKAGED_SOLAR_BENCHMARK_TICKER,
    ) -> MarketDataProviderOutcomeV2:
        requested = tuple(symbols)
        core = tuple(
            core_tickers
            if core_tickers is not None
            else DEFAULT_SOLAR_EQUITY_UNIVERSE.core_tickers
        )
        quote_rows = self._fetch_quotes(requested)
        securities: list[MarketDataSecurityV2] = []
        gaps: list[MarketDataGapV2] = []
        for symbol in requested:
            try:
                document, digest, locator = self._request_chart(symbol)
                security = self._parse_security(
                    symbol,
                    document,
                    response_sha256=digest,
                    source_locator=locator,
                    as_of_date=as_of_date,
                    quote_row=quote_rows.get(symbol),
                )
            except MarketDataError as exc:
                gaps.append(
                    _gap(
                        category="provider_unavailable",
                        reason_code=str(exc),
                        severity=(
                            "blocking" if symbol in core else "warning"
                        ),
                        ticker=symbol,
                        field_id="price_series",
                        source_locator=f"yahoo:chart:{symbol}",
                    )
                )
                continue
            securities.append(security)

        benchmark: MarketDataBenchmarkV2 | None = None
        if benchmark_ticker:
            try:
                document, digest, locator = self._request_chart(benchmark_ticker)
                benchmark = self._parse_benchmark(
                    document,
                    ticker=benchmark_ticker,
                    response_sha256=digest,
                    source_locator=locator,
                    as_of_date=as_of_date,
                )
            except MarketDataError as exc:
                gaps.append(
                    _gap(
                        category="provider_unavailable",
                        reason_code=str(exc),
                        severity="warning",
                        ticker=benchmark_ticker,
                        field_id="benchmark_series",
                        source_locator=f"yahoo:chart:{benchmark_ticker}",
                    )
                )

        fx_rates: list[MarketDataFxRateV2] = []
        for currency, symbol in _FX_SYMBOLS.items():
            try:
                document, digest, locator = self._request_chart(symbol)
                points, _actions, _meta = self._chart_parts(
                    symbol,
                    document,
                    response_sha256=digest,
                    source_locator=locator,
                    as_of_date=as_of_date,
                )
                rate = points[-1].close
                fx_rates.append(
                    MarketDataFxRateV2.model_validate(
                        {
                            "base_currency": currency,
                            "quote_currency": "USD",
                            "units_per_usd": rate,
                            "as_of": points[-1].date,
                            "data_origin": "yahoo_chart_api",
                            "source_locator": locator,
                            "source_sha256": digest,
                        },
                        strict=True,
                    )
                )
            except (MarketDataError, ValidationError, ValueError):
                gaps.append(
                    _gap(
                        category="provider_unavailable",
                        reason_code="provider_fx_unavailable",
                        severity="warning",
                        ticker=None,
                        field_id=f"fx_{currency}_usd",
                        source_locator=f"yahoo:chart:{symbol}",
                    )
                )

        return MarketDataProviderOutcomeV2(
            securities=tuple(sorted(securities, key=lambda item: item.ticker)),
            benchmark=benchmark,
            fx_rates=tuple(
                sorted(
                    fx_rates, key=lambda item: (item.base_currency, item.quote_currency)
                )
            ),
            gaps=tuple(sorted(gaps, key=lambda item: item.gap_id)),
        )

    @staticmethod
    def _request(url: str) -> tuple[dict[str, object], str]:
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "briefloop-market-data/2.0",
            },
            method="GET",
        )
        try:
            with _open_no_redirect(
                request, timeout=YAHOO_V2_TIMEOUT_SECONDS
            ) as response:
                status_code = int(getattr(response, "status", 200))
                body = response.read(YAHOO_V2_RESPONSE_BYTE_CAP + 1)
        except urllib.error.HTTPError as exc:
            status_code = int(exc.code)
            body = exc.read(YAHOO_V2_RESPONSE_BYTE_CAP + 1)
        except Exception:
            raise MarketDataError("transport_unavailable") from None
        if status_code != 200:
            raise MarketDataError("http_error")
        if not body or len(body) > YAHOO_V2_RESPONSE_BYTE_CAP:
            raise MarketDataError("response_invalid")
        try:
            document = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
            raise MarketDataError("response_invalid") from None
        if not isinstance(document, dict):
            raise MarketDataError("response_invalid")
        return document, hashlib.sha256(body).hexdigest()

    def _request_chart(self, symbol: str) -> tuple[dict[str, object], str, str]:
        query = urlencode(
            {
                "range": YAHOO_V2_RANGE,
                "interval": YAHOO_V2_INTERVAL,
                "events": "div,splits,capitalGains",
                "includeAdjustedClose": "true",
            }
        )
        locator = f"{YAHOO_DAILY_CHART_BASE}/{quote(symbol, safe='')}?{query}"
        document, digest = self._request(locator)
        return document, digest, locator

    def _fetch_quotes(
        self, symbols: tuple[str, ...]
    ) -> dict[str, tuple[dict[str, object], str, str]]:
        if not symbols:
            return {}
        locator = f"{YAHOO_QUOTE_BASE}?{urlencode({'symbols': ','.join(symbols)})}"
        try:
            document, digest = self._request(locator)
            response = document.get("quoteResponse")
            rows = response.get("result") if isinstance(response, dict) else None
            if not isinstance(rows, list):
                return {}
            return {
                str(row["symbol"]): (row, digest, locator)
                for row in rows
                if isinstance(row, dict) and isinstance(row.get("symbol"), str)
            }
        except MarketDataError:
            return {}

    @staticmethod
    def _result(document: Mapping[str, object]) -> dict[str, object]:
        chart = document.get("chart")
        if not isinstance(chart, dict) or chart.get("error") is not None:
            raise MarketDataError("response_invalid")
        values = chart.get("result")
        if (
            not isinstance(values, list)
            or len(values) != 1
            or not isinstance(values[0], dict)
        ):
            raise MarketDataError("response_invalid")
        return values[0]

    def _chart_parts(
        self,
        symbol: str,
        document: Mapping[str, object],
        *,
        response_sha256: str,
        source_locator: str,
        as_of_date: str,
    ) -> tuple[
        list[MarketDataSeriesPointV2],
        list[MarketDataCorporateActionV2],
        dict[str, object],
    ]:
        result = self._result(document)
        meta = result.get("meta")
        timestamps = result.get("timestamp")
        indicators = result.get("indicators")
        if (
            not isinstance(meta, dict)
            or not isinstance(timestamps, list)
            or not isinstance(indicators, dict)
        ):
            raise MarketDataError("response_invalid")
        quotes = indicators.get("quote")
        adjusted = indicators.get("adjclose")
        if (
            not isinstance(quotes, list)
            or len(quotes) != 1
            or not isinstance(quotes[0], dict)
        ):
            raise MarketDataError("response_invalid")
        quote_values = quotes[0]
        closes = quote_values.get("close")
        volumes = quote_values.get("volume")
        adjusted_values: object = None
        if (
            isinstance(adjusted, list)
            and len(adjusted) == 1
            and isinstance(adjusted[0], dict)
        ):
            adjusted_values = adjusted[0].get("adjclose")
        if not isinstance(closes, list) or len(closes) != len(timestamps):
            raise MarketDataError("response_invalid")
        if not isinstance(volumes, list) or len(volumes) != len(timestamps):
            volumes = [None] * len(timestamps)
        if not isinstance(adjusted_values, list) or len(adjusted_values) != len(
            timestamps
        ):
            adjusted_values = [None] * len(timestamps)
        cutoff = date.fromisoformat(as_of_date)
        points: list[MarketDataSeriesPointV2] = []
        for index, timestamp in enumerate(timestamps):
            if type(timestamp) not in {int, float}:
                continue
            point_date = datetime.fromtimestamp(
                float(timestamp), tz=timezone.utc
            ).date()
            close = _positive(closes[index])
            if close is None or point_date > cutoff:
                continue
            adjusted_close = _positive(adjusted_values[index])
            points.append(
                MarketDataSeriesPointV2.model_validate(
                    {
                        "date": point_date.isoformat(),
                        "close": close,
                        "adjusted_close": adjusted_close,
                        "volume": _non_negative_int(volumes[index]),
                        "data_origin": "yahoo_chart_api",
                        "source_locator": f"{source_locator}#timestamp={int(float(timestamp))}",
                        "source_sha256": response_sha256,
                    },
                    strict=True,
                )
            )
        deduped = {item.date: item for item in points}
        points = [deduped[key] for key in sorted(deduped)]
        if not points:
            raise MarketDataError("symbol_data_missing")

        actions: list[MarketDataCorporateActionV2] = []
        event_map = result.get("events")
        if isinstance(event_map, dict):
            for collection, action_type in (
                ("dividends", "dividend"),
                ("splits", "split"),
                ("capitalGains", "capital_gain"),
            ):
                rows = event_map.get(collection)
                if not isinstance(rows, dict):
                    continue
                for key, row in rows.items():
                    if not isinstance(row, dict):
                        continue
                    timestamp = row.get("date")
                    if type(timestamp) not in {int, float}:
                        try:
                            timestamp = float(key)
                        except (TypeError, ValueError):
                            continue
                    action_date = datetime.fromtimestamp(
                        float(timestamp), tz=timezone.utc
                    ).date()
                    if action_date > cutoff:
                        continue
                    numerator = (
                        _positive(row.get("numerator"))
                        if action_type == "split"
                        else None
                    )
                    denominator = (
                        _positive(row.get("denominator"))
                        if action_type == "split"
                        else None
                    )
                    value = (
                        numerator / denominator
                        if numerator is not None and denominator is not None
                        else _positive(row.get("amount"))
                    )
                    if value is None:
                        continue
                    action_payload = {
                        "symbol": symbol,
                        "date": action_date.isoformat(),
                        "type": action_type,
                        "value": value,
                    }
                    actions.append(
                        MarketDataCorporateActionV2.model_validate(
                            {
                                "action_id": _stable_id(
                                    "market-action", action_payload
                                ),
                                "date": action_date.isoformat(),
                                "action_type": action_type,
                                "value": value,
                                "currency": None
                                if action_type == "split"
                                else meta.get("currency"),
                                "split_numerator": numerator,
                                "split_denominator": denominator,
                                "data_origin": "yahoo_chart_api",
                                "source_locator": f"{source_locator}#event={key}",
                                "source_sha256": response_sha256,
                            },
                            strict=True,
                        )
                    )
        return (
            points,
            sorted(actions, key=lambda item: (item.date, item.action_id)),
            meta,
        )

    def _parse_security(
        self,
        symbol: str,
        document: Mapping[str, object],
        *,
        response_sha256: str,
        source_locator: str,
        as_of_date: str,
        quote_row: tuple[dict[str, object], str, str] | None,
    ) -> MarketDataSecurityV2:
        if symbol in _META:
            display_name, expected_exchange, expected_currency, universe = _META[symbol]
        else:
            display_name = symbol
            expected_exchange = "UNKNOWN"
            expected_currency = "USD"
            universe = infer_listing_group(symbol)
        points, actions, meta = self._chart_parts(
            symbol,
            document,
            response_sha256=response_sha256,
            source_locator=source_locator,
            as_of_date=as_of_date,
        )
        currency = (
            meta.get("currency")
            if isinstance(meta.get("currency"), str)
            else expected_currency
        )
        exchange = (
            meta.get("fullExchangeName")
            if isinstance(meta.get("fullExchangeName"), str)
            else expected_exchange
        )
        complete_adjusted = all(item.adjusted_close is not None for item in points)
        basis = "adjusted_close" if complete_adjusted else "close"
        fields = self._derived_return_fields(points, basis=basis, currency=currency)
        if quote_row is None:
            quote_values: dict[str, object] = {}
            quote_sha = response_sha256
            quote_locator = source_locator
        else:
            quote_values, quote_sha, quote_locator = quote_row
        fields.extend(
            self._quote_fields(
                quote_values,
                as_of=points[-1].date,
                currency=currency,
                source_sha256=quote_sha,
                source_locator=quote_locator,
            )
        )
        by_field = {item.field_id: item for item in fields}
        return MarketDataSecurityV2.model_validate(
            {
                "ticker": symbol,
                "display_name": display_name,
                "universe": universe,
                "exchange": exchange,
                "currency": currency,
                "return_basis": basis,
                "price_series": [
                    item.model_dump(mode="json", exclude_unset=False) for item in points
                ],
                "corporate_actions": [
                    item.model_dump(mode="json", exclude_unset=False)
                    for item in actions
                ],
                "fields": [
                    by_field[key].model_dump(mode="json", exclude_unset=False)
                    for key in sorted(by_field)
                ],
            },
            strict=True,
        )

    @staticmethod
    def _basis_value(point: MarketDataSeriesPointV2, basis: str) -> float:
        if basis == "adjusted_close" and point.adjusted_close is not None:
            return point.adjusted_close
        return point.close

    def _derived_return_fields(
        self,
        points: list[MarketDataSeriesPointV2],
        *,
        basis: str,
        currency: str,
    ) -> list[MarketDataFieldValueV2]:
        latest = points[-1]
        latest_value = self._basis_value(latest, basis)

        def base_for(
            target: date, *, strictly_before: bool = False
        ) -> MarketDataSeriesPointV2 | None:
            eligible = (
                [
                    item
                    for item in points
                    if date.fromisoformat(item.date) < target
                    if strictly_before
                ]
                if strictly_before
                else [
                    item for item in points if date.fromisoformat(item.date) <= target
                ]
            )
            return eligible[-1] if eligible else None

        latest_date = date.fromisoformat(latest.date)
        one_week = base_for(latest_date - timedelta(days=7))
        one_month = base_for(latest_date - timedelta(days=30))
        year_start = date(latest_date.year, 1, 1)
        ytd_base = base_for(year_start, strictly_before=True)
        if ytd_base is None:
            current_year = [
                item for item in points if date.fromisoformat(item.date) >= year_start
            ]
            ytd_base = current_year[0] if current_year else None
        locator = f"{points[0].source_locator};{latest.source_locator}"
        fields = [
            _field(
                field_id="latest_close_local",
                value=latest.close,
                unit="price",
                as_of=latest.date,
                currency=currency,
                origin="yahoo_chart_api",
                derivation="direct",
                locator=latest.source_locator,
                source_sha256=latest.source_sha256,
            )
        ]
        for field_id, base in (
            ("return_1w_pct", one_week),
            ("return_1m_pct", one_month),
            ("return_ytd_pct", ytd_base),
        ):
            value = None
            if base is not None:
                base_value = self._basis_value(base, basis)
                value = round((latest_value / base_value - 1.0) * 100.0, 6)
            fields.append(
                _field(
                    field_id=field_id,
                    value=value,
                    unit="percent",
                    as_of=latest.date,
                    currency=None,
                    origin="derived",
                    derivation="recomputed",
                    locator=locator,
                    source_sha256=latest.source_sha256,
                    reason_code="insufficient_price_history" if value is None else None,
                )
            )
        return fields

    @staticmethod
    def _quote_fields(
        values: Mapping[str, object],
        *,
        as_of: str,
        currency: str,
        source_sha256: str,
        source_locator: str,
    ) -> list[MarketDataFieldValueV2]:
        market_cap = _positive(values.get("marketCap"))
        trailing_raw = _number(values.get("trailingPE"))
        ev_sales_raw = _number(values.get("enterpriseToRevenue"))
        ev_ebitda_raw = _number(values.get("enterpriseToEbitda"))
        return [
            _field(
                field_id="market_cap_local_millions",
                value=None if market_cap is None else market_cap / 1_000_000.0,
                unit="millions",
                as_of=as_of,
                currency=currency,
                origin="yahoo_quote_summary",
                derivation="provider_fill",
                locator=source_locator,
                source_sha256=source_sha256,
            ),
            _field(
                field_id="pe_ttm",
                value=trailing_raw
                if trailing_raw is not None and trailing_raw > 0
                else None,
                unit="multiple",
                as_of=as_of,
                currency=None,
                origin="yahoo_quote_summary",
                derivation="provider_fill",
                locator=source_locator,
                source_sha256=source_sha256,
                not_meaningful=trailing_raw is not None and trailing_raw <= 0,
            ),
            _field(
                field_id="ev_sales_ttm",
                value=ev_sales_raw
                if ev_sales_raw is not None and ev_sales_raw > 0
                else None,
                unit="multiple",
                as_of=as_of,
                currency=None,
                origin="yahoo_quote_summary",
                derivation="provider_fill",
                locator=source_locator,
                source_sha256=source_sha256,
                not_meaningful=ev_sales_raw is not None and ev_sales_raw <= 0,
            ),
            _field(
                field_id="ev_ebitda_ttm",
                value=ev_ebitda_raw
                if ev_ebitda_raw is not None and ev_ebitda_raw > 0
                else None,
                unit="multiple",
                as_of=as_of,
                currency=None,
                origin="yahoo_quote_summary",
                derivation="provider_fill",
                locator=source_locator,
                source_sha256=source_sha256,
                not_meaningful=ev_ebitda_raw is not None and ev_ebitda_raw <= 0,
            ),
        ]

    def _parse_benchmark(
        self,
        document: Mapping[str, object],
        *,
        ticker: str,
        response_sha256: str,
        source_locator: str,
        as_of_date: str,
    ) -> MarketDataBenchmarkV2:
        points, _actions, meta = self._chart_parts(
            ticker,
            document,
            response_sha256=response_sha256,
            source_locator=source_locator,
            as_of_date=as_of_date,
        )
        complete_adjusted = all(item.adjusted_close is not None for item in points)
        display_name = (
            "Invesco Solar ETF" if ticker == PACKAGED_SOLAR_BENCHMARK_TICKER else ticker
        )
        return MarketDataBenchmarkV2.model_validate(
            {
                "ticker": ticker,
                "display_name": display_name,
                "currency": meta.get("currency") or "USD",
                "return_basis": "adjusted_close" if complete_adjusted else "close",
                "price_series": [
                    item.model_dump(mode="json", exclude_unset=False) for item in points
                ],
            },
            strict=True,
        )


def _merge_series(
    manual: list[MarketDataSeriesPointV2],
    provider: list[MarketDataSeriesPointV2],
) -> list[MarketDataSeriesPointV2]:
    by_date = {item.date: item for item in provider}
    for item in manual:
        provider_point = by_date.get(item.date)
        adjusted = None if provider_point is None else provider_point.adjusted_close
        by_date[item.date] = MarketDataSeriesPointV2.model_validate(
            {
                **item.model_dump(mode="json", exclude_unset=False),
                "adjusted_close": adjusted,
            },
            strict=True,
        )
    return [by_date[key] for key in sorted(by_date)][-400:]


def _merge_security(
    manual: MarketDataSecurityV2,
    provider: MarketDataSecurityV2,
    conflicts: list[MarketDataConflictV2],
) -> MarketDataSecurityV2:
    series = _merge_series(manual.price_series, provider.price_series)
    manual_fields = {item.field_id: item for item in manual.fields}
    provider_fields = {item.field_id: item for item in provider.fields}
    merged_fields: dict[str, MarketDataFieldValueV2] = {}
    for field_id in sorted(set(manual_fields) | set(provider_fields)):
        manual_field = manual_fields.get(field_id)
        provider_field = provider_fields.get(field_id)
        if manual_field is None:
            assert provider_field is not None
            merged_fields[field_id] = provider_field
            continue
        if provider_field is None or manual_field.status == "available":
            merged_fields[field_id] = manual_field
        elif provider_field.status == "available":
            merged_fields[field_id] = MarketDataFieldValueV2.model_validate(
                {
                    **provider_field.model_dump(mode="json", exclude_unset=False),
                    "derivation": "provider_fill",
                },
                strict=True,
            )
        else:
            merged_fields[field_id] = manual_field
        if (
            manual_field.status == "available"
            and provider_field is not None
            and provider_field.status == "available"
            and manual_field.value_number is not None
            and provider_field.value_number is not None
        ):
            tolerance = max(0.01, abs(manual_field.value_number) * 0.005)
            if abs(manual_field.value_number - provider_field.value_number) > tolerance:
                identity = {
                    "ticker": manual.ticker,
                    "field_id": field_id,
                    "manual": manual_field.value_number,
                    "provider": provider_field.value_number,
                }
                conflicts.append(
                    MarketDataConflictV2.model_validate(
                        {
                            "conflict_id": _stable_id("market-conflict", identity),
                            "severity": "warning",
                            "category": "manual_provider_value_mismatch",
                            "ticker": manual.ticker,
                            "field_id": field_id,
                            "manual_value": manual_field.value_number,
                            "provider_value": provider_field.value_number,
                            "resolution": "manual_wins",
                            "source_locator": (
                                f"{manual_field.source_locator};{provider_field.source_locator}"
                            ),
                        },
                        strict=True,
                    )
                )
    actions = {
        item.action_id: item
        for item in (*provider.corporate_actions, *manual.corporate_actions)
    }
    complete_adjusted = all(item.adjusted_close is not None for item in series)
    return MarketDataSecurityV2.model_validate(
        {
            **manual.model_dump(mode="json", exclude_unset=False),
            "return_basis": "adjusted_close" if complete_adjusted else "close",
            "price_series": [
                item.model_dump(mode="json", exclude_unset=False) for item in series
            ],
            "corporate_actions": [
                actions[key].model_dump(mode="json", exclude_unset=False)
                for key in sorted(actions, key=lambda key: (actions[key].date, key))
            ],
            "fields": [
                merged_fields[key].model_dump(mode="json", exclude_unset=False)
                for key in sorted(merged_fields)
            ],
        },
        strict=True,
    )


def _with_usd_fields(
    security: MarketDataSecurityV2,
    fx_rates: Mapping[tuple[str, str], MarketDataFxRateV2],
) -> MarketDataSecurityV2:
    fields = {item.field_id: item for item in security.fields}
    rate_record = fx_rates.get((security.currency, "USD"))
    rate = (
        1.0
        if security.currency == "USD"
        else (None if rate_record is None else rate_record.units_per_usd)
    )
    for local_id, usd_id, unit in (
        ("latest_close_local", "latest_close_usd", "price"),
        ("market_cap_local_millions", "market_cap_usd_millions", "millions_usd"),
    ):
        current = fields.get(usd_id)
        local = fields.get(local_id)
        if current is not None and current.status == "available":
            continue
        value = None
        locator = "market-data:conversion-unavailable"
        digest = "0" * 64
        as_of = security.price_series[-1].date
        if local is not None:
            as_of = local.as_of or as_of
            locator = local.source_locator
            digest = local.source_sha256
            if (
                local.status == "available"
                and local.value_number is not None
                and rate is not None
            ):
                value = local.value_number / rate
                if rate_record is not None:
                    locator = f"{locator};{rate_record.source_locator}"
                    digest = canonical_fingerprint(
                        {
                            "local_sha256": local.source_sha256,
                            "fx_sha256": rate_record.source_sha256,
                            "base_currency": security.currency,
                        }
                    )
        fields[usd_id] = _field(
            field_id=usd_id,
            value=value,
            unit=unit,
            as_of=as_of,
            currency="USD",
            origin="derived",
            derivation="converted",
            locator=locator,
            source_sha256=digest,
            reason_code="fx_rate_unavailable" if value is None else None,
        )
    return MarketDataSecurityV2.model_validate(
        {
            **security.model_dump(mode="json", exclude_unset=False),
            "fields": [
                fields[key].model_dump(mode="json", exclude_unset=False)
                for key in sorted(fields)
            ],
        },
        strict=True,
    )


def merge_manual_workbook_with_yahoo(
    manual_payload: Mapping[str, object],
    provider: MarketDataProviderOutcomeV2,
) -> dict[str, object]:
    """Merge provider fills into one strict workbook payload; manual values win."""

    manual_securities = {
        item.ticker: item
        for item in (
            MarketDataSecurityV2.model_validate(value, strict=True)
            for value in manual_payload.get("securities", [])
        )
    }
    provider_securities = {item.ticker: item for item in provider.securities}
    conflicts = [
        MarketDataConflictV2.model_validate(value, strict=True)
        for value in manual_payload.get("conflicts", [])
    ]
    merge_tickers = tuple(manual_payload.get("universe_tickers") or _UNIVERSE)
    securities: list[MarketDataSecurityV2] = []
    for ticker in merge_tickers:
        manual = manual_securities.get(ticker)
        fetched = provider_securities.get(ticker)
        if manual is not None and fetched is not None:
            securities.append(_merge_security(manual, fetched, conflicts))
        elif manual is not None:
            securities.append(manual)
        elif fetched is not None:
            securities.append(fetched)

    resolved_tickers = {item.ticker for item in securities}
    manual_gaps = [
        MarketDataGapV2.model_validate(value, strict=True)
        for value in manual_payload.get("gaps", [])
    ]
    gaps = [
        item
        for item in manual_gaps
        if not (
            item.category == "missing_security_series"
            and item.ticker in resolved_tickers
        )
    ]
    for item in provider.gaps:
        if not (item.field_id == "price_series" and item.ticker in resolved_tickers):
            gaps.append(item)

    manual_fx = {
        (item.base_currency, item.quote_currency): item
        for item in (
            MarketDataFxRateV2.model_validate(value, strict=True)
            for value in manual_payload.get("fx_rates", [])
        )
    }
    for item in provider.fx_rates:
        manual_fx.setdefault((item.base_currency, item.quote_currency), item)

    securities = [_with_usd_fields(item, manual_fx) for item in securities]

    benchmark_value = manual_payload.get("benchmark")
    manual_benchmark = (
        None
        if benchmark_value is None
        else MarketDataBenchmarkV2.model_validate(benchmark_value, strict=True)
    )
    benchmark = provider.benchmark if manual_benchmark is None else manual_benchmark
    if manual_benchmark is not None and provider.benchmark is not None:
        series = _merge_series(
            manual_benchmark.price_series,
            provider.benchmark.price_series,
        )
        complete_adjusted = all(item.adjusted_close is not None for item in series)
        benchmark = MarketDataBenchmarkV2.model_validate(
            {
                **manual_benchmark.model_dump(mode="json", exclude_unset=False),
                "return_basis": "adjusted_close" if complete_adjusted else "close",
                "price_series": [
                    item.model_dump(mode="json", exclude_unset=False) for item in series
                ],
            },
            strict=True,
        )

    provider_ids = sorted(
        set(str(value) for value in manual_payload.get("provider_ids", []))
        | set(provider.provider_ids)
    )
    unique_gaps = {item.gap_id: item for item in gaps}
    unique_conflicts = {item.conflict_id: item for item in conflicts}
    return {
        **dict(manual_payload),
        "provider_ids": provider_ids,
        "securities": [
            item.model_dump(mode="json", exclude_unset=False)
            for item in sorted(securities, key=lambda item: item.ticker)
        ],
        "benchmark": (
            None
            if benchmark is None
            else benchmark.model_dump(mode="json", exclude_unset=False)
        ),
        "fx_rates": [
            manual_fx[key].model_dump(mode="json", exclude_unset=False)
            for key in sorted(manual_fx)
        ],
        "gaps": [
            unique_gaps[key].model_dump(mode="json", exclude_unset=False)
            for key in sorted(unique_gaps)
        ],
        "conflicts": [
            unique_conflicts[key].model_dump(mode="json", exclude_unset=False)
            for key in sorted(unique_conflicts)
        ],
        "derivation_version": "solar-market-data-v2-manual-first-yahoo-fill",
    }


__all__ = [
    "MarketDataProviderOutcomeV2",
    "YAHOO_DAILY_CHART_BASE",
    "YAHOO_QUOTE_BASE",
    "YAHOO_V2_PROVIDER_ID",
    "YahooMarketDataV2Adapter",
    "merge_manual_workbook_with_yahoo",
]
