from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from multi_agent_brief.cli.init_wizard import create_demo_workspace
from multi_agent_brief.cli.main import main as cli_main
from multi_agent_brief.contracts.v2 import (
    Approval,
    ArtifactRecord,
    ArtifactRevision,
    CoreRunInitializeRequest,
    Delivery,
    EventEnvelope,
    Invocation,
    MarketDataSecurityGapV1,
    MarketDataSecurityV1,
    MarketDataSnapshotV1,
    RunIdentity,
    StageState,
    WorkspaceRunHead,
)
from multi_agent_brief.control_store import SQLiteControlStore
from multi_agent_brief.control_store.serialization import (
    canonical_fingerprint,
    canonical_json_bytes,
)
from multi_agent_brief.core_run_v2 import CoreRunService
from multi_agent_brief.core_run_v2.integrity import read_workspace_file
from multi_agent_brief.core_run_v2.verifier import CoreRunDomainVerifier
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
BLOB = b"BriefLoop market data channel test artifact.\n"
BLOB_SHA256 = hashlib.sha256(BLOB).hexdigest()

_WEEK_START = int(datetime(2026, 8, 3, tzinfo=timezone.utc).timestamp())
_WEEK_END = int(datetime(2026, 8, 10, tzinfo=timezone.utc).timestamp())


