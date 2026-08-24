"""Configurable equity-periodic watchlist.

Solar Stock Periodic keeps a packaged default universe. Delivery never treats
that default as a quota: missing non-core names are coverage disclosures.
Core subject tickers come from report_spec metadata when present.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from multi_agent_brief.product.report_spec import ReportSpecLoadError, load_report_spec


@dataclass(frozen=True)
class EquityPeriodicUniverse:
    core_tickers: tuple[str, ...]
    primary_tickers: tuple[str, ...]
    overseas_tickers: tuple[str, ...]
    event_only_entities: tuple[str, ...] = ()

    @property
    def watchlist(self) -> tuple[str, ...]:
        seen: list[str] = []
        for ticker in (*self.primary_tickers, *self.overseas_tickers):
            if ticker not in seen:
                seen.append(ticker)
        return tuple(seen)

    def group_for(self, ticker: str) -> str:
        if ticker in self.overseas_tickers:
            return "overseas"
        return "primary"

    def is_core(self, ticker: str) -> bool:
        return ticker in self.core_tickers


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    seen: list[str] = []
    for item in values:
        text = str(item).strip()
        if text and text not in seen:
            seen.append(text)
    return tuple(seen)


def universe_from_mapping(metadata: Mapping[str, Any]) -> EquityPeriodicUniverse | None:
    primary = _unique(metadata.get("primary_tickers") or ())
    overseas = _unique(metadata.get("overseas_tickers") or ())
    if not primary and not overseas:
        return None
    core = _unique(metadata.get("core_tickers") or ()) or primary[:1]
    events = _unique(metadata.get("event_only_entities") or ())
    return EquityPeriodicUniverse(
        core_tickers=core,
        primary_tickers=primary,
        overseas_tickers=overseas,
        event_only_entities=events,
    )


DEFAULT_SOLAR_EQUITY_UNIVERSE = EquityPeriodicUniverse(
    core_tickers=("TOYO",),
    primary_tickers=("TOYO", "TE", "FSLR", "CSIQ", "JKS", "NXT", "DQ"),
    overseas_tickers=(
        "009830.KS",
        "WAAREEENER.NS",
        "PREMIERENE.NS",
        "VIKRAMSOLR.NS",
    ),
    event_only_entities=(
        "Qcells",
        "Illuminate USA",
        "ES Foundry",
        "Suniva",
        "Talon PV",
    ),
)

SOLAR_STOCK_CORE_SECURITIES = DEFAULT_SOLAR_EQUITY_UNIVERSE.core_tickers
SOLAR_STOCK_PRIMARY_SECURITIES = DEFAULT_SOLAR_EQUITY_UNIVERSE.primary_tickers
SOLAR_STOCK_OVERSEAS_SECURITIES = DEFAULT_SOLAR_EQUITY_UNIVERSE.overseas_tickers
SOLAR_STOCK_EVENT_ONLY_ENTITIES = DEFAULT_SOLAR_EQUITY_UNIVERSE.event_only_entities


def load_equity_universe(workspace: str | Path | None) -> EquityPeriodicUniverse:
    """Read watchlist from report_spec metadata; otherwise the solar preset."""

    if workspace is None:
        return DEFAULT_SOLAR_EQUITY_UNIVERSE
    spec_path = Path(workspace).expanduser().resolve() / "report_spec.yaml"
    if not spec_path.is_file():
        return DEFAULT_SOLAR_EQUITY_UNIVERSE
    try:
        spec = load_report_spec(spec_path)
    except ReportSpecLoadError:
        return DEFAULT_SOLAR_EQUITY_UNIVERSE
    metadata = spec.get("metadata")
    if isinstance(metadata, dict):
        loaded = universe_from_mapping(metadata)
        if loaded is not None:
            return loaded
    return DEFAULT_SOLAR_EQUITY_UNIVERSE


def listed_company_search_tasks(
    universe: EquityPeriodicUniverse,
) -> list[dict[str, object]]:
    """Generic listed-company discovery tasks from a watchlist."""

    tasks: list[dict[str, object]] = []
    for ticker in universe.watchlist:
        slug = ticker.lower().replace(".", "-")
        name = ticker.split(".", 1)[0]
        tasks.append(
            {
                "task_id": f"equity-listed-{slug}",
                "task_category": "listed_company",
                "entity_id": ticker,
                "query": f"{name} {ticker} earnings guidance orders financing",
                "topic": "news",
                "domains": [],
                "max_results": 20,
                "recency_days": 7,
                "search_depth": "advanced",
                "minimum_extract_successes": 2,
                "backfill": {
                    "enabled": True,
                    "recency_days": 30,
                    "query": (
                        f"{name} {ticker} official filing investor relations"
                    ),
                    "domains": [],
                    "max_results": 20,
                    "search_depth": "advanced",
                },
            }
        )
    for entity in universe.event_only_entities:
        slug = entity.lower().replace(" ", "-")
        tasks.append(
            {
                "task_id": f"equity-event-{slug}",
                "task_category": "event_entity",
                "entity_id": entity,
                "query": f"{entity} capacity financing announcement",
                "topic": "news",
                "domains": [],
                "max_results": 20,
                "recency_days": 7,
                "search_depth": "advanced",
                "minimum_extract_successes": 2,
                "backfill": {
                    "enabled": True,
                    "recency_days": 30,
                    "query": f"{entity} official filing investor relations",
                    "domains": [],
                    "max_results": 20,
                    "search_depth": "advanced",
                },
            }
        )
    return sorted(tasks, key=lambda item: str(item["task_id"]))


def infer_listing_group(ticker: str, universe: EquityPeriodicUniverse | None = None) -> str:
    if universe is not None:
        return universe.group_for(ticker)
    if ticker in DEFAULT_SOLAR_EQUITY_UNIVERSE.overseas_tickers:
        return "overseas"
    if "." in ticker:
        return "overseas"
    return "primary"


__all__ = [
    "DEFAULT_SOLAR_EQUITY_UNIVERSE",
    "EquityPeriodicUniverse",
    "SOLAR_STOCK_CORE_SECURITIES",
    "SOLAR_STOCK_EVENT_ONLY_ENTITIES",
    "SOLAR_STOCK_OVERSEAS_SECURITIES",
    "SOLAR_STOCK_PRIMARY_SECURITIES",
    "infer_listing_group",
    "listed_company_search_tasks",
    "load_equity_universe",
    "universe_from_mapping",
]
