"""Store-native market data snapshot writer and deterministic projection.

This is the sole writer of the append-only ``market_data_snapshots``
authority surface.  It consumes already-acquired quotes (Yahoo chart API or
manual input files), freezes exactly one snapshot per (run, as_of_date)
through one Store transaction, and projects the latest frozen snapshot into
``output/intermediate/market_data_tables.md`` for the analyst and formatter
roles.  It never estimates or backfills a quote: a security without data
appears only as a value-free gap and as an explicit ``NOT AVAILABLE`` row.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal, Mapping
import uuid

from pydantic import Field, ValidationError, model_validator

from multi_agent_brief.contracts.v2 import (
    IsoDate,
    MarketDataSecurityGapV1,
    MarketDataSecurityV1,
    MarketDataSnapshotV1,
    StrictModel,
)
from multi_agent_brief.control_store.errors import ControlStoreError
from multi_agent_brief.control_store.serialization import (
    canonical_fingerprint,
    sha256_hex,
)
from multi_agent_brief.control_store.sqlite_store import SQLiteControlStore
from multi_agent_brief.product.post_final_assessment import (
    _event,
    _id,
    _utc_now,
)
from multi_agent_brief.sources.market_data import MarketDataError
from multi_agent_brief.sources.solar_stock_plan import (
    SOLAR_STOCK_OVERSEAS_SECURITIES,
    SOLAR_STOCK_PRIMARY_SECURITIES,
)

MARKET_DATA_RECORD_INPUT_SCHEMA = "briefloop.market_data_record_input.v1"
MARKET_DATA_SNAPSHOT_TRANSACTION_TYPE = "market_data_snapshot"
MARKET_DATA_TABLES_PATH = "output/intermediate/market_data_tables.md"
_NOT_AVAILABLE = "NOT AVAILABLE"


class MarketDataRecordInputV1(StrictModel):
    """Deterministic record request for one weekly market data snapshot."""

    schema_version: Literal["briefloop.market_data_record_input.v1"]
    as_of_date: IsoDate
    securities: list[MarketDataSecurityV1] = Field(min_length=1, max_length=11)
    gaps: list[MarketDataSecurityGapV1] = Field(max_length=11)

    @model_validator(mode="after")
    def tickers_are_partitioned(self) -> "MarketDataRecordInputV1":
        tickers = [item.ticker for item in self.securities]
        if tickers != sorted(set(tickers)):
            raise ValueError("market data securities must be sorted and unique")
        gap_tickers = [item.ticker for item in self.gaps]
        if gap_tickers != sorted(set(gap_tickers)):
            raise ValueError("market data gaps must be sorted and unique")
        if set(tickers) & set(gap_tickers):
            raise ValueError("market data gap tickers must not carry a quote")
        return self


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _format_price(value: float | None) -> str:
    return _NOT_AVAILABLE if value is None else f"{value:.2f}"


def _format_change_pct(value: float | None) -> str:
    return _NOT_AVAILABLE if value is None else f"{value:+.2f}"


def _format_whole(value: float | None) -> str:
    return _NOT_AVAILABLE if value is None else f"{value:.0f}"


def _comparison_table(
    snapshot: MarketDataSnapshotV1,
    tickers: tuple[str, ...],
) -> list[str]:
    by_ticker = {item.ticker: item for item in snapshot.securities}
    lines = [
        "| Ticker | Exchange | Currency | As Of | Week Close"
        " | Weekly Change % | Market Cap | Trailing P/E | Data Origin |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for ticker in tickers:
        security = by_ticker.get(ticker)
        if security is None:
            cells = [ticker] + [_NOT_AVAILABLE] * 8
        else:
            cells = [
                security.ticker,
                security.exchange,
                security.currency,
                security.as_of,
                _format_price(security.week_close),
                _format_change_pct(security.weekly_change_pct),
                _format_whole(security.market_cap),
                _format_price(security.trailing_pe),
                security.data_origin,
            ]
        lines.append("| " + " | ".join(cells) + " |")
    return lines


def render_market_data_tables(snapshot: MarketDataSnapshotV1) -> str:
    """Render the deterministic primary/overseas comparison projection."""

    lines = [
        "<!-- mabw:market-data-tables",
        f"run_id: {snapshot.run_id}",
        f"market_data_snapshot_id: {snapshot.market_data_snapshot_id}",
        f"as_of_date: {snapshot.as_of_date}",
        f"provider_id: {snapshot.provider_id}",
        f"snapshot_fingerprint: {snapshot.snapshot_fingerprint}",
        f"recorded_at: {snapshot.recorded_at}",
        "-->",
        "",
        "# Market Data Tables",
        "",
        f"- Run ID: {snapshot.run_id}",
        f"- Source snapshot: {snapshot.market_data_snapshot_id}",
        f"- As of date: {snapshot.as_of_date}",
        f"- Provider: {snapshot.provider_id}",
        f"- Snapshot fingerprint: {snapshot.snapshot_fingerprint}",
        "- Rows marked NOT AVAILABLE had no quote in the frozen snapshot;"
        " nothing is estimated or backfilled.",
        "- The frozen Store snapshot above is authoritative; this projection"
        " rounds prices and valuations for display only.",
        "",
        "## Primary Equity Comparison",
        "",
    ]
    lines.extend(_comparison_table(snapshot, SOLAR_STOCK_PRIMARY_SECURITIES))
    lines.extend(["", "## Overseas Equity Comparison", ""])
    lines.extend(_comparison_table(snapshot, SOLAR_STOCK_OVERSEAS_SECURITIES))
    lines.extend(["", "## Coverage Gaps", ""])
    if snapshot.gaps:
        for gap in snapshot.gaps:
            lines.append(f"- {gap.ticker}: {gap.failure_class}")
    else:
        lines.append("- none")
    return "\n".join(lines) + "\n"


class MarketDataService:
    """Sole coordinator for market data snapshot record and projection."""

    def __init__(self, workspace: str | Path) -> None:
        self.workspace = Path(workspace).expanduser().resolve()

    @property
    def _database_path(self) -> Path:
        return self.workspace / "briefloop.db"

    @staticmethod
    def _validate(value: Mapping[str, object]) -> MarketDataRecordInputV1:
        try:
            return MarketDataRecordInputV1.model_validate(value, strict=True)
        except (TypeError, ValidationError, ValueError) as exc:
            raise MarketDataError("market_data_snapshot_request_invalid") from exc

    def _current_run(self, store: SQLiteControlStore) -> tuple[str, int, Any]:
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
        return run_id, history.store_revision, snapshot

    def record_snapshot(self, value: Mapping[str, object]) -> dict[str, object]:
        """Freeze one exact snapshot; same input replays, same date conflicts."""

        command = self._validate(value)
        identity = {
            "schema_version": MARKET_DATA_RECORD_INPUT_SCHEMA,
            "as_of_date": command.as_of_date,
            "securities": [
                item.model_dump(mode="json", exclude_unset=False)
                for item in command.securities
            ],
            "gaps": [
                item.model_dump(mode="json", exclude_unset=False)
                for item in command.gaps
            ],
        }
        try:
            with SQLiteControlStore.open(self._database_path) as store:
                run_id, store_revision, snapshot = self._current_run(store)
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
                        return {
                            "ok": True,
                            "replayed": True,
                            "market_data_snapshot_id": (
                                existing.market_data_snapshot_id
                            ),
                            "as_of_date": existing.as_of_date,
                            "security_count": existing.security_count,
                            "gap_count": len(existing.gaps),
                            "snapshot_fingerprint": existing.snapshot_fingerprint,
                            "receipt_id": receipt.transaction_id,
                        }
                    raise MarketDataError("market_data_snapshot_conflict")
                transaction_id = _id("market-data-snapshot-tx", scoped_identity)
                event_id = _id("market-data-snapshot-event", scoped_identity)
                recorded_at = _utc_now()
                payload: dict[str, object] = {
                    "schema_version": MarketDataSnapshotV1.schema_id,
                    "market_data_snapshot_id": snapshot_id,
                    "run_id": run_id,
                    "as_of_date": command.as_of_date,
                    "security_count": len(command.securities),
                    "provider_id": "yahoo_finance_chart",
                    "securities": identity["securities"],
                    "gaps": identity["gaps"],
                    "record_event_id": event_id,
                    "accepted_transaction_id": transaction_id,
                    "recorded_at": recorded_at,
                }
                payload["snapshot_fingerprint"] = canonical_fingerprint(payload)
                try:
                    record = MarketDataSnapshotV1.model_validate(payload, strict=True)
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
        return {
            "ok": True,
            "replayed": False,
            "market_data_snapshot_id": record.market_data_snapshot_id,
            "as_of_date": record.as_of_date,
            "security_count": record.security_count,
            "gap_count": len(record.gaps),
            "snapshot_fingerprint": record.snapshot_fingerprint,
            "receipt_id": receipt.transaction_id,
        }

    def project_tables(self) -> dict[str, object]:
        """Project the latest frozen snapshot into the intermediate artifact."""

        try:
            with SQLiteControlStore.open(self._database_path) as store:
                _run_id, _revision, snapshot = self._current_run(store)
                records = snapshot.market_data_snapshots
        except ControlStoreError as exc:
            raise MarketDataError(str(exc)) from exc
        if not records:
            raise MarketDataError("market_data_snapshot_unavailable")
        latest = max(
            records,
            key=lambda item: (item.as_of_date, item.market_data_snapshot_id),
        )
        text = render_market_data_tables(latest)
        path = self.workspace / MARKET_DATA_TABLES_PATH
        _write_text_atomic(path, text)
        return {
            "ok": True,
            "path": MARKET_DATA_TABLES_PATH,
            "market_data_snapshot_id": latest.market_data_snapshot_id,
            "as_of_date": latest.as_of_date,
            "security_count": latest.security_count,
            "gap_count": len(latest.gaps),
            "snapshot_fingerprint": latest.snapshot_fingerprint,
            "sha256": sha256_hex(text.encode("utf-8")),
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
    "MARKET_DATA_RECORD_INPUT_SCHEMA",
    "MARKET_DATA_SNAPSHOT_TRANSACTION_TYPE",
    "MARKET_DATA_TABLES_PATH",
    "MarketDataRecordInputV1",
    "MarketDataService",
    "render_market_data_tables",
]