def _security_payload(**overrides) -> dict[str, object]:
    payload: dict[str, object] = {
        "ticker": "TOYO",
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


def _bootstrap_workspace(workspace: Path) -> None:
    """Create one raw Store workspace with a current run head."""

    workspace.mkdir(parents=True, exist_ok=True)
    store = SQLiteControlStore.create(
        workspace / "briefloop.db",
        workspace_id=WORKSPACE_ID,
        clock=lambda: COMMITTED_AT,
    )

    def record(model_type, **values):
        return model_type.model_validate(
            {"schema_version": model_type.schema_id, **values}
        )

    try:
        unit = store.begin(RUN_ID, "TX-MARKET-DATA-BOOTSTRAP", "bootstrap", 0)
        unit.put_run(
            record(
                RunIdentity,
                run_id=RUN_ID,
                workspace_id=WORKSPACE_ID,
                runtime="operator",
                created_at=NOW,
            )
        )
        unit.put_workspace_run_head(
            record(
                WorkspaceRunHead,
                workspace_id=WORKSPACE_ID,
                current_run_id=RUN_ID,
                updated_at=NOW,
            )
        )
        unit.put_stage_state(
            record(
                StageState,
                run_id=RUN_ID,
                stage_id="scout",
                status="ready",
                revision=1,
                updated_at=NOW,
            )
        )
        unit.put_invocation(
            record(
                Invocation,
                invocation_id="INV-MARKET-DATA-001",
                run_id=RUN_ID,
                role_id="scout",
                runtime="operator",
                status="completed",
                started_at=NOW,
                completed_at=NOW,
            )
        )
        unit.put_artifact(
            record(
                ArtifactRecord,
                run_id=RUN_ID,
                artifact_id="brief",
                current_revision=1,
                status="valid",
                required=True,
                path=f"output/artifacts/{BLOB_SHA256}/brief.md",
                format="markdown",
            )
        )
        unit.put_artifact_revision(
            record(
                ArtifactRevision,
                run_id=RUN_ID,
                artifact_id="brief",
                revision=1,
                path=f"output/artifacts/{BLOB_SHA256}/brief.md",
                sha256=BLOB_SHA256,
                size_bytes=len(BLOB),
                frozen=True,
                producer_kind="workflow_stage",
                producer_id="scout",
                created_at=NOW,
            ),
            BLOB,
        )
        unit.append_event(
            record(
                EventEnvelope,
                event_id="EVT-MARKET-DATA-BOOTSTRAP",
                run_id=RUN_ID,
                event_type="run_initialized",
                created_at=NOW,
                actor="cli",
                transaction_id="TX-MARKET-DATA-BOOTSTRAP",
                decision="initialized",
                reason="Market data test bootstrap.",
            )
        )
        unit.commit()
    finally:
        store.close()


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
    result = service.initialize(
        CoreRunInitializeRequest.model_validate(
            _bind_init_payload(request), strict=True
        )
    )
    assert result.status == "committed", result.to_dict()


# ── DTO validation ────────────────────────────────────────────────────────────


def test_snapshot_dto_round_trips_and_recomputes_fingerprint() -> None:
    snapshot = _snapshot()
    assert snapshot.snapshot_fingerprint == canonical_fingerprint(
        snapshot.model_dump(mode="json", exclude={"snapshot_fingerprint"})
    )
    assert snapshot.security_count == 1
    assert snapshot.securities[0].trailing_pe is None


@pytest.mark.parametrize(
    "override",
    [
        {"schema_version": "briefloop.market_data_snapshot.v2"},
        {"provider_id": "yahoo_chart"},
        {"security_count": 2},
        {"security_count": 0},
        {"as_of_date": "2026-8-10"},
        {"snapshot_fingerprint": "0" * 64},
    ],
)
def test_snapshot_dto_rejects_constraint_violations(override) -> None:
    payload = _snapshot_payload()
    fingerprint_tamper = "snapshot_fingerprint" in override
    payload.update(override)
    if not fingerprint_tamper:
        payload["snapshot_fingerprint"] = canonical_fingerprint(
            {key: value for key, value in payload.items()}
        )
    with pytest.raises(ValidationError):
        MarketDataSnapshotV1.model_validate(payload, strict=True)


def test_snapshot_dto_rejects_unsorted_and_overlapping_tickers() -> None:
    payload = _snapshot_payload(
        securities=[
            _security_payload(ticker="TE"),
            _security_payload(ticker="DQ"),
        ],
        security_count=2,
    )
    with pytest.raises(ValidationError):
        MarketDataSnapshotV1.model_validate(payload, strict=True)

    payload = _snapshot_payload(
        gaps=[{"ticker": "TOYO", "failure_class": "http_error"}],
    )
    with pytest.raises(ValidationError):
        MarketDataSnapshotV1.model_validate(payload, strict=True)


def test_snapshot_dto_rejects_twelve_securities() -> None:
    payload = _snapshot_payload(
        securities=[_security_payload(ticker=f"T{item:02d}") for item in range(12)],
        security_count=12,
    )
    with pytest.raises(ValidationError):
        MarketDataSnapshotV1.model_validate(payload, strict=True)


def test_security_dto_rejects_incoherent_or_negative_bar() -> None:
    with pytest.raises(ValidationError):
        _snapshot(securities=[_security_payload(week_high=9.0, week_low=10.1)])
    with pytest.raises(ValidationError):
        _snapshot(securities=[_security_payload(week_close=-1.0)])
    with pytest.raises(ValidationError):
        _snapshot(securities=[_security_payload(currency="usd")])


# ── Yahoo adapter (mocked transport) ─────────────────────────────────────────


def test_yahoo_adapter_parses_weekly_bar_and_explicit_nulls(monkeypatch) -> None:
    monkeypatch.setattr(
        "urllib.request.urlopen",
        _mock_urlopen({"TOYO": (200, _yahoo_chart_body())}),
    )
    outcome = YahooMarketDataAdapter().fetch_weekly(["TOYO"])
    assert not outcome.gaps
    security = outcome.securities[0]
    assert security.ticker == "TOYO"
    assert security.as_of == "2026-08-10"
    assert security.week_close == 10.62
    assert security.weekly_change_pct == round((10.62 / 10.36 - 1) * 100, 4)
    assert security.market_cap == 812000000.0
    assert security.trailing_pe is None
    assert security.data_origin == "yahoo_chart_api"


def test_yahoo_adapter_isolates_single_security_failures(monkeypatch) -> None:
    bodies = {
        "TOYO": (200, _yahoo_chart_body()),
        "TE": (200, b"not-json"),
        "FSLR": (500, b"{}"),
        "CSIQ": (200, json.dumps({"chart": {"error": None, "result": []}}).encode()),
        "JKS": ConnectionError("sandbox host must not leak"),
    }
    monkeypatch.setattr("urllib.request.urlopen", _mock_urlopen(bodies))
    outcome = YahooMarketDataAdapter().fetch_weekly(
        ["TOYO", "TE", "FSLR", "CSIQ", "JKS"]
    )
    assert [item.ticker for item in outcome.securities] == ["TOYO"]
    gaps = {item.ticker: item.failure_class for item in outcome.gaps}
    assert gaps == {
        "TE": "response_invalid",
        "FSLR": "http_error",
        "CSIQ": "response_invalid",
        "JKS": "transport_unavailable",
    }
    assert "sandbox host" not in json.dumps(
        [item.model_dump(mode="json") for item in outcome.gaps]
    )


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


def test_yahoo_adapter_rejects_overbound_response(monkeypatch) -> None:
    overbound = _yahoo_chart_body() + b"x" * (2 * 1024 * 1024)
    monkeypatch.setattr(
        "urllib.request.urlopen", _mock_urlopen({"TOYO": (200, overbound)})
    )
    outcome = YahooMarketDataAdapter().fetch_weekly(["TOYO"])
    assert outcome.securities == ()
    assert [item.failure_class for item in outcome.gaps] == ["response_invalid"]


# ── Manual input channel ──────────────────────────────────────────────────────


def test_manual_json_file_validates_rows_and_defaults(tmp_path: Path) -> None:
    path = tmp_path / "weekly.json"
    path.write_text(
        json.dumps(
            {
                "as_of_date": "2026-08-10",
                "securities": [
                    {
                        "ticker": "TOYO",
                        "exchange": "NasdaqCM",
                        "currency": "USD",
                        "week_close": 10.62,
                        "market_cap": 812000000,
                    },
                    {"ticker": "DQ", "exchange": "NYSE", "currency": "USD"},
                ],
            }
        ),
        encoding="utf-8",
    )
    manual = load_manual_market_data_file(path)
    assert len(manual.securities) == 1
    security = manual.securities[0]
    assert security.as_of == "2026-08-10"
    assert security.data_origin == "manual_input"
    assert security.week_open is None
    assert security.trailing_pe is None
    assert [item.failure_class for item in manual.gaps] == ["manual_record_invalid"]
    assert manual.gaps[0].ticker == "DQ"


def test_manual_csv_file_parses_empty_cells_as_nulls(tmp_path: Path) -> None:
    path = tmp_path / "weekly.csv"
    path.write_text(
        "ticker,exchange,currency,as_of,week_open,week_high,week_low,"
        "week_close,week_volume,weekly_change_pct,market_cap,trailing_pe\n"
        "TOYO,NasdaqCM,USD,2026-08-10,10.40,10.90,10.10,10.62,1523400,2.31,812000000,\n",
        encoding="utf-8",
    )
    manual = load_manual_market_data_file(path)
    assert not manual.gaps
    security = manual.securities[0]
    assert security.week_close == 10.62
    assert security.week_volume == 1523400
    assert security.trailing_pe is None


def test_manual_file_unparseable_is_fail_closed(tmp_path: Path) -> None:
    path = tmp_path / "weekly.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(MarketDataError):
        load_manual_market_data_file(path)
    csv_path = tmp_path / "weekly.csv"
    csv_path.write_text("ticker,close\nTOYO,10.62\n", encoding="utf-8")
    with pytest.raises(MarketDataError):
        load_manual_market_data_file(csv_path)


def test_manual_input_wins_over_api_for_same_ticker(tmp_path: Path) -> None:
    path = tmp_path / "weekly.json"
    path.write_text(
        json.dumps(
            {
                "securities": [
                    {
                        "ticker": "TOYO",
                        "exchange": "NasdaqCM",
                        "currency": "USD",
                        "as_of": "2026-08-09",
                        "week_close": 11.11,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    manual = load_manual_market_data_file(path)
    api_security = MarketDataSecurityV1.model_validate(_security_payload(), strict=True)
    fetched = MarketDataFetchOutcome(
        securities=(api_security,),
        # The API-side gap for the same ticker must be resolved by the
        # manual row.
        gaps=(
            MarketDataSecurityGapV1.model_validate(
                {"ticker": "TOYO", "failure_class": "http_error"}, strict=True
            ),
        ),
    )
    merged = merge_manual_first([manual], fetched)
    assert [item.ticker for item in merged.securities] == ["TOYO"]
    assert merged.securities[0].data_origin == "manual_input"
    assert merged.securities[0].week_close == 11.11
    assert merged.gaps == ()


# ── Store recording ───────────────────────────────────────────────────────────


def test_record_replay_and_as_of_conflict(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    _bootstrap_workspace(workspace)
    service = MarketDataService(workspace)

    result = service.record_snapshot(_record_request())
    assert result["ok"] and not result["replayed"]
    replay = service.record_snapshot(_record_request())
    assert replay["replayed"]
    assert replay["receipt_id"] == result["receipt_id"]

    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        snapshot = store.load_snapshot(RUN_ID)
    assert len(snapshot.market_data_snapshots) == 1
    record = snapshot.market_data_snapshots[0]
    assert record.as_of_date == "2026-08-10"
    assert record.security_count == 1
    assert [gap.ticker for gap in record.gaps] == ["DQ"]

    with pytest.raises(MarketDataError) as excinfo:
        service.record_snapshot(_record_request(week_close=11.0))
    assert str(excinfo.value) == "market_data_snapshot_conflict"
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        assert len(store.load_snapshot(RUN_ID).market_data_snapshots) == 1


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
                "INSERT INTO market_data_snapshots("
                "run_id, market_data_snapshot_id, schema_version, as_of_date,"
                " security_count, provider_id, snapshot_fingerprint,"
                " accepted_transaction_id, recorded_at, payload_json)"
                " SELECT run_id, 'MARKET-DATA-SNAPSHOT-COPY', schema_version,"
                " as_of_date, security_count, provider_id, snapshot_fingerprint,"
                " accepted_transaction_id, recorded_at, payload_json"
                " FROM market_data_snapshots"
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
    assert "market_data_snapshot_graph_invalid" in str(excinfo.value)


def test_recording_survives_full_core_run_verification(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    create_demo_workspace(workspace)
    _initialize_core_run(workspace)
    result = MarketDataService(workspace).record_snapshot(_record_request())
    assert result["ok"]
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        verified = CoreRunDomainVerifier().verify(store, RUN_ID)
    assert len(verified.snapshot.market_data_snapshots) == 1
    record = verified.snapshot.market_data_snapshots[0]
    assert record.provider_id == "yahoo_finance_chart"
    event = next(
        item
        for item in verified.snapshot.events
        if item.event_id == record.record_event_id
    )
    assert event.event_type == "market_data_snapshot_recorded"
    assert event.decision == record.market_data_snapshot_id


# ── Projection ────────────────────────────────────────────────────────────────


def test_projection_renders_both_tables_with_not_available_rows(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    _bootstrap_workspace(workspace)
    service = MarketDataService(workspace)
    service.record_snapshot(_record_request())
    projection = service.project_tables()
    assert projection["ok"]
    text = (workspace / MARKET_DATA_TABLES_PATH).read_text(encoding="utf-8")
    assert "## Primary Equity Comparison" in text
    assert "## Overseas Equity Comparison" in text
    assert "| TOYO | NasdaqCM | USD | 2026-08-10 | 10.62 | +2.31 |" in text
    dq_row = next(line for line in text.splitlines() if line.startswith("| DQ |"))
    assert dq_row.count("NOT AVAILABLE") == 8
    assert "- DQ: transport_unavailable" in text
    header = text.splitlines()[1]
    assert "snapshot_fingerprint" in text and header.startswith("run_id:")


def test_projection_requires_a_frozen_snapshot(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    _bootstrap_workspace(workspace)
    with pytest.raises(MarketDataError) as excinfo:
        MarketDataService(workspace).project_tables()
    assert str(excinfo.value) == "market_data_snapshot_unavailable"


def test_render_covers_the_full_solar_universe() -> None:
    securities = [
        _security_payload(ticker=ticker, trailing_pe=18.5)
        for ticker in sorted(
            SOLAR_STOCK_PRIMARY_SECURITIES + SOLAR_STOCK_OVERSEAS_SECURITIES
        )
    ]
    snapshot = _snapshot(
        securities=securities,
        security_count=len(securities),
        gaps=[],
    )
    text = render_market_data_tables(snapshot)
    assert "| NOT AVAILABLE |" not in text
    for ticker in SOLAR_STOCK_PRIMARY_SECURITIES:
        assert f"| {ticker} |" in text
    assert "- none" in text


# ── CLI ───────────────────────────────────────────────────────────────────────


def test_cli_ingest_records_and_projects(tmp_path: Path, capsys) -> None:
    workspace = tmp_path / "workspace"
    _bootstrap_workspace(workspace)
    manual = tmp_path / "weekly.csv"
    manual.write_text(
        "ticker,exchange,currency,as_of,week_open,week_high,week_low,"
        "week_close,week_volume,weekly_change_pct,market_cap,trailing_pe\n"
        "TOYO,NasdaqCM,USD,2026-08-10,,,,10.62,,,,\n",
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
        "TOYO,NasdaqCM,USD,2026-08-10,,,,11.01,,,,\n",
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
        for symbol in SOLAR_STOCK_PRIMARY_SECURITIES
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
    assert by_ticker["DQ"].data_origin == "manual_input"
    assert by_ticker["DQ"].week_close == 2.34
    assert by_ticker["TOYO"].data_origin == "yahoo_chart_api"
    assert {gap.ticker for gap in record.gaps} == set(SOLAR_STOCK_OVERSEAS_SECURITIES)


def test_cli_fetch_total_failure_records_nothing(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    workspace = tmp_path / "workspace"
    _bootstrap_workspace(workspace)

    def urlopen(request, timeout: int = 30):
        raise ConnectionError("offline")

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    status = cli_main(["market-data", "fetch", "--workspace", str(workspace), "--json"])
    assert status == 1
    output = json.loads(capsys.readouterr().out)
    assert output["reason_code"] == "market_data_unavailable"
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        assert store.load_snapshot(RUN_ID).market_data_snapshots == ()
