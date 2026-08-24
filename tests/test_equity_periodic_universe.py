from __future__ import annotations

from multi_agent_brief.sources.equity_universe import (
    DEFAULT_SOLAR_EQUITY_UNIVERSE,
    EquityPeriodicUniverse,
    is_packaged_solar_universe,
    listed_company_search_tasks,
    universe_from_mapping,
)
from multi_agent_brief.sources.solar_stock_plan import (
    search_tasks_for_universe,
    solar_stock_search_tasks,
)


def test_packaged_solar_universe_is_the_default_preset() -> None:
    universe = DEFAULT_SOLAR_EQUITY_UNIVERSE
    assert universe.core_tickers == ("TOYO",)
    assert len(universe.watchlist) == 11
    assert universe.benchmark_ticker == "TAN"
    assert is_packaged_solar_universe(universe)


def test_custom_watchlist_does_not_inherit_solar_peers_or_tan() -> None:
    universe = universe_from_mapping(
        {
            "primary_tickers": ["AAPL", "MSFT"],
            "overseas_tickers": [],
            "core_tickers": ["AAPL"],
            "event_only_entities": [],
            "benchmark_ticker": None,
        }
    )
    assert universe == EquityPeriodicUniverse(
        core_tickers=("AAPL",),
        primary_tickers=("AAPL", "MSFT"),
        overseas_tickers=(),
        event_only_entities=(),
        benchmark_ticker=None,
    )
    assert not is_packaged_solar_universe(universe)


def test_solar_watchlist_without_explicit_benchmark_keeps_tan() -> None:
    universe = universe_from_mapping(
        {
            "primary_tickers": list(DEFAULT_SOLAR_EQUITY_UNIVERSE.primary_tickers),
            "overseas_tickers": list(DEFAULT_SOLAR_EQUITY_UNIVERSE.overseas_tickers),
        }
    )
    assert universe is not None
    assert universe.core_tickers == ("TOYO",)
    assert universe.benchmark_ticker == "TAN"


def test_search_tasks_stay_frozen_for_solar_and_generic_otherwise() -> None:
    assert search_tasks_for_universe(DEFAULT_SOLAR_EQUITY_UNIVERSE) == (
        solar_stock_search_tasks()
    )
    custom = universe_from_mapping(
        {
            "primary_tickers": ["AAPL", "MSFT"],
            "overseas_tickers": [],
            "event_only_entities": [],
        }
    )
    assert custom is not None
    tasks = search_tasks_for_universe(custom)
    assert [item["entity_id"] for item in tasks] == ["AAPL", "MSFT"]
    assert tasks == listed_company_search_tasks(custom)
    assert all(str(item["task_id"]).startswith("equity-listed-") for item in tasks)
