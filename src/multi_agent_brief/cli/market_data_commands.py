"""Market data channel CLI commands.

Exposes the solar stock weekly market data channel to a workspace:
``fetch`` pulls the Yahoo chart API (manual files under
``input/market_data/`` take precedence), ``ingest`` records one manual
file without any network access, and ``project`` re-renders the
deterministic comparison tables from the latest frozen snapshot.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from multi_agent_brief.product.market_data_service import (
    MARKET_DATA_RECORD_INPUT_SCHEMA,
    MarketDataService,
)
from multi_agent_brief.sources.market_data import (
    MARKET_DATA_INPUT_DIR,
    MarketDataError,
    YahooMarketDataAdapter,
    load_manual_market_data_file,
    merge_manual_first,
)
from multi_agent_brief.sources.solar_stock_plan import (
    SOLAR_STOCK_OVERSEAS_SECURITIES,
    SOLAR_STOCK_PRIMARY_SECURITIES,
)

_SOLAR_STOCK_UNIVERSE = SOLAR_STOCK_PRIMARY_SECURITIES + SOLAR_STOCK_OVERSEAS_SECURITIES


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "market-data",
        help="Acquire and freeze weekly market data snapshots for a workspace.",
    )
    actions = parser.add_subparsers(dest="market_data_action", required=True)

    fetch_parser = actions.add_parser(
        "fetch",
        help=(
            "Pull weekly quotes from the Yahoo chart API, merge manual files "
            "from input/market_data/ first, freeze the snapshot, and project "
            "the comparison tables."
        ),
    )
    fetch_parser.add_argument(
        "--workspace", required=True, help="Path to workspace directory."
    )
    fetch_parser.add_argument(
        "--as-of",
        dest="as_of",
        help="Logical snapshot date (YYYY-MM-DD); defaults to the latest quote date.",
    )
    fetch_parser.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON."
    )

    ingest_parser = actions.add_parser(
        "ingest",
        help=(
            "Freeze one manual market data file (JSON or CSV) without any "
            "network access, then project the comparison tables."
        ),
    )
    ingest_parser.add_argument(
        "--workspace", required=True, help="Path to workspace directory."
    )
    ingest_parser.add_argument(
        "--file", required=True, help="Manual market data file (JSON or CSV)."
    )
    ingest_parser.add_argument(
        "--as-of",
        dest="as_of",
        help="Logical snapshot date (YYYY-MM-DD); defaults to the latest row date.",
    )
    ingest_parser.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON."
    )

    project_parser = actions.add_parser(
        "project",
        help=(
            "Re-render output/intermediate/market_data_tables.md from the "
            "latest frozen snapshot."
        ),
    )
    project_parser.add_argument(
        "--workspace", required=True, help="Path to workspace directory."
    )
    project_parser.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON."
    )


def _print_payload(label: str, payload: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return
    print(f"[{label}] ok: {payload.get('ok')}")
    for key in (
        "market_data_snapshot_id",
        "as_of_date",
        "security_count",
        "gap_count",
        "snapshot_fingerprint",
        "receipt_id",
        "path",
        "reason_code",
        "detail",
    ):
        if key in payload:
            print(f"{key}: {payload[key]}")
    for section in ("record", "projection"):
        nested = payload.get(section)
        if isinstance(nested, dict):
            print(
                f"{section}:"
                f" snapshot={nested.get('market_data_snapshot_id')}"
                f" as_of={nested.get('as_of_date')}"
                f" securities={nested.get('security_count')}"
                f" gaps={nested.get('gap_count')}"
            )


def _snapshot_request_payload(
    *,
    securities: tuple[Any, ...],
    gaps: tuple[Any, ...],
    as_of: str | None,
) -> dict[str, object]:
    if not securities:
        raise MarketDataError("market_data_unavailable")
    ordered = sorted(securities, key=lambda item: item.ticker)
    ordered_gaps = sorted(gaps, key=lambda item: item.ticker)
    as_of_date = as_of or max(item.as_of for item in ordered)
    return {
        "schema_version": MARKET_DATA_RECORD_INPUT_SCHEMA,
        "as_of_date": as_of_date,
        "securities": [
            item.model_dump(mode="json", exclude_unset=False) for item in ordered
        ],
        "gaps": [
            item.model_dump(mode="json", exclude_unset=False) for item in ordered_gaps
        ],
    }


def _load_manual_inputs(workspace: Path) -> list:
    input_dir = workspace / MARKET_DATA_INPUT_DIR
    if not input_dir.is_dir():
        return []
    manuals = []
    for path in sorted(input_dir.iterdir()):
        if path.is_file() and path.suffix.lower() in {".json", ".csv"}:
            manuals.append(load_manual_market_data_file(path))
    return manuals


def _handle_fetch(args: argparse.Namespace, workspace: Path) -> dict[str, object]:
    manuals = _load_manual_inputs(workspace)
    fetched = YahooMarketDataAdapter().fetch_weekly(_SOLAR_STOCK_UNIVERSE)
    merged = merge_manual_first(manuals, fetched)
    request = _snapshot_request_payload(
        securities=merged.securities,
        gaps=merged.gaps,
        as_of=getattr(args, "as_of", None),
    )
    service = MarketDataService(workspace)
    record = service.record_snapshot(request)
    projection = service.project_tables()
    return {"ok": True, "record": record, "projection": projection}


def _handle_ingest(args: argparse.Namespace, workspace: Path) -> dict[str, object]:
    manual = load_manual_market_data_file(args.file)
    request = _snapshot_request_payload(
        securities=manual.securities,
        gaps=manual.gaps,
        as_of=getattr(args, "as_of", None),
    )
    service = MarketDataService(workspace)
    record = service.record_snapshot(request)
    projection = service.project_tables()
    return {"ok": True, "record": record, "projection": projection}


def handle(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace).expanduser().resolve()
    action = getattr(args, "market_data_action", "")
    as_json = bool(getattr(args, "json", False))
    try:
        if action == "fetch":
            payload = _handle_fetch(args, workspace)
        elif action == "ingest":
            payload = _handle_ingest(args, workspace)
        elif action == "project":
            payload = MarketDataService(workspace).project_tables()
        else:
            return 1
    except MarketDataError as exc:
        payload = {
            "ok": False,
            "status": "unavailable",
            "reason_code": str(exc),
            "boundary": "deterministic_market_data_acquisition_no_fabrication",
        }
        _print_payload(f"market-data {action}", payload, as_json=as_json)
        return 1
    except OSError as exc:
        payload = {
            "ok": False,
            "status": "unavailable",
            "reason_code": "market_data_workspace_unavailable",
            "detail": str(exc),
            "boundary": "deterministic_market_data_acquisition_no_fabrication",
        }
        _print_payload(f"market-data {action}", payload, as_json=as_json)
        return 1
    _print_payload(f"market-data {action}", payload, as_json=as_json)
    return 0
