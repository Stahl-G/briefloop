"""Store-native market data authority and deterministic projections.

Schema19 writes only ``MarketDataSnapshotV2``.  Acquisition adapters and XLSX
parsers remain replaceable inputs; this service is the sole writer of the
append-only Store record.  Markdown and JSON files are projections that may be
regenerated from the Store without reading the original workbook or provider.
"""

from __future__ import annotations

from datetime import date, timedelta
import json
import os
from pathlib import Path
from typing import Any, Literal, Mapping
import uuid

from pydantic import Field, ValidationError, model_validator

from multi_agent_brief.contracts.v2 import (
    IsoDate,
    MarketDataBenchmarkV2,
    MarketDataConflictV2,
    MarketDataEventReactionV2,
    MarketDataFieldValueV2,
    MarketDataFxRateV2,
    MarketDataGapV2,
    MarketDataSecurityGapV1,
    MarketDataSecurityV1,
    MarketDataSecurityV2,
    MarketDataSeriesPointV2,
    MarketDataSnapshotV2,
    MarketDataWorkbookIdentityV2,
    StrictModel,
)
from multi_agent_brief.control_store.errors import ControlStoreError
from multi_agent_brief.control_store.serialization import (
    canonical_fingerprint,
    canonical_json_bytes,
    sha256_hex,
)
from multi_agent_brief.control_store.sqlite_store import SQLiteControlStore
from multi_agent_brief.product.post_final_assessment import _event, _id, _utc_now
from multi_agent_brief.product.market_data_charts import (
    CHART_MANIFEST_PATH,
    CHART_RENDERER_VERSION,
    MarketChartAsset,
    render_market_chart_assets,
)
from multi_agent_brief.product.market_data_read_model import (
    load_latest_market_data_snapshot,
    market_data_projection,
)
from multi_agent_brief.sources.market_data import MarketDataError
from multi_agent_brief.sources.solar_stock_plan import (
    SOLAR_STOCK_OVERSEAS_SECURITIES,
    SOLAR_STOCK_PRIMARY_SECURITIES,
)

MARKET_DATA_RECORD_INPUT_SCHEMA = "briefloop.market_data_record_input.v2"
MARKET_DATA_SNAPSHOT_TRANSACTION_TYPE = "market_data_snapshot"
MARKET_DATA_TABLES_PATH = "output/intermediate/market_data_tables.md"
MARKET_DATA_PROJECTION_PATH = "output/intermediate/market_data_projection.json"
_NOT_AVAILABLE = "NOT AVAILABLE"
_NOT_MEANINGFUL = "N/M"
_UNIVERSE = SOLAR_STOCK_PRIMARY_SECURITIES + SOLAR_STOCK_OVERSEAS_SECURITIES


class MarketDataRecordInputV1(StrictModel):
    """Read-compatible input upgraded deterministically before schema19 writes."""

    schema_version: Literal["briefloop.market_data_record_input.v1"]
    as_of_date: IsoDate
    securities: list[MarketDataSecurityV1] = Field(min_length=1, max_length=11)
    gaps: list[MarketDataSecurityGapV1] = Field(max_length=11)

    @model_validator(mode="after")
    def tickers_are_partitioned(self) -> "MarketDataRecordInputV1":
        tickers = [item.ticker for item in self.securities]
        gap_tickers = [item.ticker for item in self.gaps]
        if tickers != sorted(set(tickers)) or gap_tickers != sorted(set(gap_tickers)):
            raise ValueError("market data tickers must be sorted and unique")
        if set(tickers) & set(gap_tickers):
            raise ValueError("market data gap tickers must not carry a quote")
        return self


