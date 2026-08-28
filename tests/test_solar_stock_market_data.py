from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

import pytest

from multi_agent_brief.cli.init_wizard import create_demo_workspace
from multi_agent_brief.cli.main import main as cli_main
from multi_agent_brief.contracts.v2 import (
    CoreRunInitializeRequest,
    IntegrityCheckRequest,
    Invocation,
    InvocationStartRequest,
    MarketDataSecurityGapV1,
    MarketDataSecurityV1,
    MarketDataSnapshotV1,
)
from multi_agent_brief.control_store import SQLiteControlStore
from multi_agent_brief.control_store.serialization import (
    canonical_fingerprint,
    canonical_json_bytes,
)
from multi_agent_brief.core_run_v2 import CoreRunService
from multi_agent_brief.core_run_v2.integrity import read_workspace_file
from multi_agent_brief.core_run_v2.verifier import CoreRunDomainVerifier
from multi_agent_brief.sources.equity_universe import load_equity_universe
from multi_agent_brief.product.market_data_service import (
    MARKET_DATA_TABLES_PATH,
    MarketDataService,
    render_market_data_tables,
)
from multi_agent_brief.sources.market_data import (
    MarketDataError,
    MarketDataFetchOutcome,
    YahooMarketDataAdapter,
    load_manual_market_data_file,
    merge_manual_first,
)
from multi_agent_brief.sources.solar_stock_plan import (
    SOLAR_STOCK_OVERSEAS_SECURITIES,
    SOLAR_STOCK_PRIMARY_SECURITIES,
)
from pydantic import ValidationError


RUN_ID = "RUN-MARKET-DATA-001"
WORKSPACE_ID = "WS-MARKET-DATA-001"
NOW = "2026-08-07T09:00:00Z"
COMMITTED_AT = datetime(2026, 8, 7, 9, 0, tzinfo=timezone.utc)
_WEEK_START = int(datetime(2026, 8, 3, tzinfo=timezone.utc).timestamp())
_WEEK_END = int(datetime(2026, 8, 10, tzinfo=timezone.utc).timestamp())


def _security_payload(**overrides) -> dict[str, object]:
    payload: dict[str, object] = {
        "ticker": "DEMO",
        "exchange": "NasdaqCM",
        "currency": "USD",
        "as_of": "2026-08-10",
        "data_origin": "yahoo_chart_api",
        "week_open": 10.4,
        "week_high": 10.9,
        "week_low": 10.1,
        "week_close": 10.62,
        "week_volume": 1523400,
        "weekly_change_pct": 2.31,
        "market_cap": 812000000.0,
        "trailing_pe": None,
    }
    payload.update(overrides)
    return payload


def _snapshot_payload(**overrides) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": MarketDataSnapshotV1.schema_id,
        "market_data_snapshot_id": "MARKET-DATA-SNAPSHOT-TEST-001",
        "run_id": RUN_ID,
        "as_of_date": "2026-08-10",
        "security_count": 1,
        "provider_id": "yahoo_finance_chart",
        "securities": [_security_payload()],
        "gaps": [{"ticker": "DQ", "failure_class": "transport_unavailable"}],
        "record_event_id": "EVT-MARKET-DATA-TEST-001",
        "accepted_transaction_id": "TXN-MARKET-DATA-TEST-001",
        "recorded_at": NOW,
    }
    payload.update(overrides)
    payload["snapshot_fingerprint"] = canonical_fingerprint(payload)
    return payload


def _snapshot(**overrides) -> MarketDataSnapshotV1:
    return MarketDataSnapshotV1.model_validate(_snapshot_payload(**overrides))


