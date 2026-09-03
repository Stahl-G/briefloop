from __future__ import annotations

from multi_agent_brief.sources.equity_universe import (
    DEFAULT_SOLAR_EQUITY_UNIVERSE,
    is_packaged_solar_universe,
    universe_from_mapping,
)
from multi_agent_brief.sources.solar_stock_plan import (
    search_tasks_for_universe,
    solar_stock_search_tasks,
)


def _universe_with_core(core: str, name: str = "") -> object:
    metadata: dict[str, object] = {
        "core_tickers": [core],
        "primary_tickers": [core, *DEFAULT_SOLAR_EQUITY_UNIVERSE.primary_tickers],
        "overseas_tickers": list(DEFAULT_SOLAR_EQUITY_UNIVERSE.overseas_tickers),
        "event_only_entities": list(DEFAULT_SOLAR_EQUITY_UNIVERSE.event_only_entities),
        "benchmark_ticker": "TAN",
    }
    if name:
        metadata["core_names"] = [name]
    return universe_from_mapping(metadata)


def test_packaged_preset_carries_peers_but_no_issuer() -> None:
    universe = DEFAULT_SOLAR_EQUITY_UNIVERSE
    assert universe.core_tickers == ()
    assert len(universe.watchlist) == 10
    assert universe.benchmark_ticker == "TAN"
    assert is_packaged_solar_universe(universe)
    # The packaged sector plan alone carries no issuer-bound task.
    assert len(solar_stock_search_tasks()) == 19


def test_explicit_core_subject_gets_its_own_search_task() -> None:
    universe = _universe_with_core("DEMO", "Demo Solar Co.")
    assert universe is not None
    assert is_packaged_solar_universe(universe)

    tasks = search_tasks_for_universe(universe)
    assert len(tasks) == 20
    core_task = next(
        item for item in tasks if item["task_id"] == "solar-stock-listed-demo"
    )
    assert core_task["entity_id"] == "DEMO"
    assert core_task["query"] == (
        "Demo Solar Co. DEMO earnings guidance orders financing capacity asset disposal"
    )


def test_custom_watchlist_falls_back_to_generic_tasks() -> None:
    custom = universe_from_mapping(
        {
            "primary_tickers": ["AAPL", "MSFT"],
            "overseas_tickers": [],
            "core_tickers": ["AAPL"],
            "event_only_entities": [],
            "benchmark_ticker": None,
        }
    )
    assert custom is not None
    assert not is_packaged_solar_universe(custom)
    tasks = search_tasks_for_universe(custom)
    assert [item["entity_id"] for item in tasks] == ["AAPL", "MSFT"]
    assert all(str(item["task_id"]).startswith("equity-listed-") for item in tasks)
