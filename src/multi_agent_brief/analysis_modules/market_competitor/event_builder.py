"""Event Builder — deterministically merge entity-tagged Claims into MarketEvents.

Reads the screened ClaimLedger, groups claims by entity_id + event_type +
dimension, and produces MarketEvent objects for the renderer.
"""
from __future__ import annotations

from multi_agent_brief.analysis_modules.market_competitor.schemas import (
    CompetitorUniverse,
    MarketEvent,
    Materiality,
    Confidence,
    Dimension,
    EventType,
    EventStatus,
)
from multi_agent_brief.core.claim_ledger import ClaimLedger
from multi_agent_brief.core.schemas import Claim


def build_events(ledger: ClaimLedger, universe: CompetitorUniverse) -> list[MarketEvent]:
    """Build MarketEvents from entity-tagged Claim Ledger entries.

    Only claims with ``metadata.entity_ids`` are considered.  Claims sharing
    the same (entity_id, event_type, dimension) are merged into a single
    MarketEvent.
    """
    # Group claims by composite key
    groups: dict[str, list[Claim]] = {}
    for claim in ledger:
        entity_ids = claim.metadata.get("entity_ids")
        if not entity_ids:
            continue
        if not isinstance(entity_ids, list) or len(entity_ids) == 0:
            continue

        event_type = claim.metadata.get("event_type", "other")
        dimension = claim.metadata.get("dimension", "other")

        for eid in entity_ids:
            key = f"{eid}|{event_type}|{dimension}"
            groups.setdefault(key, []).append(claim)

    events: list[MarketEvent] = []
    idx = 0
    for key, claims in groups.items():
        eid, etype, dim = key.split("|", 2)

        # Determine status from claim metadata
        status: EventStatus = _pick_status(claims)
        # Geography from first claim that has one
        geo = ""
        for c in claims:
            g = c.metadata.get("geography", "")
            if g:
                geo = g
                break

        # Best event date
        event_date = ""
        for c in claims:
            d = c.metadata.get("published_at") or c.metadata.get("retrieved_at", "")
            if d:
                event_date = d
                break

        # Summary: first claim's statement
        summary = claims[0].statement if claims else ""

        # Supporting claim IDs
        supporting = [c.claim_id for c in claims]

        # Confidence: high if ≥2 sources, else medium
        confidence: Confidence = "high" if len(claims) >= 2 else "medium"

        # Materiality: high if any event in priority dimensions
        materiality: Materiality = "high" if dim in ("capacity", "technology", "financials") else "medium"

        idx += 1
        event_id = f"EVT_{eid}_{etype}_{dim}_{idx:03d}"

        events.append(MarketEvent(
            event_id=event_id,
            entity_ids=[eid],
            event_type=etype,  # type: ignore[arg-type]
            dimension=dim,  # type: ignore[arg-type]
            status=status,
            geography=geo,
            event_date=event_date,
            summary=summary[:200],
            supporting_claim_ids=supporting,
            source_count=len(supporting),
            confidence=confidence,
            materiality=materiality,
        ))

    return events


def _pick_status(claims: list[Claim]) -> EventStatus:
    """Infer event status from claim text."""
    text = " ".join(c.statement.lower() + " " + c.evidence_text.lower() for c in claims)
    if any(w in text for w in ("cancelled", "canceled", "terminated", "scrapped")):
        return "cancelled"
    if any(w in text for w in ("delayed", "pushed back", "postponed")):
        return "delayed"
    if any(w in text for w in ("operational", "producing", "production started")):
        return "operational"
    if any(w in text for w in ("commissioning", "testing", "ramping up")):
        return "commissioning"
    if any(w in text for w in ("under construction", "building", "groundbreaking")):
        return "under_construction"
    if any(w in text for w in ("planned", "planning", "proposed")):
        return "planned"
    if any(w in text for w in ("announced", "revealed", "unveiled", "disclosed")):
        return "announced"
    if any(w in text for w in ("rumored", "speculated", "reportedly considering")):
        return "rumored"
    return ""
