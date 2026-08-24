"""Solar preset discovery plan for the equity-periodic engine."""

from __future__ import annotations

from copy import deepcopy

from multi_agent_brief.sources.equity_universe import (
    DEFAULT_SOLAR_EQUITY_UNIVERSE,
    EquityPeriodicUniverse,
    SOLAR_STOCK_CORE_SECURITIES,
    SOLAR_STOCK_EVENT_ONLY_ENTITIES,
    SOLAR_STOCK_OVERSEAS_SECURITIES,
    SOLAR_STOCK_PRIMARY_SECURITIES,
    listed_company_search_tasks,
)


def _task(
    task_id: str,
    task_category: str,
    query: str,
    *,
    entity_id: str | None = None,
    topic: str = "news",
    minimum_extract_successes: int = 2,
    backfill_query: str | None = None,
    backfill_domains: tuple[str, ...] = (),
) -> dict[str, object]:
    return {
        "task_id": task_id,
        "task_category": task_category,
        "entity_id": entity_id,
        "query": query,
        "topic": topic,
        "domains": [],
        "max_results": 20,
        "recency_days": 7,
        "search_depth": "advanced",
        "minimum_extract_successes": minimum_extract_successes,
        "backfill": {
            "enabled": True,
            "recency_days": 30,
            "query": backfill_query
            or f"{query} official filing investor relations",
            "domains": sorted(backfill_domains),
            "max_results": 20,
            "search_depth": "advanced",
        },
    }


