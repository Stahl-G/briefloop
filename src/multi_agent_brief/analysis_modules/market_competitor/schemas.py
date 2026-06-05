"""Market & Competitor Intelligence — core data contracts.

All dataclasses in this module are the canonical schema for the
Market & Competitor Analysis Module.  They are used by the Python
deterministic layer (event builder, renderer, auditor) and by
LLM subagents (planner, analyst, auditor).

Design constraints
------------------
- MarketEvent.supporting_claim_ids  MUST be non-empty (every event
  must be traceable to at least one Claim Ledger entry).
- AnalysisCard.supporting_claim_ids MUST have ≥1 claim (no orphan
  analysis judgments).
- AnalysisCard MUST NOT expose a free-text ``statement`` or ``text``
  field — analytical narrative belongs in the final brief, not here.
  Use ``headline`` + ``observation`` + ``implication_for_target`` instead.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


# ── Enums -------------------------------------------------------------------

EventType = Literal[
    "capacity_expansion",
    "capacity_delay",
    "plant_opening",
    "plant_closure",
    "product_launch",
    "technology_change",
    "price_change",
    "customer_win",
    "partnership",
    "supply_agreement",
    "fundraising",
    "acquisition",
    "asset_sale",
    "earnings_change",
    "guidance_change",
    "policy_exposure",
    "trade_action",
    "litigation",
    "patent_action",
    "management_change",
    "other",
]

EventStatus = Literal[
    "rumored",
    "announced",
    "planned",
    "under_construction",
    "commissioning",
    "operational",
    "cancelled",
    "delayed",
    "",
]

Dimension = Literal[
    "capacity",
    "technology",
    "customers_partnerships",
    "financials",
    "policy_compliance",
    "market_demand",
    "price",
    "supply_chain",
    "management",
    "other",
]

EntityRelation = Literal[
    "direct_competitor",
    "adjacent_competitor",
    "technology_substitute",
    "benchmark_company",
    "customer",
    "supplier",
    "regulator",
    "market_actor",
]

ChangeStatus = Literal[
    "new",
    "changed",
    "unchanged",
    "resolved",
    "cancelled",
]

FindingType = Literal[
    "relative_position_change",
    "opportunity",
    "risk",
    "evidence_gap",
    "follow_up",
    "market_shift",
    "other",
]

TimeHorizon = Literal[
    "immediate",
    "3-6_months",
    "6-12_months",
    "12-24_months",
    "long_term",
]

ModuleMode = Literal[
    "weekly_monitor",
    "competitor_deep_dive",
    "market_landscape",
]

Confidence = Literal["low", "medium", "high"]
Materiality = Literal["low", "medium", "high"]
Priority = Literal["primary", "secondary", "tertiary"]


# ── Entity / Universe -------------------------------------------------------


@dataclass
class CompetitorEntity:
    """A single tracked entity (competitor, benchmark, regulator, etc.)."""

    entity_id: str
    name: str
    aliases: list[str] = field(default_factory=list)
    relation: EntityRelation = "direct_competitor"
    priority: Priority = "primary"
    geographies: list[str] = field(default_factory=list)
    technologies: list[str] = field(default_factory=list)


@dataclass
class CompetitorUniverse:
    """Full competitive landscape for a workspace — loaded from
    ``competitor_universe.yaml``."""

    target: CompetitorEntity
    market_scope: dict[str, list[str]] = field(default_factory=dict)
    entities: list[CompetitorEntity] = field(default_factory=list)
    mode: ModuleMode = "weekly_monitor"
    enabled: bool = False

    @property
    def primary_competitors(self) -> list[CompetitorEntity]:
        return [e for e in self.entities if e.priority == "primary"]

    @property
    def all_competitors(self) -> list[CompetitorEntity]:
        return [e for e in self.entities if e.relation in (
            "direct_competitor", "adjacent_competitor", "technology_substitute",
        )]

    def get_entity(self, entity_id: str) -> CompetitorEntity | None:
        if self.target.entity_id == entity_id:
            return self.target
        for ent in self.entities:
            if ent.entity_id == entity_id:
                return ent
        return None

    def match_name(self, name: str) -> str | None:
        """Return entity_id if ``name`` matches any entity name/alias (case-insensitive).

        Returns None if no match.
        """
        normalized = name.strip().lower()
        candidates = [self.target] + self.entities
        for ent in candidates:
            if ent.name.strip().lower() == normalized:
                return ent.entity_id
            for alias in ent.aliases:
                if alias.strip().lower() == normalized:
                    return ent.entity_id
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": {
                "entity_id": self.target.entity_id,
                "name": self.target.name,
                "aliases": self.target.aliases,
                "relation": self.target.relation,
                "priority": self.target.priority,
                "geographies": self.target.geographies,
                "technologies": self.target.technologies,
            },
            "market_scope": self.market_scope,
            "entities": [
                {
                    "entity_id": ent.entity_id,
                    "name": ent.name,
                    "aliases": ent.aliases,
                    "relation": ent.relation,
                    "priority": ent.priority,
                    "geographies": ent.geographies,
                    "technologies": ent.technologies,
                }
                for ent in self.entities
            ],
            "mode": self.mode,
            "enabled": self.enabled,
        }


# ── MarketEvent -------------------------------------------------------------


@dataclass
class MarketEvent:
    """A structured, source-grounded competitive intelligence event.

    Events are built deterministically from Claim Ledger entries that carry
    entity tags.  Every event MUST reference at least one Claim ID.
    """

    event_id: str
    entity_ids: list[str]
    event_type: EventType
    dimension: Dimension = "other"
    status: EventStatus = ""
    geography: str = ""
    event_date: str = ""
    summary: str = ""
    supporting_claim_ids: list[str] = field(default_factory=list)
    source_count: int = 0
    confidence: Confidence = "medium"
    materiality: Materiality = "medium"
    change_status: ChangeStatus = "new"

    def __post_init__(self) -> None:
        if not self.supporting_claim_ids:
            raise ValueError(
                f"MarketEvent '{self.event_id}' must have at least one "
                f"supporting_claim_ids entry."
            )
        if self.source_count < 1:
            self.source_count = len(self.supporting_claim_ids)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "entity_ids": self.entity_ids,
            "event_type": self.event_type,
            "dimension": self.dimension,
            "status": self.status,
            "geography": self.geography,
            "event_date": self.event_date,
            "summary": self.summary,
            "supporting_claim_ids": self.supporting_claim_ids,
            "source_count": self.source_count,
            "confidence": self.confidence,
            "materiality": self.materiality,
            "change_status": self.change_status,
        }


# ── AnalysisCard ------------------------------------------------------------


@dataclass
class AnalysisCard:
    """A structured analytical judgment backed by one or more Claim Ledger entries.

    IMPORTANT: AnalysisCards MUST NOT contain free-form narrative text that
    could be mistaken for source facts.  Use ``headline``, ``observation``,
    and ``implication_for_target`` to express analytical judgments clearly
    separated from source evidence.  The final management-facing prose belongs
    in the brief, not here.

    Every AnalysisCard MUST reference at least one Claim ID via
    ``supporting_claim_ids``.  Single-source interpretations MUST set
    ``confidence = "low"``.
    """

    analysis_id: str
    finding_type: FindingType
    headline: str
    observation: str
    implication_for_target: str = ""
    time_horizon: TimeHorizon = "immediate"
    confidence: Confidence = "medium"
    supporting_claim_ids: list[str] = field(default_factory=list)
    counterevidence_claim_ids: list[str] = field(default_factory=list)
    evidence_gaps: list[str] = field(default_factory=list)
    follow_up_questions: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.supporting_claim_ids:
            raise ValueError(
                f"AnalysisCard '{self.analysis_id}' must have at least one "
                f"supporting_claim_ids entry."
            )
        if len(self.supporting_claim_ids) == 1 and self.confidence not in ("low",):
            raise ValueError(
                f"AnalysisCard '{self.analysis_id}' has only one supporting claim "
                f"but confidence is '{self.confidence}' — single-source "
                f"interpretations must set confidence='low'."
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "analysis_id": self.analysis_id,
            "finding_type": self.finding_type,
            "headline": self.headline,
            "observation": self.observation,
            "implication_for_target": self.implication_for_target,
            "time_horizon": self.time_horizon,
            "confidence": self.confidence,
            "supporting_claim_ids": self.supporting_claim_ids,
            "counterevidence_claim_ids": self.counterevidence_claim_ids,
            "evidence_gaps": self.evidence_gaps,
            "follow_up_questions": self.follow_up_questions,
        }


# ── CompetitorMatrix --------------------------------------------------------


@dataclass
class CompetitorMatrixCell:
    """One cell in the competitor matrix — entity × dimension."""

    entity_id: str
    dimension: Dimension
    summary: str = ""
    evidence_claim_ids: list[str] = field(default_factory=list)
    status: str = ""


@dataclass
class CompetitorMatrix:
    """Entity × dimension comparison table.

    Dimensions are rows, entities are columns (or vice versa depending on
    the rendering context).
    """

    entities: list[str] = field(default_factory=list)
    dimensions: list[Dimension] = field(default_factory=list)
    cells: list[CompetitorMatrixCell] = field(default_factory=list)
    report_date: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "entities": self.entities,
            "dimensions": self.dimensions,
            "cells": [
                {
                    "entity_id": cell.entity_id,
                    "dimension": cell.dimension,
                    "summary": cell.summary,
                    "evidence_claim_ids": cell.evidence_claim_ids,
                    "status": cell.status,
                }
                for cell in self.cells
            ],
            "report_date": self.report_date,
        }


# ── CoverageReport ----------------------------------------------------------


@dataclass
class CoverageReport:
    """Entity and dimension coverage gaps for the current reporting period."""

    primary_competitors_total: int = 0
    primary_competitors_with_recent_evidence: int = 0
    missing_entities: list[str] = field(default_factory=list)
    undercovered_dimensions: list[str] = field(default_factory=list)
    absence_of_evidence_entities: list[str] = field(default_factory=list)
    generated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "primary_competitors_total": self.primary_competitors_total,
            "primary_competitors_with_recent_evidence": (
                self.primary_competitors_with_recent_evidence
            ),
            "missing_entities": self.missing_entities,
            "undercovered_dimensions": self.undercovered_dimensions,
            "absence_of_evidence_entities": self.absence_of_evidence_entities,
            "generated_at": self.generated_at,
        }


# ── Watchlist ---------------------------------------------------------------


@dataclass
class WatchlistItem:
    """A single cross-period tracking item."""

    item_id: str
    description: str
    entity_id: str = ""
    status: Literal["open", "resolved", "carry_forward"] = "open"
    last_updated: str = ""
    related_claim_ids: list[str] = field(default_factory=list)


@dataclass
class Watchlist:
    """Cross-period tracking list — items that need verification in future cycles."""

    items: list[WatchlistItem] = field(default_factory=list)
    generated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "items": [
                {
                    "item_id": item.item_id,
                    "description": item.description,
                    "entity_id": item.entity_id,
                    "status": item.status,
                    "last_updated": item.last_updated,
                    "related_claim_ids": item.related_claim_ids,
                }
                for item in self.items
            ],
            "generated_at": self.generated_at,
        }


# ── ModuleConfig ------------------------------------------------------------

_DEFAULT_DIMENSIONS: list[Dimension] = [
    "market_demand", "price", "capacity", "technology",
    "customers_partnerships", "financials", "policy_compliance",
]

_DEFAULT_EVENT_TYPES: list[EventType] = [
    "capacity_expansion", "product_launch", "customer_win",
    "partnership", "acquisition", "earnings_change",
    "trade_action", "litigation",
]


@dataclass
class ModuleConfig:
    """Configuration for the Market & Competitor Intelligence module.

    Deserialized from config.yaml's ``modules.market_competitor`` section
    (if present) and from ``competitor_universe.yaml``.
    """

    enabled: bool = False
    mode: ModuleMode = "weekly_monitor"
    universe_path: str = "competitor_universe.yaml"

    dimensions: list[Dimension] = field(
        default_factory=lambda: list(_DEFAULT_DIMENSIONS)
    )

    event_types: list[EventType] = field(
        default_factory=lambda: list(_DEFAULT_EVENT_TYPES)
    )

    max_events: int = 20
    max_events_per_entity: int = 4
    require_primary_competitor_coverage: bool = True
    prefer_changes_since_previous_report: bool = True
    require_implication_for_target: bool = True
    require_evidence_gaps: bool = True
    require_counterevidence_check: bool = True
    interpretations_min_supporting_claims: int = 2
    comparisons_require_evidence_for_each_entity: bool = True
    require_capacity_status: bool = True
    require_metric_period_and_unit: bool = True

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ModuleConfig":
        if data is None:
            return cls()
        return cls(
            enabled=bool(data.get("enabled", False)),
            mode=data.get("mode", "weekly_monitor"),
            universe_path=data.get("universe_path", "competitor_universe.yaml"),
            dimensions=data.get("dimensions", _DEFAULT_DIMENSIONS),
            event_types=data.get("event_types", _DEFAULT_EVENT_TYPES),
            max_events=int(data.get("max_events", 20)),
            max_events_per_entity=int(data.get("max_events_per_entity", 4)),
            require_primary_competitor_coverage=bool(
                data.get("require_primary_competitor_coverage", True)
            ),
            prefer_changes_since_previous_report=bool(
                data.get("prefer_changes_since_previous_report", True)
            ),
            require_implication_for_target=bool(
                data.get("require_implication_for_target", True)
            ),
            require_evidence_gaps=bool(data.get("require_evidence_gaps", True)),
            require_counterevidence_check=bool(
                data.get("require_counterevidence_check", True)
            ),
            interpretations_min_supporting_claims=int(
                data.get("interpretations_min_supporting_claims", 2)
            ),
            comparisons_require_evidence_for_each_entity=bool(
                data.get("comparisons_require_evidence_for_each_entity", True)
            ),
            require_capacity_status=bool(data.get("require_capacity_status", True)),
            require_metric_period_and_unit=bool(
                data.get("require_metric_period_and_unit", True)
            ),
        )
