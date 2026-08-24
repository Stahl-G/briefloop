"""Market data channel CLI commands.

Exposes the solar stock weekly market data channel to a workspace:
``fetch`` pulls the Yahoo chart API (manual files under
``input/market_data/`` take precedence), ``ingest`` records one manual
JSON/CSV file or one profile-bound XLSX workbook without network access, and
``project`` re-renders deterministic comparison tables, JSON, and chart
projections from the latest frozen snapshot.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from multi_agent_brief.product.market_data_service import (
    MarketDataService,
)
from multi_agent_brief.sources.equity_universe import load_equity_universe
from multi_agent_brief.sources.market_data import (
    MARKET_DATA_INPUT_DIR,
    MarketDataError,
    YahooMarketDataAdapter,
    load_manual_market_data_file,
    merge_manual_first,
)
from multi_agent_brief.sources.market_data_xlsx import (
    TOYO_WEEKLY_XLSX_PROFILE_ID,
    parse_toyo_weekly_xlsx,
)
from multi_agent_brief.sources.market_data_v2 import (
    YahooMarketDataV2Adapter,
    merge_manual_workbook_with_yahoo,
)


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
        "--workbook",
        help=(
            "Optional Solar Stock Periodic XLSX workbook. Manual cells win and "
            "Yahoo supplies missing history, securities, FX, and fields before "
            "one snapshot is committed."
        ),
    )
    fetch_parser.add_argument(
        "--profile",
        choices=[TOYO_WEEKLY_XLSX_PROFILE_ID],
        help="Required deterministic workbook profile when --workbook is used.",
    )
    fetch_parser.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON."
    )

    ingest_parser = actions.add_parser(
        "ingest",
        help=(
            "Freeze one manual market data file (JSON, CSV, or XLSX) without any "
            "network access, then project the comparison tables."
        ),
    )
    ingest_parser.add_argument(
        "--workspace", required=True, help="Path to workspace directory."
    )
    ingest_parser.add_argument(
        "--file", required=True, help="Manual market data file (JSON, CSV, or XLSX)."
    )
    ingest_parser.add_argument(
        "--profile",
        choices=[TOYO_WEEKLY_XLSX_PROFILE_ID],
        help="Required deterministic workbook profile for XLSX input.",
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
            "Re-render market-data tables, JSON, deterministic PNG charts, and "
            "the chart manifest from the latest frozen snapshot."
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
        "conflict_count",
        "snapshot_fingerprint",
        "receipt_id",
        "path",
        "projection_path",
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
        "schema_version": "briefloop.market_data_record_input.v1",
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
    service = MarketDataService(workspace)
    service.require_recording_allowed()
    workbook_path = getattr(args, "workbook", None)
    if workbook_path is not None:
        if getattr(args, "profile", None) != TOYO_WEEKLY_XLSX_PROFILE_ID:
            raise MarketDataError("market_data_xlsx_profile_required")
        parsed = parse_toyo_weekly_xlsx(
            Path(workbook_path).expanduser().resolve(),
            universe=load_equity_universe(workspace),
        )
        requested_as_of = getattr(args, "as_of", None)
        if requested_as_of is not None and requested_as_of != parsed.as_of_date:
            raise MarketDataError("market_data_xlsx_as_of_mismatch")
        universe = load_equity_universe(workspace)
        provider = YahooMarketDataV2Adapter().fetch(
            universe.watchlist,
            as_of_date=parsed.as_of_date,
            core_tickers=universe.core_tickers,
            benchmark_ticker=universe.benchmark_ticker,
        )
        request = merge_manual_workbook_with_yahoo(parsed.record_payload(), provider)
        # Provider calls happen only after the first read-only preflight.  The
        # Store service repeats the active-invocation check inside its commit.
        record = service.record_snapshot(request)
        projection = service.project_tables()
        return {"ok": True, "record": record, "projection": projection}
    if getattr(args, "profile", None) is not None:
        raise MarketDataError("market_data_profile_not_applicable")
    manuals = _load_manual_inputs(workspace)
    fetched = YahooMarketDataAdapter().fetch_weekly(
        load_equity_universe(workspace).watchlist
    )
    merged = merge_manual_first(manuals, fetched)
    request = _snapshot_request_payload(
        securities=merged.securities,
        gaps=merged.gaps,
        as_of=getattr(args, "as_of", None),
    )
    record = service.record_snapshot(request)
    projection = service.project_tables()
    return {"ok": True, "record": record, "projection": projection}


def _handle_ingest(args: argparse.Namespace, workspace: Path) -> dict[str, object]:
    service = MarketDataService(workspace)
    service.require_recording_allowed()
    file_path = Path(args.file).expanduser().resolve()
    if file_path.suffix.lower() == ".xlsx":
        if getattr(args, "profile", None) != TOYO_WEEKLY_XLSX_PROFILE_ID:
            raise MarketDataError("market_data_xlsx_profile_required")
        parsed = parse_toyo_weekly_xlsx(
            file_path, universe=load_equity_universe(workspace)
        )
        requested_as_of = getattr(args, "as_of", None)
        if requested_as_of is not None and requested_as_of != parsed.as_of_date:
            raise MarketDataError("market_data_xlsx_as_of_mismatch")
        request = parsed.record_payload()
    else:
        if getattr(args, "profile", None) is not None:
            raise MarketDataError("market_data_profile_not_applicable")
        manual = load_manual_market_data_file(file_path)
        request = _snapshot_request_payload(
            securities=manual.securities,
            gaps=manual.gaps,
            as_of=getattr(args, "as_of", None),
        )
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
