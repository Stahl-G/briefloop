"""Entity & Event Enricher — deterministic Claim metadata tagging.

Runs between Scout and Screener: reads CompetitorUniverse, matches entity
names/aliases against each Claim's statement+evidence_text, and writes
entity_ids / event_type / geography / dimension into claim.metadata.

Pure deterministic logic — zero LLM calls.
"""
from __future__ import annotations

from multi_agent_brief.analysis_modules.market_competitor.schemas import (
    CompetitorUniverse,
    Dimension,
    EventType,
)
from multi_agent_brief.core.schemas import Claim

# ── Keyword maps ───────────────────────────────────────────────────────────

_GEO_KEYWORDS: list[tuple[str, str]] = [
    ("united states", "United States"), ("us ", "United States"), ("u.s.", "United States"),
    ("china", "China"), ("chinese", "China"),
    ("europe", "Europe"), ("european union", "Europe"), ("eu ", "Europe"),
    ("india", "India"), ("indian", "India"),
    ("japan", "Japan"), ("japanese", "Japan"),
    ("south korea", "South Korea"), ("korea", "South Korea"),
    ("germany", "Germany"), ("france", "France"),
    ("brazil", "Brazil"), ("canada", "Canada"), ("australia", "Australia"),
]

_EVENT_TYPE_KEYWORDS: list[tuple[str, EventType]] = [
    ("new factory", "capacity_expansion"), ("new plant", "capacity_expansion"),
    ("capacity expansion", "capacity_expansion"), ("expand capacity", "capacity_expansion"),
    ("capacity delay", "capacity_delay"), ("delayed capacity", "capacity_delay"),
    ("plant opening", "plant_opening"), ("facility opening", "plant_opening"),
    ("plant closure", "plant_closure"), ("closing plant", "plant_closure"),
    ("product launch", "product_launch"), ("new product", "product_launch"),
    ("launching", "product_launch"), ("unveiled", "product_launch"),
    ("technology change", "technology_change"), ("new technology", "technology_change"),
    ("price change", "price_change"), ("price increase", "price_change"),
    ("price decrease", "price_change"), ("lowered price", "price_change"),
    ("customer win", "customer_win"), ("signed customer", "customer_win"),
    ("supply agreement", "supply_agreement"), ("supply deal", "supply_agreement"),
    ("partnership", "partnership"), ("joint venture", "partnership"),
    ("collaboration", "partnership"), ("strategic alliance", "partnership"),
    ("fundraising", "fundraising"), ("raised", "fundraising"),
    ("financing round", "fundraising"), ("series", "fundraising"),
    ("acquisition", "acquisition"), ("acquired", "acquisition"),
    ("merger", "acquisition"), ("takeover", "acquisition"),
    ("asset sale", "asset_sale"), ("divestiture", "asset_sale"),
    ("earnings report", "earnings_change"), ("quarterly results", "earnings_change"),
    ("revenue growth", "earnings_change"), ("revenue decline", "earnings_change"),
    ("guidance", "guidance_change"), ("outlook", "guidance_change"),
    ("tariff", "trade_action"), ("antidumping", "trade_action"),
    ("countervailing", "trade_action"), ("section 337", "trade_action"),
    ("trade action", "trade_action"), ("trade war", "trade_action"),
    ("lawsuit", "litigation"), ("litigation", "litigation"),
    ("sued", "litigation"), ("court", "litigation"),
    ("patent", "patent_action"), ("intellectual property", "patent_action"),
    ("ceo", "management_change"), ("appointed", "management_change"),
    ("resigned", "management_change"), ("management change", "management_change"),
]

