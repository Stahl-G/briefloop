from __future__ import annotations

import re
from dataclasses import dataclass, field

from multi_agent_brief.audit.deterministic import parse_date
from multi_agent_brief.core.claim_ledger import ClaimLedger
from multi_agent_brief.core.schemas import Claim, PipelineContext


@dataclass
class SelectionResult:
    selected: list[Claim]
    excluded: list[dict] = field(default_factory=list)
    stats: dict = field(default_factory=dict)


TOPIC_CAPS = {
    "policy": 24,
    "compliance": 18,
    "earnings": 24,
    "competitor": 18,
    "market": 24,
    "demand": 18,
    "rates": 12,
    "capital": 14,
    "technology": 12,
    "general": 18,
}


def select_reportable_claims(
    ledger: ClaimLedger,
    context: PipelineContext,
    previous_report_text: str = "",
) -> SelectionResult:
    scored: list[tuple[str, int, Claim]] = []
    excluded: list[dict] = []

    for claim in ledger:
        reason = exclusion_reason(claim, context)
        if reason:
            claim.metadata["excluded_reason"] = reason
            excluded.append({"claim_id": claim.claim_id, "reason": reason, "statement": claim.statement})
            continue

        topic = infer_topic(claim)
        repeat = previous_report_topic_hit(claim_search_text(claim), previous_report_text)
        score = novelty_score(claim, topic, repeat)
        claim.metadata.update({"topic": topic, "repeat": repeat, "novelty_score": score})
        scored.append((topic, score, claim))

    selected: list[Claim] = []
    omitted_by_topic: dict[str, int] = {}
    for topic, cap in TOPIC_CAPS.items():
        bucket = [item for item in scored if item[0] == topic]
        bucket.sort(key=lambda item: item[1], reverse=True)
        selected.extend(item[2] for item in bucket[:cap])
        if len(bucket) > cap:
            omitted_by_topic[topic] = len(bucket) - cap

    selected.sort(key=lambda claim: int(claim.metadata.get("novelty_score", 0)), reverse=True)
    selected = selected[: context.max_claims]

    return SelectionResult(
        selected=selected,
        excluded=excluded,
        stats={
            "input_claims": len(ledger),
            "selected_claims": len(selected),
            "excluded_claims": len(excluded),
            "omitted_by_topic": omitted_by_topic,
            "repeat_claims": sum(1 for claim in selected if claim.metadata.get("repeat") is True),
            "quiet_week": len(selected) < context.quiet_week_min_claims,
        },
    )


def ledger_from_selected(result: SelectionResult) -> ClaimLedger:
    return ClaimLedger(result.selected)


def exclusion_reason(claim: Claim, context: PipelineContext) -> str:
    if claim.claim_type == "needs_recrawl":
        return "needs_recrawl"
    if str(claim.metadata.get("source_tier", "")) == "T5":
        return "low_confidence_source"
    report_day = parse_date(context.report_date)
    published_day = parse_date(str(claim.metadata.get("published_at", "")))
    if report_day and published_day and context.max_source_age_days is not None:
        if (report_day - published_day).days > context.max_source_age_days:
            return "stale_source"
    return ""


def claim_search_text(claim: Claim) -> str:
    return " ".join(
        str(part or "")
        for part in (
            claim.claim_id,
            claim.statement,
            claim.evidence_text,
            claim.source_id,
            claim.source_url,
            claim.claim_type,
            claim.metadata.get("source_tier", ""),
        )
    )


def compact_text_for_match(text: str) -> str:
    return re.sub(r"[\W_]+", "", (text or "").lower(), flags=re.UNICODE)


def previous_report_topic_hit(text: str, previous_report_text: str) -> bool:
    if not text or not previous_report_text:
        return False
    compact = compact_text_for_match(text)
    previous = compact_text_for_match(previous_report_text)
    if len(compact) >= 32 and (compact[:80] in previous or compact[:48] in previous):
        return True
    theme_groups = [
        ("adcvd", "antidumping", "countervailing"),
        ("feoc", "45x", "48e"),
        ("uflpa", "detain", "forcedlabor"),
        ("chapter11", "bankrupt", "residential"),
        ("section337", "topcon", "patent"),
    ]
    for group in theme_groups:
        hits = sum(1 for term in group if term in compact and term in previous)
        if hits >= 2:
            return True
    return False


def infer_topic(claim: Claim) -> str:
    text = claim_search_text(claim).lower()
    if any(term in text for term in ("ad/cvd", "antidumping", "countervailing", "tariff", "section 337", "policy", "regulation")):
        return "policy"
    if any(term in text for term in ("uflpa", "cbp", "detain", "forced labor", "compliance")):
        return "compliance"
    if any(term in text for term in ("revenue", "eps", "ebitda", "gross margin", "earnings", "backlog", "guidance")):
        return "earnings"
    if any(term in text for term in ("competitor", "capacity", "expansion", "plant", "launch")):
        return "competitor"
    if any(term in text for term in ("price", "demand", "inventory", "market")):
        return "market"
    if any(term in text for term in ("installation", "generation", "ppa", "interconnection", "load growth")):
        return "demand"
    if any(term in text for term in ("treasury", "yield", "sofr", "fed", "rate")):
        return "rates"
    if any(term in text for term in ("acquisition", "investment", "fund", "capital")):
        return "capital"
    if any(term in text for term in ("topcon", "hjt", "bc", "technology", "efficiency")):
        return "technology"
    return "general"


def novelty_score(claim: Claim, topic: str, repeat: bool) -> int:
    text = claim_search_text(claim).lower()
    score = 10
    tier = str(claim.metadata.get("source_tier", "") or "")
    score += {"T1": 28, "T2": 24, "T3": 18, "T4": 14, "T5": 2}.get(tier, 8)
    if claim.claim_type in {"number", "date"}:
        score += 8
    if claim.claim_type in {"risk", "forecast"}:
        score += 6
    high_signal_terms = [
        "tariff",
        "section 337",
        "investigation",
        "earnings",
        "eps",
        "ebitda",
        "backlog",
        "guidance",
        "bankrupt",
        "acquisition",
        "capex",
        "data center",
        "storage",
    ]
    score += sum(6 for term in high_signal_terms if term in text)
    if topic in {"policy", "earnings", "market"}:
        score += 4
    if repeat:
        score -= 35
    return score