class MarketDataRecordInputV2(StrictModel):
    schema_version: Literal["briefloop.market_data_record_input.v2"]
    report_window_start: IsoDate
    report_window_end: IsoDate
    as_of_date: IsoDate
    universe_tickers: list[str] = Field(min_length=1, max_length=20)
    provider_ids: list[str] = Field(min_length=1, max_length=8)
    workbook: MarketDataWorkbookIdentityV2 | None = None
    securities: list[MarketDataSecurityV2] = Field(min_length=1, max_length=20)
    benchmark: MarketDataBenchmarkV2 | None = None
    fx_rates: list[MarketDataFxRateV2] = Field(max_length=16)
    events: list[MarketDataEventReactionV2] = Field(max_length=128)
    gaps: list[MarketDataGapV2] = Field(max_length=128)
    conflicts: list[MarketDataConflictV2] = Field(max_length=128)
    derivation_version: str

    @model_validator(mode="after")
    def input_is_canonical(self) -> "MarketDataRecordInputV2":
        if self.report_window_end < self.report_window_start:
            raise ValueError("market data report window is inverted")
        if not (self.report_window_start <= self.as_of_date <= self.report_window_end):
            raise ValueError("market data as-of date is outside the report window")
        if self.universe_tickers != list(dict.fromkeys(self.universe_tickers)):
            raise ValueError("market data universe must be ordered and unique")
        if self.provider_ids != sorted(set(self.provider_ids)):
            raise ValueError("market data providers must be sorted and unique")
        tickers = [item.ticker for item in self.securities]
        if tickers != sorted(set(tickers)):
            raise ValueError("market data securities must be sorted and unique")
        if not set(tickers) <= set(self.universe_tickers):
            raise ValueError("market data security is outside the frozen universe")
        return self


def _write_bytes_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_bytes(payload)
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _field(
    security: MarketDataSecurityV2, field_id: str
) -> MarketDataFieldValueV2 | None:
    return next((item for item in security.fields if item.field_id == field_id), None)


def _display_field(field: MarketDataFieldValueV2 | None, *, decimals: int = 2) -> str:
    if field is None or field.status == "unavailable":
        return _NOT_AVAILABLE
    if field.status == "not_meaningful":
        return _NOT_MEANINGFUL
    if field.value_text is not None:
        return field.value_text
    if field.value_number is None:
        return _NOT_AVAILABLE
    return f"{field.value_number:.{decimals}f}"


