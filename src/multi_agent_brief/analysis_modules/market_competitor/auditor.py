"""Market & Competitor specialist audits.

Implements AuditAgentInterface — plugs into CompositeAuditAgent when the
market_competitor module is enabled.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from multi_agent_brief.audit.interfaces import AuditAgentInterface
from multi_agent_brief.core.claim_ledger import ClaimLedger
from multi_agent_brief.core.schemas import AuditFinding, AuditReport, PipelineContext


class MarketCompetitorAuditor(AuditAgentInterface):
    """6 specialist audit checks for market & competitor analysis quality."""

    name = "market-competitor-auditor"

    def run_audit(
        self,
        markdown: str,
        ledger: ClaimLedger,
        context: PipelineContext | None = None,
    ) -> AuditReport:
        findings: list[AuditFinding] = []
        idx = 0

        # Load analysis_cards.json if available
        analysis_cards = self._load_analysis_cards(context)
        events_data = self._load_events(context)

        # 1 — comparison_missing_entity_evidence
        idx, findings = _check_comparison_evidence(
            analysis_cards, ledger, idx, findings,
        )

        # 2 — capacity_status_missing
        idx, findings = _check_capacity_status(events_data, idx, findings)

        # 3 — metric_basis_missing
        idx, findings = _check_metric_basis(analysis_cards, ledger, idx, findings)

        # 4 — unsupported_market_trend
        idx, findings = _check_market_trend(analysis_cards, idx, findings)

        # 5 — single_source_interpretation
        idx, findings = _check_single_source(analysis_cards, idx, findings)

        # 6 — competitor_coverage_gap
        idx, findings = _check_coverage_gap(context, idx, findings)

        return AuditReport(
            audit_status="pass",
            audit_score=100,
            findings=findings,
            metadata={"module": "market_competitor", "check_count": idx},
        )

    def _load_analysis_cards(self, context: PipelineContext | None) -> list[dict[str, Any]]:
        if not context:
            return []
        output_dir = context.output_dir
        path = Path(output_dir) / "intermediate" / "market_competitor" / "analysis_cards.json"
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                return data.get("cards", data.get("analysis_cards", []))
            except (json.JSONDecodeError, TypeError):
                return []
        return []

    def _load_events(self, context: PipelineContext | None) -> list[dict[str, Any]]:
        if not context:
            return []
        output_dir = context.output_dir
        path = Path(output_dir) / "intermediate" / "market_competitor" / "events.json"
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                return data.get("events", [])
            except (json.JSONDecodeError, TypeError):
                return []
        return []


# ── Audit checks ────────────────────────────────────────────────────────────

def _check_comparison_evidence(
    cards: list[dict[str, Any]],
    ledger: ClaimLedger,
    idx: int,
    findings: list[AuditFinding],
) -> tuple[int, list[AuditFinding]]:
    """Comparison findings require evidence for each entity cited."""
    comp_keywords = ("relative_position", "comparison", "gap", "narrowing", "widening",
                     "closing", "wider than", "narrower than", "vs", "versus", "领先",
                     "落后", "差距", "缩小", "扩大")
    for card in cards:
        headline = (card.get("headline") or "").lower()
        observation = (card.get("observation") or "").lower()
        text = headline + " " + observation
        if not any(kw in text for kw in comp_keywords):
            continue

        claim_ids = card.get("supporting_claim_ids", [])
        entities_with_evidence: set[str] = set()
        for cid in claim_ids:
            claim = ledger.get_claim(cid)
            if claim:
                for eid in claim.metadata.get("entity_ids", []):
                    entities_with_evidence.add(eid)

        if len(entities_with_evidence) < 2:
            idx += 1
            findings.append(AuditFinding(
                finding_id=f"MC_COMP_{idx:03d}",
                severity="high",
                finding_type="comparison_missing_entity_evidence",
                description=(
                    f"Analysis comparison in card '{card.get('analysis_id', '?')}' "
                    f"references entities but only has evidence for "
                    f"{len(entities_with_evidence)} side(s)."
                ),
                recommendation="Each entity in a comparison must have its own supporting claim.",
            ))
    return idx, findings


def _check_capacity_status(
    events: list[dict[str, Any]],
    idx: int,
    findings: list[AuditFinding],
) -> tuple[int, list[AuditFinding]]:
    """Capacity events must have a status (announced/under_construction/etc)."""
    for ev in events:
        if ev.get("event_type") not in ("capacity_expansion", "capacity_delay",
                                         "plant_opening", "plant_closure"):
            continue
        if not ev.get("status"):
            idx += 1
            findings.append(AuditFinding(
                finding_id=f"MC_CAP_{idx:03d}",
                severity="medium",
                finding_type="capacity_status_missing",
                description=(
                    f"Capacity event '{ev.get('event_id', '?')}' has no status. "
                    f"Announced capacity must be distinguished from operational capacity."
                ),
                recommendation="Set event status to announced/under_construction/operational/etc.",
            ))
    return idx, findings


def _check_metric_basis(
    cards: list[dict[str, Any]],
    ledger: ClaimLedger,
    idx: int,
    findings: list[AuditFinding],
) -> tuple[int, list[AuditFinding]]:
    """Numbers in analysis cards must have period + unit in supporting claims."""
    import re
    num_pat = re.compile(r"\b\d+(?:\.\d+)?\s*(?:%|GW|MW|GWh|billion|million|元|美元)")
    for card in cards:
        text = " ".join(str(v) for v in [
            card.get("headline", ""), card.get("observation", ""),
            card.get("implication_for_target", ""),
        ])
        numbers = num_pat.findall(text)
        if not numbers:
            continue

        claim_ids = card.get("supporting_claim_ids", [])
        for cid in claim_ids:
            claim = ledger.get_claim(cid)
            if not claim:
                continue
            evidence = (claim.evidence_text or "").lower()
            has_unit = any(u in evidence for u in ("gw", "mw", "gwh", "mwh", "billion",
                                                    "million", "percent", "%", "元", "美元"))
            has_period = any(p in evidence for p in ("q1", "q2", "q3", "q4", "fy", "fiscal",
                                                      "annual", "quarterly", "h1", "h2",
                                                      "年", "季度", "上半年", "下半年"))
            if not has_unit or not has_period:
                idx += 1
                findings.append(AuditFinding(
                    finding_id=f"MC_METRIC_{idx:03d}",
                    severity="medium",
                    finding_type="metric_basis_missing",
                    description=(
                        f"Analysis card '{card.get('analysis_id', '?')}' contains "
                        f"numeric values but claim '{cid}' lacks period and/or unit."
                    ),
                    recommendation="Ensure supporting claims include time period and measurement unit.",
                ))
    return idx, findings


def _check_market_trend(
    cards: list[dict[str, Any]],
    idx: int,
    findings: list[AuditFinding],
) -> tuple[int, list[AuditFinding]]:
    """Market trend claims need at least 2 supporting claims."""
    trend_keywords = ("trend", "shifting", "momentum", "trajectory", "outlook",
                      "趋势", "势头", "走向", "前景", "market shift")
    for card in cards:
        headline = (card.get("headline") or "").lower()
        observation = (card.get("observation") or "").lower()
        text = headline + " " + observation
        if not any(kw in text for kw in trend_keywords):
            continue

        claim_ids = card.get("supporting_claim_ids", [])
        if len(claim_ids) < 2:
            idx += 1
            findings.append(AuditFinding(
                finding_id=f"MC_TREND_{idx:03d}",
                severity="high",
                finding_type="unsupported_market_trend",
                description=(
                    f"Analysis card '{card.get('analysis_id', '?')}' makes a market "
                    f"trend claim with only {len(claim_ids)} supporting claim(s). "
                    f"Trend claims need at least 2."
                ),
                recommendation="Add more supporting evidence or weaken the claim language.",
            ))
    return idx, findings


def _check_single_source(
    cards: list[dict[str, Any]],
    idx: int,
    findings: list[AuditFinding],
) -> tuple[int, list[AuditFinding]]:
    """Single-source interpretations must have confidence=low."""
    for card in cards:
        claim_ids = card.get("supporting_claim_ids", [])
        if len(claim_ids) == 1 and card.get("confidence", "medium") != "low":
            # Only flag analytical cards, not fact-type
            if card.get("finding_type", "fact") != "fact":
                idx += 1
                findings.append(AuditFinding(
                    finding_id=f"MC_SINGLE_{idx:03d}",
                    severity="medium",
                    finding_type="single_source_interpretation",
                    description=(
                        f"Analysis card '{card.get('analysis_id', '?')}' has only 1 "
                        f"supporting claim but confidence is '{card.get('confidence', '?')}'. "
                        f"Single-source interpretations must set confidence='low'."
                    ),
                    recommendation="Set confidence='low' or add more supporting claims.",
                ))
    return idx, findings


def _check_coverage_gap(
    context: PipelineContext | None,
    idx: int,
    findings: list[AuditFinding],
) -> tuple[int, list[AuditFinding]]:
    """Primary competitors must have coverage."""
    if not context:
        return idx, findings
    output_dir = context.output_dir
    path = Path(output_dir) / "intermediate" / "market_competitor" / "coverage_report.json"
    if not path.exists():
        return idx, findings
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, TypeError):
        return idx, findings

    missing = data.get("missing_entities", [])
    primary_total = data.get("primary_competitors_total", 0)
    if missing and primary_total > 0:
        idx += 1
        findings.append(AuditFinding(
            finding_id=f"MC_COV_{idx:03d}",
            severity="high" if primary_total - len(missing) < primary_total / 2 else "medium",
            finding_type="competitor_coverage_gap",
            description=(
                f"Coverage gap: {len(missing)} of {primary_total} primary competitors "
                f"have no recent evidence ({', '.join(missing)})."
            ),
            recommendation="Expand search scope or mark quiet-week exception.",
        ))
    return idx, findings
