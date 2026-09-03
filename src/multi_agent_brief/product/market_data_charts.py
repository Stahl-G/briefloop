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

CHART_RENDERER_VERSION = "market-chart-png-v3"
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


# Compact 5x7 bitmap font (ASCII uppercase + digits + punctuation) so
# chart legends and axis labels stay dependency-free and deterministic.
_FONT_5X7: dict[str, tuple[str, ...]] = {
    "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
    "B": ("11110", "10001", "10001", "11110", "10001", "10001", "11110"),
    "C": ("01110", "10001", "10000", "10000", "10000", "10001", "01110"),
    "D": ("11110", "10001", "10001", "10001", "10001", "10001", "11110"),
    "E": ("11111", "10000", "10000", "11110", "10000", "10000", "11111"),
    "F": ("11111", "10000", "10000", "11110", "10000", "10000", "10000"),
    "G": ("01110", "10001", "10000", "10111", "10001", "10001", "01110"),
    "H": ("10001", "10001", "10001", "11111", "10001", "10001", "10001"),
    "I": ("11111", "00100", "00100", "00100", "00100", "00100", "11111"),
    "J": ("00111", "00010", "00010", "00010", "00010", "10010", "01100"),
    "K": ("10001", "10010", "10100", "11000", "10100", "10010", "10001"),
    "L": ("10000", "10000", "10000", "10000", "10000", "10000", "11111"),
    "M": ("10001", "11011", "10101", "10101", "10001", "10001", "10001"),
    "N": ("10001", "10001", "11001", "10101", "10011", "10001", "10001"),
    "O": ("01110", "10001", "10001", "10001", "10001", "10001", "01110"),
    "P": ("11110", "10001", "10001", "11110", "10000", "10000", "10000"),
    "Q": ("01110", "10001", "10001", "10001", "10101", "10011", "01111"),
    "R": ("11110", "10001", "10001", "11110", "10100", "10010", "10001"),
    "S": ("01111", "10000", "10000", "01110", "00001", "00001", "11110"),
    "T": ("11111", "00100", "00100", "00100", "00100", "00100", "00100"),
    "U": ("10001", "10001", "10001", "10001", "10001", "10001", "01110"),
    "V": ("10001", "10001", "10001", "10001", "10001", "01010", "00100"),
    "W": ("10001", "10001", "10001", "10101", "10101", "11011", "10001"),
    "X": ("10001", "10001", "01010", "00100", "01010", "10001", "10001"),
    "Y": ("10001", "10001", "01010", "00100", "00100", "00100", "00100"),
    "Z": ("11111", "00001", "00010", "00100", "01000", "10000", "11111"),
    "0": ("01110", "10001", "10011", "10101", "11001", "10001", "01110"),
    "1": ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
    "2": ("01110", "10001", "00001", "00110", "01000", "10000", "11111"),
    "3": ("11110", "00001", "00001", "01110", "00001", "00001", "11110"),
    "4": ("00010", "00110", "01010", "10010", "11111", "00010", "00010"),
    "5": ("11111", "10000", "11110", "00001", "00001", "10001", "01110"),
    "6": ("00110", "01000", "10000", "11110", "10001", "10001", "01110"),
    "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    "8": ("01110", "10001", "10001", "01110", "10001", "10001", "01110"),
    "9": ("01110", "10001", "10001", "01111", "00001", "00010", "01100"),
    ".": ("00000", "00000", "00000", "00000", "00000", "00110", "00110"),
    ",": ("00000", "00000", "00000", "00000", "00110", "00110", "01100"),
    "-": ("00000", "00000", "00000", "01110", "00000", "00000", "00000"),
    "+": ("00000", "00100", "00100", "11111", "00100", "00100", "00000"),
    "%": ("11001", "11010", "00010", "00100", "01000", "01011", "10011"),
    "/": ("00001", "00010", "00010", "00100", "01000", "01000", "10000"),
    ":": ("00000", "00110", "00110", "00000", "00110", "00110", "00000"),
    "(": ("00010", "00100", "01000", "01000", "01000", "00100", "00010"),
    ")": ("01000", "00100", "00010", "00010", "00010", "00100", "01000"),
    " ": ("00000", "00000", "00000", "00000", "00000", "00000", "00000"),
}

_FONT_W = 5
_FONT_H = 7
_FONT_TRACK = 1  # horizontal tracking in pixels


def _text_width(text: str) -> int:
    return max(len(text) * (_FONT_W + _FONT_TRACK) - _FONT_TRACK, 0)