def _comparison_table(
    snapshot: MarketDataSnapshotV2, tickers: tuple[str, ...]
) -> list[str]:
    by_ticker = {item.ticker: item for item in snapshot.securities}
    lines = [
        "| Ticker | Exchange | Currency | Latest Close | 1W % | 1M % | YTD %"
        " | Market Cap USD (m) | EV/Sales | EV/EBITDA | P/E TTM |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for ticker in tickers:
        security = by_ticker.get(ticker)
        if security is None:
            cells = [ticker] + [_NOT_AVAILABLE] * 10
        else:
            cells = [
                ticker,
                security.exchange,
                security.currency,
                _display_field(_field(security, "latest_close_local")),
                _display_field(_field(security, "return_1w_pct")),
                _display_field(_field(security, "return_1m_pct")),
                _display_field(_field(security, "return_ytd_pct")),
                _display_field(_field(security, "market_cap_usd_millions"), decimals=0),
                _display_field(_field(security, "ev_sales_ttm")),
                _display_field(_field(security, "ev_ebitda_ttm")),
                _display_field(_field(security, "pe_ttm")),
            ]
        lines.append("| " + " | ".join(cells) + " |")
    return lines


def render_market_data_tables(
    snapshot: MarketDataSnapshotV2,
    *,
    chart_assets: tuple[MarketChartAsset, ...] = (),
) -> str:
    """Render the deterministic reader-facing comparison projection."""

    lines = [
        "<!-- briefloop:market-data-tables",
        f"run_id: {snapshot.run_id}",
        f"market_data_snapshot_id: {snapshot.market_data_snapshot_id}",
        f"snapshot_fingerprint: {snapshot.snapshot_fingerprint}",
        "-->",
        "",
        "# Solar Stock Periodic · Structured Market Data",
        "",
        f"- Report window: {snapshot.report_window_start} to {snapshot.report_window_end}",
        f"- As of: {snapshot.as_of_date}",
        f"- Frozen providers: {', '.join(snapshot.provider_ids)}",
        "- Manual workbook fields win over provider fills; conflicts remain visible.",
        "- N/M means the multiple is not economically meaningful; NOT AVAILABLE means no valid field was frozen.",
        "- Price reactions are contemporaneous observations, not proof of causation.",
        "",
        "## Primary Equity Comparison",
        "",
    ]
    lines.extend(_comparison_table(snapshot, SOLAR_STOCK_PRIMARY_SECURITIES))
    lines.extend(["", "## Overseas Equity Comparison", ""])
    lines.extend(_comparison_table(snapshot, SOLAR_STOCK_OVERSEAS_SECURITIES))
    if chart_assets:
        lines.extend(
            [
                "",
                "## Deterministic Charts",
                "",
                "Price co-movement is descriptive and does not prove event causation.",
                "",
            ]
        )
        for asset in chart_assets:
            relative = "../charts/market_data/" + Path(asset.relative_path).name
            lines.extend(
                [
                    f"### {asset.title}",
                    "",
                    f"![{asset.title}]({relative})",
                    "",
                ]
            )
    lines.extend(["", "## Event Timeline", ""])
    if snapshot.events:
        lines.extend(
            [
                "| Date | Ticker | Event | Event-day % | Excess % | T+1 % | Evidence |",
                "| --- | --- | --- | ---: | ---: | ---: | --- |",
            ]
        )
        for event in sorted(
            snapshot.events, key=lambda item: (item.published_at, item.event_id)
        ):
            cells = [
                event.published_at,
                event.ticker,
                event.title.replace("|", "\\|"),
                _NOT_AVAILABLE
                if event.event_day_return_pct is None
                else f"{event.event_day_return_pct:+.2f}",
                _NOT_AVAILABLE
                if event.event_day_excess_return_pct is None
                else f"{event.event_day_excess_return_pct:+.2f}",
                _NOT_AVAILABLE
                if event.t1_return_pct is None
                else f"{event.t1_return_pct:+.2f}",
                event.evidence_status,
            ]
            lines.append("| " + " | ".join(cells) + " |")
    else:
        lines.append("- No frozen event observations.")
    lines.extend(["", "## Visible Gaps and Conflicts", ""])
    if not snapshot.gaps and not snapshot.conflicts:
        lines.append("- none")
    for gap in snapshot.gaps:
        target = gap.ticker or gap.field_id or "workbook"
        lines.append(f"- [{gap.severity}] {target}: {gap.reason_code}")
    for conflict in snapshot.conflicts:
        target = conflict.ticker or "workbook"
        lines.append(
            f"- [{conflict.severity}] {target}/{conflict.field_id}: {conflict.category} ({conflict.resolution})"
        )
    return "\n".join(lines) + "\n"


def _v1_field(
    *,
    field_id: str,
    value: float | None,
    unit: str,
    as_of: str,
    currency: str | None,
    source_sha256: str,
) -> dict[str, object]:
    return {
        "field_id": field_id,
        "status": "available" if value is not None else "unavailable",
        "value_number": value,
        "value_text": None,
        "unit": unit,
        "as_of": as_of,
        "currency": currency,
        "data_origin": "manual_json",
        "derivation": "direct",
        "source_locator": f"legacy-v1:{field_id}",
        "source_sha256": source_sha256,
        "reason_code": None if value is not None else "legacy_field_unavailable",
    }


def _upgrade_v1(
    command: MarketDataRecordInputV1, direction: Any
) -> MarketDataRecordInputV2:
    start = (
        direction.report_window_start
        or (date.fromisoformat(command.as_of_date) - timedelta(days=7)).isoformat()
    )
    end = direction.report_window_end or command.as_of_date
    if not (start <= command.as_of_date <= end):
        start = command.as_of_date
        end = command.as_of_date
    identity = canonical_fingerprint(
        command.model_dump(mode="json", exclude_unset=False)
    )
    securities: list[dict[str, object]] = []
    for item in command.securities:
        universe = (
            "primary" if item.ticker in SOLAR_STOCK_PRIMARY_SECURITIES else "overseas"
        )
        securities.append(
            {
                "ticker": item.ticker,
                "display_name": item.ticker,
                "universe": universe,
                "exchange": item.exchange,
                "currency": item.currency,
                "return_basis": "close",
                "price_series": [
                    {
                        "date": item.as_of,
                        "close": item.week_close,
                        "adjusted_close": None,
                        "volume": item.week_volume,
                        "data_origin": "manual_json"
                        if item.data_origin == "manual_input"
                        else "yahoo_chart_api",
                        "source_locator": f"legacy-v1:{item.ticker}",
                        "source_sha256": identity,
                    }
                ],
                "corporate_actions": [],
                "fields": sorted(
                    [
                        _v1_field(
                            field_id="latest_close_local",
                            value=item.week_close,
                            unit="price",
                            as_of=item.as_of,
                            currency=item.currency,
                            source_sha256=identity,
                        ),
                        _v1_field(
                            field_id="return_1w_pct",
                            value=item.weekly_change_pct,
                            unit="percent",
                            as_of=item.as_of,
                            currency=None,
                            source_sha256=identity,
                        ),
                        _v1_field(
                            field_id="market_cap_local",
                            value=item.market_cap,
                            unit="currency",
                            as_of=item.as_of,
                            currency=item.currency,
                            source_sha256=identity,
                        ),
                        _v1_field(
                            field_id="pe_ttm",
                            value=item.trailing_pe,
                            unit="multiple",
                            as_of=item.as_of,
                            currency=None,
                            source_sha256=identity,
                        ),
                    ],
                    key=lambda field: str(field["field_id"]),
                ),
            }
        )
    gaps = [
        {
            "gap_id": _id(
                "market-gap", {"ticker": item.ticker, "failure": item.failure_class}
            ),
            "severity": "blocking",
            "category": "provider_unavailable",
            "ticker": item.ticker,
            "field_id": "price_series",
            "source_locator": None,
            "reason_code": item.failure_class,
        }
        for item in command.gaps
    ]
    return MarketDataRecordInputV2.model_validate(
        {
            "schema_version": MARKET_DATA_RECORD_INPUT_SCHEMA,
            "report_window_start": start,
            "report_window_end": end,
            "as_of_date": command.as_of_date,
            "universe_tickers": list(_UNIVERSE),
            "provider_ids": ["legacy_v1_upgrade"],
            "workbook": None,
            "securities": sorted(
                securities, key=lambda security: str(security["ticker"])
            ),
            "benchmark": None,
            "fx_rates": [],
            "events": [],
            "gaps": sorted(gaps, key=lambda gap: str(gap["gap_id"])),
            "conflicts": [],
            "derivation_version": "market-data-v1-upgrade",
        },
        strict=True,
    )


class MarketDataService:
    """Sole coordinator for market-data snapshot record and projection."""

    def __init__(self, workspace: str | Path) -> None:
        self.workspace = Path(workspace).expanduser().resolve()

    @property
    def _database_path(self) -> Path:
        return self.workspace / "briefloop.db"

    @staticmethod
    def _validate(
        value: Mapping[str, object],
    ) -> MarketDataRecordInputV1 | MarketDataRecordInputV2:
        schema = value.get("schema_version")
        model = (
            MarketDataRecordInputV1
            if schema == "briefloop.market_data_record_input.v1"
            else MarketDataRecordInputV2
        )
        try:
            return model.model_validate(value, strict=True)
        except (TypeError, ValidationError, ValueError) as exc:
            raise MarketDataError("market_data_snapshot_request_invalid") from exc

    def _current_run(self, store: SQLiteControlStore) -> tuple[str, int, Any, Any]:
        history = store.load_history()
        current_run_ids = {
            item.workspace_run_head.current_run_id
            for item in history.snapshots
            if item.workspace_run_head is not None
        }
        if len(current_run_ids) != 1:
            raise MarketDataError("market_data_snapshot_current_head_required")
        run_id = next(iter(current_run_ids))
        snapshot = store.load_snapshot(run_id)
        bindings = [
            item for item in snapshot.run_contract_bindings if item.run_id == run_id
        ]
        if len(bindings) != 1:
            raise MarketDataError("control_store_integrity_invalid")
        return run_id, history.store_revision, snapshot, bindings[0].run_direction

    @staticmethod
    def _require_no_active_invocation(snapshot: Any) -> None:
        if any(item.status == "active" for item in snapshot.invocations):
            raise MarketDataError("market_data_snapshot_run_invocation_active")

    def require_recording_allowed(self) -> dict[str, object]:
        try:
            with SQLiteControlStore.open(self._database_path) as store:
                run_id, store_revision, snapshot, _direction = self._current_run(store)
                self._require_no_active_invocation(snapshot)
        except ControlStoreError as exc:
            raise MarketDataError(str(exc)) from exc
        return {"ok": True, "run_id": run_id, "store_revision": store_revision}

    def record_snapshot(self, value: Mapping[str, object]) -> dict[str, object]:
        incoming = self._validate(value)
        try:
            with SQLiteControlStore.open(self._database_path) as store:
                run_id, store_revision, snapshot, direction = self._current_run(store)
                self._require_no_active_invocation(snapshot)
                command = (
                    _upgrade_v1(incoming, direction)
                    if isinstance(incoming, MarketDataRecordInputV1)
                    else incoming
                )
                if direction.report_type != "solar_stock_periodic":
                    raise MarketDataError("market_data_snapshot_report_type_invalid")
                if (
                    direction.report_window_start is None
                    or direction.report_window_end is None
                ):
                    raise MarketDataError("market_data_snapshot_direction_invalid")
                if (
                    command.report_window_start != direction.report_window_start
                    or command.report_window_end != direction.report_window_end
                ):
                    raise MarketDataError("market_data_snapshot_window_invalid")
                if command.universe_tickers != list(_UNIVERSE):
                    raise MarketDataError("market_data_snapshot_universe_invalid")
                identity = command.model_dump(mode="json", exclude_unset=False)
                scoped_identity = {"run_id": run_id, **identity}
                snapshot_id = _id("market-data-snapshot", scoped_identity)
                existing = next(
                    (
                        item
                        for item in snapshot.market_data_snapshots
                        if item.as_of_date == command.as_of_date
                    ),
                    None,
                )
                if existing is not None:
                    if existing.market_data_snapshot_id == snapshot_id:
                        receipt = self._receipt(
                            snapshot, existing.accepted_transaction_id
                        )
                        return self._result(
                            existing, receipt.transaction_id, replayed=True
                        )
                    raise MarketDataError("market_data_snapshot_conflict")
                transaction_id = _id("market-data-snapshot-tx", scoped_identity)
                event_id = _id("market-data-snapshot-event", scoped_identity)
                recorded_at = _utc_now()
                payload: dict[str, object] = {
                    "schema_version": MarketDataSnapshotV2.schema_id,
                    "market_data_snapshot_id": snapshot_id,
                    "run_id": run_id,
                    **{
                        key: value
                        for key, value in identity.items()
                        if key != "schema_version"
                    },
                    "security_count": len(command.securities),
                    "record_event_id": event_id,
                    "accepted_transaction_id": transaction_id,
                    "recorded_at": recorded_at,
                }
                payload["snapshot_fingerprint"] = canonical_fingerprint(payload)
                try:
                    record = MarketDataSnapshotV2.model_validate(payload, strict=True)
                except (ValidationError, TypeError, ValueError) as exc:
                    raise MarketDataError(
                        "market_data_snapshot_request_invalid"
                    ) from exc
                event = _event(
                    run_id=run_id,
                    event_id=event_id,
                    event_type="market_data_snapshot_recorded",
                    transaction_id=transaction_id,
                    decision=snapshot_id,
                    metadata={
                        "snapshot_fingerprint": record.snapshot_fingerprint,
                        "as_of_date": record.as_of_date,
                        "security_count": record.security_count,
                        "gap_count": len(record.gaps),
                        "conflict_count": len(record.conflicts),
                    },
                )
                with store.begin(
                    run_id,
                    transaction_id,
                    MARKET_DATA_SNAPSHOT_TRANSACTION_TYPE,
                    store_revision,
                ) as uow:
                    uow.append_event(event)
                    uow.put_market_data_snapshot(record)
                    receipt = uow.commit()
        except ControlStoreError as exc:
            raise self._write_error(exc) from exc
        return self._result(record, receipt.transaction_id, replayed=False)

    @staticmethod
    def _result(
        record: MarketDataSnapshotV2, receipt_id: str, *, replayed: bool
    ) -> dict[str, object]:
        return {
            "ok": True,
            "replayed": replayed,
            "market_data_snapshot_id": record.market_data_snapshot_id,
            "as_of_date": record.as_of_date,
            "security_count": record.security_count,
            "gap_count": len(record.gaps),
            "conflict_count": len(record.conflicts),
            "snapshot_fingerprint": record.snapshot_fingerprint,
            "receipt_id": receipt_id,
        }

    def latest_snapshot(self) -> MarketDataSnapshotV2:
        try:
            return load_latest_market_data_snapshot(self.workspace)
        except ValueError as exc:
            raise MarketDataError(str(exc)) from exc

    def project_tables(self) -> dict[str, object]:
        latest = self.latest_snapshot()
        chart_assets = render_market_chart_assets(latest)
        markdown = render_market_data_tables(
            latest,
            chart_assets=chart_assets,
        ).encode("utf-8")
        projection = canonical_json_bytes(market_data_projection(latest)) + b"\n"
        chart_manifest = (
            canonical_json_bytes(
                {
                    "schema_version": "briefloop.market_data_chart_manifest.v1",
                    "market_data_snapshot_id": latest.market_data_snapshot_id,
                    "snapshot_fingerprint": latest.snapshot_fingerprint,
                    "renderer_version": CHART_RENDERER_VERSION,
                    "charts": [
                        {
                            "chart_id": asset.chart_id,
                            "title": asset.title,
                            "relative_path": asset.relative_path,
                            "sha256": asset.sha256,
                            "size_bytes": len(asset.png_bytes),
                        }
                        for asset in chart_assets
                    ],
                }
            )
            + b"\n"
        )
        for asset in chart_assets:
            _write_bytes_atomic(self.workspace / asset.relative_path, asset.png_bytes)
        _write_bytes_atomic(self.workspace / MARKET_DATA_TABLES_PATH, markdown)
        _write_bytes_atomic(self.workspace / MARKET_DATA_PROJECTION_PATH, projection)
        _write_bytes_atomic(self.workspace / CHART_MANIFEST_PATH, chart_manifest)
        return {
            "ok": True,
            "path": MARKET_DATA_TABLES_PATH,
            "projection_path": MARKET_DATA_PROJECTION_PATH,
            "chart_manifest_path": CHART_MANIFEST_PATH,
            "chart_count": len(chart_assets),
            "market_data_snapshot_id": latest.market_data_snapshot_id,
            "as_of_date": latest.as_of_date,
            "security_count": latest.security_count,
            "gap_count": len(latest.gaps),
            "conflict_count": len(latest.conflicts),
            "snapshot_fingerprint": latest.snapshot_fingerprint,
            "sha256": sha256_hex(markdown),
            "projection_sha256": sha256_hex(projection),
            "chart_manifest_sha256": sha256_hex(chart_manifest),
        }

    @staticmethod
    def _receipt(snapshot: Any, transaction_id: str) -> Any:
        matches = [
            item
            for item in snapshot.transactions
            if item.transaction_id == transaction_id
        ]
        if len(matches) != 1:
            raise MarketDataError("control_store_integrity_invalid")
        return matches[0]

    @staticmethod
    def _write_error(exc: ControlStoreError) -> MarketDataError:
        if str(exc) in {"store_revision_conflict", "relational_integrity_conflict"}:
            return MarketDataError("market_data_snapshot_conflict")
        return MarketDataError(str(exc))


__all__ = [
    "MARKET_DATA_PROJECTION_PATH",
    "MARKET_DATA_RECORD_INPUT_SCHEMA",
    "MARKET_DATA_SNAPSHOT_TRANSACTION_TYPE",
    "MARKET_DATA_TABLES_PATH",
    "MarketDataRecordInputV1",
    "MarketDataRecordInputV2",
    "MarketDataService",
    "market_data_projection",
    "render_market_data_tables",
]