def _yahoo_chart_body(
    *,
    currency: str = "USD",
    exchange: str = "NasdaqCM",
    market_cap: object = 812000000,
    closes: tuple = (10.36, 10.62),
) -> bytes:
    meta: dict[str, object] = {
        "currency": currency,
        "fullExchangeName": exchange,
        "regularMarketPrice": closes[-1],
    }
    if market_cap is not None:
        meta["marketCap"] = market_cap
    return json.dumps(
        {
            "chart": {
                "error": None,
                "result": [
                    {
                        "meta": meta,
                        "timestamp": [_WEEK_START, _WEEK_END],
                        "indicators": {
                            "quote": [
                                {
                                    "open": [10.0, 10.4],
                                    "high": [10.5, 10.9],
                                    "low": [9.9, 10.1],
                                    "close": list(closes),
                                    "volume": [1200000, 1523400],
                                }
                            ]
                        },
                    }
                ],
            }
        }
    ).encode("utf-8")


class _Response:
    def __init__(self, body: bytes, status: int = 200):
        self.body = body
        self.status = status

    def read(self, limit: int = -1) -> bytes:
        return self.body if limit < 0 else self.body[:limit]

    def close(self) -> None:
        return None

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_args) -> bool:
        return False


def _mock_urlopen(bodies: dict[str, object]):
    def urlopen(request, timeout: int = 30):
        del timeout
        symbol = request.full_url.split("/chart/")[1].split("?")[0]
        outcome = bodies[symbol]
        if isinstance(outcome, Exception):
            raise outcome
        status, body = outcome
        if status != 200:
            import urllib.error

            raise urllib.error.HTTPError(
                request.full_url, status, "error", None, _Response(body, status)
            )
        return _Response(body, status)

    return urlopen


def _bootstrap_workspace(workspace: Path, *, invocation_active: bool = False) -> None:
    """Create a contract-bound Store; optionally inject an active test invocation."""

    create_demo_workspace(workspace)
    _initialize_core_run(workspace)
    if not invocation_active:
        return
    service = CoreRunService(workspace, clock=lambda: COMMITTED_AT)
    doctor = service.doctor_check(
        IntegrityCheckRequest.model_validate(
            {
                "schema_version": IntegrityCheckRequest.schema_id,
                "request_id": "REQ-MARKET-DATA-ACTIVE-DOCTOR",
                "run_id": RUN_ID,
                "expected_store_revision": 1,
            },
            strict=True,
        )
    )
    assert doctor.status == "committed"
    started = service.start_invocation(
        InvocationStartRequest.model_validate(
            {
                "schema_version": InvocationStartRequest.schema_id,
                "request_id": "REQ-MARKET-DATA-ACTIVE-INVOCATION",
                "run_id": RUN_ID,
                "stage_id": "source-discovery",
                "role_id": "source-planner",
                "runtime": "operator",
                "expected_store_revision": 2,
            },
            strict=True,
        )
    )
    assert started.status == "committed"
    invocation = Invocation.model_validate(
        {
            "schema_version": Invocation.schema_id,
            "invocation_id": started.primary_record_id,
            "run_id": RUN_ID,
            "role_id": "source-planner",
            "runtime": "operator",
            "status": "active",
            "started_at": NOW,
            "completed_at": None,
            "failure_reason": None,
        },
        strict=True,
    )
    assert invocation.status == "active"


def _record_request(*, as_of: str = "2026-08-10", **security_overrides):
    return {
        "schema_version": "briefloop.market_data_record_input.v1",
        "as_of_date": as_of,
        "securities": [_security_payload(**security_overrides)],
        "gaps": [{"ticker": "DQ", "failure_class": "transport_unavailable"}],
    }


def _bind_init_payload(payload: dict[str, object]) -> dict[str, object]:
    binding = dict(payload["runtime_adapter_binding"])  # type: ignore[arg-type]
    binding["run_id"] = payload["run_id"]
    binding["runtime"] = payload["runtime"]
    supported = set(binding["supported_role_topologies"])  # type: ignore[arg-type]
    supported.add(str(payload["role_topology"]))
    binding["supported_role_topologies"] = sorted(supported)
    binding.pop("binding_fingerprint", None)
    binding["binding_fingerprint"] = canonical_fingerprint(binding)
    payload["runtime_adapter_binding"] = binding
    return payload


