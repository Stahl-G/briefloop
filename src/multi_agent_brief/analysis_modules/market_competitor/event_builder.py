"""Event Builder — deterministically merge entity-tagged Claims into MarketEvents.

Reads the screened ClaimLedger, groups claims by entity_id + event_type +
dimension, and produces MarketEvent objects for the renderer.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from multi_agent_brief.analysis_modules.market_competitor.schemas import (
    CompetitorUniverse,
    MarketEvent,
    Materiality,
    Confidence,
    Dimension,
    EventType,
    EventStatus,
    ChangeStatus,
)
from multi_agent_brief.core.claim_ledger import ClaimLedger
from multi_agent_brief.core.schemas import Claim


def build_events(
    ledger: ClaimLedger,
    universe: CompetitorUniverse,
    *,
    state_dir: str | None = None,
) -> list[MarketEvent]:
    """Build MarketEvents from entity-tagged Claim Ledger entries.

    Only claims with ``metadata.entity_ids`` are considered.  Claims sharing
    the same (entity_id, event_type, dimension) are merged into a single
    MarketEvent.

    If ``state_dir`` is provided, compares current events against the previous
    period's event history and marks each event's ``change_status``.
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

    # Cross-period comparison
    if state_dir:
        history_path = Path(state_dir) / "market_competitor" / "event_history.jsonl"
        prev = _load_history(history_path)
        events = _compare_events(events, prev)
        _save_history(events, history_path)

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


# ── History tracking ────────────────────────────────────────────────────────

def _event_match_key(ev: MarketEvent) -> str:
    """Composite key for matching events across periods.

    Uses entity_id + event_type + dimension — status changes are still the
    same event, just progressed.
    """
    eid = ev.entity_ids[0] if ev.entity_ids else ""
    return f"{eid}|{ev.event_type}|{ev.dimension}"


def _load_history(history_path: Path) -> dict[str, dict[str, Any]]:
    """Load previous period's event history. Returns empty dict if no history."""
    if not history_path.exists():
        return {}
    prev: dict[str, dict[str, Any]] = {}
    try:
        with open(history_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                key = _event_match_key(MarketEvent(**rec))
                # Keep the latest record for each key
                prev[key] = rec
    except (json.JSONDecodeError, TypeError):
        pass
    return prev


def _compare_events(
    current: list[MarketEvent],
    previous: dict[str, dict[str, Any]],
) -> list[MarketEvent]:
    """Compare current events against history, mark change_status."""
    for ev in current:
        key = _event_match_key(ev)
        if key not in previous:
            ev.change_status = "new"
            continue

        prev_status = previous[key].get("status", "")
        if ev.status == prev_status:
            ev.change_status = "unchanged"
        elif ev.status == "":
            ev.change_status = "unchanged"
        elif prev_status == "cancelled":
            ev.change_status = "new"  # re-emerged after cancellation
        elif ev.status == "cancelled":
            ev.change_status = "cancelled"
        else:
            ev.change_status = "changed"

    # Mark previous events not seen in current period as resolved/cancelled
    current_keys = {_event_match_key(ev) for ev in current}
    for key, rec in previous.items():
        if key not in current_keys and rec.get("status") != "cancelled":
            eid = rec.get("entity_ids", [""])[0]
            current.append(MarketEvent(
                event_id=f"EVT_{eid}_resolved_{len(current)+1:03d}",
                entity_ids=rec.get("entity_ids", []),
                event_type=rec.get("event_type", "other"),
                dimension=rec.get("dimension", "other"),
                status=rec.get("status", ""),
                geography=rec.get("geography", ""),
                event_date=rec.get("event_date", ""),
                summary=rec.get("summary", ""),
                supporting_claim_ids=rec.get("supporting_claim_ids", []),
                source_count=rec.get("source_count", 0),
                confidence=rec.get("confidence", "medium"),
                materiality="low",
                change_status="resolved",
            ))

    return current


def _save_history(events: list[MarketEvent], history_path: Path) -> None:
    """Append all current events to history file."""
    history_path.parent.mkdir(parents=True, exist_ok=True)
    with open(history_path, "a", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev.to_dict(), ensure_ascii=False) + "\n")
