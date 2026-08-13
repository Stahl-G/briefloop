"""Pure Store read model for structured Solar market data.

This module deliberately imports no acquisition adapter, writer service, chart
renderer, or semantic evaluator.  UI projections can therefore read the latest
append-only snapshot without expanding their runtime authority surface.
"""

from __future__ import annotations

from pathlib import Path

from multi_agent_brief.contracts.v2 import MarketDataSnapshotV2
from multi_agent_brief.control_store.errors import ControlStoreError
from multi_agent_brief.control_store.sqlite_store import SQLiteControlStore


class MarketDataReadModelError(ValueError):
    """Raised when the current-head market snapshot cannot be projected."""


def load_latest_market_data_snapshot(
    workspace: str | Path,
) -> MarketDataSnapshotV2:
    """Load the latest snapshot for the sole current workspace run head."""

    database_path = Path(workspace).expanduser().resolve() / "briefloop.db"
    try:
        with SQLiteControlStore.open(database_path) as store:
            history = store.load_history()
            current_run_ids = {
                item.workspace_run_head.current_run_id
                for item in history.snapshots
                if item.workspace_run_head is not None
            }
            if len(current_run_ids) != 1:
                raise MarketDataReadModelError(
                    "market_data_snapshot_current_head_required"
                )
            run_id = next(iter(current_run_ids))
            records = store.load_snapshot(run_id).market_data_snapshots
    except MarketDataReadModelError:
        raise
    except ControlStoreError as exc:
        raise MarketDataReadModelError(str(exc)) from exc
    if not records:
        raise MarketDataReadModelError("market_data_snapshot_unavailable")
    return max(
        records, key=lambda item: (item.as_of_date, item.market_data_snapshot_id)
    )


def market_data_projection(snapshot: MarketDataSnapshotV2) -> dict[str, object]:
    """Return the complete JSON-safe projection for UI and regenerated files."""

    return {
        "schema_version": "briefloop.market_data_projection.v2",
        "run_id": snapshot.run_id,
        "market_data_snapshot_id": snapshot.market_data_snapshot_id,
        "snapshot_fingerprint": snapshot.snapshot_fingerprint,
        "report_window_start": snapshot.report_window_start,
        "report_window_end": snapshot.report_window_end,
        "as_of_date": snapshot.as_of_date,
        "universe_tickers": list(snapshot.universe_tickers),
        "provider_ids": list(snapshot.provider_ids),
        "workbook": None
        if snapshot.workbook is None
        else snapshot.workbook.model_dump(mode="json", exclude_unset=False),
        "securities": [
            item.model_dump(mode="json", exclude_unset=False)
            for item in snapshot.securities
        ],
        "benchmark": None
        if snapshot.benchmark is None
        else snapshot.benchmark.model_dump(mode="json", exclude_unset=False),
        "fx_rates": [
            item.model_dump(mode="json", exclude_unset=False)
            for item in snapshot.fx_rates
        ],
        "events": [
            item.model_dump(mode="json", exclude_unset=False)
            for item in snapshot.events
        ],
        "gaps": [
            item.model_dump(mode="json", exclude_unset=False) for item in snapshot.gaps
        ],
        "conflicts": [
            item.model_dump(mode="json", exclude_unset=False)
            for item in snapshot.conflicts
        ],
        "derivation_version": snapshot.derivation_version,
    }


__all__ = [
    "MarketDataReadModelError",
    "load_latest_market_data_snapshot",
    "market_data_projection",
]