def _initialize_core_run(workspace: Path) -> None:
    service = CoreRunService(workspace, clock=lambda: COMMITTED_AT)
    request = deepcopy(CoreRunInitializeRequest.minimal_example)
    request.update(
        request_id="REQ-INIT-001",
        workspace_id=WORKSPACE_ID,
        run_id=RUN_ID,
        role_topology="default",
        input_governance_required=False,
        workspace_config_sha256=read_workspace_file(workspace, "config.yaml").sha256,
        sources_config_sha256=read_workspace_file(workspace, "sources.yaml").sha256,
    )
    request["run_direction"].update(
        report_type="solar_stock_periodic",
        report_date="2026-08-10",
        report_window_start="2026-08-03",
        report_window_end="2026-08-10",
    )
    result = service.initialize(
        CoreRunInitializeRequest.model_validate(
            _bind_init_payload(request), strict=True
        )
    )
    assert result.status == "committed", result.to_dict()


# ── DTO validation ────────────────────────────────────────────────────────────












# ── Yahoo adapter (mocked transport) ─────────────────────────────────────────






def test_yahoo_adapter_marks_missing_bars_without_fabricating(monkeypatch) -> None:
    body = json.dumps(
        {
            "chart": {
                "error": None,
                "result": [
                    {
                        "meta": {"currency": "USD", "fullExchangeName": "NasdaqCM"},
                        "timestamp": [_WEEK_START],
                        "indicators": {"quote": [{"close": [None]}]},
                    }
                ],
            }
        }
    ).encode()
    monkeypatch.setattr("urllib.request.urlopen", _mock_urlopen({"DQ": (200, body)}))
    outcome = YahooMarketDataAdapter().fetch_weekly(["DQ"])
    assert outcome.securities == ()
    assert [item.failure_class for item in outcome.gaps] == ["symbol_data_missing"]




# ── Manual input channel ──────────────────────────────────────────────────────










# ── Store recording ───────────────────────────────────────────────────────────










def test_snapshots_are_append_only(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    _bootstrap_workspace(workspace)
    MarketDataService(workspace).record_snapshot(_record_request())
    connection = sqlite3.connect(workspace / "briefloop.db")
    try:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE market_data_snapshots SET security_count = 2")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("DELETE FROM market_data_snapshots")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO market_data_snapshots SELECT * FROM market_data_snapshots"
            )
    finally:
        connection.close()


