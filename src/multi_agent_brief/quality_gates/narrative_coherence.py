"""Price-versus-narrative coherence check for equity-periodic briefs.

A report whose narrative leans one way while the core subject's frozen
weekly return moved hard the other way must answer the obvious reader
question: why did the price move?  This check is structural — it never
judges whether an explanation is correct, only that the move is stated
(traceably to the frozen snapshot) and that either a risk-type claim or
an explicit no-evidence gap is present next to it.
"""

from __future__ import annotations

from typing import Any

from multi_agent_brief.core.citations import parse_internal_citation_markers
from multi_agent_brief.core.claim_ledger import ClaimLedger
from multi_agent_brief.quality_gates.section_contract import (
    limitations_section,
    section_matching,
    sections_from_markdown,
)

DEFAULT_DIVERGENCE_THRESHOLD_PCT = 10.0

_MARKET_REACTION_INTENT = "market_reaction_divergence"
_GAP_MARKERS = (
    "无证据",
    "未证实",
    "缺口",
    "原因不明",
    "no evidence",
    "not verified",
    "unverified",
    "gap",
)


def _finding(
    *,
    finding_type: str,
    description: str,
    recommendation: str,
    core_ticker: str,
    return_1w: float,
    threshold: float,
) -> dict[str, Any]:
    return {
        "finding_type": finding_type,
        "gate_id": "material_fact",
        "category": "price_narrative_coherence",
        "severity": "high",
        "blocking_level": "blocking",
        "repair_owner": "editor",
        "stage_id": "editor",
        "artifact_id": "audited_brief",
        "claim_id": None,
        "source_id": None,
        "description": description,
        "recommendation": recommendation,
        "metadata": {
            "core_ticker": core_ticker,
            "return_1w_pct": return_1w,
            "threshold_pct": threshold,
        },
    }


def _body_mentions_value(body: str, value: float) -> bool:
    for decimals in (2, 1):
        token = f"{abs(value):.{decimals}f}"
        if token in body:
            return True
    return False


def _cited_risk_claim_present(body: str, ledger: ClaimLedger | None) -> bool:
    if ledger is None:
        return False
    valid_ids = {claim.claim_id for claim in ledger if claim.claim_id}
    for marker in parse_internal_citation_markers(
        body,
        valid_claim_ids=valid_ids,
        include_bare_claim_ids=False,
    ):
        if marker.status != "resolved" or not marker.claim_id:
            continue
        claim = ledger.get_claim(marker.claim_id)
        if claim is not None and str(claim.claim_type) == "risk":
            return True
    return False


def _gap_disclosed(text: str) -> bool:
    lowered = text.lower()
    return any(marker.lower() in lowered for marker in _GAP_MARKERS)


def price_narrative_findings(
    markdown: str,
    *,
    core_ticker: str | None,
    return_1w: float | None,
    ledger: ClaimLedger | None,
    threshold_pct: float | None = None,
) -> list[dict[str, Any]]:
    """Blocking findings when a large core-subject move goes unaddressed."""

    if core_ticker is None or return_1w is None:
        return []
    threshold = (
        threshold_pct
        if threshold_pct is not None and 0 < threshold_pct <= 100
        else DEFAULT_DIVERGENCE_THRESHOLD_PCT
    )
    if abs(return_1w) < threshold:
        return []
    sections = sections_from_markdown(markdown)
    reaction = section_matching(sections, _MARKET_REACTION_INTENT)
    if reaction is None or not _body_mentions_value(reaction.body, return_1w):
        return [
            _finding(
                finding_type="price_narrative_divergence_unaddressed",
                description=(
                    f"The core subject {core_ticker} moved "
                    f"{return_1w:+.2f}% over the frozen one-week window "
                    f"(threshold ±{threshold:.0f}%) but the brief has no "
                    "market-reaction section stating the move."
                ),
                recommendation=(
                    "Add a market-reaction section that states the frozen "
                    "one-week move and either cites a risk-type claim or "
                    "explicitly discloses that no cause evidence was found."
                ),
                core_ticker=core_ticker,
                return_1w=return_1w,
                threshold=threshold,
            )
        ]
    body = reaction.body
    if _cited_risk_claim_present(body, ledger) or _gap_disclosed(body):
        return []
    limitations = limitations_section(sections)
    if limitations is not None and _gap_disclosed(limitations.body):
        return []
    return [
        _finding(
            finding_type="price_move_cause_undisclosed",
            description=(
                f"The market-reaction section states the {core_ticker} "
                f"{return_1w:+.2f}% one-week move but neither cites a "
                "risk-type claim nor discloses that no cause evidence "
                "was found."
            ),
            recommendation=(
                "Cite a risk-type Claim Ledger entry for the move, or "
                "state explicitly that no verified cause evidence exists "
                "in the ledger (a visible gap is acceptable; an invisible "
                "one is not)."
            ),
            core_ticker=core_ticker,
            return_1w=return_1w,
            threshold=threshold,
        )
    ]


__all__ = [
    "DEFAULT_DIVERGENCE_THRESHOLD_PCT",
    "price_narrative_findings",
]
