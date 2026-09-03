"""Price-versus-narrative coherence check for equity-periodic briefs.

A report whose narrative leans one way while the core subject's frozen
weekly return moved hard the other way must answer the obvious reader
question: why did the price move?  This check is structural — it never
judges whether an explanation is correct, only that the move is stated
(traceably to the frozen snapshot) and that either a risk-type claim or
an explicit no-evidence gap is present next to it.
"""

from __future__ import annotations

import re
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
_PROVENANCE_MARKERS = ("行情", "snapshot", "market data")
_DECLINE_WORDS = (
    "下跌",
    "跌",
    "下滑",
    "回落",
    "decline",
    "fell",
    "down",
    "drop",
    "slump",
)
_RISE_WORDS = ("上涨", "涨", "上升", "回升", "rise", "rose", "gain", "climb")
_NUMBER_RE = re.compile(r"[+-]?[−-]?\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?")
_VALUE_TOLERANCE = 0.05


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


def _move_is_stated(body: str, core_ticker: str, return_1w: float) -> bool:
    """True only when the section states THIS ticker's move with direction.

    Binds all four review requirements: the core ticker must be named, a
    provenance marker must be present, the percentage must match within
    tolerance, and the direction (decline/rise wording or a signed
    token) must agree with the sign of the frozen return.  Another
    company's same-magnitude move can never satisfy this.
    """

    if core_ticker.lower() not in body.lower():
        return False
    if not any(marker in body.lower() for marker in _PROVENANCE_MARKERS):
        return False
    negative = return_1w < 0
    for match in _NUMBER_RE.finditer(body):
        token = match.group(0).replace(",", "").replace("−", "-")
        try:
            number = float(token)
        except ValueError:
            continue
        if abs(abs(number) - abs(return_1w)) > _VALUE_TOLERANCE:
            continue
        line_start = body.rfind("\n", 0, match.start()) + 1
        line_end = body.find("\n", match.end())
        line = body[line_start : len(body) if line_end == -1 else line_end].lower()
        if negative:
            if number < 0:
                return True
            if any(word in line for word in _DECLINE_WORDS):
                return True
        else:
            if number > 0 and (
                token.startswith("+")
                or any(word in line for word in _RISE_WORDS)
            ):
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
    if reaction is None or not _move_is_stated(
        reaction.body, core_ticker, return_1w
    ):
        return [
            _finding(
                finding_type="price_narrative_divergence_unaddressed",
                description=(
                    f"The core subject {core_ticker} moved "
                    f"{return_1w:+.2f}% over the frozen one-week window "
                    f"(threshold ±{threshold:.0f}%) but no market-reaction "
                    "section names that ticker's move with matching "
                    "direction and snapshot provenance."
                ),
                recommendation=(
                    "Add a market-reaction section that states the core "
                    "ticker's one-week move (direction and magnitude from "
                    "the frozen snapshot) and either cites a risk-type "
                    "claim or explicitly discloses that no cause evidence "
                    "was found."
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