def _draw_text(
    canvas: _Canvas,
    x: int,
    y: int,
    text: str,
    color: tuple[int, int, int],
) -> None:
    """Draw one ASCII string with the built-in 5x7 font (top-left at x,y)."""

    cursor = x
    for char in text.upper():
        glyph = _FONT_5X7.get(char)
        if glyph is None:
            cursor += _FONT_W + _FONT_TRACK
            continue
        for row_index, row in enumerate(glyph):
            for column, bit in enumerate(row):
                if bit == "1":
                    canvas.rect(
                        cursor + column,
                        y + row_index,
                        1,
                        1,
                        color,
                    )
        cursor += _FONT_W + _FONT_TRACK


def _draw_legend(
    canvas: _Canvas,
    entries: list[tuple[str, tuple[int, int, int]]],
    *,
    x: int,
    y: int,
) -> None:
    """One-row legend of colored swatches with labels."""

    cursor = x
    for label, color in entries:
        canvas.rect(cursor, y + 2, 16, 4, color)
        _draw_text(canvas, cursor + 22, y, label, (30, 41, 59))
        cursor += 22 + _text_width(label) + 24


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
    _draw_legend(
        canvas,
        [
            (ticker, _COLORS[index % len(_COLORS)])
            for index, (ticker, _values) in enumerate(rows)
        ],
        x=left,
        y=14,
    )
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
    # Widen the bottom margin so ticker labels fit under the axis.
    left, top, width, height = 70, 42, 840, 316
    canvas.rect(0, 0, canvas.width, canvas.height, (255, 255, 255))
    for index in range(5):
        y = top + round(height * index / 4)
        canvas.line(left, y, left + width, y, (226, 232, 240))
    canvas.line(left, top, left, top + height, (100, 116, 139), thickness=2)
    axis_y = top + height
    canvas.line(left, axis_y, left + width, axis_y, (100, 116, 139), thickness=2)
    low = min(0.0, *(number for _ticker, number in rows))
    high = max(0.0, *(number for _ticker, number in rows))
    span = max(high - low, 1e-9)
    zero = top + round(height * high / span)
    canvas.line(left, zero, left + width, zero, (71, 85, 105), thickness=2)
    slot = width / len(rows)
    bar_width = max(8, min(58, round(slot * 0.6)))
    for index, (ticker, number) in enumerate(rows):
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
        center = left + round(slot * index + slot / 2)
        # Ticker label under the axis; value label above the bar.
        label = ticker if _text_width(ticker) <= slot - 6 else ticker[: max(3, int((slot - 6) / (_FONT_W + _FONT_TRACK)) )]
        _draw_text(
            canvas,
            center - _text_width(label) // 2,
            axis_y + 8,
            label,
            (30, 41, 59),
        )
        value_text = f"{number:+.1f}%" if chart_id.endswith("return") else f"{number:.1f}"
        value_y = max(top, y - 12) if number >= 0 else min(top + height - 9, max(y, y_value) + 4)
        _draw_text(
            canvas,
            center - _text_width(value_text) // 2,
            value_y,
            value_text,
            (71, 85, 105),
        )
    return MarketChartAsset(
        chart_id=chart_id,
        title=title,
        relative_path=f"{CHART_OUTPUT_DIRECTORY}/{chart_id}.png",
        png_bytes=canvas.png(),
    )


def _subject_asset(
    security: MarketDataSecurityV2 | None,
    events: tuple[object, ...] | list[object] = (),
) -> MarketChartAsset | None:
    if security is None or len(security.price_series) < 2:
        return None
    canvas = _Canvas()
    left, top, width, height = _frame(canvas)
    _draw_legend(
        canvas,
        [
            (f"{security.ticker} CLOSE", (29, 78, 216)),
            ("VOLUME", (191, 219, 254)),
            ("EVENT DAY", (190, 18, 60)),
        ],
        x=left,
        y=14,
    )
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
    # Event-day markers: a vertical line at the first series point on or
    # after each subject event date.  Co-movement only; never causal proof.
    dates = [point.date for point in security.price_series]
    for event in events:
        event_date = getattr(event, "published_at", None)
        event_ticker = getattr(event, "ticker", None)
        if event_date is None or (event_ticker and event_ticker != security.ticker):
            continue
        index = next(
            (i for i, value in enumerate(dates) if value >= event_date), None
        )
        if index is None:
            continue
        marker_x = points[index][0]
        canvas.line(
            marker_x,
            top,
            marker_x,
            top + height,
            (190, 18, 60),
            thickness=2,
        )
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
        _subject_asset(subject, snapshot.events),
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