def test_tampered_snapshot_graph_fails_closed(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    _bootstrap_workspace(workspace)
    result = MarketDataService(workspace).record_snapshot(_record_request())
    connection = sqlite3.connect(workspace / "briefloop.db")
    try:
        connection.execute("PRAGMA foreign_keys = OFF")
        row = connection.execute(
            "SELECT * FROM market_data_snapshots WHERE run_id = ?",
            (RUN_ID,),
        ).fetchone()
        columns = [
            item[1]
            for item in connection.execute("PRAGMA table_info(market_data_snapshots)")
        ]
        values = dict(zip(columns, row, strict=True))
        payload = json.loads(values["payload_json"])
        payload.pop("snapshot_fingerprint")
        payload["market_data_snapshot_id"] = "MARKET-DATA-SNAPSHOT-FORGED"
        payload["record_event_id"] = "EVT-MARKET-DATA-FORGED"
        payload["as_of_date"] = "2026-08-11"
        payload["snapshot_fingerprint"] = canonical_fingerprint(payload)
        values.update(
            {
                "market_data_snapshot_id": payload["market_data_snapshot_id"],
                "as_of_date": payload["as_of_date"],
                "snapshot_fingerprint": payload["snapshot_fingerprint"],
                "payload_json": canonical_json_bytes(payload).decode("utf-8"),
            }
        )
        connection.execute(
            "INSERT INTO market_data_snapshots VALUES "
            f"({', '.join('?' for _ in columns)})",
            tuple(values[column] for column in columns),
        )
        connection.commit()
    finally:
        connection.close()
    assert result["ok"]
    with pytest.raises(Exception) as excinfo:
        with SQLiteControlStore.open(workspace / "briefloop.db") as store:
            store.load_snapshot(RUN_ID)
    assert str(excinfo.value) in {
        "market_data_snapshot_graph_invalid",
        "stored_payload_invalid",
    }




# ── Projection ────────────────────────────────────────────────────────────────










# ── CLI ───────────────────────────────────────────────────────────────────────


def test_cli_ingest_records_and_projects(tmp_path: Path, capsys) -> None:
    workspace = tmp_path / "workspace"
    _bootstrap_workspace(workspace)
    manual = tmp_path / "weekly.csv"
    manual.write_text(
        "ticker,exchange,currency,as_of,week_open,week_high,week_low,"
        "week_close,week_volume,weekly_change_pct,market_cap,trailing_pe\n"
        "DEMO,NasdaqCM,USD,2026-08-10,,,,10.62,,,,\n",
        encoding="utf-8",
    )
    status = cli_main(
        [
            "market-data",
            "ingest",
            "--workspace",
            str(workspace),
            "--file",
            str(manual),
            "--json",
        ]
    )
    assert status == 0
    output = json.loads(capsys.readouterr().out)
    assert output["ok"]
    assert output["record"]["security_count"] == 1
    assert (workspace / MARKET_DATA_TABLES_PATH).exists()

    conflict = tmp_path / "conflict.csv"
    conflict.write_text(
        "ticker,exchange,currency,as_of,week_open,week_high,week_low,"
        "week_close,week_volume,weekly_change_pct,market_cap,trailing_pe\n"
        "DEMO,NasdaqCM,USD,2026-08-10,,,,11.01,,,,\n",
        encoding="utf-8",
    )
    status = cli_main(
        [
            "market-data",
            "ingest",
            "--workspace",
            str(workspace),
            "--file",
            str(conflict),
            "--json",
        ]
    )
    assert status == 1
    output = json.loads(capsys.readouterr().out)
    assert output["reason_code"] == "market_data_snapshot_conflict"


def test_cli_fetch_merges_manual_first_and_never_fabricates(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    workspace = tmp_path / "workspace"
    _bootstrap_workspace(workspace)
    (workspace / "report_spec.yaml").write_text(
        "\n".join(
            [
                "metadata:",
                "  core_tickers: [DEMO]",
                "  primary_tickers: [DEMO, TE, FSLR, CSIQ, JKS, NXT, DQ]",
                "  overseas_tickers: [009830.KS, WAAREEENER.NS, PREMIERENE.NS, VIKRAMSOLR.NS]",
                "",
            ]
        ),
        encoding="utf-8",
    )
    input_dir = workspace / "input" / "market_data"
    input_dir.mkdir(parents=True)
    (input_dir / "manual.json").write_text(
        json.dumps(
            {
                "securities": [
                    {
                        "ticker": "DQ",
                        "exchange": "NYSE",
                        "currency": "USD",
                        "as_of": "2026-08-10",
                        "week_close": 2.34,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    bodies = {
        symbol: (200, _yahoo_chart_body())
        for symbol in ("DEMO", *SOLAR_STOCK_PRIMARY_SECURITIES)
        if symbol != "DQ"
    }
    monkeypatch.setattr("urllib.request.urlopen", _mock_urlopen(bodies))
    status = cli_main(
        [
            "market-data",
            "fetch",
            "--workspace",
            str(workspace),
            "--json",
        ]
    )
    assert status == 0
    output = json.loads(capsys.readouterr().out)
    assert output["ok"]
    assert output["record"]["security_count"] == 7
    assert output["record"]["gap_count"] == 4
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        record = store.load_snapshot(RUN_ID).market_data_snapshots[0]
    by_ticker = {item.ticker: item for item in record.securities}
    dq_close = next(
        item for item in by_ticker["DQ"].fields if item.field_id == "latest_close_local"
    )
    assert by_ticker["DQ"].price_series[0].data_origin == "manual_json"
    assert dq_close.value_number == 2.34
    assert by_ticker["DEMO"].price_series[0].data_origin == "yahoo_chart_api"
    assert {gap.ticker for gap in record.gaps} == set(SOLAR_STOCK_OVERSEAS_SECURITIES)