_SOLAR_STOCK_SEARCH_TASKS: Final[tuple[dict[str, object], ...]] = (
    _task(
        "solar-stock-listed-toyo",
        "listed_company",
        "TOYO Solar Nasdaq earnings guidance orders financing capacity asset disposal",
        entity_id="TOYO",
        backfill_domains=("sec.gov", "toyo-solar.com"),
    ),
    _task(
        "solar-stock-listed-te",
        "listed_company",
        "T1 Energy NYSE TE earnings guidance orders financing manufacturing",
        entity_id="TE",
        backfill_domains=("ir.t1energy.com", "sec.gov"),
    ),
    _task(
        "solar-stock-listed-fslr",
        "listed_company",
        "First Solar FSLR earnings guidance bookings 45X manufacturing",
        entity_id="FSLR",
        backfill_domains=("investor.firstsolar.com", "sec.gov"),
    ),
    _task(
        "solar-stock-listed-csiq",
        "listed_company",
        "Canadian Solar CSIQ earnings guidance storage Recurrent Energy financing",
        entity_id="CSIQ",
        backfill_domains=("investors.canadiansolar.com", "sec.gov"),
    ),
    _task(
        "solar-stock-listed-jks",
        "listed_company",
        "JinkoSolar JKS earnings guidance module shipments capacity financing",
        entity_id="JKS",
        backfill_domains=("jinkosolar.com", "sec.gov"),
    ),
    _task(
        "solar-stock-listed-nxt",
        "listed_company",
        "Nextracker NXT earnings guidance backlog orders acquisitions",
        entity_id="NXT",
        backfill_domains=("investors.nextracker.com", "sec.gov"),
    ),
    _task(
        "solar-stock-listed-dq",
        "listed_company",
        "Daqo New Energy DQ earnings guidance polysilicon price production",
        entity_id="DQ",
        backfill_domains=("daqo.com", "sec.gov"),
    ),
    _task(
        "solar-stock-listed-009830-ks",
        "listed_company",
        "Hanwha Solutions 009830 Qcells earnings guidance US solar manufacturing",
        entity_id="009830.KS",
        backfill_domains=("hanwhasolutions.com", "kind.krx.co.kr"),
    ),
    _task(
        "solar-stock-listed-waareeener-ns",
        "listed_company",
        "Waaree Energies earnings orders guidance US solar capacity",
        entity_id="WAAREEENER.NS",
        backfill_domains=("bseindia.com", "nseindia.com", "waaree.com"),
    ),
    _task(
        "solar-stock-listed-premierene-ns",
        "listed_company",
        "Premier Energies earnings orders guidance solar cell module capacity",
        entity_id="PREMIERENE.NS",
        backfill_domains=("bseindia.com", "nseindia.com", "premierenergies.com"),
    ),
    _task(
        "solar-stock-listed-vikramsolr-ns",
        "listed_company",
        "Vikram Solar earnings orders guidance listing manufacturing capacity",
        entity_id="VIKRAMSOLR.NS",
        backfill_domains=("bseindia.com", "nseindia.com", "vikramsolar.com"),
    ),
    _task(
        "solar-stock-event-qcells",
        "event_entity",
        "Qcells US solar manufacturing project financing capacity asset event",
        entity_id="Qcells",
        backfill_domains=("qcells.com",),
    ),
    _task(
        "solar-stock-event-illuminate-usa",
        "event_entity",
        "Illuminate USA solar manufacturing financing capacity project event",
        entity_id="Illuminate USA",
        backfill_domains=("illuminateusa.com",),
    ),
    _task(
        "solar-stock-event-es-foundry",
        "event_entity",
        "ES Foundry solar cell manufacturing financing capacity project event",
        entity_id="ES Foundry",
        backfill_domains=("esfoundry.com",),
    ),
    _task(
        "solar-stock-event-suniva",
        "event_entity",
        "Suniva US solar cell manufacturing financing capacity project event",
        entity_id="Suniva",
        backfill_domains=("suniva.com",),
    ),
    _task(
        "solar-stock-event-talon-pv",
        "event_entity",
        "Talon PV US solar manufacturing financing capacity project event",
        entity_id="Talon PV",
        backfill_domains=("talonpv.com",),
    ),
    _task(
        "solar-stock-theme-input-prices",
        "industry_prices",
        "solar polysilicon wafer cell module price weekly market",
        topic="general",
        minimum_extract_successes=3,
        backfill_query="solar polysilicon wafer cell module price index 30 days",
    ),
    _task(
        "solar-stock-theme-us-policy",
        "us_policy",
        "US solar policy 45X FEOC AD CVD tariff Treasury Commerce",
        topic="general",
        minimum_extract_successes=3,
        backfill_query="45X FEOC solar AD CVD official guidance notice",
        backfill_domains=("commerce.gov", "federalregister.gov", "irs.gov"),
    ),
    _task(
        "solar-stock-theme-china-policy",
        "china_policy",
        "China solar anti-involution policy polysilicon capacity price",
        topic="general",
        minimum_extract_successes=3,
        backfill_query="China solar anti-involution official policy polysilicon capacity",
        backfill_domains=("gov.cn", "miit.gov.cn", "ndrc.gov.cn"),
    ),
    _task(
        "solar-stock-theme-capital-markets",
        "capital_markets",
        "solar financing merger acquisition securitization project finance capital markets",
        topic="general",
        minimum_extract_successes=3,
        backfill_query="solar project finance M&A securitization official announcement 30 days",
        backfill_domains=("sec.gov",),
    ),
)


def solar_stock_search_tasks() -> list[dict[str, object]]:
    """Return a detached copy of the exact 20-task solar default."""

    return deepcopy(sorted(_SOLAR_STOCK_SEARCH_TASKS, key=lambda item: item["task_id"]))


def search_tasks_for_universe(
    universe: EquityPeriodicUniverse,
) -> list[dict[str, object]]:
    """Use the frozen solar 20-task plan only for the packaged default universe."""

    if (
        universe.watchlist == DEFAULT_SOLAR_EQUITY_UNIVERSE.watchlist
        and universe.event_only_entities
        == DEFAULT_SOLAR_EQUITY_UNIVERSE.event_only_entities
    ):
        return solar_stock_search_tasks()
    return listed_company_search_tasks(universe)


__all__ = [
    "SOLAR_STOCK_CORE_SECURITIES",
    "SOLAR_STOCK_EVENT_ONLY_ENTITIES",
    "SOLAR_STOCK_OVERSEAS_SECURITIES",
    "SOLAR_STOCK_PRIMARY_SECURITIES",
    "search_tasks_for_universe",
    "solar_stock_search_tasks",
]