_DIMENSION_KEYWORDS: list[tuple[str, Dimension]] = [
    ("capacity", "capacity"), ("gw", "capacity"), ("mw", "capacity"),
    ("plant", "capacity"), ("factory", "capacity"), ("production line", "capacity"),
    ("technology", "technology"), ("efficiency", "technology"),
    ("hjt", "technology"), ("topcon", "technology"), ("perovskite", "technology"),
    ("customer", "customers_partnerships"), ("client", "customers_partnerships"),
    ("partnership", "customers_partnerships"), ("supply agreement", "customers_partnerships"),
    ("revenue", "financials"), ("earnings", "financials"), ("eps", "financials"),
    ("margin", "financials"), ("profit", "financials"), ("ebitda", "financials"),
    ("regulation", "policy_compliance"), ("policy", "policy_compliance"),
    ("tariff", "policy_compliance"), ("compliance", "policy_compliance"),
    ("demand", "market_demand"), ("market growth", "market_demand"),
    ("price", "price"), ("pricing", "price"), ("cost", "price"),
    ("supply chain", "supply_chain"), ("logistics", "supply_chain"),
    ("management", "management"), ("ceo", "management"), ("executive", "management"),
]


def _match_event_type(text: str) -> EventType:
    t = text.lower()
    for keyword, etype in _EVENT_TYPE_KEYWORDS:
        if keyword in t:
            return etype
    return "other"


def _match_geography(text: str) -> str:
    t = text.lower()
    for keyword, geo in _GEO_KEYWORDS:
        if keyword in t:
            return geo
    return ""


def _match_dimension(text: str) -> Dimension:
    t = text.lower()
    for keyword, dim in _DIMENSION_KEYWORDS:
        if keyword in t:
            return dim
    return "other"


class EntityEventEnricher:
    """Deterministic entity + event tagger for Claim metadata.

    Reads a CompetitorUniverse, matches entity names/aliases against each
    Claim's statement + evidence_text, and writes entity_ids, event_type,
    geography, and dimension into ``claim.metadata``.

    Claims with no entity match are left untouched (not dropped).
    """

    def __init__(self, universe: CompetitorUniverse) -> None:
        self._universe = universe
        # Build a flat lookup: normalized name → entity_id
        self._name_map: dict[str, str] = {}
        candidates = [self._universe.target] + self._universe.entities
        for ent in candidates:
            key = ent.name.strip().lower()
            if key:
                self._name_map[key] = ent.entity_id
            for alias in ent.aliases:
                key_a = alias.strip().lower()
                if key_a:
                    self._name_map[key_a] = ent.entity_id

    def enrich(self, claims: list[Claim]) -> list[Claim]:
        """Tag claims with entity and event metadata in-place."""
        for claim in claims:
            text = (claim.statement + " " + claim.evidence_text).lower()

            # Entity matching — check all known names/aliases
            matched_ids: list[str] = []
            for name_lower, entity_id in self._name_map.items():
                if name_lower in text:
                    if entity_id not in matched_ids:
                        matched_ids.append(entity_id)

            if matched_ids:
                claim.metadata["entity_ids"] = matched_ids
                claim.metadata["matched_entity_name"] = matched_ids[0]

                # Fill event_type, geography, dimension
                event_type = _match_event_type(text)
                claim.metadata["event_type"] = event_type

                geo = _match_geography(text)
                if geo:
                    claim.metadata["geography"] = geo

                dim = _match_dimension(text)
                claim.metadata["dimension"] = dim

        return claims


# ── Search task generators ──────────────────────────────────────────────────

_DIMENSION_SEARCH_KEYWORDS: dict[Dimension, str] = {
    "capacity": "capacity expansion production plant factory",
    "technology": "technology product launch innovation",
    "customers_partnerships": "customer partnership agreement deal",
    "financials": "revenue earnings results financial",
    "policy_compliance": "regulation policy compliance tariff",
    "market_demand": "market demand growth trend",
    "price": "price pricing cost",
    "supply_chain": "supply chain logistics",
    "management": "CEO management executive",
    "other": "",
}


def generate_competitor_search_tasks(universe: CompetitorUniverse) -> list[dict[str, str]]:
    """Generate web_search tasks for each competitor × dimension.

    Returns a list of dicts with ``query`` and ``domains`` keys, suitable
    for merging into ``source_config.web_search["search_tasks"]``.
    """
    tasks: list[dict[str, str]] = []
    for ent in universe.entities:
        if ent.priority != "primary":
            continue
        for dim in ["capacity", "technology", "customers_partnerships", "financials"]:
            kw = _DIMENSION_SEARCH_KEYWORDS.get(dim, "")
            if not kw:
                continue
            query = f"{ent.name} {kw}"
            tasks.append({"query": query, "domains": None})
    return tasks
