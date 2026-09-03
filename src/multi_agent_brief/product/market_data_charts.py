"""Deterministic PNG chart projections for equity-periodic market data.

The Store snapshot remains the authority.  PNG files are replaceable reader
projections whose manifest binds every byte to the exact snapshot fingerprint
and renderer version.  The implementation uses only the Python standard
library so source installs and non-editable wheels produce identical bytes.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import struct
import zlib
from typing import Callable, Iterable

from multi_agent_brief.contracts.v2 import MarketDataSecurityV2, MarketDataSnapshotV2
from multi_agent_brief.sources.equity_universe import (
    DEFAULT_SOLAR_EQUITY_UNIVERSE,
    EquityPeriodicUniverse,
)

CHART_RENDERER_VERSION = "market-chart-png-v1"
CHART_OUTPUT_DIRECTORY = "output/charts/market_data"
CHART_MANIFEST_PATH = "output/intermediate/market_data_chart_manifest.json"
_WIDTH = 960
_HEIGHT = 480
_COLORS = (
    (37, 99, 235),
    (220, 38, 38),
    (5, 150, 105),
    (124, 58, 237),
    (234, 88, 12),
    (8, 145, 178),
    (79, 70, 229),
    (190, 18, 60),
    (101, 163, 13),
    (147, 51, 234),
    (15, 118, 110),
)


@dataclass(frozen=True)
class MarketChartAsset:
    chart_id: str
    title: str
    relative_path: str
    png_bytes: bytes

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.png_bytes).hexdigest()


class _Canvas:
    def __init__(self, width: int = _WIDTH, height: int = _HEIGHT) -> None:
        self.width = width
        self.height = height
        self.pixels = bytearray([255] * width * height * 3)

    def pixel(self, x: int, y: int, color: tuple[int, int, int]) -> None:
        if not (0 <= x < self.width and 0 <= y < self.height):
            return
        offset = (y * self.width + x) * 3
        self.pixels[offset : offset + 3] = bytes(color)

    def rect(
        self,
        x: int,
        y: int,
        width: int,
        height: int,
        color: tuple[int, int, int],
    ) -> None:
        for row in range(max(0, y), min(self.height, y + max(height, 0))):
            start = (row * self.width + max(0, x)) * 3
            end = (row * self.width + min(self.width, x + max(width, 0))) * 3
            if end > start:
                self.pixels[start:end] = bytes(color) * ((end - start) // 3)

    def line(
        self,
        x0: int,
        y0: int,
        x1: int,
        y1: int,
        color: tuple[int, int, int],
        *,
        thickness: int = 1,
    ) -> None:
        dx = abs(x1 - x0)
        sx = 1 if x0 < x1 else -1
        dy = -abs(y1 - y0)
        sy = 1 if y0 < y1 else -1
        error = dx + dy
        while True:
            radius = max(0, thickness // 2)
            self.rect(x0 - radius, y0 - radius, thickness, thickness, color)
            if x0 == x1 and y0 == y1:
                break
            twice = 2 * error
            if twice >= dy:
                error += dy
                x0 += sx
            if twice <= dx:
                error += dx
                y0 += sy

    def png(self) -> bytes:
        raw = bytearray()
        stride = self.width * 3
        for row in range(self.height):
            raw.append(0)
            start = row * stride
            raw.extend(self.pixels[start : start + stride])

        def chunk(kind: bytes, payload: bytes) -> bytes:
            return (
                struct.pack(">I", len(payload))
                + kind
                + payload
                + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
            )

        return (
            b"\x89PNG\r\n\x1a\n"
            + chunk(
                b"IHDR", struct.pack(">IIBBBBB", self.width, self.height, 8, 2, 0, 0, 0)
            )
            + chunk(b"IDAT", zlib.compress(bytes(raw), level=9))
            + chunk(b"IEND", b"")
        )


def _field_number(security: MarketDataSecurityV2, field_id: str) -> float | None:
    field = next((item for item in security.fields if item.field_id == field_id), None)
    if field is None or field.status != "available":
        return None
    return field.value_number


def _frame(canvas: _Canvas) -> tuple[int, int, int, int]:
    left, top, width, height = 70, 42, 840, 350
    canvas.rect(0, 0, canvas.width, canvas.height, (255, 255, 255))
    for index in range(5):
        y = top + round(height * index / 4)
        canvas.line(left, y, left + width, y, (226, 232, 240))
    canvas.line(left, top, left, top + height, (100, 116, 139), thickness=2)
    canvas.line(
        left, top + height, left + width, top + height, (100, 116, 139), thickness=2
    )
    return left, top, width, height


def _line_asset(
    *,
    chart_id: str,
    title: str,
    securities: Iterable[MarketDataSecurityV2],
    indexed: bool,
) -> MarketChartAsset | None:
    rows: list[tuple[str, list[float]]] = []
    for security in securities:
        if len(security.price_series) < 2:
            continue
        values = [
            point.adjusted_close
            if security.return_basis == "adjusted_close"
            and point.adjusted_close is not None
            else point.close
            for point in security.price_series
        ]
        if indexed:
            values = [value / values[0] * 100.0 for value in values]
        rows.append((security.ticker, values))
    if not rows:
        return None
    canvas = _Canvas()
    left, top, width, height = _frame(canvas)
    all_values = [value for _ticker, values in rows for value in values]
    low, high = min(all_values), max(all_values)
    pad = max((high - low) * 0.1, abs(high) * 0.01, 0.1)
    low -= pad
    high += pad
    for index, (_ticker, values) in enumerate(rows):
        points = []
        for position, value in enumerate(values):
            x = left + round(width * position / max(len(values) - 1, 1))
            y = top + round(height * (high - value) / max(high - low, 1e-9))
            points.append((x, y))
        for first, second in zip(points, points[1:]):
            canvas.line(*first, *second, _COLORS[index % len(_COLORS)], thickness=3)
        canvas.rect(76 + index * 70, 420, 48, 8, _COLORS[index % len(_COLORS)])
    return MarketChartAsset(
        chart_id=chart_id,
        title=title,
        relative_path=f"{CHART_OUTPUT_DIRECTORY}/{chart_id}.png",
        png_bytes=canvas.png(),
    )


def _bar_asset(
    *,
    chart_id: str,
    title: str,
    securities: Iterable[MarketDataSecurityV2],
    value: Callable[[MarketDataSecurityV2], float | None],
) -> MarketChartAsset | None:
    rows = [
        (item.ticker, number)
        for item in securities
        if (number := value(item)) is not None
    ]
    if not rows:
        return None
    canvas = _Canvas()
    left, top, width, height = _frame(canvas)
    low = min(0.0, *(number for _ticker, number in rows))
    high = max(0.0, *(number for _ticker, number in rows))
    span = max(high - low, 1e-9)
    zero = top + round(height * high / span)
    canvas.line(left, zero, left + width, zero, (71, 85, 105), thickness=2)
    slot = width / len(rows)
    bar_width = max(8, min(58, round(slot * 0.6)))
    for index, (_ticker, number) in enumerate(rows):
        x = left + round(slot * index + (slot - bar_width) / 2)
        y_value = top + round(height * (high - number) / span)
        y = min(zero, y_value)
        canvas.rect(
            x,
            y,
            bar_width,
            max(1, abs(zero - y_value)),
            _COLORS[index % len(_COLORS)] if number >= 0 else (220, 38, 38),
        )
    return MarketChartAsset(
        chart_id=chart_id,
        title=title,
        relative_path=f"{CHART_OUTPUT_DIRECTORY}/{chart_id}.png",
        png_bytes=canvas.png(),
    )


def _subject_asset(security: MarketDataSecurityV2 | None) -> MarketChartAsset | None:
    if security is None or len(security.price_series) < 2:
        return None
    canvas = _Canvas()
    left, top, width, height = _frame(canvas)
    prices = [point.close for point in security.price_series]
    volumes = [point.volume or 0 for point in security.price_series]
    low, high = min(prices), max(prices)
    pad = max((high - low) * 0.1, high * 0.01, 0.1)
    low -= pad
    high += pad
    max_volume = max(1, *volumes)
    points: list[tuple[int, int]] = []
    for index, point in enumerate(security.price_series):
        x = left + round(width * index / max(len(security.price_series) - 1, 1))
        bar_height = round(height * 0.36 * (point.volume or 0) / max_volume)
        canvas.rect(x - 9, top + height - bar_height, 18, bar_height, (191, 219, 254))
        y = top + round(height * (high - point.close) / max(high - low, 1e-9))
        points.append((x, y))
    for first, second in zip(points, points[1:]):
        canvas.line(*first, *second, (29, 78, 216), thickness=4)
    return MarketChartAsset(
        chart_id="subject-price-volume",
        title=f"{security.ticker} Close and Volume",
        relative_path=f"{CHART_OUTPUT_DIRECTORY}/subject-price-volume.png",
        png_bytes=canvas.png(),
    )


def _event_asset(snapshot: MarketDataSnapshotV2) -> MarketChartAsset | None:
    rows = [
        (event.event_id, event.event_day_excess_return_pct)
        for event in snapshot.events
        if event.event_day_excess_return_pct is not None
    ]
    if not rows:
        return None
    canvas = _Canvas()
    left, top, width, height = _frame(canvas)
    low = min(0.0, *(number for _event, number in rows))
    high = max(0.0, *(number for _event, number in rows))
    span = max(high - low, 1e-9)
    zero = top + round(height * high / span)
    canvas.line(left, zero, left + width, zero, (71, 85, 105), thickness=2)
    slot = width / len(rows)
    for index, (_event, number) in enumerate(rows):
        bar_width = min(90, round(slot * 0.55))
        x = left + round(slot * index + (slot - bar_width) / 2)
        y_value = top + round(height * (high - number) / span)
        canvas.rect(
            x,
            min(zero, y_value),
            bar_width,
            max(1, abs(zero - y_value)),
            (5, 150, 105) if number >= 0 else (220, 38, 38),
        )
    return MarketChartAsset(
        chart_id="event-excess-return",
        title="PR Event-day Excess Return",
        relative_path=f"{CHART_OUTPUT_DIRECTORY}/event-excess-return.png",
        png_bytes=canvas.png(),
    )


def render_market_chart_assets(
    snapshot: MarketDataSnapshotV2,
    *,
    universe: EquityPeriodicUniverse | None = None,
) -> tuple[MarketChartAsset, ...]:
    watchlist = universe or DEFAULT_SOLAR_EQUITY_UNIVERSE
    securities = list(snapshot.securities)
    primary = [item for item in securities if item.universe == "primary"]
    overseas = [item for item in securities if item.universe == "overseas"]
    subject = next(
        (
            item
            for ticker in watchlist.core_tickers
            for item in securities
            if item.ticker == ticker
        ),
        None,
    )
    if subject is None and primary:
        subject = primary[0]
    candidates: tuple[MarketChartAsset | None, ...] = (
        _line_asset(
            chart_id="primary-indexed-trend",
            title="Primary Equity Indexed Trend",
            securities=primary,
            indexed=True,
        ),
        _line_asset(
            chart_id="overseas-indexed-trend",
            title="Overseas Equity Indexed Trend",
            securities=overseas,
            indexed=True,
        ),
        _subject_asset(subject),
        _event_asset(snapshot),
        _bar_asset(
            chart_id="one-week-return",
            title="One-week Return Comparison",
            securities=securities,
            value=lambda item: _field_number(item, "return_1w_pct"),
        ),
        _bar_asset(
            chart_id="market-cap-usd",
            title="USD Market Capitalization",
            securities=securities,
            value=lambda item: _field_number(item, "market_cap_usd_millions"),
        ),
        _bar_asset(
            chart_id="ev-sales",
            title="EV/Sales Valuation Comparison",
            securities=securities,
            value=lambda item: _field_number(item, "ev_sales_ttm"),
        ),
        _bar_asset(
            chart_id="ps-ttm",
            title="P/S (TTM) Valuation Comparison",
            securities=securities,
            value=lambda item: _field_number(item, "ps_ttm"),
        ),
    )
    return tuple(asset for asset in candidates if asset is not None)


__all__ = [
    "CHART_MANIFEST_PATH",
    "CHART_OUTPUT_DIRECTORY",
    "CHART_RENDERER_VERSION",
    "MarketChartAsset",
    "render_market_chart_assets",
]
