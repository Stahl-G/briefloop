"""Renderer — write market_competitor intermediate artifacts to disk."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from multi_agent_brief.analysis_modules.market_competitor.schemas import (
    CompetitorEntity,
    CompetitorMatrix,
    CompetitorMatrixCell,
    CompetitorUniverse,
    CoverageReport,
    Dimension,
    MarketEvent,
    Watchlist,
    WatchlistItem,
)
from multi_agent_brief.core.claim_ledger import ClaimLedger
from multi_agent_brief.core.schemas import Claim, utc_now_iso


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def render_events_json(events: list[MarketEvent], output_dir: Path) -> Path:
    """Write events.json."""
    p = output_dir / "intermediate" / "market_competitor" / "events.json"
    _write_json(p, {
        "events": [ev.to_dict() for ev in events],
        "count": len(events),
        "generated_at": utc_now_iso(),
    })
    return p


def render_competitor_matrix(
    events: list[MarketEvent],
    universe: CompetitorUniverse,
    output_dir: Path,
) -> Path:
    """Write competitor_matrix.json — entity × dimension comparison table."""
    entities = [universe.target] + universe.entities
    entity_ids = [e.entity_id for e in entities if e.entity_id]
    entity_names = {e.entity_id: e.name for e in entities}

    dimensions: list[Dimension] = [
        "capacity", "technology", "customers_partnerships",
        "financials", "policy_compliance", "market_demand",
    ]

    cells: list[CompetitorMatrixCell] = []
    for eid in entity_ids:
        for dim in dimensions:
            # Find events for this entity × dimension
            cell_events = [
                ev for ev in events
                if eid in ev.entity_ids and ev.dimension == dim
            ]
            if not cell_events:
                continue

            claim_ids: list[str] = []
            summaries: list[str] = []
            for ev in cell_events:
                claim_ids.extend(ev.supporting_claim_ids)
                if ev.summary:
                    summaries.append(ev.summary)

            cells.append(CompetitorMatrixCell(
                entity_id=eid,
                dimension=dim,
                summary="; ".join(summaries[:3]),
                evidence_claim_ids=claim_ids[:10],
                status=cell_events[0].status if cell_events else "",
            ))

    matrix = CompetitorMatrix(
        entities=[entity_names.get(eid, eid) for eid in entity_ids],
        dimensions=dimensions,
        cells=cells,
        report_date=utc_now_iso(),
    )

    p = output_dir / "intermediate" / "market_competitor" / "competitor_matrix.json"
    _write_json(p, matrix.to_dict())
    return p


def render_coverage_report(
    events: list[MarketEvent],
    universe: CompetitorUniverse,
    output_dir: Path,
) -> Path:
    """Write coverage_report.json."""
    primary_ids = [e.entity_id for e in universe.primary_competitors]
    primary_total = len(primary_ids)

    entities_with_evidence: set[str] = set()
    dimensions_seen: set[str] = set()
    for ev in events:
        for eid in ev.entity_ids:
            entities_with_evidence.add(eid)
        if ev.dimension:
            dimensions_seen.add(ev.dimension)

    missing = [eid for eid in primary_ids if eid not in entities_with_evidence]

    # absence_of_evidence: entities present in history but with no NEW events
    # resolved events (change_status=resolved, materiality=low) count as absence
    resolved_entities = {
        eid for ev in events
        if ev.change_status == "resolved"
        for eid in ev.entity_ids
    }

    all_dims: list[Dimension] = [
        "capacity", "technology", "customers_partnerships",
        "financials", "policy_compliance", "market_demand",
    ]
    undercovered = [d for d in all_dims if d not in dimensions_seen]

    report = CoverageReport(
        primary_competitors_total=primary_total,
        primary_competitors_with_recent_evidence=(
            primary_total - len(missing)
        ),
        missing_entities=missing,
        undercovered_dimensions=undercovered,
        absence_of_evidence_entities=sorted(resolved_entities),
        generated_at=utc_now_iso(),
    )

    p = output_dir / "intermediate" / "market_competitor" / "coverage_report.json"
    _write_json(p, report.to_dict())
    return p


def render_watchlist(output_dir: Path) -> Path:
    """Write watchlist.json — empty template (cross-period tracking in PR #6)."""
    wl = Watchlist(generated_at=utc_now_iso())
    p = output_dir / "intermediate" / "market_competitor" / "watchlist.json"
    _write_json(p, wl.to_dict())
    return p


def render_evidence_pack(
    events: list[MarketEvent],
    ledger: ClaimLedger,
    output_dir: Path,
) -> Path:
    """Write evidence_pack.json — all structured evidence for LLM subagent consumption."""
    claim_index: dict[str, Any] = {}
    for claim in ledger:
        claim_index[claim.claim_id] = {
            "claim_id": claim.claim_id,
            "statement": claim.statement,
            "evidence_text": claim.evidence_text,
            "source_url": claim.source_url,
            "source_type": claim.source_type,
            "metadata": claim.metadata,
        }

    event_pack: list[dict[str, Any]] = []
    for ev in events:
        evidence = [claim_index.get(cid) for cid in ev.supporting_claim_ids if cid in claim_index]
        event_pack.append({
            **ev.to_dict(),
            "claims": evidence,
        })

    p = output_dir / "intermediate" / "market_competitor" / "evidence_pack.json"
    _write_json(p, {
        "events": event_pack,
        "event_count": len(event_pack),
        "generated_at": utc_now_iso(),
    })
    return p


def render_all(
    events: list[MarketEvent],
    ledger: ClaimLedger,
    universe: CompetitorUniverse,
    output_dir: Path,
) -> dict[str, str]:
    """Write all 5 intermediate artifacts. Returns path map."""
    return {
        "events": str(render_events_json(events, output_dir)),
        "competitor_matrix": str(render_competitor_matrix(events, universe, output_dir)),
        "coverage_report": str(render_coverage_report(events, universe, output_dir)),
        "watchlist": str(render_watchlist(output_dir)),
        "evidence_pack": str(render_evidence_pack(events, ledger, output_dir)),
    }
